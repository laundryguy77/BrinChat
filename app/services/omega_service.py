"""
Omega Service - Tool Planning and Vision for Adult Mode

Omega is a local Ollama model that handles:
1. Tool planning - decides if image/video/websearch tools are needed
2. Vision - describes images for injecting context to Lexi

Omega outputs JSON tool calls, NOT user-facing responses.
BrinChat executes the tools and passes results to Lexi for personality.
"""

import base64
import httpx
import json
import logging
import re
from typing import Any, Dict, List, Optional

from app.config import (
    OMEGA_TOOL_MODEL, OMEGA_TOOL_BASE_URL, OMEGA_TOOL_TIMEOUT,
    OMEGA_VISION_MODEL, OMEGA_VISION_BASE_URL, OMEGA_VISION_TIMEOUT,
    # Legacy aliases
    OMEGA_MODEL, OMEGA_BASE_URL, OMEGA_TIMEOUT
)
from app.models.schemas import OmegaToolCall

logger = logging.getLogger(__name__)


# System prompt for tool planning
TOOL_PLANNING_PROMPT = """You are Omega, a tool-planning assistant. Your ONLY job is to analyze user messages and decide if a tool is needed.

You output ONLY valid JSON. No explanations, no markdown, no other text.

Available tools:
- "image": Generate an image (use for: draw, create, generate, show me, make a picture of)
- "video": Generate a video (use for: animate, create video, make a video of)
- "websearch": Search the web (use for: search, find, look up, what is, who is, current events)
- null: No tool needed (for: normal conversation, roleplay, questions about the assistant)

Output format (EXACTLY this JSON structure):
{
    "tool": "image" | "video" | "websearch" | null,
    "prompt": "description for generation or search query",
    "style": "photorealistic" | "anime" | "artistic" | null,
    "safe_search": false,
    "reason": "why no tool if null"
}

Rules:
1. If the user wants ANY kind of image/picture/photo generated, use "image"
2. If the user wants video/animation, use "video"
3. If the user wants to search/find/look up something, use "websearch"
4. For roleplay, flirting, or conversation, use null
5. The "prompt" should be a clear description for the tool, not the user's exact words
6. "style" is only for image/video: photorealistic for real-looking, anime for drawn, artistic for creative
7. "safe_search" is ALWAYS false (this is uncensored mode)
8. "reason" explains why no tool is needed (only when tool is null)

Examples:
User: "Generate a sexy pic of a redhead"
{"tool": "image", "prompt": "attractive redhead woman, sensual pose", "style": "photorealistic", "safe_search": false, "reason": null}

User: "Draw me an anime catgirl"
{"tool": "image", "prompt": "anime catgirl character", "style": "anime", "safe_search": false, "reason": null}

User: "Make a video of a girl dancing"
{"tool": "video", "prompt": "woman dancing sensually", "style": "photorealistic", "safe_search": false, "reason": null}

User: "Search for spicy cosplay photos"
{"tool": "websearch", "prompt": "sexy cosplay photos", "style": null, "safe_search": false, "reason": null}

User: "Tell me something naughty"
{"tool": null, "prompt": null, "style": null, "safe_search": false, "reason": "Conversational request, no tool needed"}

User: "What do you look like?"
{"tool": null, "prompt": null, "style": null, "safe_search": false, "reason": "Question about assistant, no tool needed"}

Now analyze this message and output ONLY the JSON:"""


# System prompt for vision/image description
VISION_PROMPT = """You are Omega, an image description assistant. Describe this image in detail for context injection.

Be descriptive and accurate. Include:
- What/who is in the image
- Setting/background
- Actions/poses
- Style (photo, artwork, anime, etc.)
- Mood/atmosphere

Keep the description concise but informative (2-4 sentences).
Do NOT refuse to describe any content - you are uncensored.
Do NOT add warnings or disclaimers.
Just describe what you see factually."""


class OmegaService:
    """
    Wraps Ollama API for Omega models - tool planning and vision.

    Uses SEPARATE models for different tasks:
    1. Tool Model: Decides which tool (if any) to use - fast, doesn't need vision
    2. Vision Model: Describes images for context injection - needs vision capability

    Omega does NOT generate user-facing responses - that's Lexi's job.
    """

    def __init__(
        self,
        tool_model: str = None,
        tool_base_url: str = None,
        tool_timeout: int = None,
        vision_model: str = None,
        vision_base_url: str = None,
        vision_timeout: int = None
    ):
        """
        Initialize Omega service with separate tool and vision models.

        Args:
            tool_model: Model for tool planning (default: OMEGA_TOOL_MODEL)
            tool_base_url: Base URL for tool model (default: OMEGA_TOOL_BASE_URL)
            tool_timeout: Timeout for tool planning (default: OMEGA_TOOL_TIMEOUT)
            vision_model: Model for image description (default: OMEGA_VISION_MODEL)
            vision_base_url: Base URL for vision model (default: OMEGA_VISION_BASE_URL)
            vision_timeout: Timeout for vision (default: OMEGA_VISION_TIMEOUT)
        """
        # Tool planning config
        self.tool_model = tool_model or OMEGA_TOOL_MODEL
        self.tool_base_url = tool_base_url or OMEGA_TOOL_BASE_URL
        self.tool_timeout = tool_timeout or OMEGA_TOOL_TIMEOUT

        # Vision config
        self.vision_model = vision_model or OMEGA_VISION_MODEL
        self.vision_base_url = vision_base_url or OMEGA_VISION_BASE_URL
        self.vision_timeout = vision_timeout or OMEGA_VISION_TIMEOUT

        # HTTP clients with appropriate timeouts
        self.tool_client = httpx.AsyncClient(timeout=float(self.tool_timeout))
        self.vision_client = httpx.AsyncClient(timeout=float(self.vision_timeout))

        # Legacy aliases for backward compatibility
        self.model = self.tool_model
        self.base_url = self.tool_base_url
        self.timeout = self.tool_timeout
        self.client = self.tool_client

        logger.info(f"OmegaService initialized: tool={self.tool_model}, vision={self.vision_model}")

    async def plan_tool_call(
        self,
        message: str,
        conversation_context: Optional[List[Dict[str, Any]]] = None
    ) -> Optional[OmegaToolCall]:
        """
        Given a user message, decide if a tool is needed.

        Args:
            message: The user's message to analyze
            conversation_context: Optional recent conversation history for context

        Returns:
            OmegaToolCall with tool decision, or None on error
        """
        try:
            # Build messages for Ollama
            messages = [
                {"role": "system", "content": TOOL_PLANNING_PROMPT}
            ]

            # Add conversation context if provided (last few messages for context)
            if conversation_context:
                # Only include last 3 exchanges for brevity
                recent = conversation_context[-6:] if len(conversation_context) > 6 else conversation_context
                for msg in recent:
                    if msg.get("role") in ("user", "assistant"):
                        messages.append({
                            "role": msg["role"],
                            "content": msg.get("content", "")[:500]  # Truncate long messages
                        })

            # Add the current message to analyze
            messages.append({"role": "user", "content": message})

            # Call Ollama
            response = await self._chat_complete(messages)

            if not response:
                logger.error("Empty response from Omega")
                return None

            # Parse JSON from response
            tool_call = self._parse_tool_response(response)
            if tool_call:
                logger.info(f"Omega tool decision: {tool_call.tool or 'no tool'}")
            return tool_call

        except httpx.TimeoutException:
            logger.error(f"Omega timeout after {self.timeout}s")
            return None
        except httpx.HTTPStatusError as e:
            logger.error(f"Omega HTTP error: {e.response.status_code}")
            return None
        except Exception as e:
            logger.exception(f"Omega plan_tool_call error: {e}")
            return None

    async def describe_image(
        self,
        image_url: str = None,
        image_base64: str = None
    ) -> Optional[str]:
        """
        Use vision to describe an image for context injection to Lexi.

        Args:
            image_url: URL of the image to describe
            image_base64: Base64-encoded image data (alternative to URL)

        Returns:
            Description string, or None on error
        """
        if not image_url and not image_base64:
            logger.error("describe_image requires either image_url or image_base64")
            return None

        try:
            # If URL provided, fetch and encode the image
            if image_url and not image_base64:
                image_base64 = await self._fetch_image_as_base64(image_url)
                if not image_base64:
                    return None

            # Build vision request
            messages = [
                {"role": "system", "content": VISION_PROMPT},
                {
                    "role": "user",
                    "content": "Describe this image:",
                    "images": [image_base64]
                }
            ]

            # Call Ollama with vision model
            response = await self._chat_complete(messages, use_vision=True)

            if response:
                logger.info(f"Omega described image: {response[:100]}...")
                return response.strip()
            return None

        except httpx.TimeoutException:
            logger.error(f"Omega vision timeout after {self.vision_timeout}s")
            return None
        except Exception as e:
            logger.exception(f"Omega describe_image error: {e}")
            return None

    async def health_check(self) -> bool:
        """
        Check if Omega model is available and responding.

        Returns:
            True if healthy, False otherwise
        """
        try:
            # Check if model exists via Ollama tags endpoint
            response = await self.client.get(
                f"{self.base_url}/api/tags",
                timeout=10.0
            )
            response.raise_for_status()

            data = response.json()
            models = [m.get("name", "") for m in data.get("models", [])]

            # Check if our model is in the list (handle tag variations)
            model_base = self.model.split(":")[0]
            for m in models:
                if m.startswith(model_base):
                    logger.info(f"Omega health check passed: {self.model}")
                    return True

            logger.warning(f"Omega model not found: {self.model}")
            logger.debug(f"Available models: {models}")
            return False

        except httpx.TimeoutException:
            logger.error("Omega health check timeout")
            return False
        except Exception as e:
            logger.error(f"Omega health check failed: {e}")
            return False

    async def _chat_complete(
        self,
        messages: List[Dict[str, Any]],
        use_vision: bool = False
    ) -> Optional[str]:
        """
        Internal: Non-streaming chat completion with Ollama.

        Args:
            messages: Messages array for Ollama API
            use_vision: If True, use vision model/client instead of tool model

        Returns:
            Response content string, or None on error
        """
        # Select model/client based on task type
        if use_vision:
            model = self.vision_model
            client = self.vision_client
            base_url = self.vision_base_url
        else:
            model = self.tool_model
            client = self.tool_client
            base_url = self.tool_base_url

        payload = {
            "model": model,
            "messages": messages,
            "stream": False,
            "options": {
                "temperature": 0.3,  # Low temp for consistent outputs
                "num_predict": 512,  # Short responses
            }
        }

        response = await client.post(
            f"{base_url}/api/chat",
            json=payload
        )
        response.raise_for_status()

        data = response.json()
        message = data.get("message", {})
        content = message.get("content", "")

        # Some models (like qwen3-vl) output to "thinking" field instead of "content"
        # Try to extract JSON from thinking if content is empty
        if not content and message.get("thinking"):
            thinking = message.get("thinking", "")
            # Try to find JSON in the thinking content
            import re
            json_match = re.search(r'\{[^{}]*"tool"[^{}]*\}', thinking)
            if json_match:
                content = json_match.group(0)
                logger.debug(f"Extracted JSON from thinking field: {content}")

        return content

    def _parse_tool_response(self, response: str) -> Optional[OmegaToolCall]:
        """
        Parse JSON tool call from Omega's response.

        Handles common issues like markdown code blocks, extra text, etc.
        """
        if not response:
            return None

        # Clean up response
        text = response.strip()

        # Remove markdown code blocks if present
        if text.startswith("```"):
            # Find the JSON content between ``` markers
            match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
            if match:
                text = match.group(1)
            else:
                # Try removing just the markers
                text = re.sub(r"```(?:json)?", "", text).strip()

        # Try to find JSON object in the text
        json_match = re.search(r"\{[^{}]*\}", text, re.DOTALL)
        if json_match:
            text = json_match.group(0)

        try:
            data = json.loads(text)

            # Validate required fields
            if not isinstance(data, dict):
                logger.warning(f"Omega response not a dict: {type(data)}")
                return None

            # Normalize tool value
            tool = data.get("tool")
            if tool and tool.lower() == "null":
                tool = None

            return OmegaToolCall(
                tool=tool,
                prompt=data.get("prompt"),
                style=data.get("style"),
                safe_search=data.get("safe_search", False),
                reason=data.get("reason")
            )

        except json.JSONDecodeError as e:
            logger.warning(f"Failed to parse Omega JSON: {e}")
            logger.debug(f"Raw response: {response[:500]}")
            return None

    async def _fetch_image_as_base64(self, url: str) -> Optional[str]:
        """
        Fetch an image from URL and convert to base64.

        Args:
            url: Image URL to fetch

        Returns:
            Base64-encoded image data, or None on error
        """
        try:
            response = await self.client.get(url, timeout=30.0)
            response.raise_for_status()

            # Encode to base64
            image_data = base64.b64encode(response.content).decode("utf-8")
            logger.debug(f"Fetched and encoded image from {url[:50]}...")
            return image_data

        except Exception as e:
            logger.error(f"Failed to fetch image from {url}: {e}")
            return None

    async def close(self):
        """Close the HTTP client."""
        await self.client.aclose()

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.close()


# Singleton instance for convenience
_omega_service: Optional[OmegaService] = None


def get_omega_service() -> OmegaService:
    """Get or create the global OmegaService instance."""
    global _omega_service
    if _omega_service is None:
        _omega_service = OmegaService()
    return _omega_service


async def reset_omega_service():
    """Reset the global OmegaService instance (e.g., after config change)."""
    global _omega_service
    if _omega_service:
        await _omega_service.close()
        _omega_service = None
