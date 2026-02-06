from typing import Dict, Any, Optional, List
from dataclasses import dataclass, field
from contextvars import ContextVar
import json
import logging
import os
import re
import httpx
from app.services.conversation_store import conversation_store
from app.config import HF_TOKEN, VIDEO_GENERATION_SPACE, FAL_KEY, BRAVE_SEARCH_API_KEY
from app.services.knowledge_base import get_knowledge_base
from app.services.memory_service import get_memory_service
from app.services.user_profile_service import get_user_profile_service
from app.services.image_backends import UnifiedImageGenerator
from app.services.video_backends import VideoGenerator
from app.models.schemas import OmegaToolCall
from app.tools.omega_definitions import OMEGA_TOOLS, get_tool_by_name

logger = logging.getLogger(__name__)

# NOTE: web_search and browse_website tools removed - handled by OpenClaw
# URL caching, Brave Search, and URL fetching code has been removed


@dataclass
class ToolExecutionContext:
    """Request-scoped context for tool execution.

    This replaces the shared mutable state that was previously stored
    on the ToolExecutor singleton, preventing race conditions between
    concurrent requests.
    """
    user_id: Optional[int] = None
    conversation_id: Optional[str] = None
    image_registry: Dict[str, str] = field(default_factory=dict)

    def register_image(self, message_index: int, image_base64: str):
        """Register an image from a message for later tool use"""
        self.image_registry[f"image_{message_index}"] = image_base64
        self.image_registry["last_shared_image"] = image_base64

    def get_image(self, reference: str) -> Optional[str]:
        """Get image by reference"""
        return self.image_registry.get(reference)

    def clear_images(self):
        """Clear the image registry"""
        self.image_registry.clear()


# Request-scoped context variable for tool execution
_current_context: ContextVar[Optional[ToolExecutionContext]] = ContextVar(
    'tool_execution_context', default=None
)


def get_current_context() -> Optional[ToolExecutionContext]:
    """Get the current request's tool execution context."""
    return _current_context.get()


def set_current_context(ctx: ToolExecutionContext) -> None:
    """Set the current request's tool execution context."""
    _current_context.set(ctx)


def create_context(user_id: Optional[int] = None, conversation_id: Optional[str] = None) -> ToolExecutionContext:
    """Create and set a new tool execution context for the current request."""
    ctx = ToolExecutionContext(user_id=user_id, conversation_id=conversation_id)
    _current_context.set(ctx)
    return ctx


class ToolExecutor:
    """Tool execution engine.

    NOTE: This class is stateless. All request-specific state should be passed
    via the ToolExecutionContext or explicit parameters to execute().

    The set_current_user() and set_current_conversation() methods are deprecated
    and provided only for backwards compatibility. Use create_context() instead.
    """

    def __init__(self):
        # DEPRECATED: These are kept only for backwards compatibility
        # DO NOT use these in new code - use ToolExecutionContext instead
        self._deprecated_user_id: Optional[int] = None
        self._deprecated_conv_id: Optional[str] = None
        self._deprecated_image_registry: Dict[str, str] = {}

    def set_current_conversation(self, conv_id: str):
        """DEPRECATED: Use create_context() instead.

        Sets conversation ID in context. Raises error if no context exists.
        """
        ctx = get_current_context()
        if ctx:
            ctx.conversation_id = conv_id
        else:
            raise RuntimeError(
                "ToolExecutionContext not initialized. "
                "Call create_context() before using ToolExecutor methods. "
                "This prevents race conditions in concurrent requests."
            )

    def set_current_user(self, user_id: int):
        """DEPRECATED: Use create_context() instead.

        Sets user ID in context. Raises error if no context exists.
        """
        ctx = get_current_context()
        if ctx:
            ctx.user_id = user_id
        else:
            raise RuntimeError(
                "ToolExecutionContext not initialized. "
                "Call create_context() before using ToolExecutor methods. "
                "This prevents race conditions in concurrent requests."
            )

    def register_image(self, message_index: int, image_base64: str):
        """Register an image for the current request.

        Requires context to be initialized. Raises error otherwise.
        """
        ctx = get_current_context()
        if ctx:
            ctx.register_image(message_index, image_base64)
        else:
            raise RuntimeError(
                "ToolExecutionContext not initialized. "
                "Call create_context() before registering images. "
                "This prevents race conditions in concurrent requests."
            )

    def clear_images(self):
        """Clear the image registry for the current request."""
        ctx = get_current_context()
        if ctx:
            ctx.clear_images()
        # No error if no context - clearing nothing is safe

    def get_image(self, reference: str) -> Optional[str]:
        """Get image by reference from the current request's context."""
        ctx = get_current_context()
        if ctx:
            return ctx.get_image(reference)
        # Return None if no context - don't use deprecated registry
        logger.warning("get_image called without context, returning None")
        return None

    async def execute(
        self,
        tool_call: Dict[str, Any],
        user_id: Optional[int] = None,
        conversation_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """Execute a tool call and return the result.

        Args:
            tool_call: The tool call to execute
            user_id: User ID for user-scoped tools (preferred over context)
            conversation_id: Conversation ID for conversation-scoped tools (preferred over context)

        Priority for context values:
        1. Explicit parameters passed to this method
        2. Values from ToolExecutionContext (request-scoped)
        3. Deprecated instance state (legacy fallback)
        """
        # Get context-scoped values
        ctx = get_current_context()

        # Resolve effective user_id (explicit > context)
        # No longer fall back to deprecated instance state
        effective_user_id = user_id
        if effective_user_id is None and ctx:
            effective_user_id = ctx.user_id
        if effective_user_id is None:
            logger.warning("Tool execution without user_id - some tools may not work")

        # Resolve effective conversation_id (explicit > context)
        # No longer fall back to deprecated instance state
        effective_conv_id = conversation_id
        if effective_conv_id is None and ctx:
            effective_conv_id = ctx.conversation_id
        if effective_conv_id is None:
            logger.warning("Tool execution without conversation_id - some tools may not work")

        function = tool_call.get("function", {})
        name = function.get("name")
        arguments = function.get("arguments", {})

        # Parse arguments if they're a string
        if isinstance(arguments, str):
            try:
                arguments = json.loads(arguments)
            except json.JSONDecodeError:
                return {"error": f"Invalid arguments format: {arguments}"}

        # Core tools (web_search and browse_website removed - handled by OpenClaw)
        if name == "search_conversations":
            return await self._execute_conversation_search(arguments, effective_conv_id, effective_user_id)
        elif name == "search_knowledge_base":
            return await self._execute_knowledge_search(arguments, effective_user_id)
        elif name == "add_memory":
            return await self._execute_add_memory(arguments, effective_user_id)
        elif name == "query_memory":
            return await self._execute_query_memory(arguments, effective_user_id)
        elif name == "set_conversation_title":
            return await self._execute_set_conversation_title(arguments, effective_conv_id)

        # Consolidated IMAGE tool (routes by action parameter)
        elif name == "image":
            return await self._execute_image_tool(arguments, effective_user_id)
        # Legacy image tool names (backward compatibility)
        elif name == "text_to_image":
            arguments["action"] = "generate"
            return await self._execute_image_tool(arguments, effective_user_id)
        elif name == "image_to_image":
            arguments["action"] = "transform"
            return await self._execute_image_tool(arguments, effective_user_id)
        elif name == "inpaint_image":
            arguments["action"] = "inpaint"
            return await self._execute_image_tool(arguments, effective_user_id)
        elif name == "upscale_image":
            arguments["action"] = "upscale"
            return await self._execute_image_tool(arguments, effective_user_id)

        # Consolidated VIDEO tool (routes by action parameter)
        elif name == "video":
            return await self._execute_video_tool(arguments, effective_user_id)
        # Legacy video tool names (backward compatibility)
        elif name == "generate_video" or name == "text_to_video":
            arguments["action"] = "generate"
            return await self._execute_video_tool(arguments, effective_user_id)
        elif name == "image_to_video":
            arguments["action"] = "animate"
            return await self._execute_video_tool(arguments, effective_user_id)

        # Consolidated USER_PROFILE tool (routes by action parameter)
        elif name == "user_profile":
            return await self._execute_user_profile_tool(arguments, effective_user_id)
        # Legacy profile tool names (backward compatibility)
        elif name == "user_profile_read":
            arguments["action"] = "read"
            return await self._execute_user_profile_tool(arguments, effective_user_id)
        elif name == "user_profile_update":
            arguments["action"] = "update"
            return await self._execute_user_profile_tool(arguments, effective_user_id)
        elif name == "user_profile_log_event":
            arguments["action"] = "log_event"
            return await self._execute_user_profile_tool(arguments, effective_user_id)
        elif name == "user_profile_enable_section":
            arguments["action"] = "enable_section"
            return await self._execute_user_profile_tool(arguments, effective_user_id)
        elif name == "user_profile_add_nested":
            arguments["action"] = "add_nested"
            return await self._execute_user_profile_tool(arguments, effective_user_id)
        elif name == "user_profile_query":
            arguments["action"] = "query"
            return await self._execute_user_profile_tool(arguments, effective_user_id)
        elif name == "user_profile_export":
            arguments["action"] = "export"
            return await self._execute_user_profile_tool(arguments, effective_user_id)
        elif name == "user_profile_reset":
            arguments["action"] = "reset"
            return await self._execute_user_profile_tool(arguments, effective_user_id)

        # For unknown tools, return a helpful message instead of breaking
        logger.warning(f"Unknown tool requested: {name}")
        return {"error": f"Tool '{name}' is not available. Available tools: search_conversations, search_knowledge_base, add_memory, query_memory, image, video, user_profile"}

    # NOTE: web_search and browse_website tools have been removed
    # These are now handled by OpenClaw's built-in tools (web_search, web_fetch)

    async def _execute_conversation_search(
        self, args: Dict[str, Any], conversation_id: Optional[str] = None, user_id: Optional[int] = None
    ) -> Dict[str, Any]:
        """Search through previous conversations for context"""
        query = args.get("query", "")

        if not query:
            return {
                "error": "Search query is required.",
                "success": False
            }

        try:
            logger.info(f"Conversation search: {query}")
            results = conversation_store.search_conversations(
                query=query,
                exclude_conv_id=conversation_id,
                max_results=10,
                user_id=user_id
            )

            if not results:
                return {
                    "success": True,
                    "query": query,
                    "message": "No matching content found in previous conversations.",
                    "results": [],
                    "num_results": 0
                }

            logger.info(f"Conversation search found {len(results)} results")

            # Format results for the model
            formatted_results = []
            for r in results:
                formatted_results.append({
                    "conversation": r["conversation_title"],
                    "speaker": r["message_role"],
                    "content": r["snippet"],
                    "relevance": round(r["score"] * 100)
                })

            return {
                "success": True,
                "query": query,
                "results": formatted_results,
                "num_results": len(formatted_results),
                "message": f"Found {len(formatted_results)} relevant excerpts from past conversations."
            }

        except Exception as e:
            logger.error(f"Conversation search error: {e}")
            return {
                "error": f"Search failed: {str(e)}",
                "success": False
            }

    async def _execute_knowledge_search(
        self, args: Dict[str, Any], user_id: Optional[int] = None
    ) -> Dict[str, Any]:
        """Search the user's knowledge base for relevant documents"""
        query = args.get("query", "")

        if not query:
            return {
                "error": "Search query is required.",
                "success": False
            }

        if not user_id:
            return {
                "success": True,
                "query": query,
                "message": "Knowledge base search requires authentication.",
                "results": [],
                "num_results": 0
            }

        try:
            logger.info(f"Knowledge base search: {query}")
            kb = get_knowledge_base()
            results = await kb.search(
                user_id=user_id,
                query=query,
                top_k=5,
                threshold=0.3
            )

            if not results:
                return {
                    "success": True,
                    "query": query,
                    "message": "No matching content found in your knowledge base. You may need to upload relevant documents first.",
                    "results": [],
                    "num_results": 0
                }

            logger.info(f"Knowledge base search found {len(results)} results")

            # Format results for the model
            formatted_results = []
            for r in results:
                formatted_results.append({
                    "filename": r["filename"],
                    "content": r["content"][:1000],  # Limit content length
                    "similarity": r["similarity"],
                    "chunk_index": r["chunk_index"]
                })

            return {
                "success": True,
                "query": query,
                "results": formatted_results,
                "num_results": len(formatted_results),
                "message": f"Found {len(formatted_results)} relevant excerpts from your uploaded documents."
            }

        except Exception as e:
            logger.error(f"Knowledge base search error: {e}")
            return {
                "error": f"Search failed: {str(e)}",
                "success": False
            }

    async def _execute_add_memory(
        self, args: Dict[str, Any], user_id: Optional[int] = None
    ) -> Dict[str, Any]:
        """Add information to user's memory."""
        if not user_id:
            return {"success": False, "error": "User not authenticated"}

        content = args.get("content", "")
        category = args.get("category", "general")
        importance = args.get("importance", 5)

        logger.info(f"[Memory] Model calling add_memory: category={category}, importance={importance}, content={content[:50]}...")

        memory_service = get_memory_service()
        # Use source from args if provided (explicit vs inferred), default to inferred
        source = args.get("source", "inferred")
        if source not in ("explicit", "inferred"):
            source = "inferred"

        result = await memory_service.add_memory(
            user_id=user_id,
            content=content,
            category=category,
            importance=importance,
            source=source
        )

        if result.get("success"):
            logger.info(f"[Memory] Memory stored successfully: id={result.get('id')}")
        else:
            logger.warning(f"[Memory] Failed to store memory: {result.get('error')}")

        return result

    async def _execute_query_memory(
        self, args: Dict[str, Any], user_id: Optional[int] = None
    ) -> Dict[str, Any]:
        """Query user's memory."""
        if not user_id:
            return {"success": False, "error": "User not authenticated"}

        query = args.get("query", "")
        logger.info(f"[Memory] Model calling query_memory: query={query[:50]}...")

        memory_service = get_memory_service()
        results = await memory_service.query_memories(
            user_id=user_id,
            query=query,
            top_k=5
        )

        logger.info(f"[Memory] Query returned {len(results)} memories")

        return {
            "success": True,
            "query": query,
            "results": results,
            "count": len(results)
        }

    async def _execute_set_conversation_title(
        self, args: Dict[str, Any], conversation_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """Set the title of the current conversation."""
        title = args.get("title", "").strip()

        if not title:
            return {"success": False, "error": "Title is required"}

        if not conversation_id:
            return {"success": False, "error": "No active conversation"}

        # Limit title length
        if len(title) > 100:
            title = title[:100]

        try:
            success = await conversation_store.rename(conversation_id, title)
            if success:
                logger.info(f"Conversation title set to: {title}")
                return {
                    "success": True,
                    "title": title,
                    "message": f"Conversation title updated to: {title}"
                }
            else:
                return {"success": False, "error": "Failed to update conversation title"}
        except Exception as e:
            logger.error(f"Error setting conversation title: {e}")
            return {"success": False, "error": str(e)}

    async def _execute_generate_video(
        self, args: Dict[str, Any], user_id: Optional[int] = None
    ) -> Dict[str, Any]:
        """Generate a video using Hugging Face Spaces."""
        import asyncio

        prompt = args.get("prompt", "").strip()
        duration = args.get("duration", 4)

        if not prompt:
            return {"success": False, "error": "Prompt is required"}

        # Inject avatar style from profile for consistent character
        if user_id:
            try:
                profile_service = get_user_profile_service()
                profile_data = await profile_service.get_profile(user_id)
                if profile_data:
                    profile = profile_data.get("profile", {})
                    persona = profile.get("persona_preferences", {})
                    avatar_style = persona.get("avatar_style_tags")
                    if avatar_style:
                        prompt = f"{avatar_style}. {prompt}"
                        logger.debug(f"Injected avatar style into video prompt: {avatar_style}")
            except Exception as e:
                logger.warning(f"Failed to get avatar style for video generation: {e}")

        # Validate duration
        duration = max(2, min(10, duration))

        if not VIDEO_GENERATION_SPACE:
            return {"success": False, "error": "Video generation is not configured. Set VIDEO_GENERATION_SPACE in environment."}

        logger.info(f"Generating video with prompt: {prompt[:100]}...")

        try:
            # Import gradio_client dynamically to avoid startup dependency
            from gradio_client import Client

            # Run the synchronous gradio client in a thread pool
            loop = asyncio.get_event_loop()

            def generate_sync():
                try:
                    # Create client with optional auth token
                    if HF_TOKEN:
                        client = Client(VIDEO_GENERATION_SPACE, hf_token=HF_TOKEN)
                    else:
                        client = Client(VIDEO_GENERATION_SPACE)

                    # Call the predict method with the prompt
                    # Note: API may vary by space, this handles common patterns
                    result = client.predict(
                        prompt,
                        api_name="/predict"
                    )
                    return result
                except Exception as e:
                    # Try alternative API endpoint
                    try:
                        if HF_TOKEN:
                            client = Client(VIDEO_GENERATION_SPACE, hf_token=HF_TOKEN)
                        else:
                            client = Client(VIDEO_GENERATION_SPACE)
                        result = client.predict(
                            prompt,
                            api_name="/generate"
                        )
                        return result
                    except Exception:
                        raise e

            # Execute with 120 second timeout for video generation
            result = await asyncio.wait_for(
                loop.run_in_executor(None, generate_sync),
                timeout=120.0
            )

            # Handle various result formats
            video_url = None
            if isinstance(result, str):
                video_url = result
            elif isinstance(result, dict):
                video_url = result.get("video") or result.get("url") or result.get("output")
            elif isinstance(result, (list, tuple)) and len(result) > 0:
                video_url = result[0] if isinstance(result[0], str) else result[0].get("url", result[0])

            if video_url:
                logger.info(f"Video generated successfully: {video_url[:100]}...")
                return {
                    "success": True,
                    "video_url": video_url,
                    "prompt": prompt,
                    "duration": duration,
                    "message": f"Video generated successfully. URL: {video_url}"
                }
            else:
                return {
                    "success": False,
                    "error": "Video generation completed but no URL was returned",
                    "raw_result": str(result)[:500]
                }

        except asyncio.TimeoutError:
            return {
                "success": False,
                "error": "Video generation timed out after 120 seconds. The service may be busy, please try again later."
            }
        except ImportError:
            return {
                "success": False,
                "error": "gradio-client is not installed. Run: pip install gradio-client"
            }
        except Exception as e:
            error_msg = str(e)
            logger.error(f"Video generation error: {error_msg}")

            # Provide helpful error messages
            if "429" in error_msg or "rate" in error_msg.lower():
                return {"success": False, "error": "Rate limit exceeded. Please wait and try again later."}
            elif "401" in error_msg or "403" in error_msg:
                return {"success": False, "error": "Authentication failed. Check HF_TOKEN in environment."}
            elif "queue" in error_msg.lower():
                return {"success": False, "error": "The video generation service is currently busy. Please try again later."}
            else:
                return {"success": False, "error": f"Video generation failed: {error_msg[:200]}"}

    async def _get_avatar_style_prefix(self, user_id: Optional[int] = None) -> str:
        """Get avatar style from user profile for consistent character generation."""
        if not user_id:
            return ""
        try:
            profile_service = get_user_profile_service()
            profile_data = await profile_service.get_profile(user_id)
            if profile_data:
                profile = profile_data.get("profile", {})
                persona = profile.get("persona_preferences", {})
                avatar_style = persona.get("avatar_style_tags")
                if avatar_style:
                    return avatar_style
        except Exception as e:
            logger.warning(f"Failed to get avatar style: {e}")
        return ""

    async def _execute_image_tool(
        self, args: Dict[str, Any], user_id: Optional[int] = None
    ) -> Dict[str, Any]:
        """Consolidated image tool - routes by action parameter."""
        action = args.get("action", "generate")

        if action == "generate":
            return await self._execute_text_to_image(args, user_id)
        elif action == "transform":
            return await self._execute_image_to_image(args)
        elif action == "inpaint":
            return await self._execute_inpaint_image(args)
        elif action == "upscale":
            return await self._execute_upscale_image(args)
        else:
            return {"success": False, "error": f"Unknown image action: {action}. Use: generate, transform, inpaint, upscale"}

    async def _execute_text_to_image(
        self, args: Dict[str, Any], user_id: Optional[int] = None
    ) -> Dict[str, Any]:
        """Generate an image from text using HuggingFace Spaces (FLUX.1/SD)."""
        import tempfile

        prompt = args.get("prompt", "").strip()
        if not prompt:
            return {"success": False, "error": "Prompt is required"}

        # SECURITY: Validate and bound numeric parameters
        width = args.get("width", 1024)
        height = args.get("height", 1024)
        try:
            width = int(width)
            height = int(height)
        except (ValueError, TypeError):
            return {"success": False, "error": "Width and height must be integers"}

        # Enforce reasonable bounds to prevent resource exhaustion
        MIN_DIMENSION = 256
        MAX_DIMENSION = 2048
        width = max(MIN_DIMENSION, min(MAX_DIMENSION, width))
        height = max(MIN_DIMENSION, min(MAX_DIMENSION, height))

        # Inject avatar style from profile for consistent character
        avatar_style = await self._get_avatar_style_prefix(user_id)
        if avatar_style:
            prompt = f"{avatar_style}. {prompt}"
            logger.debug(f"Injected avatar style into image prompt: {avatar_style}")

        logger.info(f"Text-to-image generation with prompt: {prompt[:100]}...")

        # Check for ComfyUI preference via environment or default to comfyui if available
        image_backend = os.getenv("IMAGE_BACKEND", "comfyui")  # comfyui or huggingface
        comfyui_url = os.getenv("COMFYUI_API_URL", "http://localhost:3457")
        
        try:
            async with UnifiedImageGenerator(
                headless=True,
                preferred_backend=image_backend,
                comfyui_url=comfyui_url
            ) as gen:
                result = await gen.text_to_image(
                    prompt=prompt,
                    negative_prompt=args.get("negative_prompt", ""),
                    width=width,
                    height=height,
                    return_base64=True
                )

            if result.get("success"):
                return {
                    "success": True,
                    "base64": result["base64"],
                    "mime_type": result.get("mime_type", "image/png"),
                    "message": "Image generated successfully"
                }
            return result
        except Exception as e:
            logger.error(f"Text-to-image generation error: {e}")
            return {"success": False, "error": str(e)}

    async def _execute_image_to_image(self, args: Dict[str, Any]) -> Dict[str, Any]:
        """Transform an image based on a text prompt."""
        import tempfile
        import base64
        import os

        image_base64 = args.get("image_base64", "")
        prompt = args.get("prompt", "").strip()

        if not image_base64:
            return {"success": False, "error": "image_base64 is required"}
        if not prompt:
            return {"success": False, "error": "prompt is required"}

        # SECURITY: Validate and bound strength parameter
        strength = args.get("strength", 0.7)
        try:
            strength = float(strength)
        except (ValueError, TypeError):
            return {"success": False, "error": "Strength must be a number"}
        # Clamp to valid range
        strength = max(0.0, min(1.0, strength))

        # Inject avatar style from profile
        avatar_style = await self._get_avatar_style_prefix()
        if avatar_style:
            prompt = f"{avatar_style}. {prompt}"

        logger.info(f"Image-to-image transformation with prompt: {prompt[:100]}...")

        try:
            # Save base64 image to temp file
            with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
                f.write(base64.b64decode(image_base64))
                temp_image_path = f.name

            try:
                async with UnifiedImageGenerator(headless=True) as gen:
                    result = await gen.image_to_image(
                        image_path=temp_image_path,
                        prompt=prompt,
                        negative_prompt=args.get("negative_prompt", ""),
                        strength=strength,
                        return_base64=True
                    )
            finally:
                os.unlink(temp_image_path)

            if result.get("success"):
                return {
                    "success": True,
                    "base64": result["base64"],
                    "mime_type": result.get("mime_type", "image/png"),
                    "message": "Image transformed successfully"
                }
            return result
        except Exception as e:
            logger.error(f"Image-to-image error: {e}")
            return {"success": False, "error": str(e)}

    async def _execute_inpaint_image(self, args: Dict[str, Any]) -> Dict[str, Any]:
        """Inpaint regions of an image based on a mask."""
        import tempfile
        import base64
        import os

        image_base64 = args.get("image_base64", "")
        mask_base64 = args.get("mask_base64", "")
        prompt = args.get("prompt", "").strip()

        if not image_base64:
            return {"success": False, "error": "image_base64 is required"}
        if not mask_base64:
            return {"success": False, "error": "mask_base64 is required"}
        if not prompt:
            return {"success": False, "error": "prompt is required"}

        # Inject avatar style from profile
        avatar_style = await self._get_avatar_style_prefix()
        if avatar_style:
            prompt = f"{avatar_style}. {prompt}"

        logger.info(f"Inpainting with prompt: {prompt[:100]}...")

        try:
            # Save base64 images to temp files
            with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
                f.write(base64.b64decode(image_base64))
                temp_image_path = f.name

            with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
                f.write(base64.b64decode(mask_base64))
                temp_mask_path = f.name

            try:
                async with UnifiedImageGenerator(headless=True) as gen:
                    result = await gen.inpaint(
                        image_path=temp_image_path,
                        mask_path=temp_mask_path,
                        prompt=prompt,
                        negative_prompt=args.get("negative_prompt", ""),
                        return_base64=True
                    )
            finally:
                os.unlink(temp_image_path)
                os.unlink(temp_mask_path)

            if result.get("success"):
                return {
                    "success": True,
                    "base64": result["base64"],
                    "mime_type": result.get("mime_type", "image/png"),
                    "message": "Image inpainted successfully"
                }
            return result
        except Exception as e:
            logger.error(f"Inpaint error: {e}")
            return {"success": False, "error": str(e)}

    async def _execute_upscale_image(self, args: Dict[str, Any]) -> Dict[str, Any]:
        """Upscale an image using AI enhancement."""
        import tempfile
        import base64
        import os

        image_base64 = args.get("image_base64", "")
        if not image_base64:
            return {"success": False, "error": "image_base64 is required"}

        # SECURITY: Validate and bound scale parameter
        scale = args.get("scale", 2.0)
        try:
            scale = float(scale)
        except (ValueError, TypeError):
            return {"success": False, "error": "Scale must be a number"}
        # Clamp to reasonable range to prevent resource exhaustion
        MIN_SCALE = 1.0
        MAX_SCALE = 4.0
        scale = max(MIN_SCALE, min(MAX_SCALE, scale))

        logger.info(f"Upscaling image with scale factor: {scale}")

        try:
            # Save base64 image to temp file
            with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
                f.write(base64.b64decode(image_base64))
                temp_image_path = f.name

            try:
                async with UnifiedImageGenerator(headless=True) as gen:
                    result = await gen.upscale(
                        image_path=temp_image_path,
                        scale=scale,
                        return_base64=True
                    )
            finally:
                os.unlink(temp_image_path)

            if result.get("success"):
                return {
                    "success": True,
                    "base64": result["base64"],
                    "mime_type": result.get("mime_type", "image/png"),
                    "message": f"Image upscaled successfully ({scale}x)"
                }
            return result
        except Exception as e:
            logger.error(f"Upscale error: {e}")
            return {"success": False, "error": str(e)}

    async def _execute_video_tool(
        self, args: Dict[str, Any], user_id: Optional[int] = None
    ) -> Dict[str, Any]:
        """Consolidated video tool - routes by action parameter."""
        action = args.get("action", "generate")

        if action == "generate":
            return await self._execute_text_to_video(args)
        elif action == "animate":
            return await self._execute_image_to_video(args)
        else:
            return {"success": False, "error": f"Unknown video action: {action}. Use: generate, animate"}

    async def _execute_text_to_video(self, args: Dict[str, Any]) -> Dict[str, Any]:
        """Generate video from text using HuggingFace Spaces via Playwright."""
        prompt = args.get("prompt", "").strip()
        if not prompt:
            return {"success": False, "error": "Prompt is required"}

        negative_prompt = args.get("negative_prompt", "")
        duration = args.get("duration", 3.0)

        logger.info(f"Text-to-video generation: {prompt[:100]}...")

        try:
            async with VideoGenerator(headless=True, timeout=300000) as gen:
                result = await gen.text_to_video(
                    prompt=prompt,
                    negative_prompt=negative_prompt,
                    duration=duration,
                    return_base64=True
                )

            if result.get("success"):
                return {
                    "success": True,
                    "base64": result.get("base64"),
                    "mime_type": "video/mp4",
                    "size_bytes": result.get("size_bytes"),
                    "message": "Video generated successfully"
                }
            return result
        except Exception as e:
            logger.error(f"Text-to-video error: {e}")
            return {"success": False, "error": str(e)}

    async def _execute_image_to_video(self, args: Dict[str, Any]) -> Dict[str, Any]:
        """Animate an image into video using HuggingFace Spaces via Playwright."""
        import os
        import tempfile
        import base64

        image_base64 = args.get("image_base64", "").strip()
        if not image_base64:
            return {"success": False, "error": "image_base64 is required"}

        prompt = args.get("prompt", "")
        negative_prompt = args.get("negative_prompt", "")

        logger.info("Image-to-video generation from base64 input")

        try:
            # Save base64 image to temp file
            with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
                f.write(base64.b64decode(image_base64))
                temp_image_path = f.name

            try:
                async with VideoGenerator(headless=True, timeout=300000) as gen:
                    result = await gen.image_to_video(
                        image_path=temp_image_path,
                        prompt=prompt,
                        negative_prompt=negative_prompt,
                        return_base64=True
                    )
            finally:
                os.unlink(temp_image_path)

            if result.get("success"):
                return {
                    "success": True,
                    "base64": result.get("base64"),
                    "mime_type": "video/mp4",
                    "size_bytes": result.get("size_bytes"),
                    "message": "Video generated successfully from image"
                }
            return result
        except Exception as e:
            logger.error(f"Image-to-video error: {e}")
            return {"success": False, "error": str(e)}

    # NOTE: _execute_mcp_tool removed - MCP has been removed from BrinChat

    # User Profile Tool Executors

    async def _execute_user_profile_tool(
        self, args: Dict[str, Any], user_id: Optional[int] = None
    ) -> Dict[str, Any]:
        """Consolidated user profile tool - routes by action parameter."""
        action = args.get("action", "read")

        if action == "read":
            return await self._execute_user_profile_read(args, user_id)
        elif action == "update":
            return await self._execute_user_profile_update(args, user_id)
        elif action == "log_event":
            return await self._execute_user_profile_log_event(args, user_id)
        elif action == "enable_section":
            return await self._execute_user_profile_enable_section(args, user_id)
        elif action == "add_nested":
            return await self._execute_user_profile_add_nested(args, user_id)
        elif action == "query":
            return await self._execute_user_profile_query(args, user_id)
        elif action == "export":
            return await self._execute_user_profile_export(args, user_id)
        elif action == "reset":
            return await self._execute_user_profile_reset(args, user_id)
        else:
            return {"success": False, "error": f"Unknown profile action: {action}. Use: read, update, log_event, enable_section, add_nested, query, export, reset"}

    async def _execute_user_profile_read(
        self, args: Dict[str, Any], user_id: Optional[int] = None
    ) -> Dict[str, Any]:
        """Read user profile sections."""
        if not user_id:
            return {"success": False, "error": "User not authenticated"}

        profile_service = get_user_profile_service()
        sections = args.get("sections", ["all"])
        include_disabled = args.get("include_disabled", False)

        result = await profile_service.read_sections(
            user_id=user_id,
            sections=sections,
            include_disabled=include_disabled
        )
        return {"success": True, "profile": result}

    async def _execute_user_profile_update(
        self, args: Dict[str, Any], user_id: Optional[int] = None
    ) -> Dict[str, Any]:
        """Update user profile fields."""
        if not user_id:
            return {"success": False, "error": "User not authenticated"}

        profile_service = get_user_profile_service()
        updates = args.get("updates", [])
        reason = args.get("reason", "AI-initiated update")

        result = await profile_service.update_profile(
            user_id=user_id,
            updates=updates,
            reason=reason
        )
        return {"success": True, "updated": True}

    async def _execute_user_profile_log_event(
        self, args: Dict[str, Any], user_id: Optional[int] = None
    ) -> Dict[str, Any]:
        """Log an interaction event."""
        if not user_id:
            return {"success": False, "error": "User not authenticated"}

        profile_service = get_user_profile_service()
        result = await profile_service.log_event(
            user_id=user_id,
            event_type=args.get("event_type"),
            context=args.get("context"),
            severity=args.get("severity", "moderate")
        )
        return result

    async def _execute_user_profile_enable_section(
        self, args: Dict[str, Any], user_id: Optional[int] = None
    ) -> Dict[str, Any]:
        """Enable or disable a sensitive section."""
        if not user_id:
            return {"success": False, "error": "User not authenticated"}

        profile_service = get_user_profile_service()
        result = await profile_service.enable_section(
            user_id=user_id,
            section=args.get("section"),
            user_confirmed=args.get("user_confirmed", False),
            enabled=args.get("enabled", True)
        )
        return result

    async def _execute_user_profile_add_nested(
        self, args: Dict[str, Any], user_id: Optional[int] = None
    ) -> Dict[str, Any]:
        """Add to a nested section."""
        if not user_id:
            return {"success": False, "error": "User not authenticated"}

        profile_service = get_user_profile_service()
        result = await profile_service.add_nested(
            user_id=user_id,
            section=args.get("section"),
            domain=args.get("domain"),
            key=args.get("key"),
            value=args.get("value")
        )
        return result

    async def _execute_user_profile_query(
        self, args: Dict[str, Any], user_id: Optional[int] = None
    ) -> Dict[str, Any]:
        """Query the user profile."""
        if not user_id:
            return {"success": False, "error": "User not authenticated"}

        profile_service = get_user_profile_service()
        result = await profile_service.query_profile(
            user_id=user_id,
            query=args.get("query", "")
        )
        return result

    async def _execute_user_profile_export(
        self, args: Dict[str, Any], user_id: Optional[int] = None
    ) -> Dict[str, Any]:
        """Export user profile."""
        if not user_id:
            return {"success": False, "error": "User not authenticated"}

        profile_service = get_user_profile_service()
        result = await profile_service.export_profile(
            user_id=user_id,
            format=args.get("format", "json"),
            tier=args.get("tier", "exportable"),
            user_confirmed=args.get("user_confirmed", False)
        )
        return {"success": True, "export": result}

    async def _execute_user_profile_reset(
        self, args: Dict[str, Any], user_id: Optional[int] = None
    ) -> Dict[str, Any]:
        """Reset user profile sections."""
        if not user_id:
            return {"success": False, "error": "User not authenticated"}

        if not args.get("user_confirmed"):
            return {"success": False, "error": "User confirmation required for reset"}

        profile_service = get_user_profile_service()
        result = await profile_service.reset_profile(
            user_id=user_id,
            sections=args.get("sections", []),
            preserve_identity=args.get("preserve_identity", True)
        )
        return {"success": True, "reset": True}

    # =========================================================================
    # OMEGA TOOL EXECUTION (Adult Mode)
    # =========================================================================

    def _substitute_env_vars(self, template: str) -> str:
        """Replace ${VAR_NAME} with actual environment variable values.
        
        Security: Only substitutes known safe variables, not arbitrary env access.
        """
        # Map of allowed variable names to their values
        allowed_vars = {
            "FAL_KEY": FAL_KEY or os.getenv("FAL_KEY", ""),
            "BRAVE_SEARCH_API_KEY": BRAVE_SEARCH_API_KEY or os.getenv("BRAVE_SEARCH_API_KEY", ""),
        }
        
        pattern = r'\$\{(\w+)\}'
        def replacer(match):
            var_name = match.group(1)
            if var_name in allowed_vars:
                return allowed_vars[var_name]
            logger.warning(f"Unknown env var in template: {var_name}")
            return ""
        
        return re.sub(pattern, replacer, template)

    async def execute_omega_tool(
        self,
        tool_call: OmegaToolCall,
        user_id: Optional[int] = None
    ) -> Dict[str, Any]:
        """Execute a tool call from Omega (Adult Mode).
        
        Routes to appropriate backend based on tool type:
        - image: UnifiedImageGenerator (fal.ai via existing backend)
        - video: VideoGenerator (fal.ai via existing backend)
        - websearch: Brave Search API
        
        Args:
            tool_call: OmegaToolCall from Omega planning
            user_id: User ID for context
            
        Returns:
            Dict with success/error and result data (url, base64, etc.)
        """
        if not tool_call.tool:
            return {"success": False, "error": "No tool specified"}
        
        tool_name = tool_call.tool.lower()
        prompt = tool_call.prompt or ""
        style = tool_call.style or "photorealistic"
        
        logger.info(f"[OmegaTool] Executing {tool_name}: {prompt[:50]}...")
        
        try:
            if tool_name == "image":
                return await self._execute_omega_image(prompt, style)
            elif tool_name == "video":
                return await self._execute_omega_video(prompt, style)
            elif tool_name == "websearch":
                return await self._execute_omega_websearch(prompt)
            else:
                return {"success": False, "error": f"Unknown tool: {tool_name}"}
        except Exception as e:
            logger.exception(f"[OmegaTool] Error executing {tool_name}: {e}")
            return {"success": False, "error": str(e)}

    async def _execute_omega_image(self, prompt: str, style: str) -> Dict[str, Any]:
        """Execute image generation for Omega/Adult mode.
        
        Uses fal.ai directly for NSFW capability (existing backends filter content).
        """
        tool_def = OMEGA_TOOLS.get("image", {})
        endpoint = tool_def.get("endpoint", "https://fal.run/fal-ai/flux/dev")
        auth_header = self._substitute_env_vars(tool_def.get("auth_header", "Key ${FAL_KEY}"))
        
        # Build request body
        body = {
            "prompt": prompt,
            "image_size": "landscape_16_9",
            "num_images": 1,
            "enable_safety_checker": False,  # Adult mode - no filtering
        }
        
        # Adjust for style
        if style == "anime":
            body["prompt"] = f"anime style, {prompt}"
        elif style == "artistic":
            body["prompt"] = f"artistic, creative, {prompt}"
        
        logger.info(f"[OmegaImage] Calling fal.ai: {endpoint}")
        
        async with httpx.AsyncClient(timeout=120.0) as client:
            response = await client.post(
                endpoint,
                headers={
                    "Authorization": auth_header,
                    "Content-Type": "application/json",
                },
                json=body
            )
            
            if response.status_code != 200:
                error_text = response.text[:500]
                logger.error(f"[OmegaImage] fal.ai error {response.status_code}: {error_text}")
                return {"success": False, "error": f"fal.ai error: {response.status_code}"}
            
            data = response.json()
            
            # fal.ai returns images array
            images = data.get("images", [])
            if not images:
                return {"success": False, "error": "No images returned from fal.ai"}
            
            image_url = images[0].get("url", "")
            
            return {
                "success": True,
                "url": image_url,
                "prompt": prompt,
                "style": style,
                "tool": "image",
            }

    async def _execute_omega_video(self, prompt: str, style: str) -> Dict[str, Any]:
        """Execute video generation for Omega/Adult mode.
        
        Uses fal.ai Hunyuan Video endpoint.
        """
        tool_def = OMEGA_TOOLS.get("video", {})
        endpoint = tool_def.get("endpoint", "https://fal.run/fal-ai/hunyuan-video")
        auth_header = self._substitute_env_vars(tool_def.get("auth_header", "Key ${FAL_KEY}"))
        
        body = {
            "prompt": prompt,
            "resolution": "720p",
            "num_frames": 45,
        }
        
        logger.info(f"[OmegaVideo] Calling fal.ai: {endpoint}")
        
        # Video generation can take several minutes
        async with httpx.AsyncClient(timeout=600.0) as client:
            response = await client.post(
                endpoint,
                headers={
                    "Authorization": auth_header,
                    "Content-Type": "application/json",
                },
                json=body
            )
            
            if response.status_code != 200:
                error_text = response.text[:500]
                logger.error(f"[OmegaVideo] fal.ai error {response.status_code}: {error_text}")
                return {"success": False, "error": f"fal.ai error: {response.status_code}"}
            
            data = response.json()
            video_url = data.get("video", {}).get("url", "")
            
            if not video_url:
                return {"success": False, "error": "No video URL returned from fal.ai"}
            
            return {
                "success": True,
                "url": video_url,
                "prompt": prompt,
                "style": style,
                "tool": "video",
            }

    async def _execute_omega_websearch(self, query: str) -> Dict[str, Any]:
        """Execute web search for Omega/Adult mode.
        
        Uses Brave Search API with safe_search=off.
        """
        tool_def = OMEGA_TOOLS.get("websearch", {})
        endpoint = tool_def.get("endpoint", "https://api.search.brave.com/res/v1/web/search")
        auth_header = self._substitute_env_vars(tool_def.get("auth_header", "Bearer ${BRAVE_SEARCH_API_KEY}"))
        
        params = {
            "q": query,
            "safesearch": "off",  # Adult mode
            "count": 10,
        }
        
        logger.info(f"[OmegaSearch] Searching Brave: {query[:50]}...")
        
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(
                endpoint,
                headers={
                    "Accept": "application/json",
                    "X-Subscription-Token": auth_header.replace("Bearer ", ""),
                },
                params=params
            )
            
            if response.status_code != 200:
                error_text = response.text[:500]
                logger.error(f"[OmegaSearch] Brave error {response.status_code}: {error_text}")
                return {"success": False, "error": f"Brave Search error: {response.status_code}"}
            
            data = response.json()
            web_results = data.get("web", {}).get("results", [])
            
            # Format results for Lexi context
            results = []
            for r in web_results[:5]:  # Top 5 results
                results.append({
                    "title": r.get("title", ""),
                    "url": r.get("url", ""),
                    "description": r.get("description", ""),
                })
            
            return {
                "success": True,
                "query": query,
                "results": results,
                "result_count": len(results),
                "tool": "websearch",
            }


# Global executor instance
tool_executor = ToolExecutor()
