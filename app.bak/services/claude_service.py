"""
Claude API Service via OpenClaw

Provides an OpenAI-compatible interface to Claude through the local OpenClaw API.
This replaces the Ollama service for chat completions.

Session Routing:
- Primary user (OPENCLAW_PRIMARY_USER_ID) shares the main OpenClaw session for unified context
- Other users get stable isolated sessions via OpenClaw's user-based routing
"""
import httpx
import json
import logging
from typing import AsyncGenerator, List, Optional, Dict, Any

from app.config import (
    OPENCLAW_API_URL, OPENCLAW_API_KEY, CLAUDE_MODEL,
    OPENCLAW_PRIMARY_USER_ID, OPENCLAW_PRIMARY_USERNAME, OPENCLAW_MAIN_SESSION_KEY
)

logger = logging.getLogger(__name__)


class ClaudeService:
    """Service for interacting with Claude via OpenClaw's OpenAI-compatible API."""
    
    def __init__(self):
        self.base_url = OPENCLAW_API_URL.rstrip('/')
        self.api_key = OPENCLAW_API_KEY
        self.model = CLAUDE_MODEL  # Default model from config
        self.client = httpx.AsyncClient(timeout=300.0)
        logger.info(f"ClaudeService initialized: base_url={self.base_url}, model={self.model}")
    
    def _is_primary_user(self, user_id: Optional[int] = None, username: Optional[str] = None) -> bool:
        """Check if user is the primary user (shares main session).
        
        Matches by user ID OR username (case-insensitive).
        """
        if user_id and OPENCLAW_PRIMARY_USER_ID and user_id == OPENCLAW_PRIMARY_USER_ID:
            return True
        if username and OPENCLAW_PRIMARY_USERNAME and username.lower() == OPENCLAW_PRIMARY_USERNAME:
            return True
        return False
    
    def _get_headers(
        self,
        user_id: Optional[int] = None,
        username: Optional[str] = None,
        conversation_id: Optional[str] = None,
    ) -> Dict[str, str]:
        """Get headers for API requests.

        NOTE: BrinChat maintains its own conversation history and sends it each request.
        Using a dedicated OpenClaw session per BrinChat conversation avoids cross-talk
        from internal system events (e.g., HEARTBEAT) and keeps threads isolated.
        """
        headers = {
            "Content-Type": "application/json"
        }
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        if self._is_primary_user(user_id, username):
            # Per-conversation OpenClaw session key (prevents HEARTBEAT/system noise leaking into BrinChat responses)
            if conversation_id:
                session_key = f"agent:main:brinchat:{conversation_id}"
            else:
                session_key = OPENCLAW_MAIN_SESSION_KEY
            headers["x-openclaw-session-key"] = session_key
            logger.info(f"Routing primary user (id={user_id}, name={username}) to OpenClaw session: {session_key}")
        else:
            # IMPORTANT: For non-primary users we MUST still set a session key.
            # If we don't, OpenClaw may default to the last-active (often heartbeat) session,
            # causing responses like HEARTBEAT_OK to leak into BrinChat.
            if user_id:
                who = (username or str(user_id)).lower()
                base = f"agent:main:openai-user:brinchat:{who}"
                session_key = f"{base}:{conversation_id}" if conversation_id else base
                headers["x-openclaw-session-key"] = session_key
                logger.info(f"Routing non-primary user (id={user_id}, name={username}) to OpenClaw session: {session_key}")

        return headers
    
    def _get_user_field(
        self,
        user_id: Optional[int] = None,
        username: Optional[str] = None,
        conversation_id: Optional[str] = None,
    ) -> Optional[str]:
        """Get OpenAI 'user' field for stable session routing.

        For non-primary users, we also include conversation_id to isolate threads.
        """
        if not user_id:
            return None

        # Primary user uses header routing, not user field
        if self._is_primary_user(user_id, username):
            return None

        # Other users get stable isolated sessions (per conversation if available)
        suffix = f":{conversation_id}" if conversation_id else ""
        user_key = f"brinchat:{username or user_id}{suffix}"
        logger.debug(f"Non-primary user {user_id} -> stable session: {user_key}")
        return user_key
    
    async def is_vision_model(self, model_name: str = None) -> bool:
        """Claude supports vision natively."""
        return True
    
    async def supports_tools(self, model_name: str = None) -> bool:
        """Claude supports tool/function calling natively."""
        return True
    
    async def get_model_capabilities(self, model_name: str = None) -> dict:
        """Get Claude's capabilities - always returns full capabilities."""
        return {
            "capabilities": ["completion", "tools", "vision"],
            "details": {
                "family": "claude",
                "parameter_size": "unknown"
            },
            "template": ""
        }
    
    async def get_model_context_window(self, model_name: str = None) -> int:
        """Claude has a large context window."""
        return 200000  # 200k tokens for Claude
    
    async def get_comprehensive_capabilities(self, model_name: str = None) -> dict:
        """Get all Claude capabilities."""
        return {
            "supports_vision": True,
            "supports_tools": True,
            "supports_thinking": True,
            "context_window": 200000,
            "capabilities": ["completion", "tools", "vision", "thinking"]
        }
    
    def build_system_prompt(self, persona: Optional[str] = None, has_vision: bool = True, has_tools: bool = True) -> str:
        """Build system prompt with optional persona."""
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
- image: Generate, transform, or upscale images
- video: Generate videos from text or images
- add_memory / query_memory: Store and recall information about the user"""
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
        """Build messages array for OpenAI-compatible API."""
        messages = []

        # System prompt with persona
        messages.append({
            "role": "system",
            "content": self.build_system_prompt(persona, has_vision=is_vision_model, has_tools=has_tools)
        })

        # Add conversation history
        for msg in history:
            messages.append(msg)

        # Add current user message
        user_msg = {"role": "user", "content": user_message}
        
        # Handle images for vision (OpenAI format with base64)
        if images and is_vision_model:
            content = [{"type": "text", "text": user_message}]
            for img in images:
                # Assume img is base64 encoded
                content.append({
                    "type": "image_url",
                    "image_url": {
                        "url": f"data:image/jpeg;base64,{img}" if not img.startswith("data:") else img
                    }
                })
            user_msg["content"] = content
            logger.debug(f"Adding {len(images)} images to user message")
        
        messages.append(user_msg)
        logger.debug(f"Built {len(messages)} messages")
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
        """Build messages with an explicit system prompt."""
        messages = []

        # Use provided system prompt directly
        messages.append({
            "role": "system",
            "content": system_prompt
        })

        # Add conversation history
        for msg in history:
            clean_msg = dict(msg)
            # Remove tool_calls if not needed (though Claude always supports them)
            messages.append(clean_msg)

        # Add current user message
        user_msg = {"role": "user", "content": user_message}
        
        # Handle images for vision
        if images and is_vision_model:
            content = [{"type": "text", "text": user_message}]
            for idx, img in enumerate(images):
                # If img already has data URL format, use it directly (preserves MIME type)
                # Otherwise, wrap raw base64 with jpeg (legacy fallback)
                if img.startswith("data:"):
                    img_url = img
                    # Extract MIME type for logging
                    mime_match = img[5:img.find(';')] if ';' in img else 'unknown'
                    logger.info(f"[Image {idx}] Using full data URL with MIME: {mime_match}")
                else:
                    img_url = f"data:image/jpeg;base64,{img}"
                    logger.warning(f"[Image {idx}] Raw base64 received, defaulting to image/jpeg")
                content.append({
                    "type": "image_url",
                    "image_url": {
                        "url": img_url
                    }
                })
            user_msg["content"] = content
        
        messages.append(user_msg)
        return messages

    def _convert_tools_to_openai_format(self, tools: Optional[List[Dict]]) -> Optional[List[Dict]]:
        """Ensure tools are in OpenAI function calling format."""
        if not tools:
            return None
        
        openai_tools = []
        for tool in tools:
            if "function" in tool:
                openai_tools.append({
                    "type": "function",
                    "function": tool["function"]
                })
            else:
                # Already in correct format
                openai_tools.append(tool)
        
        return openai_tools

    async def chat_stream(
        self,
        messages: List[Dict[str, Any]],
        model: str = None,
        tools: Optional[List[Dict]] = None,
        options: Optional[Dict] = None,
        think: Optional[bool] = None,
        user_id: Optional[int] = None,
        username: Optional[str] = None,
        conversation_id: Optional[str] = None,
    ) -> AsyncGenerator[Dict[str, Any], None]:
        """Stream chat response from Claude via OpenClaw.
        
        Args:
            messages: Conversation messages
            model: Model identifier
            tools: Tool definitions
            options: Generation options
            think: Enable thinking mode
            user_id: User ID for session routing (primary user shares main session)
            username: Username for stable session key generation
        """
        
        payload = {
            "model": model or self.model,
            "messages": messages,
            "stream": True
        }

        # Add user field for non-primary users (stable isolated sessions)
        user_field = self._get_user_field(user_id, username, conversation_id)
        if user_field:
            payload["user"] = user_field

        # Convert tools to OpenAI format
        if tools:
            openai_tools = self._convert_tools_to_openai_format(tools)
            if openai_tools:
                payload["tools"] = openai_tools

        # Apply options as parameters
        if options:
            if "temperature" in options:
                payload["temperature"] = options["temperature"]
            if "top_p" in options:
                payload["top_p"] = options["top_p"]
            if "num_ctx" in options or "max_tokens" in options:
                payload["max_tokens"] = options.get("max_tokens", options.get("num_ctx", 4096))

        logger.debug(f"Chat stream: model={payload.get('model')}, messages={len(messages)}, tools={bool(tools)}, user_id={user_id}")
        # Debug: dump EXACT payload being sent to OpenClaw
        import json as _json
        for i, msg in enumerate(messages[:3]):  # First 3 messages
            role = msg.get("role", "?")
            content = msg.get("content")
            if isinstance(content, list):
                logger.warning(f"[PAYLOAD] msg[{i}] role={role} content=array[{len(content)}]")
            else:
                preview = str(content)[:100] if content else ""
                logger.warning(f"[PAYLOAD] msg[{i}] role={role} len={len(str(content)) if content else 0} preview='{preview}'")

        try:
            async with self.client.stream(
                "POST",
                f"{self.base_url}/chat/completions",
                json=payload,
                headers=self._get_headers(user_id, username, conversation_id)
            ) as response:
                if response.status_code >= 400:
                    error_body = await response.aread()
                    logger.error(f"OpenClaw API error {response.status_code}: {error_body.decode()[:500]}")
                    response.raise_for_status()
                
                collected_tool_calls = []
                
                async for line in response.aiter_lines():
                    # OpenClaw uses SSE. Be permissive about the exact "data:" spacing.
                    if not line or not line.startswith("data:"):
                        continue

                    # Remove "data:" prefix (with or without a following space)
                    data = line[5:].lstrip()
                    if data == "[DONE]":
                        yield {"done": True}
                        break
                    
                    try:
                        chunk = json.loads(data)
                        choice = chunk.get("choices", [{}])[0]
                        delta = choice.get("delta", {})
                        
                        # Build response in Ollama-like format for compatibility
                        result = {"message": {}}
                        
                        # Handle content
                        if delta.get("content"):
                            result["message"]["content"] = delta["content"]
                        
                        # Handle tool calls
                        if delta.get("tool_calls"):
                            for tc in delta["tool_calls"]:
                                index = tc.get("index", 0)
                                
                                # Extend collected_tool_calls if needed
                                while len(collected_tool_calls) <= index:
                                    collected_tool_calls.append({
                                        "function": {"name": "", "arguments": ""}
                                    })
                                
                                # Update the tool call at this index
                                if tc.get("function", {}).get("name"):
                                    collected_tool_calls[index]["function"]["name"] = tc["function"]["name"]
                                if tc.get("function", {}).get("arguments"):
                                    collected_tool_calls[index]["function"]["arguments"] += tc["function"]["arguments"]
                        
                        # Check if this is the final chunk (has finish_reason)
                        finish_reason = choice.get("finish_reason")
                        if finish_reason:
                            # If we collected tool calls, include them in final message
                            if collected_tool_calls:
                                # Parse arguments from string to dict
                                for tc in collected_tool_calls:
                                    if isinstance(tc["function"]["arguments"], str):
                                        try:
                                            tc["function"]["arguments"] = json.loads(tc["function"]["arguments"])
                                        except json.JSONDecodeError:
                                            tc["function"]["arguments"] = {}
                                
                                result["message"]["tool_calls"] = collected_tool_calls
                            
                            result["done"] = True
                        
                        if result["message"] or result.get("done"):
                            yield result
                            
                    except json.JSONDecodeError:
                        continue
                        
        except httpx.HTTPStatusError as e:
            logger.error(f"HTTP error from OpenClaw API: {e.response.status_code} - {e.request.url}")
            raise
        except Exception as e:
            logger.error(f"Error streaming from OpenClaw: {type(e).__name__}: {e}")
            raise

    async def chat_complete(
        self,
        messages: List[Dict[str, Any]],
        model: str = None,
        tools: Optional[List[Dict]] = None,
        options: Optional[Dict] = None,
        user_id: Optional[int] = None,
        username: Optional[str] = None
    ) -> Dict[str, Any]:
        """Non-streaming chat completion.
        
        Args:
            messages: Conversation messages
            model: Model identifier
            tools: Tool definitions
            options: Generation options
            user_id: User ID for session routing (primary user shares main session)
            username: Username for stable session key generation
        """
        
        payload = {
            "model": model or self.model,
            "messages": messages,
            "stream": False
        }

        # Add user field for non-primary users (stable isolated sessions)
        user_field = self._get_user_field(user_id, username)
        if user_field:
            payload["user"] = user_field

        if tools:
            openai_tools = self._convert_tools_to_openai_format(tools)
            if openai_tools:
                payload["tools"] = openai_tools

        if options:
            if "temperature" in options:
                payload["temperature"] = options["temperature"]
            if "top_p" in options:
                payload["top_p"] = options["top_p"]

        response = await self.client.post(
            f"{self.base_url}/chat/completions",
            json=payload,
            headers=self._get_headers(user_id, username)
        )
        if response.status_code >= 400:
            logger.error(f"OpenClaw API error {response.status_code}: {response.text[:500]}")
            response.raise_for_status()
        
        data = response.json()
        choice = data.get("choices", [{}])[0]
        message = choice.get("message", {})
        
        # Convert to Ollama-like format for compatibility
        result = {
            "message": {
                "content": message.get("content", ""),
                "role": message.get("role", "assistant")
            }
        }
        
        if message.get("tool_calls"):
            result["message"]["tool_calls"] = [
                {
                    "function": {
                        "name": tc["function"]["name"],
                        "arguments": json.loads(tc["function"]["arguments"]) if isinstance(tc["function"]["arguments"], str) else tc["function"]["arguments"]
                    }
                }
                for tc in message["tool_calls"]
            ]
        
        return result

    async def abort_generation(
        self,
        user_id: Optional[int] = None,
        username: Optional[str] = None,
        conversation_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Abort all active generations for the user's OpenClaw session.

        Calls OpenClaw's /v1/chat/abort endpoint to stop LLM generation server-side.

        IMPORTANT: BrinChat routes OpenClaw sessions per conversation (and per user for non-primary).
        Abort must use the same session key routing logic, otherwise the Stop button will be flaky.

        Args:
            user_id: Optional user ID for session routing.
            username: Optional username for session routing.
            conversation_id: BrinChat conversation id (preferred).

        Returns:
            Dict with 'ok', 'aborted', and 'run_ids' fields.
        """
        session_key: Optional[str] = None

        if self._is_primary_user(user_id, username):
            # Primary user: per-conversation OpenClaw session when available
            if conversation_id:
                session_key = f"agent:main:brinchat:{conversation_id}"
            else:
                session_key = OPENCLAW_MAIN_SESSION_KEY
        else:
            # Non-primary: always use the explicit session key format we set in headers
            if user_id:
                who = (username or str(user_id)).lower()
                base = f"agent:main:openai-user:brinchat:{who}"
                session_key = f"{base}:{conversation_id}" if conversation_id else base

        if not session_key:
            logger.warning("[Abort] No session key available for abort request")
            return {"ok": False, "error": "No session key"}

        try:
            headers = {
                "Content-Type": "application/json",
            }
            if self.api_key:
                headers["Authorization"] = f"Bearer {self.api_key}"

            response = await self.client.post(
                f"{self.base_url}/chat/abort",
                headers=headers,
                json={"session_key": session_key},
                timeout=10.0  # Short timeout for abort requests
            )

            if response.status_code == 200:
                result = response.json()
                logger.info(f"[Abort] OpenClaw abort successful: aborted={result.get('aborted')}, run_ids={result.get('run_ids', [])}")
                return result
            else:
                logger.warning(f"[Abort] OpenClaw abort failed: status={response.status_code}, body={response.text}")
                return {"ok": False, "error": f"HTTP {response.status_code}"}

        except Exception as e:
            logger.error(f"[Abort] OpenClaw abort request failed: {e}")
            return {"ok": False, "error": str(e)}

    async def close(self):
        """Close the HTTP client."""
        await self.client.aclose()

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.close()


# Global service instance
claude_service = ClaudeService()
