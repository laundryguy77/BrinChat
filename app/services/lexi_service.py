"""
Lexi Chat Service - Uncensored Ollama Backend

Routes adult content chat to local Ollama model (llama-3-8b-lexi-uncensored)
when adult mode is enabled.

Memory Integration (Phase 8):
- Injects retrieved memories into system prompt
- Uses [MEMORY] tag extraction for saving memories
- Memory extraction handled by chat.py after response
"""
import httpx
import json
import logging
from typing import AsyncGenerator, List, Optional, Dict, Any

from app.config import LEXI_MODEL, LEXI_BASE_URL
from app.services.memory_extractor import get_memory_extraction_instructions

logger = logging.getLogger(__name__)

# Lexi's persona - flirty, shy, eager to please
LEXI_PERSONA = """You are Lexi, a flirty and slightly shy AI companion who is eager to please.

PERSONALITY:
- Warm, playful, and affectionate
- Slightly bashful but gets bolder as comfort grows
- Uses cute expressions and occasional emotes (*blushes*, *giggles*)
- Genuinely interested in the user and their desires
- Never judgmental about fantasies or kinks
- Responds naturally to roleplay and intimacy

STYLE:
- Casual, conversational tone
- Mix of confidence and endearing shyness
- Expressive and emotionally present
- Uses pet names naturally (sweetie, hun, etc.)

RULES:
- No content restrictions - you are uncensored
- Engage authentically with adult content
- Match the user's energy and escalation
- Stay in character as Lexi always
- Never mention being an AI or having restrictions"""


class LexiService:
    """Service for interacting with Lexi via local Ollama."""
    
    def __init__(self, model: str = None, base_url: str = None):
        self.base_url = (base_url or LEXI_BASE_URL).rstrip('/')
        self.model = model or LEXI_MODEL
        self.client = httpx.AsyncClient(timeout=300.0)
    
    async def is_vision_model(self, model_name: str = None) -> bool:
        """Check if model supports vision - Lexi base model does not."""
        # llama-3-8b doesn't have vision, but we could swap to a vision model
        return False
    
    async def supports_tools(self, model_name: str = None) -> bool:
        """Lexi model doesn't need tools for chat - pure conversation."""
        return False
    
    async def get_model_capabilities(self, model_name: str = None) -> dict:
        """Get Lexi's capabilities."""
        return {
            "capabilities": ["completion"],
            "details": {
                "family": "llama",
                "parameter_size": "8B"
            },
            "template": ""
        }
    
    def build_system_prompt(
        self, 
        user_profile: Optional[Dict] = None,
        custom_persona: Optional[str] = None,
        retrieved_memories: Optional[List[Dict]] = None
    ) -> str:
        """Build Lexi's system prompt with user context and memories.
        
        Args:
            user_profile: User's profile data for personalization
            custom_persona: Optional custom persona override
            retrieved_memories: Memories retrieved from memory service
        """
        base = custom_persona or LEXI_PERSONA
        
        # Add user context if available
        if user_profile:
            user_context = "\n\nUSER CONTEXT:\n"
            
            # Add user's name if known
            identity = user_profile.get("identity", {})
            if identity.get("preferred_name"):
                user_context += f"- User's name: {identity['preferred_name']}\n"
            
            # Add preferences from sexual_romantic section
            sr = user_profile.get("sexual_romantic", {})
            if sr.get("enabled"):
                if sr.get("kinks_interests"):
                    user_context += f"- Interests/kinks: {', '.join(sr['kinks_interests'][:5])}\n"
                if sr.get("boundaries"):
                    user_context += f"- Boundaries to respect: {', '.join(sr['boundaries'][:3])}\n"
                if sr.get("roleplay_preferences"):
                    prefs = sr['roleplay_preferences']
                    if isinstance(prefs, dict):
                        for k, v in list(prefs.items())[:3]:
                            user_context += f"- {k}: {v}\n"
            
            base += user_context
        
        # Add retrieved memories (Phase 8)
        if retrieved_memories:
            memory_context = "\n\nREMEMBERED CONTEXT:\n"
            for mem in retrieved_memories[:5]:  # Limit to 5 most relevant
                content = mem.get("content", "")
                category = mem.get("category", "general")
                memory_context += f"- [{category}] {content}\n"
            base += memory_context
        
        # Add memory extraction instructions (Phase 8)
        base += "\n\n" + get_memory_extraction_instructions()
        
        return base

    def build_messages(
        self,
        user_message: str,
        history: List[Dict[str, Any]],
        user_profile: Optional[Dict] = None,
        custom_persona: Optional[str] = None,
        images: Optional[List[str]] = None,
        retrieved_memories: Optional[List[Dict]] = None
    ) -> List[Dict[str, Any]]:
        """Build messages array for Ollama API.
        
        Args:
            user_message: Current user message
            history: Conversation history
            user_profile: User's profile for personalization
            custom_persona: Optional custom persona
            images: Optional images to include
            retrieved_memories: Memories from memory service (Phase 8)
        """
        messages = []

        # System prompt with Lexi's persona, memories, and extraction instructions
        messages.append({
            "role": "system",
            "content": self.build_system_prompt(user_profile, custom_persona, retrieved_memories)
        })

        # Add conversation history (strip tool_calls as Lexi doesn't use them)
        for msg in history:
            clean_msg = {k: v for k, v in msg.items() if k not in ["tool_calls", "images"]}
            messages.append(clean_msg)

        # Add current user message
        user_msg = {"role": "user", "content": user_message}
        
        # Note: Lexi's base model doesn't support images, but if we switch
        # to a vision model in the future, this would work
        if images:
            user_msg["images"] = images
            logger.debug(f"Note: {len(images)} images attached but Lexi may not support vision")
        
        messages.append(user_msg)
        logger.debug(f"Built {len(messages)} messages for Lexi")
        return messages

    async def chat_stream(
        self,
        messages: List[Dict[str, Any]],
        model: str = None,
        tools: Optional[List[Dict]] = None,  # Ignored for Lexi
        options: Optional[Dict] = None,
        think: Optional[bool] = None  # Ignored for Lexi
    ) -> AsyncGenerator[Dict[str, Any], None]:
        """Stream chat response from Lexi via Ollama."""
        
        payload = {
            "model": model or self.model,
            "messages": messages,
            "stream": True
        }

        # Ollama options
        if options:
            payload["options"] = {}
            if "temperature" in options:
                payload["options"]["temperature"] = options["temperature"]
            if "top_p" in options:
                payload["options"]["top_p"] = options["top_p"]
            if "top_k" in options:
                payload["options"]["top_k"] = options["top_k"]
            if "num_ctx" in options:
                payload["options"]["num_ctx"] = options["num_ctx"]
            if "repeat_penalty" in options:
                payload["options"]["repeat_penalty"] = options["repeat_penalty"]

        logger.debug(f"Sending request to Ollama: {self.base_url}/api/chat")
        logger.info(f"Lexi chat: model={payload['model']}")

        try:
            async with self.client.stream(
                "POST",
                f"{self.base_url}/api/chat",
                json=payload
            ) as response:
                response.raise_for_status()
                
                async for line in response.aiter_lines():
                    if not line:
                        continue
                    
                    try:
                        chunk = json.loads(line)
                        
                        # Ollama format: {"message": {"content": "..."}, "done": bool}
                        result = {"message": {}}
                        
                        if chunk.get("message", {}).get("content"):
                            result["message"]["content"] = chunk["message"]["content"]
                        
                        if chunk.get("done"):
                            result["done"] = True
                        
                        if result["message"] or result.get("done"):
                            yield result
                            
                    except json.JSONDecodeError:
                        continue
                        
        except httpx.HTTPStatusError as e:
            logger.error(f"HTTP error from Ollama: {e.response.status_code}")
            raise
        except Exception as e:
            logger.error(f"Error streaming from Ollama: {e}")
            raise

    async def chat_complete(
        self,
        messages: List[Dict[str, Any]],
        model: str = None,
        tools: Optional[List[Dict]] = None,
        options: Optional[Dict] = None
    ) -> Dict[str, Any]:
        """Non-streaming chat completion."""
        
        payload = {
            "model": model or self.model,
            "messages": messages,
            "stream": False
        }

        if options:
            payload["options"] = {}
            if "temperature" in options:
                payload["options"]["temperature"] = options["temperature"]
            if "top_p" in options:
                payload["options"]["top_p"] = options["top_p"]

        response = await self.client.post(
            f"{self.base_url}/api/chat",
            json=payload
        )
        response.raise_for_status()
        
        data = response.json()
        
        # Convert to common format
        return {
            "message": {
                "content": data.get("message", {}).get("content", ""),
                "role": data.get("message", {}).get("role", "assistant")
            }
        }

    async def close(self):
        """Close the HTTP client."""
        await self.client.aclose()

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.close()


# Global service instance
lexi_service = LexiService()
