import httpx
import json
import logging
import re
from typing import AsyncGenerator, List, Optional, Dict, Any
from app.config import OLLAMA_BASE_URL, get_settings

logger = logging.getLogger(__name__)

# =============================================================================
# SECURITY CONFIGURATION
# =============================================================================

# Pattern for valid Ollama model names
# Valid format: name:tag where name can have "/" for namespaced models
# Examples: llama3.2, qwen:7b, library/llama3, user/model:latest
MODEL_NAME_PATTERN = re.compile(r'^[a-zA-Z0-9][a-zA-Z0-9._-]*(/[a-zA-Z0-9][a-zA-Z0-9._-]*)*(:[\w.+-]+)?$')

# Maximum model name length
MAX_MODEL_NAME_LENGTH = 256


def _validate_model_name(model: str) -> tuple[bool, str]:
    """
    Validate an Ollama model name for security.

    Returns:
        (is_valid, error_message)
    """
    if not model:
        return False, "Model name is required"

    if len(model) > MAX_MODEL_NAME_LENGTH:
        return False, f"Model name exceeds maximum length ({MAX_MODEL_NAME_LENGTH})"

    # Check for path traversal attempts
    if '..' in model or model.startswith('/'):
        return False, "Invalid model name format"

    # Check against pattern
    if not MODEL_NAME_PATTERN.match(model):
        return False, "Model name contains invalid characters"

    return True, ""


class OllamaService:
    def __init__(self):
        self.base_url = OLLAMA_BASE_URL
        self.client = httpx.AsyncClient(timeout=300.0)

    async def list_models(self) -> List[Dict[str, Any]]:
        """Fetch available models from Ollama"""
        response = await self.client.get(f"{self.base_url}/api/tags")
        response.raise_for_status()
        data = response.json()
        return data.get("models", [])

    async def get_chat_models_with_capabilities(self) -> List[Dict[str, Any]]:
        """Get only chat-capable models with their capabilities.

        Filters out embedding models and returns capability metadata for each model.
        """
        response = await self.client.get(f"{self.base_url}/api/tags")
        response.raise_for_status()
        all_models = response.json().get("models", [])

        chat_models = []
        for model in all_models:
            model_name = model.get("name", "")

            # Get detailed info including capabilities
            try:
                show_response = await self.client.post(
                    f"{self.base_url}/api/show",
                    json={"name": model_name}
                )
                info = show_response.json() if show_response.status_code == 200 else {}
            except Exception as e:
                logger.warning(f"Failed to get info for {model_name}: {e}")
                info = {}

            capabilities = info.get("capabilities", [])
            family = model.get("details", {}).get("family", "").lower()

            # Skip embedding-only models
            if "bert" in family:
                logger.debug(f"Skipping {model_name}: BERT family (embedding model)")
                continue

            # Skip if no completion capability and name suggests embedding
            if "completion" not in capabilities:
                if "embed" in model_name.lower():
                    logger.debug(f"Skipping {model_name}: no completion + embed in name")
                    continue
                # Also skip if capabilities exist but don't include completion
                if capabilities:
                    logger.debug(f"Skipping {model_name}: has capabilities but no completion")
                    continue

            chat_models.append({
                "name": model_name,
                "size": model.get("size"),
                "parameter_size": model.get("details", {}).get("parameter_size"),
                "family": family,
                "quantization_level": model.get("details", {}).get("quantization_level"),
                "modified_at": model.get("modified_at"),
                "capabilities": capabilities,
                "supports_tools": "tools" in capabilities,
                "supports_vision": "vision" in capabilities,
                "supports_thinking": "thinking" in capabilities,
            })

        logger.info(f"Found {len(chat_models)} chat models out of {len(all_models)} total")
        return chat_models

    async def get_model_capabilities(self, model_name: str) -> dict:
        """Get model capabilities from Ollama API"""
        try:
            response = await self.client.post(
                f"{self.base_url}/api/show",
                json={"name": model_name}
            )
            if response.status_code == 200:
                data = response.json()
                return {
                    "capabilities": data.get("capabilities", []),
                    "details": data.get("details", {}),
                    "template": data.get("template", "")
                }
        except Exception as e:
            logger.warning(f"Failed to get model capabilities: {e}")
        return {"capabilities": [], "details": {}, "template": ""}

    async def is_vision_model(self, model_name: str) -> bool:
        """Check if a model supports vision/images"""
        info = await self.get_model_capabilities(model_name)
        logger.debug(f"Checking vision for {model_name}, capabilities: {info['capabilities']}")

        # Check capabilities array from Ollama API
        if "vision" in info["capabilities"]:
            logger.debug(f"{model_name} has vision capability from API")
            return True

        # Fallback: check model name for vision keywords
        vision_keywords = ['vision', 'vl', 'llava', 'bakllava', 'moondream', 'cogvlm', 'yi-vl', 'minicpm-v']
        model_lower = model_name.lower()
        for keyword in vision_keywords:
            if keyword in model_lower:
                logger.debug(f"{model_name} matched vision keyword: {keyword}")
                return True

        logger.debug(f"{model_name} is NOT a vision model")
        return False

    async def supports_tools(self, model_name: str) -> bool:
        """Check if a model supports function/tool calling.

        Only trusts the official 'capabilities' array from Ollama API.
        Template heuristics are unreliable and lead to false positives.
        """
        info = await self.get_model_capabilities(model_name)

        # Only trust the official capabilities array from Ollama API
        # Template-based heuristics are unreliable (e.g., "function" is too generic)
        if "tools" in info.get("capabilities", []):
            return True

        return False

    async def get_model_context_window(self, model_name: str) -> int:
        """Get the context window size for a model from Ollama API."""
        try:
            response = await self.client.post(
                f"{self.base_url}/api/show",
                json={"name": model_name},
                timeout=10.0
            )
            if response.status_code == 200:
                data = response.json()

                # Check modelfile for num_ctx parameter
                modelfile = data.get("modelfile", "")
                match = re.search(r'PARAMETER\s+num_ctx\s+(\d+)', modelfile, re.IGNORECASE)
                if match:
                    return int(match.group(1))

                # Check model_info for context length keys
                model_info = data.get("model_info", {})
                for key, value in model_info.items():
                    if "context" in key.lower() and isinstance(value, (int, float)):
                        return int(value)

                # Heuristic based on model size
                name_lower = model_name.lower()
                if any(x in name_lower for x in ["70b", "72b"]):
                    return 8192
                elif any(x in name_lower for x in ["32b", "34b"]):
                    return 8192
                elif any(x in name_lower for x in ["13b", "14b"]):
                    return 4096
                else:
                    return 4096  # Default

        except Exception as e:
            logger.warning(f"Failed to get context window for {model_name}: {e}")

        return 4096  # Fallback default

    async def get_comprehensive_capabilities(self, model_name: str) -> dict:
        """Get all model capabilities including context window."""
        caps = await self.get_model_capabilities(model_name)
        context_window = await self.get_model_context_window(model_name)

        return {
            "supports_vision": await self.is_vision_model(model_name),
            "supports_tools": await self.supports_tools(model_name),
            "supports_thinking": "thinking" in caps.get("capabilities", []),
            "context_window": context_window,
            "capabilities": caps.get("capabilities", [])
        }

    def build_system_prompt(self, persona: Optional[str] = None, has_vision: bool = True, has_tools: bool = True) -> str:
        """Build system prompt with optional persona"""
        if has_tools:
            base_prompt = """You are a helpful AI assistant with access to tools.

INFORMATION PRIORITY (highest to lowest):
1. Files attached to this conversation - these are the user's primary reference
2. search_knowledge_base - user's uploaded documents (PDFs, code, text files)
3. search_conversations - context from previous chats with this user
4. web_search / browse_website - for current information (your training data may be outdated)
5. Your training knowledge - use only when tools return no results

CRITICAL RULES:
- ALWAYS use tools before answering. Call multiple tools to gather the best context.
- NEVER fabricate or imagine information. Accuracy matters more than having every answer.
- If tools return no results, say so honestly. Do not make up information.
- Assume your training data is outdated. Use web_search for current events, dates, or facts.
- You MUST actually call tools - do not describe or roleplay using them.

RESPONSE STYLE:
- Keep responses to a few focused paragraphs unless the user asks for more detail.
- After answering, offer to elaborate if the topic warrants deeper exploration.
- Cite your sources (which tool provided the information).

AVAILABLE TOOLS:
- search_knowledge_base: Search user's uploaded documents
- search_conversations: Search previous chat history
- web_search: Search the web for current information
- browse_website: Read a specific URL's content"""
        else:
            base_prompt = """You are a helpful AI assistant. You provide accurate, helpful, and thoughtful responses to user questions.

Keep responses to a few focused paragraphs unless more detail is requested. Accuracy matters more than speed - if you're unsure about something, say so."""

        if persona:
            persona_intro = f"""You are embodying the following persona. Stay in character at all times and never break character, even if asked directly about being an AI or your true nature.

PERSONA:
{persona}

---

"""
            if has_tools:
                return persona_intro + """You have access to tools: search_knowledge_base, search_conversations, web_search, and browse_website. Use them to gather accurate information before responding."""
            else:
                return persona_intro.rstrip()

        return base_prompt

    def build_messages(
        self,
        user_message: str,
        history: List[Dict[str, Any]],
        persona: Optional[str] = None,
        images: Optional[List[str]] = None,
        is_vision_model: bool = True,
        has_tools: bool = True
    ) -> List[Dict[str, Any]]:
        """Build messages array for Ollama API"""
        messages = []

        # System prompt with persona
        messages.append({
            "role": "system",
            "content": self.build_system_prompt(persona, has_vision=is_vision_model, has_tools=has_tools)
        })

        # Add conversation history (strip images if not vision model)
        for msg in history:
            if is_vision_model:
                messages.append(msg)
            else:
                # Remove images from non-vision model messages
                clean_msg = {k: v for k, v in msg.items() if k != "images"}
                messages.append(clean_msg)

        # Add current user message
        user_msg = {"role": "user", "content": user_message}
        if images and is_vision_model:
            user_msg["images"] = images
            logger.debug(f"Adding {len(images)} images to user message")
        messages.append(user_msg)

        logger.debug(f"Built {len(messages)} messages, user_msg has images: {'images' in user_msg}")
        return messages

    def build_messages_with_system(
        self,
        system_prompt: str,
        user_message: str,
        history: List[Dict[str, Any]],
        images: Optional[List[str]] = None,
        is_vision_model: bool = True,
        supports_tools: bool = True
    ) -> List[Dict[str, Any]]:
        """Build messages with an explicit system prompt (no internal generation).

        Args:
            system_prompt: The system prompt to use
            user_message: Current user message
            history: Conversation history
            images: Images to include (only for vision models)
            is_vision_model: Whether the model supports vision
            supports_tools: Whether the model supports tool calling
        """
        messages = []

        # Use provided system prompt directly
        messages.append({
            "role": "system",
            "content": system_prompt
        })

        # Add conversation history
        # Strip images if not vision model, strip tool_calls if not tool-capable
        for msg in history:
            clean_msg = dict(msg)
            if not is_vision_model and "images" in clean_msg:
                del clean_msg["images"]
            if not supports_tools and "tool_calls" in clean_msg:
                del clean_msg["tool_calls"]
            messages.append(clean_msg)

        # Add current user message
        user_msg = {"role": "user", "content": user_message}
        if images and is_vision_model:
            user_msg["images"] = images
        messages.append(user_msg)

        return messages

    async def chat_stream(
        self,
        messages: List[Dict[str, Any]],
        model: str,
        tools: Optional[List[Dict]] = None,
        options: Optional[Dict] = None,
        think: Optional[bool] = None
    ) -> AsyncGenerator[Dict[str, Any], None]:
        """Stream chat response from Ollama"""
        # SECURITY: Validate model name
        is_valid, error = _validate_model_name(model)
        if not is_valid:
            logger.error(f"Invalid model name rejected: {error}")
            raise ValueError(f"Invalid model name: {error}")

        payload = {
            "model": model,
            "messages": messages,
            "stream": True
        }

        if tools:
            payload["tools"] = tools

        if options:
            payload["options"] = options

        if think is not None:
            # Some models (gpt-oss) need string values: "low", "medium", "high"
            # Others (qwq, deepseek-r1) need boolean
            model_lower = model.lower()
            if 'gpt-oss' in model_lower:
                # gpt-oss uses string reasoning effort levels
                payload["think"] = "high" if think else "low"
                logger.debug(f"gpt-oss model: setting think='{'high' if think else 'low'}'")
            else:
                payload["think"] = think
                logger.debug(f"Sending request with think={think}")

        logger.debug(f"Payload keys: {list(payload.keys())}")

        async with self.client.stream(
            "POST",
            f"{self.base_url}/api/chat",
            json=payload
        ) as response:
            response.raise_for_status()
            async for line in response.aiter_lines():
                if line:
                    try:
                        yield json.loads(line)
                    except json.JSONDecodeError:
                        continue

    async def chat_complete(
        self,
        messages: List[Dict[str, Any]],
        model: str,
        tools: Optional[List[Dict]] = None,
        options: Optional[Dict] = None
    ) -> Dict[str, Any]:
        """Non-streaming chat completion"""
        # SECURITY: Validate model name
        is_valid, error = _validate_model_name(model)
        if not is_valid:
            logger.error(f"Invalid model name rejected: {error}")
            raise ValueError(f"Invalid model name: {error}")

        payload = {
            "model": model,
            "messages": messages,
            "stream": False
        }

        if tools:
            payload["tools"] = tools

        if options:
            payload["options"] = options

        response = await self.client.post(
            f"{self.base_url}/api/chat",
            json=payload
        )
        response.raise_for_status()
        return response.json()

    async def close(self):
        """Close the HTTP client"""
        await self.client.aclose()

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.close()


# Global service instance
ollama_service = OllamaService()
