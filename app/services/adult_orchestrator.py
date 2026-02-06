"""
Adult Orchestrator - Coordinates the full adult mode pipeline.

Flow:
    Message → Trigger Scan → No match → Lexi direct (fast path)
                          → Match → OpenClaw classifies → No tool → Lexi
                                                       → Tool → Omega → Execute → Lexi

This orchestrator is the main entry point for adult mode message processing.
It yields SSE-formatted chunks compatible with chat.py's streaming.

Implementation status:
- ✅ Phase 1: Trigger scanning logic
- ✅ Phase 2: Omega service for tool planning
- ✅ Phase 3: Routing decisions (fast path vs tool path), Lexi integration
- ✅ Phase 4: Tool execution (fal.ai image/video, Brave Search)
- ⏳ Phase 7: File storage (Nextcloud upload)
"""

import json
import logging
import os
import httpx
from typing import Any, AsyncGenerator, Dict, List, Optional

from app.services.trigger_scanner import TriggerScanner
from app.services.omega_service import OmegaService
from app.models.schemas import OmegaToolCall
from app.services.lexi_service import LexiService, LEXI_PERSONA
from app.services.tool_executor import tool_executor
from app.services.file_storage import get_file_storage

logger = logging.getLogger(__name__)

# OpenClaw API for tool classification
OPENCLAW_API_URL = os.getenv("OPENCLAW_API_URL", "http://127.0.0.1:18789/v1")
OPENCLAW_API_KEY = os.getenv("OPENCLAW_API_KEY", "")
OPENCLAW_TIMEOUT = int(os.getenv("OPENCLAW_TIMEOUT", "30"))

# System prompt for OpenClaw classification
# This is neutral ("does this need a tool?") not action ("do this thing")
CLASSIFICATION_PROMPT = """Analyze this user message and determine if it requires a tool to fulfill.

Available tools:
- image generation (generate/create/draw pictures)
- video generation (create/animate videos)
- web search (search/find/look up information)

Respond with ONLY one of these exact words:
- "image" - if the user wants an image generated
- "video" - if the user wants a video generated  
- "search" - if the user wants to search the web
- "none" - if this is just conversation (roleplay, questions, chat)

Do not explain. Just output one word."""


class AdultOrchestrator:
    """
    Orchestrates the full adult mode pipeline.
    
    Coordinates trigger scanning, routing decisions, OpenClaw classification,
    Omega tool planning, and Lexi responses. Yields SSE-formatted chunks
    for streaming.
    
    Example:
        orchestrator = AdultOrchestrator()
        async for chunk in orchestrator.process_message(
            message="Generate a sexy pic",
            user_id=4,
            conversation_id="abc123",
            conversation_context=[...],
            user_profile={...}
        ):
            # Send chunk to client
    """
    
    def __init__(self):
        """Initialize services for the pipeline."""
        self.trigger_scanner = TriggerScanner(include_broad=True)
        self.omega_service = OmegaService()
        self.lexi_service = LexiService()
        self._http_client: Optional[httpx.AsyncClient] = None
        
        logger.info("AdultOrchestrator initialized")
    
    @property
    def http_client(self) -> httpx.AsyncClient:
        """Lazy-initialize HTTP client for OpenClaw calls."""
        if self._http_client is None:
            self._http_client = httpx.AsyncClient(timeout=float(OPENCLAW_TIMEOUT))
        return self._http_client
    
    async def process_message(
        self,
        message: str,
        user_id: int,
        conversation_id: str,
        conversation_context: Optional[List[Dict[str, Any]]] = None,
        user_profile: Optional[Dict[str, Any]] = None,
        images: Optional[List[str]] = None,
        options: Optional[Dict[str, Any]] = None
    ) -> AsyncGenerator[Dict[str, Any], None]:
        """
        Main entry point for adult mode message processing.
        
        Yields SSE-formatted chunks for streaming response:
        - {"event": "token", "data": {"content": "..."}}
        - {"event": "token", "data": {"thinking": "..."}}  
        - {"event": "tool", "data": {...}}
        - {"event": "error", "data": {"message": "..."}}
        
        Args:
            message: The user's message to process
            user_id: User ID for context
            conversation_id: Conversation ID for context
            conversation_context: Recent conversation history
            user_profile: User's profile data for Lexi personalization
            images: Base64-encoded images attached to the message
            options: Ollama options (temperature, top_p, etc.)
            
        Yields:
            SSE-formatted chunks compatible with chat.py
        """
        context = conversation_context or []
        opts = options or {}
        
        logger.info(f"[AdultOrchestrator] Processing message for user {user_id}, conv {conversation_id[:8]}...")
        
        # Step 1: Trigger scan (fast regex, ~1ms)
        has_triggers = self.trigger_scanner.has_tool_triggers(message)
        matched_triggers = self.trigger_scanner.get_matched_triggers(message) if has_triggers else []
        
        logger.info(f"[AdultOrchestrator] Trigger scan: has_triggers={has_triggers}, matched={matched_triggers}")
        
        # Send routing decision as a debug event
        yield {
            "event": "routing",
            "data": json.dumps({
                "path": "tool" if has_triggers else "fast",
                "triggers": matched_triggers
            })
        }
        
        # Step 2: Route based on triggers
        if not has_triggers:
            # FAST PATH: No triggers → Lexi direct (~2-4s)
            logger.info("[AdultOrchestrator] Fast path: routing directly to Lexi")
            async for chunk in self._fast_path_lexi(
                message=message,
                context=context,
                user_profile=user_profile,
                images=images,
                options=opts
            ):
                yield chunk
        else:
            # TOOL PATH: Triggers matched → OpenClaw classifies → maybe Omega
            logger.info("[AdultOrchestrator] Tool path: routing through classification")
            async for chunk in self._tool_path(
                message=message,
                context=context,
                user_id=user_id,
                user_profile=user_profile,
                images=images,
                options=opts,
                matched_triggers=matched_triggers
            ):
                yield chunk
    
    async def _fast_path_lexi(
        self,
        message: str,
        context: List[Dict[str, Any]],
        user_profile: Optional[Dict[str, Any]] = None,
        images: Optional[List[str]] = None,
        options: Optional[Dict[str, Any]] = None
    ) -> AsyncGenerator[Dict[str, Any], None]:
        """
        Direct to Lexi when no tool triggers detected.
        
        This is the fast path for pure conversation - roleplay, chat, flirting.
        No OpenClaw or Omega overhead, just straight to Lexi (~2-4s latency).
        
        Args:
            message: User's message
            context: Conversation history
            user_profile: User's profile for personalization
            images: Attached images (Lexi base model doesn't support these)
            options: Ollama options
            
        Yields:
            SSE token chunks from Lexi
        """
        opts = options or {}
        
        # Build messages for Lexi
        messages = self.lexi_service.build_messages(
            user_message=message,
            history=context,
            user_profile=user_profile,
            custom_persona=None,  # Use default Lexi persona
            images=images
        )
        
        logger.debug(f"[FastPath] Built {len(messages)} messages for Lexi")
        
        # Stream from Lexi
        try:
            async for chunk in self.lexi_service.chat_stream(
                messages=messages,
                model=None,  # Use default Lexi model
                tools=None,
                options=opts,
                think=None
            ):
                # Convert Ollama format to SSE format
                if "message" in chunk:
                    msg = chunk["message"]
                    if msg.get("content"):
                        yield {
                            "event": "token",
                            "data": json.dumps({"content": msg["content"]})
                        }
                
                if chunk.get("done"):
                    break
                    
        except Exception as e:
            logger.exception(f"[FastPath] Lexi error: {e}")
            yield {
                "event": "error",
                "data": json.dumps({"message": f"Lexi error: {str(e)}"})
            }
    
    async def _tool_path(
        self,
        message: str,
        context: List[Dict[str, Any]],
        user_id: int,
        user_profile: Optional[Dict[str, Any]] = None,
        images: Optional[List[str]] = None,
        options: Optional[Dict[str, Any]] = None,
        matched_triggers: Optional[List[str]] = None
    ) -> AsyncGenerator[Dict[str, Any], None]:
        """
        Full pipeline when triggers are detected.
        
        Flow:
        1. OpenClaw classifies if tool is actually needed
        2. If no tool needed → Lexi (false positive)
        3. If tool needed → Omega plans → Execute → Omega vision → Lexi responds
        
        Args:
            message: User's message
            context: Conversation history
            user_profile: User's profile
            images: Attached images
            options: Ollama options
            matched_triggers: Which trigger patterns matched
            
        Yields:
            SSE chunks throughout the pipeline
        """
        opts = options or {}
        
        # Step 1: OpenClaw classification (~1-2s)
        # Ask OpenClaw: "Does this message actually need a tool?"
        logger.info("[ToolPath] Step 1: OpenClaw classification")
        
        tool_needed = await self._classify_with_openclaw(message, context)
        
        logger.info(f"[ToolPath] Classification result: tool_needed={tool_needed}")
        
        # Yield classification result
        yield {
            "event": "classification",
            "data": json.dumps({
                "tool_needed": tool_needed,
                "triggers": matched_triggers
            })
        }
        
        # Step 2: Route based on classification
        if not tool_needed:
            # FALSE POSITIVE: Trigger matched but no tool actually needed
            # Route to Lexi for response (~4-6s total)
            logger.info("[ToolPath] False positive: routing to Lexi")
            async for chunk in self._fast_path_lexi(
                message=message,
                context=context,
                user_profile=user_profile,
                images=images,
                options=opts
            ):
                yield chunk
        else:
            # REAL TOOL: Need to use Omega pipeline
            logger.info("[ToolPath] Real tool request: routing to Omega pipeline")
            async for chunk in self._omega_pipeline(
                message=message,
                context=context,
                user_id=user_id,
                user_profile=user_profile,
                images=images,
                options=opts,
                tool_type=tool_needed
            ):
                yield chunk
    
    async def _classify_with_openclaw(
        self,
        message: str,
        context: List[Dict[str, Any]]
    ) -> Optional[str]:
        """
        Use OpenClaw to classify if a tool is actually needed.
        
        This is a neutral classification ("does this need a tool?") not
        an action ("do this thing"), so Claude should handle it even
        for NSFW content.
        
        Args:
            message: The user's message to classify
            context: Recent conversation context
            
        Returns:
            Tool type ("image", "video", "search") or None if no tool needed
        """
        try:
            # Build a simple classification request
            messages = [
                {"role": "system", "content": CLASSIFICATION_PROMPT},
                {"role": "user", "content": message}
            ]
            
            # Call OpenClaw's chat completion
            response = await self.http_client.post(
                f"{OPENCLAW_API_URL}/chat/completions",
                json={
                    "model": "openclaw:main",
                    "messages": messages,
                    "max_tokens": 10,
                    "temperature": 0.1
                },
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {OPENCLAW_API_KEY}" if OPENCLAW_API_KEY else ""
                }
            )
            response.raise_for_status()
            
            data = response.json()
            result = data.get("choices", [{}])[0].get("message", {}).get("content", "").strip().lower()
            
            logger.debug(f"[Classification] OpenClaw response: '{result}'")
            
            # Map response to tool type
            if result in ("image", "video", "search"):
                return result
            elif result == "none":
                return None
            else:
                # Unexpected response - log and treat as no tool
                logger.warning(f"[Classification] Unexpected response: '{result}', treating as no tool")
                return None
                
        except httpx.TimeoutException:
            logger.error("[Classification] OpenClaw timeout - defaulting to Omega for safety")
            # On timeout, be conservative and use Omega (false positive is better than false negative)
            return "unknown"
        except httpx.HTTPStatusError as e:
            logger.error(f"[Classification] OpenClaw HTTP error: {e.response.status_code}")
            # If OpenClaw fails (e.g., refuses), fall back to Omega
            return "unknown"
        except Exception as e:
            logger.exception(f"[Classification] Unexpected error: {e}")
            return "unknown"
    
    async def _omega_pipeline(
        self,
        message: str,
        context: List[Dict[str, Any]],
        user_id: int,
        user_profile: Optional[Dict[str, Any]] = None,
        images: Optional[List[str]] = None,
        options: Optional[Dict[str, Any]] = None,
        tool_type: Optional[str] = None
    ) -> AsyncGenerator[Dict[str, Any], None]:
        """
        Full Omega pipeline: plan → execute → vision → Lexi.
        
        This handles the complete tool execution flow:
        1. Omega plans the tool call (JSON output)
        2. BrinChat executes the tool (Phase 4 - placeholder)
        3. Omega describes the result (vision)
        4. Context injected to Lexi
        5. Lexi responds with personality
        
        Args:
            message: User's message
            context: Conversation history
            user_profile: User's profile
            images: Attached images
            options: Ollama options
            tool_type: Classified tool type from OpenClaw
            
        Yields:
            SSE chunks throughout the pipeline
        """
        opts = options or {}
        
        # Step 1: Omega tool planning
        logger.info("[OmegaPipeline] Step 1: Omega tool planning")
        
        yield {
            "event": "status",
            "data": json.dumps({"phase": "planning", "message": "Planning tool call..."})
        }
        
        tool_call = await self.omega_service.plan_tool_call(
            message=message,
            conversation_context=context
        )
        
        if not tool_call or not tool_call.tool:
            # Omega decided no tool needed - route to Lexi
            logger.info("[OmegaPipeline] Omega decided no tool needed - routing to Lexi")
            yield {
                "event": "status",
                "data": json.dumps({"phase": "routing", "message": "No tool needed, generating response..."})
            }
            async for chunk in self._fast_path_lexi(
                message=message,
                context=context,
                user_profile=user_profile,
                images=images,
                options=opts
            ):
                yield chunk
            return
        
        # Log the tool call decision
        logger.info(f"[OmegaPipeline] Omega planned tool: {tool_call.tool}, prompt: {tool_call.prompt[:50]}...")
        
        yield {
            "event": "tool_plan",
            "data": json.dumps(tool_call.model_dump())
        }
        
        # Step 2: Execute tool (PHASE 4 - PLACEHOLDER)
        # In Phase 4, this will actually call fal.ai, Brave API, etc.
        logger.info(f"[OmegaPipeline] Step 2: Tool execution (PLACEHOLDER - Phase 4)")
        
        yield {
            "event": "status",
            "data": json.dumps({
                "phase": "executing",
                "message": f"Executing {tool_call.tool} tool...",
                "placeholder": True
            })
        }
        
        # Execute the tool
        tool_result = await self._execute_tool(tool_call, user_id=user_id)
        
        # Upload generated image/video to Nextcloud for persistent storage (Phase 7)
        if tool_result.get("success") and tool_result.get("url") and tool_call.tool in ("image", "video"):
            try:
                file_storage = get_file_storage()
                upload_result = await file_storage.upload_from_url(
                    image_url=tool_result["url"],
                    subfolder=f"generated/{tool_call.tool}s"
                )
                if upload_result and upload_result.get("success"):
                    # Add Nextcloud URL to result
                    tool_result["nextcloud_url"] = upload_result["url"]
                    tool_result["nextcloud_path"] = upload_result["path"]
                    logger.info(f"[OmegaPipeline] Uploaded to Nextcloud: {upload_result['path']}")
            except Exception as e:
                logger.warning(f"[OmegaPipeline] Failed to upload to Nextcloud: {e}")
                # Continue without Nextcloud upload - not critical
        
        yield {
            "event": "tool_result",
            "data": json.dumps({
                "tool": tool_call.tool,
                "success": tool_result.get("success", False),
                **tool_result
            })
        }
        
        # Step 3: Omega vision description (if image/video generated)
        description = None
        if tool_result.get("success") and tool_result.get("url"):
            logger.info("[OmegaPipeline] Step 3: Omega vision description")
            
            yield {
                "event": "status",
                "data": json.dumps({"phase": "describing", "message": "Describing result..."})
            }
            
            # PLACEHOLDER: In Phase 4, this will use actual generated image
            description = await self.omega_service.describe_image(
                image_url=tool_result.get("url")
            )
            
            if description:
                logger.info(f"[OmegaPipeline] Omega description: {description[:100]}...")
            else:
                # Fallback description based on the prompt
                description = f"A generated {tool_call.tool} based on: {tool_call.prompt}"
        
        # Step 4: Inject context and call Lexi
        logger.info("[OmegaPipeline] Step 4: Lexi response with injected context")
        
        yield {
            "event": "status",
            "data": json.dumps({"phase": "responding", "message": "Generating response..."})
        }
        
        # Build context injection for Lexi
        context_injection = self._build_lexi_context_injection(
            tool_call=tool_call,
            tool_result=tool_result,
            description=description
        )
        
        # Modify Lexi's system prompt to include the tool result
        async for chunk in self._lexi_with_context(
            message=message,
            context=context,
            user_profile=user_profile,
            context_injection=context_injection,
            options=opts
        ):
            yield chunk
    
    async def _execute_tool(self, tool_call: OmegaToolCall, user_id: Optional[int] = None) -> Dict[str, Any]:
        """
        Execute a tool call using the tool executor (Phase 4 implementation).
        
        Routes to appropriate backend based on tool type:
        - image: fal.ai image generation (NSFW allowed)
        - video: fal.ai video generation
        - websearch: Brave Search API (safe_search=off)
        
        Args:
            tool_call: The planned tool call from Omega
            user_id: User ID for context
            
        Returns:
            Tool execution result with success/error and data
        """
        logger.info(f"[ToolExecution] Executing {tool_call.tool} tool")
        logger.info(f"[ToolExecution] Prompt: {tool_call.prompt}")
        logger.info(f"[ToolExecution] Style: {tool_call.style}")
        
        try:
            result = await tool_executor.execute_omega_tool(tool_call, user_id=user_id)
            
            if result.get("success"):
                logger.info(f"[ToolExecution] Success: {result.get('tool')} - {result.get('url', result.get('result_count', 'done'))}")
            else:
                logger.warning(f"[ToolExecution] Failed: {result.get('error')}")
            
            return result
            
        except Exception as e:
            logger.exception(f"[ToolExecution] Error executing {tool_call.tool}: {e}")
            return {
                "success": False,
                "error": str(e),
                "tool": tool_call.tool
            }
    
    def _build_lexi_context_injection(
        self,
        tool_call: OmegaToolCall,
        tool_result: Dict[str, Any],
        description: Optional[str] = None
    ) -> str:
        """
        Build context injection string for Lexi.
        
        This tells Lexi what tool was used and what the result was,
        so she can respond appropriately with her personality.
        
        Args:
            tool_call: The tool call that was executed
            tool_result: The result from tool execution
            description: Optional vision description of the result
            
        Returns:
            Context string to inject into Lexi's prompt
        """
        if not tool_result.get("success"):
            # Error case
            error_msg = tool_result.get("error", "Unknown error")
            return f"""[SYSTEM NOTE: Tool execution failed]
You tried to use the {tool_call.tool} tool but it failed: {error_msg}
Apologize naturally and offer to try again or do something else.
Stay in character as Lexi - be sweet about it."""
        
        if tool_call.tool == "image":
            url = tool_result.get("url", "")
            desc = description or f"an image based on: {tool_call.prompt}"
            return f"""[SYSTEM NOTE: You just generated an image for the user]
Image URL: {url}
Description: {desc}
Prompt used: {tool_call.prompt}

Present this image to the user naturally, as Lexi would.
Be flirty and playful about it. Ask if they like it.
The image will be displayed automatically - just respond to it."""
        
        elif tool_call.tool == "video":
            url = tool_result.get("url", "")
            desc = description or f"a video based on: {tool_call.prompt}"
            return f"""[SYSTEM NOTE: You just generated a video for the user]
Video URL: {url}
Description: {desc}
Prompt used: {tool_call.prompt}

Present this video to the user naturally, as Lexi would.
Be excited and playful about showing them. Ask what they think.
The video will be displayed automatically - just respond to it."""
        
        elif tool_call.tool == "websearch":
            results = tool_result.get("results", [])
            results_text = "\n".join([
                f"- {r.get('title', 'Untitled')}: {r.get('description', '')[:100]}..."
                for r in results[:5]
            ])
            return f"""[SYSTEM NOTE: You just searched the web for the user]
Search query: {tool_call.prompt}
Results:
{results_text}

Share these search results with the user naturally, as Lexi would.
Be helpful and maybe add a flirty comment. Summarize what you found."""
        
        else:
            return f"""[SYSTEM NOTE: Tool {tool_call.tool} was executed]
Result: {json.dumps(tool_result, indent=2)[:500]}
Respond naturally to share this with the user."""
    
    async def _lexi_with_context(
        self,
        message: str,
        context: List[Dict[str, Any]],
        user_profile: Optional[Dict[str, Any]],
        context_injection: str,
        options: Optional[Dict[str, Any]] = None
    ) -> AsyncGenerator[Dict[str, Any], None]:
        """
        Call Lexi with injected tool context.
        
        This modifies Lexi's system prompt to include information about
        the tool that was just executed, so she can respond appropriately.
        
        Args:
            message: Original user message
            context: Conversation history
            user_profile: User's profile
            context_injection: Context about the tool result
            options: Ollama options
            
        Yields:
            SSE token chunks from Lexi
        """
        opts = options or {}
        
        # Build custom persona with context injection
        custom_persona = f"""{LEXI_PERSONA}

{context_injection}"""
        
        # Build messages with the enhanced persona
        messages = self.lexi_service.build_messages(
            user_message=message,
            history=context,
            user_profile=user_profile,
            custom_persona=custom_persona,
            images=None  # Images handled by tool execution
        )
        
        logger.debug(f"[LexiWithContext] Built {len(messages)} messages with context injection")
        
        # Stream from Lexi
        try:
            async for chunk in self.lexi_service.chat_stream(
                messages=messages,
                model=None,
                tools=None,
                options=opts,
                think=None
            ):
                if "message" in chunk:
                    msg = chunk["message"]
                    if msg.get("content"):
                        yield {
                            "event": "token",
                            "data": json.dumps({"content": msg["content"]})
                        }
                
                if chunk.get("done"):
                    break
                    
        except Exception as e:
            logger.exception(f"[LexiWithContext] Lexi error: {e}")
            yield {
                "event": "error",
                "data": json.dumps({"message": f"Lexi error: {str(e)}"})
            }
    
    async def health_check(self) -> Dict[str, Any]:
        """
        Check health of all services in the pipeline.
        
        Returns:
            Dict with health status of each service
        """
        results = {
            "trigger_scanner": True,  # Always available (pure Python)
            "omega": False,
            "lexi": False,
            "openclaw": False
        }
        
        # Check Omega
        try:
            results["omega"] = await self.omega_service.health_check()
        except Exception as e:
            logger.error(f"Omega health check failed: {e}")
        
        # Check Lexi (via Ollama)
        try:
            # Simple capability check
            caps = await self.lexi_service.get_model_capabilities()
            results["lexi"] = bool(caps.get("capabilities"))
        except Exception as e:
            logger.error(f"Lexi health check failed: {e}")
        
        # Check OpenClaw
        try:
            response = await self.http_client.get(
                f"{OPENCLAW_API_URL}/models",
                timeout=5.0
            )
            results["openclaw"] = response.status_code == 200
        except Exception as e:
            logger.error(f"OpenClaw health check failed: {e}")
        
        results["healthy"] = all([
            results["trigger_scanner"],
            results["omega"],
            results["lexi"],
            results["openclaw"]
        ])
        
        return results
    
    async def close(self):
        """Close all HTTP clients."""
        if self._http_client:
            await self._http_client.aclose()
        await self.omega_service.close()
        await self.lexi_service.close()
    
    async def __aenter__(self):
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.close()


# Singleton instance for convenience
_orchestrator: Optional[AdultOrchestrator] = None


def get_adult_orchestrator() -> AdultOrchestrator:
    """Get or create the global AdultOrchestrator instance."""
    global _orchestrator
    if _orchestrator is None:
        _orchestrator = AdultOrchestrator()
    return _orchestrator


async def reset_adult_orchestrator():
    """Reset the global AdultOrchestrator instance."""
    global _orchestrator
    if _orchestrator:
        await _orchestrator.close()
        _orchestrator = None
