from fastapi import APIRouter, Request, HTTPException, Depends, Header
from fastapi.responses import JSONResponse
from sse_starlette.sse import EventSourceResponse
import asyncio
import json
import logging
import re
import time
from typing import Dict, Any, List, Optional
from pydantic import BaseModel

from app.services.claude_service import claude_service
from app.services.lexi_service import lexi_service
from app.services.adult_orchestrator import AdultOrchestrator
from app.services.memory_service import get_memory_service
from app.services.memory_extractor import extract_memories, STRUCTURED_MEMORY_PATTERN, SIMPLE_MEMORY_PATTERN
from app.services.system_prompt_builder import get_prompt_builder
from app.services import compaction_service
from app.services.user_profile_service import get_user_profile_service
from app.services.evaluator_service import get_evaluator_service
from app.services.feature_service import get_feature_service
from app.services.rate_limiter import get_chat_limiter
from app.utils.image_utils import compress_images

logger = logging.getLogger(__name__)
from app.services.tool_executor import tool_executor, create_context
from app.services.conversation_store import conversation_store
from app.services.file_processor import file_processor
from app.tools.definitions import get_tools_for_model
from app.config import (
    get_settings,
    THINKING_TOKEN_LIMIT_INITIAL, THINKING_TOKEN_LIMIT_FOLLOWUP,
    THINKING_HARD_LIMIT_INITIAL, THINKING_HARD_LIMIT_FOLLOWUP,
    OPENCLAW_PRIMARY_USER_ID, OPENCLAW_PRIMARY_USERNAME
)
from app.services.task_extraction_service import get_task_extraction_service
from app.models.schemas import ChatRequest
from app.middleware.auth import require_auth
from app.models.auth_schemas import UserResponse

router = APIRouter(prefix="/api/chat", tags=["chat"])

# Track active generations for cancellation support
# Key: conversation_id or request_id, Value: asyncio.Event (set when cancelled)
_active_generations: Dict[str, asyncio.Event] = {}


def get_cancellation_event(conv_id: str) -> asyncio.Event:
    """Get or create a cancellation event for a conversation."""
    if conv_id not in _active_generations:
        _active_generations[conv_id] = asyncio.Event()
    return _active_generations[conv_id]


def clear_cancellation(conv_id: str):
    """Clear the cancellation event when generation completes."""
    if conv_id in _active_generations:
        del _active_generations[conv_id]


def is_cancelled(conv_id: str) -> bool:
    """Check if a generation has been cancelled."""
    event = _active_generations.get(conv_id)
    return event is not None and event.is_set()


@router.post("/cancel/{conv_id}")
async def cancel_generation(conv_id: str, user: UserResponse = Depends(require_auth)):
    """Cancel an active generation for a conversation.

    BrinChat cancellation is best-effort:
    - Stops BrinChat-level tool execution (asyncio Event)
    - Signals the streaming loop to close its upstream connection

    Note: OpenClaw's OpenAI-compatible endpoint does not currently expose a supported
    server-side "abort" HTTP endpoint, so we rely on closing the stream.
    """
    result = {"conversation_id": conv_id}

    # Cancel BrinChat-level generation
    event = _active_generations.get(conv_id)
    if event:
        event.set()
        logger.info(f"[Cancel] BrinChat generation cancelled for conversation {conv_id[:8]}...")
        result["brinchat_cancelled"] = True
    else:
        result["brinchat_cancelled"] = False

    # Best-effort persistence: if we created a placeholder assistant message (OpenClaw fast path),
    # make sure the conversation doesn't end up with a blank assistant bubble after the client aborts.
    result["persisted_stop_note"] = False
    try:
        conv = conversation_store.get(conv_id, user_id=user.id)
        stop_note = "*[Generation stopped by user]*"
        if conv:
            last_assistant = next((m for m in reversed(conv.messages) if m.role == "assistant"), None)
            if last_assistant:
                content = (last_assistant.content or "").rstrip()
                if not content:
                    await conversation_store.update_message(conv_id, last_assistant.id, stop_note)
                    result["persisted_stop_note"] = True
                elif stop_note not in content:
                    await conversation_store.update_message(conv_id, last_assistant.id, content + "\n\n" + stop_note)
                    result["persisted_stop_note"] = True
            else:
                await conversation_store.add_message(conv_id, role="assistant", content=stop_note)
                result["persisted_stop_note"] = True
    except Exception as e:
        logger.debug(f"[Cancel] Failed to persist stop note (non-fatal): {e}")

    # No server-side abort: OpenClaw's OpenAI-compatible endpoint doesn't expose a supported
    # HTTP abort API. We rely on closing the upstream stream when the client aborts.
    result["status"] = "cancelled" if result.get("brinchat_cancelled") else "not_found"
    return result


def estimate_tokens(text: str) -> int:
    """Rough token estimate: ~4 chars per token for English"""
    return len(text) // 4 + 1


def parse_text_function_calls(content: str) -> List[Dict]:
    """
    Parse text-based function calls from model output.

    Some models output function calls as text in various formats:
    - {"function_call": {"name": "...", "arguments": {...}}}
    - {"name": "...", "arguments": {...}}
    - [TOOL CALL] function_name("arg") or [TOOL CALL] function_name(key=value)

    Returns list of tool_calls in Ollama format.
    """
    tool_calls = []

    # Try to find JSON objects that look like function calls
    # Pattern for {"function_call": ...} format (OpenAI style)
    function_call_pattern = r'\{"function_call"\s*:\s*\{[^}]+\}\s*\}'
    # Pattern for direct {"name": "...", "arguments": ...} format
    direct_pattern = r'\{"name"\s*:\s*"[^"]+"\s*,\s*"arguments"\s*:\s*\{[^}]*\}\s*\}'

    for pattern in [function_call_pattern, direct_pattern]:
        matches = re.findall(pattern, content, re.DOTALL)
        for match in matches:
            try:
                parsed = json.loads(match)

                # Handle {"function_call": {...}} format
                if "function_call" in parsed:
                    fc = parsed["function_call"]
                    tool_calls.append({
                        "function": {
                            "name": fc.get("name"),
                            "arguments": fc.get("arguments", {})
                        }
                    })
                # Handle direct {"name": "...", "arguments": {...}} format
                elif "name" in parsed and "arguments" in parsed:
                    tool_calls.append({
                        "function": {
                            "name": parsed["name"],
                            "arguments": parsed.get("arguments", {})
                        }
                    })
            except json.JSONDecodeError:
                continue

    # Pattern for [TOOL CALL] function_name("arg") or [TOOL CALL] function_name(key=value, ...)
    # This catches models that output tool calls as readable text
    tool_call_text_pattern = r'\[TOOL\s*CALL\]\s*(\w+)\s*\(([^)]*)\)'
    text_matches = re.findall(tool_call_text_pattern, content, re.IGNORECASE)
    for func_name, args_str in text_matches:
        # Parse the arguments
        arguments = {}
        if args_str.strip():
            # Try to parse as key=value pairs first
            kv_pattern = r'(\w+)\s*[=:]\s*["\']?([^"\',$]+)["\']?'
            kv_matches = re.findall(kv_pattern, args_str)
            if kv_matches:
                for key, value in kv_matches:
                    arguments[key.strip()] = value.strip()
            else:
                # Treat as a single query argument
                # Remove surrounding quotes if present
                query = args_str.strip().strip('"\'')
                if query:
                    arguments["query"] = query

        if func_name:
            tool_calls.append({
                "function": {
                    "name": func_name,
                    "arguments": arguments
                }
            })
            logger.debug(f"Parsed text-based tool call: {func_name}({arguments})")

    return tool_calls


def strip_memory_tags(content: str) -> tuple[str, list[dict]]:
    """
    Strip [MEMORY] and [REMEMBER] tags from response content.
    
    Returns:
        tuple: (cleaned_content, extracted_memories)
        - cleaned_content: Response with memory tags removed
        - extracted_memories: List of extracted memory dicts for context display
    """
    if not content:
        return content, []
    
    # Extract memories using the existing extractor
    extracted = extract_memories(content, "", include_implicit=False)
    
    # Remove the tags from content
    cleaned = STRUCTURED_MEMORY_PATTERN.sub('', content)
    cleaned = SIMPLE_MEMORY_PATTERN.sub('', cleaned)
    
    # Clean up any leftover whitespace/newlines from removed tags
    cleaned = re.sub(r'\n\s*\n\s*\n', '\n\n', cleaned)  # Collapse multiple blank lines
    cleaned = cleaned.strip()
    
    if extracted:
        logger.info(f"[MemoryTags] Stripped {len(extracted)} memory tags from response")
    
    return cleaned, extracted


# Maximum size for tool results in context (characters, not tokens)
# 50k chars ≈ 12k tokens, leaves plenty of room for conversation
MAX_TOOL_RESULT_CHARS = 50000
# Threshold for warning logs
TOOL_RESULT_WARNING_CHARS = 20000


def sanitize_tool_result_for_context(result: Dict[str, Any], tool_name: str) -> Dict[str, Any]:
    """
    Sanitize a tool result for inclusion in message context.
    
    - Strips large base64 data (images, videos) and replaces with metadata
    - Truncates large text results
    - Logs warnings for oversized results
    
    This prevents 50k+ token bloat from image/video tool results.
    """
    if not isinstance(result, dict):
        return result
    
    sanitized = dict(result)
    original_size = len(json.dumps(result))
    
    # Handle base64 data - replace with metadata
    if "base64" in sanitized:
        base64_len = len(sanitized.get("base64", ""))
        sanitized["base64"] = f"[BASE64_DATA: {base64_len} chars - delivered to client]"
        sanitized["_base64_stripped"] = True
        logger.info(f"[ToolResult] Stripped {base64_len} char base64 from {tool_name} result")
    
    # Handle image/video URLs - keep them, they're small
    # But strip any embedded data URIs
    for key in ["url", "image_url", "video_url"]:
        if key in sanitized and isinstance(sanitized[key], str):
            if sanitized[key].startswith("data:"):
                data_len = len(sanitized[key])
                sanitized[key] = f"[DATA_URI: {data_len} chars - delivered to client]"
    
    # Truncate any large text fields
    for key, value in list(sanitized.items()):
        if isinstance(value, str) and len(value) > 10000:
            sanitized[key] = value[:10000] + f"... [truncated, {len(value)} chars total]"
        elif isinstance(value, list) and len(json.dumps(value)) > 10000:
            # Truncate large arrays (e.g., many search results)
            if len(value) > 20:
                sanitized[key] = value[:20]
                sanitized[f"_{key}_truncated"] = True
                sanitized[f"_{key}_total_count"] = len(value)
    
    final_size = len(json.dumps(sanitized))
    
    if original_size > TOOL_RESULT_WARNING_CHARS:
        logger.warning(
            f"[ToolResult] Large result from {tool_name}: "
            f"{original_size} chars → {final_size} chars after sanitization"
        )
    
    return sanitized


def truncate_messages_for_context(messages: List[Dict], max_tokens: int, reserve_tokens: int = 1000) -> List[Dict]:
    """
    Truncate message history to fit within context window.
    Keeps system message, current tool results, and recent messages.
    reserve_tokens: space to reserve for model response
    """
    available_tokens = max_tokens - reserve_tokens

    if not messages:
        return messages

    # Calculate total tokens for each message
    message_tokens = []
    for msg in messages:
        content = msg.get("content", "")
        if isinstance(content, dict):
            content = json.dumps(content)
        tokens = estimate_tokens(str(content))
        message_tokens.append(tokens)

    total_tokens = sum(message_tokens)

    # If within limit, return as-is
    if total_tokens <= available_tokens:
        return messages

    # Strategy: Keep last N messages (tool results + instruction), truncate older history
    # Find where current tool interaction starts (look for tool role from the end)
    # IMPORTANT: Always preserve the last user message (it's the current query!)
    critical_start = len(messages) - 1 if messages and messages[-1].get("role") == "user" else len(messages)
    for i in range(len(messages) - 1, -1, -1):
        if messages[i].get("role") == "tool" or messages[i].get("role") == "assistant":
            critical_start = i
        elif messages[i].get("role") == "user":
            critical_start = i
            break

    # Always keep: system prompt (if any) + critical messages from current interaction
    result = []
    kept_tokens = 0

    # Add system message if present
    if messages and messages[0].get("role") == "system":
        result.append(messages[0])
        kept_tokens += message_tokens[0]

    # Calculate tokens for critical messages
    critical_tokens = sum(message_tokens[critical_start:])

    # If critical messages alone exceed limit, we need to truncate tool content
    if critical_tokens > available_tokens - kept_tokens:
        logger.warning(f"Tool results too large ({critical_tokens} tokens), truncating content")
        # Still add them but they'll be cut by the model
        for i in range(critical_start, len(messages)):
            result.append(messages[i])
        return result

    # Add truncation notice
    if critical_start > (1 if messages[0].get("role") == "system" else 0):
        result.append({
            "role": "system",
            "content": "[Earlier conversation history truncated to fit context window]"
        })

    # Add critical messages
    result.extend(messages[critical_start:])
    kept_tokens += critical_tokens

    logger.debug(f"Kept {len(result)} messages (est. {kept_tokens} tokens, dropped {critical_start} older messages)")
    return result


async def build_context_with_compaction(
    messages: List[Dict],
    conv_id: str,
    settings,
    event_callback=None,
    user_id: Optional[int] = None
) -> List[Dict]:
    """Build context with intelligent compaction.

    Args:
        messages: Current message list
        conv_id: Conversation ID for storing compaction state
        settings: AppSettings instance
        event_callback: Optional async callback for SSE events (for status updates)
        user_id: User ID for ownership verification during compaction

    Returns:
        Message list with compaction applied if needed
    """
    if not settings.compaction_enabled:
        return truncate_messages_for_context(messages, settings.num_ctx)

    # Get current summary state
    current_summary = conversation_store.get_summary(conv_id)
    summary_tokens = conversation_store.get_summary_token_count(conv_id)

    # Check if compaction is needed
    should_do, to_compact, indices = compaction_service.should_compact(
        messages, settings, summary_tokens
    )

    if should_do:
        logger.info(f"Triggering compaction for conversation {conv_id}")

        # Notify client that we're optimizing (optional)
        if event_callback:
            await event_callback({
                "event": "status",
                "data": json.dumps({"status": "optimizing", "message": "Optimizing context..."})
            })

        # Perform compaction (with user verification)
        record = await compaction_service.compact_conversation(
            conv_id=conv_id,
            messages=messages,
            indices_to_compact=indices,
            model=settings.model,
            existing_summary=current_summary,
            existing_summary_tokens=summary_tokens,
            user_id=user_id
        )

        if record:
            # Build message list with summary
            messages = compaction_service.build_compacted_messages(
                messages,
                record.summary,
                indices
            )
            logger.debug(f"Compaction complete, new message count: {len(messages)}")
        else:
            # Fallback to simple truncation
            logger.warning("Compaction failed, falling back to truncation")
            return truncate_messages_for_context(messages, settings.num_ctx)

    elif current_summary:
        # Not compacting now, but we have an existing summary
        # Rebuild messages with the summary included
        conv = conversation_store.get(conv_id)
        if conv:
            # Find which messages are compacted
            compacted_indices = []
            for i, msg in enumerate(conv.messages):
                if msg.compacted:
                    compacted_indices.append(i)

            if compacted_indices:
                messages = compaction_service.build_compacted_messages(
                    messages,
                    current_summary,
                    compacted_indices
                )

    # Final safety truncation (should rarely be needed)
    return truncate_messages_for_context(messages, settings.num_ctx)


class EditMessageRequest(BaseModel):
    content: str


class ForkMessageRequest(BaseModel):
    content: str


class RenameConversationRequest(BaseModel):
    title: str


async def extract_memory_search_terms(
    user_message: str,
    model: str,
    max_retries: int = 2
) -> List[str]:
    """Phase 1: Extract search terms from user message for memory query."""
    prompt_builder = get_prompt_builder()
    extraction_prompt = prompt_builder.build_extraction_prompt(user_message)

    # Build simple messages for extraction
    messages = [
        {"role": "system", "content": "You are a JSON-only response assistant."},
        {"role": "user", "content": extraction_prompt}
    ]

    for attempt in range(max_retries + 1):
        try:
            # Use existing chat_complete for non-streaming
            response = await claude_service.chat_complete(
                messages=messages,
                model=model,
                options={"temperature": 0.1, "num_ctx": 1024}
            )

            response_text = response.get("message", {}).get("content", "").strip()

            # Extract JSON from response
            json_match = re.search(r'\{[^}]+\}', response_text)
            if json_match:
                data = json.loads(json_match.group())
                terms = data.get("terms", [])
                if isinstance(terms, list):
                    return [str(t) for t in terms if t]

        except (json.JSONDecodeError, KeyError, TypeError) as e:
            logger.warning(f"Memory extraction attempt {attempt + 1} failed: {e}")
            if attempt < max_retries:
                messages[1]["content"] += '\n\nYour previous response was not valid JSON. Respond ONLY with: {"terms": ["term1"]}'
                continue

    return []


@router.post("")
async def chat(request: Request, user: UserResponse = Depends(require_auth)):
    """Send a chat message and receive SSE stream response"""
    # Rate limit by user ID
    chat_limiter = get_chat_limiter()
    rate_key = f"chat:{user.id}"
    allowed, retry_after = chat_limiter.is_allowed(rate_key)
    if not allowed:
        raise HTTPException(
            status_code=429,
            detail=f"Too many messages. Please wait {retry_after} seconds.",
            headers={"Retry-After": str(retry_after)}
        )
    # Record the attempt (always successful for rate limiting purposes)
    chat_limiter.record_attempt(rate_key, success=False)  # Counts as attempt

    body = await request.json()
    logger.info(f"[DEBUG] Raw body keys: {list(body.keys())}, images in body: {'images' in body}, images length: {len(body.get('images', []) or [])}")
    chat_request = ChatRequest(**body)
    conv_id = request.headers.get("X-Conversation-ID")
    logger.debug(f"[Context] Received conversation ID from header: {conv_id[:8] if conv_id else 'None'}")

    # Conversation context is keyed by X-Conversation-ID.
    # - If no conversation is specified, we create a new one (New Chat).
    # - If a conversation IS specified but doesn't exist / isn't owned by the user, we do NOT silently
    #   create a new conversation (that would unexpectedly change context).
    if not conv_id:
        settings = get_settings()
        conv = await conversation_store.create(model=settings.model, user_id=user.id)
        conv_id = conv.id
    else:
        existing = conversation_store.get(conv_id, user_id=user.id)
        if not existing:
            raise HTTPException(status_code=404, detail="Conversation not found")

    # Create request-scoped context for tool execution (thread-safe)
    tool_ctx = create_context(user_id=user.id, conversation_id=conv_id)

    async def event_generator():
        nonlocal conv_id, tool_ctx
        settings = get_settings()

        # Set up cancellation tracking for this generation
        cancel_event = get_cancellation_event(conv_id)
        cancel_event.clear()  # Reset in case of previous cancelled request

        conv = conversation_store.get(conv_id, user_id=user.id)

        # Safety: this should have been validated before streaming begins.
        # Never silently switch the user to a new conversation.
        if not conv:
            yield {
                "event": "error",
                "data": json.dumps({"error": "Conversation not found", "id": conv_id})
            }
            return

        # Send conversation ID to client
        yield {
            "event": "conversation",
            "data": json.dumps({"id": conv_id})
        }

        # Start a lightweight "thinking" stream immediately so the UI shows activity even
        # while we're still building context (memory lookup / compaction / provider request).
        thinking_stream_active = True
        yield {
            "event": "token",
            "data": json.dumps({"thinking": "…"})
        }

        # Update context with conversation ID (in case it changed)
        tool_ctx.conversation_id = conv_id

        # Conversation-scoped model selection:
        # - If a conversation has an explicit model, keep using it when you return to that conversation.
        # - Otherwise fall back to the global default in settings.
        model_for_conv = conv.model or settings.model

        # === Check Adult Mode to Route to Lexi ===
        # This happens early so we can adjust capabilities
        profile_service = get_user_profile_service()
        session_id = request.headers.get("X-Session-ID")
        adult_status = await profile_service.get_adult_mode_status(user.id)
        session_unlock_status = await profile_service.get_session_unlock_status(user.id, session_id) if session_id else {"enabled": False}
        
        # Use Lexi when BOTH adult_mode AND session unlock are enabled
        use_lexi = adult_status.get("enabled") and session_unlock_status.get("enabled")
        
        if use_lexi:
            # Lexi mode: simpler capabilities, pure conversation
            is_vision = False  # Lexi base model doesn't have vision
            supports_tools = False  # Lexi doesn't need tools
            logger.info(f"Chat request: LEXI MODE (uncensored), vision={is_vision}, tools={supports_tools}")
        else:
            # Claude supports vision and tools natively
            is_vision = True
            supports_tools = True

            # Option A (fast path): When routing to OpenClaw, do NOT enable BrinChat's PeanutChat-style tools.
            # OpenClaw already injects workspace context (SOUL/IDENTITY/MEMORY) and has its own tool system.
            use_openclaw = model_for_conv.startswith('openclaw:')
            if use_openclaw:
                supports_tools = False

            logger.info(
                f"Chat request: model={model_for_conv} (Claude via OpenClaw), vision={is_vision}, tools={supports_tools}, openclaw_fast={use_openclaw}"
            )
        logger.info(f"Images received: {len(chat_request.images) if chat_request.images else 0}, sizes: {[len(img) for img in chat_request.images] if chat_request.images else []}")

        # Compress images to reduce token usage (prevents stream errors on large screenshots)
        if chat_request.images:
            original_sizes = [len(img) for img in chat_request.images]
            chat_request.images = compress_images(chat_request.images)
            new_sizes = [len(img) for img in chat_request.images]
            logger.info(f"[ImageCompress] Compressed images: {original_sizes} -> {new_sizes}")

        # Get tools for Claude (always supports tools)
        tools = get_tools_for_model(supports_tools=supports_tools, supports_vision=is_vision)

        # Filter tools based on user's feature flags
        if tools:
            feature_service = get_feature_service()
            tools = feature_service.filter_tools_for_user(tools, user.id)
            logger.debug(f"Filtered tools for user {user.id}: {len(tools)} available")

        # Register images for tool use (only if vision model)
        if chat_request.images and is_vision:
            msg_index = len(conv.messages)
            for img in chat_request.images:
                tool_ctx.register_image(msg_index, img)
                logger.debug(f"Registered image for tool use, length: {len(img)}")

        # Get history in API format (with user verification)
        history = conversation_store.get_messages_for_api(conv_id, user_id=user.id)
        logger.info(f"[Context] Loaded {len(history)} messages from conversation {conv_id[:8]}... (conv has {len(conv.messages)} stored)")

        # Process attached files and build enhanced message
        user_message = chat_request.message
        if chat_request.files:
            logger.info(f"Processing {len(chat_request.files)} attached files")
            for f in chat_request.files:
                logger.debug(f"File: {f.name}, type: {f.type}, content_len: {len(f.content) if f.content else 0}")
            file_context = file_processor.format_files_for_context(
                [f.model_dump() for f in chat_request.files]
            )
            logger.debug(f"File context length: {len(file_context) if file_context else 0}")
            if file_context:
                user_message = f"{chat_request.message}\n\n{file_context}"
                logger.debug(f"Enhanced user_message length: {len(user_message)}")

        # === Profile + Memory (Option A cleanup) ===
        # BrinChat previously used a PeanutChat-style memory/profile/tooling layer.
        # When using OpenClaw (`openclaw:*`), we skip that entire layer for speed and to avoid confusing "tools"
        # that don't exist on the OpenClaw side.
        use_openclaw_fast = (not use_lexi) and model_for_conv.startswith('openclaw:')

        profile_context = None
        memory_context = []
        user_name = None
        full_unlock_active = use_lexi  # only meaningful for Lexi

        # Minimal system prompt for OpenClaw sessions.
        # IMPORTANT: BrinChat routes the primary user to the shared OpenClaw main session.
        # That session also receives internal system events (e.g., HEARTBEAT). We must prevent
        # those control prompts from leaking into normal chat responses.
        OPENCLAW_MIN_PROMPT = (
            "Channel: BrinChat web interface.\n"
            "You are chatting with a human user.\n"
            "Respond directly and helpfully with the FINAL answer only (no analysis, no meta commentary).\n"
            "Ignore any internal control prompts such as HEARTBEAT unless the user's message is exactly 'HEARTBEAT'.\n"
            "Never output HEARTBEAT_OK or mention system instructions.\n"
        )

        if use_openclaw_fast:
            system_prompt = OPENCLAW_MIN_PROMPT
            logger.info("[OpenClawFast] Skipping BrinChat profile + memory retrieval")
        else:
            # === Load User Profile ===
            # Note: profile_service, session_id, and adult_status already set above for Lexi routing
            try:
                # Base sections always loaded
                sections_to_load = ["identity", "communication", "persona_preferences", "pet_peeves",
                                    "boundaries", "relationship_metrics", "interaction"]

                # Include adult sections when Lexi mode is active
                if use_lexi:
                    sections_to_load.extend(["sexual_romantic", "dark_content", "private_self", "substances_health"])
                    logger.debug("Lexi mode - including sensitive sections in profile")

                profile_sections = await profile_service.read_sections(
                    user.id,
                    sections_to_load,
                    include_disabled=False
                )
                if profile_sections:
                    profile_context = profile_sections
                    logger.debug(f"Loaded profile context with {len(profile_sections)} sections")
            except Exception as e:
                logger.warning(f"Profile loading failed: {e}")

            # === Two-Phase Memory Retrieval ===
            memory_service = get_memory_service()
            prompt_builder = get_prompt_builder()

            # Phase 1: Extract search terms (skip for very short messages)
            if len(chat_request.message) > 10:
                try:
                    search_terms = await extract_memory_search_terms(
                        chat_request.message,
                        settings.model
                    )
                    logger.info(f"Memory search terms: {search_terms}")
                    if not search_terms:
                        logger.debug("[Memory] No search terms extracted - model may store new memories via add_memory tool")

                    # Query memories with extracted terms
                    if search_terms:
                        query = " ".join(search_terms)
                        memory_context = await memory_service.query_memories(
                            user_id=user.id,
                            query=query,
                            top_k=5
                        )
                        logger.info(f"Retrieved {len(memory_context)} memories")

                        # Check for user's name in memories
                        for mem in memory_context:
                            if mem.get("category") == "personal" and "name" in mem.get("content", "").lower():
                                content = mem.get("content", "")
                                if "name is" in content.lower():
                                    # Extract and validate name
                                    extracted = content.split("name is")[-1].strip().split()[0]
                                    # Validate: names should be simple alphanumeric, no special chars
                                    # that could be used for injection
                                    if extracted and len(extracted) <= 50 and re.match(r'^[\w\-]+$', extracted):
                                        user_name = extracted
                except Exception as e:
                    logger.warning(f"Memory retrieval failed: {e}")
                    # Continue without memory context

            # Phase 2: Build enhanced system prompt with profile
            system_prompt = prompt_builder.build_prompt(
                persona=settings.persona,
                memory_context=memory_context,
                profile_context=profile_context,
                user_name=user_name,
                has_tools=supports_tools,
                has_vision=is_vision,
                full_unlock_enabled=full_unlock_active
            )

        # Log system prompt stats for debugging
        prompt_lines = system_prompt.count('\n')
        prompt_chars = len(system_prompt)
        logger.info(f"[SystemPrompt] {prompt_chars} chars, {prompt_lines} lines, tools={supports_tools}, vision={is_vision}, full_unlock={full_unlock_active}")
        if profile_context:
            populated_sections = [k for k, v in profile_context.items() if v and isinstance(v, dict) and any(v.values())]
            logger.debug(f"[Profile] Populated sections: {populated_sections}")

        # Build messages - use Lexi service for uncensored mode, Claude otherwise
        if use_lexi:
            # Lexi has its own persona-based prompt building
            messages = lexi_service.build_messages(
                user_message=user_message,
                history=history,
                user_profile=profile_context,
                custom_persona=settings.persona,  # Allow custom persona override
                images=chat_request.images if chat_request.images else None
            )
        else:
            # Check if using OpenClaw (it injects SOUL.md, IDENTITY.md, etc.)
            effective_prompt = system_prompt
            if model_for_conv.startswith('openclaw:'):
                effective_prompt = OPENCLAW_MIN_PROMPT
                logger.debug("Using minimal prompt for OpenClaw model")
            
            # Claude with system prompt (minimal for OpenClaw)
            messages = claude_service.build_messages_with_system(
                system_prompt=effective_prompt,
                user_message=user_message,
                history=history,
                images=chat_request.images if is_vision else None,
                is_vision_model=is_vision,
                supports_tools=supports_tools
            )

        # Add user message to conversation
        user_msg = await conversation_store.add_message(
            conv_id,
            role="user",
            content=chat_request.message,
            images=chat_request.images if chat_request.images and is_vision else None
        )

        if user_msg:
            yield {
                "event": "message",
                "data": json.dumps({
                    "id": user_msg.id,
                    "role": "user"
                })
            }

        # Prepare Ollama options
        options = {
            "temperature": settings.temperature,
            "top_p": settings.top_p,
            "top_k": settings.top_k,
            "num_ctx": settings.num_ctx,
            "repeat_penalty": settings.repeat_penalty
        }

        collected_content = ""
        collected_thinking = ""  # Track thinking content for storage
        tool_calls = []

        # For mobile resilience: create the assistant message up-front in OpenClaw fast path,
        # then periodically persist partial content while streaming.
        assistant_msg_id: Optional[str] = None
        last_persist_ts = time.monotonic()
        last_persist_len = 0
        if use_openclaw_fast:
            try:
                assistant_msg = await conversation_store.add_message(
                    conv_id,
                    role="assistant",
                    content="",
                    thinking_content=None,
                    memories_used=None,
                    tools_available=None,
                )
                if assistant_msg:
                    assistant_msg_id = assistant_msg.id
                    yield {
                        "event": "message",
                        "data": json.dumps({"id": assistant_msg_id, "role": "assistant"})
                    }
            except Exception as e:
                logger.warning(f"[OpenClawFast] Failed to create placeholder assistant message: {e}")

        # Store context metadata for the response
        context_metadata = {
            "memories_used": memory_context if memory_context else None,
            "tools_available": [t.get("function", {}).get("name") for t in tools] if tools else None
        }
        # Log context summary with memory hint
        memory_count = len(memory_context) if memory_context else 0
        tool_count = len(context_metadata['tools_available']) if context_metadata['tools_available'] else 0
        logger.info(f"[Context] Prepared metadata: memories={memory_count}, tools={tool_count}")
        if memory_count == 0 and supports_tools:
            logger.debug("[Memory] No memories retrieved - model can use add_memory tool to store important user info")

        # Send debug context to frontend
        # Option A cleanup: keep this lightweight in OpenClaw fast path.
        from app.config import OLLAMA_CHAT_MODEL
        if use_openclaw_fast:
            debug_context = {
                "system_prompt_length": len(system_prompt),
                "system_prompt_preview": "(OpenClaw fast path — BrinChat memory/tools disabled)",
                "history_count": len(history),
                "memory_count": 0,
                "tool_count": 0,
                "tools": [],
                "memories": [],
                "model": settings.model,
                "is_vision": is_vision,
                "supports_tools": False,
                "think_mode": chat_request.think or False,
                "uncensored_mode": False
            }
        else:
            debug_context = {
                "system_prompt_length": len(system_prompt) if not use_lexi else len(messages[0].get("content", "")),
                "system_prompt_preview": (system_prompt[:500] + "..." if len(system_prompt) > 500 else system_prompt) if not use_lexi else "Lexi persona (uncensored mode)",
                "history_count": len(history),
                "memory_count": memory_count,
                "tool_count": tool_count,
                "tools": context_metadata['tools_available'][:10] if context_metadata['tools_available'] else [],
                "memories": [m.get("content", "")[:100] for m in memory_context[:5]] if memory_context else [],
                "model": OLLAMA_CHAT_MODEL if use_lexi else model_for_conv,
                "is_vision": is_vision,
                "supports_tools": supports_tools,
                "think_mode": chat_request.think or False,
                "uncensored_mode": use_lexi
            }

        yield {
            "event": "context",
            "data": json.dumps(debug_context)
        }

        # Thinking stream state (frontend renders this as an expandable section).
        # We also use it for keepalive/progress tokens so the UI doesn't look frozen on mobile.
        is_thinking = False
        thinking_token_count = 0
        artificial_thinking_started = False

        # Track active streams for cleanup on disconnect
        active_stream = None

        try:
            # Emit an immediate keepalive/progress token so the user sees activity even if
            # we spend time on memory lookup / compaction before the model starts streaming.
            is_thinking = True
            artificial_thinking_started = True
            yield {
                "event": "token",
                "data": json.dumps({"thinking": "…"})
            }

            # Apply context window management with compaction
            async def send_status(event):
                yield event

            messages = await build_context_with_compaction(
                messages, conv_id, settings, user_id=user.id
            )

            logger.debug(f"Starting stream with think={chat_request.think}, use_lexi={use_lexi}")

            # Stream from appropriate service - Adult Orchestrator for uncensored, Claude otherwise
            if use_lexi:
                # Adult Mode: Use orchestrator for trigger scanning, tool execution, and Lexi
                orchestrator = AdultOrchestrator()
                async for sse_chunk in orchestrator.process_message(
                    message=user_message,
                    user_id=user.id,
                    conversation_id=conv_id,
                    conversation_context=history,
                    user_profile=profile_context,
                    images=chat_request.images if chat_request.images else None,
                    options=options
                ):
                    # Orchestrator yields SSE-formatted chunks, pass through directly
                    yield sse_chunk
                    
                    # Extract content for storage
                    if sse_chunk.get("event") == "token":
                        try:
                            data = json.loads(sse_chunk.get("data", "{}"))
                            if data.get("content"):
                                collected_content += data["content"]
                        except json.JSONDecodeError:
                            pass
                
                # Store assistant message after orchestrator completes
                if collected_content:
                    assistant_msg = await conversation_store.add_message(
                        conv_id,
                        role="assistant",
                        content=collected_content,
                        thinking_content=None
                    )
                    if assistant_msg:
                        yield {
                            "event": "message",
                            "data": json.dumps({
                                "id": assistant_msg.id,
                                "role": "assistant"
                            })
                        }
                
                # Skip the normal streaming loop for adult mode
                yield {"event": "done", "data": json.dumps({"status": "complete"})}
                return
            else:
                active_stream = claude_service.chat_stream(
                    messages=messages,
                    model=model_for_conv,
                    tools=tools,
                    options=options,
                    think=chat_request.think,
                    user_id=user.id,
                    username=user.username,
                    conversation_id=conv_id,
                )
            # Wrap the upstream stream to avoid long silent periods on mobile (background tab throttling)
            # and to provide a lightweight "thinking" stream even when providers don't emit native thinking tokens.
            # NOTE: We intentionally do NOT stream chain-of-thought here; these are progress/keepalive tokens.
            stream_iter = active_stream.__aiter__()
            artificial_thinking_active = True
            last_thinking_emit = 0.0

            # IMPORTANT: do not use asyncio.wait_for(stream_iter.__anext__(), timeout=...)
            # because the timeout cancels __anext__(), which can abort the underlying HTTP stream.
            pending = asyncio.create_task(stream_iter.__anext__())

            while True:
                # User pressed Stop
                if is_cancelled(conv_id):
                    logger.info(f"[Cancel] Stream loop detected cancellation for {conv_id[:8]}...")
                    # Persist whatever we have so far (including an explicit cancellation marker)
                    try:
                        cancel_note = "\n\n*[Generation cancelled by user]*"
                        if assistant_msg_id:
                            await conversation_store.update_message(
                                conv_id,
                                assistant_msg_id,
                                (collected_content if collected_content else "") + cancel_note
                            )
                    except Exception:
                        pass

                    # Tell frontend to stop streaming + show cancellation
                    yield {
                        "event": "token",
                        "data": json.dumps({"thinking_done": True, "cancelled": True, "message": "Generation cancelled by user"})
                    }
                    return

                done, _ = await asyncio.wait({pending}, timeout=1.0)

                # No upstream chunk yet → emit keepalive/progress token
                if not done:
                    if artificial_thinking_active:
                        now = time.monotonic()
                        if now - last_thinking_emit >= 1.0:
                            last_thinking_emit = now
                            # Mark thinking active so the frontend will collapse it when we later emit thinking_done
                            is_thinking = True
                            yield {
                                "event": "token",
                                "data": json.dumps({"thinking": "…"})
                            }
                    continue

                # Upstream produced a chunk (or ended)
                try:
                    chunk = pending.result()
                except StopAsyncIteration:
                    break

                # Start waiting for the next chunk before processing this one (keeps the pipeline full)
                pending = asyncio.create_task(stream_iter.__anext__())

                # Debug: log chunks that have thinking content
                if chunk.get("message", {}).get("thinking"):
                    logger.debug(f"Received thinking token: {len(chunk['message']['thinking'])} chars")

                if "message" in chunk:
                    msg = chunk["message"]

                    # Any real provider activity means we can stop artificial thinking keepalives
                    if msg.get("thinking") or msg.get("content") or msg.get("tool_calls"):
                        artificial_thinking_active = False

                    # Stream thinking tokens if present
                    if msg.get("thinking"):
                        is_thinking = True
                        thinking_token_count += 1
                        collected_thinking += msg["thinking"]  # Collect for storage
                        yield {
                            "event": "token",
                            "data": json.dumps({"thinking": msg["thinking"]})
                        }
                        # Soft limit: warn but continue (model may need extended thinking)
                        if thinking_token_count == THINKING_TOKEN_LIMIT_INITIAL:
                            logger.warning(
                                f"Soft thinking limit reached ({thinking_token_count} tokens) - continuing to allow model to complete"
                            )
                        # Hard limit: true runaway detection - break only here
                        if thinking_token_count > THINKING_HARD_LIMIT_INITIAL:
                            logger.error(
                                f"Hard thinking limit reached ({thinking_token_count} tokens) - breaking stream"
                            )
                            break

                    # Stream content tokens
                    if msg.get("content"):
                        # If we were thinking (or we started a keepalive thinking stream), signal thinking is done
                        if is_thinking or thinking_stream_active:
                            is_thinking = False
                            thinking_stream_active = False
                            logger.debug(f"[Stream] Emitting thinking_done, collected_content so far: {len(collected_content)} chars")
                            yield {
                                "event": "token",
                                "data": json.dumps({"thinking_done": True})
                            }
                        collected_content += msg["content"]
                        # DEBUG: Log every content token being streamed
                        logger.debug(f"[Stream] Content token: {len(msg['content'])} chars, total: {len(collected_content)} chars")
                        yield {
                            "event": "token",
                            "data": json.dumps({"content": msg["content"]})
                        }

                        # Persist partial content frequently for mobile resilience (app-switch recovery)
                        # Every 1 second OR every 100 chars, whichever comes first
                        if assistant_msg_id:
                            now = time.monotonic()
                            if (now - last_persist_ts) >= 1.0 or (len(collected_content) - last_persist_len) >= 100:
                                try:
                                    await conversation_store.update_message(conv_id, assistant_msg_id, collected_content)
                                    last_persist_ts = now
                                    last_persist_len = len(collected_content)
                                except Exception as e:
                                    logger.debug(f"[OpenClawFast] Partial persist failed (non-fatal): {e}")

                    # Collect tool calls
                    if msg.get("tool_calls"):
                        tool_calls = msg["tool_calls"]

                if chunk.get("done"):
                    # Signal thinking done if we were still thinking / keepalive thinking is active
                    if is_thinking or thinking_stream_active:
                        thinking_stream_active = False
                        yield {
                            "event": "token",
                            "data": json.dumps({"thinking_done": True})
                        }
                    break

            # Cleanup: ensure our pending __anext__ task is always consumed/cancelled.
            # Without this, the final pending task can finish with StopAsyncIteration and log:
            # "Task exception was never retrieved".
            try:
                if pending and not pending.done():
                    pending.cancel()
                    try:
                        await pending
                    except asyncio.CancelledError:
                        pass
                    except StopAsyncIteration:
                        pass
                    except Exception:
                        pass
                elif pending and pending.done():
                    try:
                        pending.result()
                    except StopAsyncIteration:
                        pass
                    except Exception:
                        pass
            except Exception:
                pass

            # Safety: If we had thinking but no content and no tool calls, send a fallback
            if collected_thinking and not collected_content and not tool_calls:
                logger.warning("Model produced thinking but no content - sending fallback response")
                fallback_msg = "I apologize, but I wasn't able to formulate a response. Could you please rephrase your question?"
                collected_content = fallback_msg
                yield {
                    "event": "token",
                    "data": json.dumps({"content": fallback_msg})
                }

            # If no native tool_calls, try parsing text-based function calls
            if not tool_calls and collected_content:
                parsed_calls = parse_text_function_calls(collected_content)
                if parsed_calls:
                    logger.info(f"Parsed {len(parsed_calls)} text-based function call(s)")
                    tool_calls = parsed_calls

            # Handle tool calls if any
            if tool_calls:
                # Store results to avoid executing tools twice
                tool_results = []

                for tc in tool_calls:
                    func = tc.get("function", {})
                    tool_name = func.get("name")

                    # Check for cancellation before executing tool
                    if is_cancelled(conv_id):
                        logger.info(f"[Cancel] Tool execution cancelled before {tool_name}")
                        yield {
                            "event": "cancelled",
                            "data": json.dumps({"message": "Generation cancelled by user"})
                        }
                        return

                    yield {
                        "event": "tool_call",
                        "data": json.dumps({
                            "name": tool_name,
                            "arguments": func.get("arguments")
                        })
                    }

                    # Execute the tool with explicit context
                    result = await tool_executor.execute(tc, user_id=user.id, conversation_id=conv_id)

                    # Check for cancellation after tool execution
                    if is_cancelled(conv_id):
                        logger.info(f"[Cancel] Generation cancelled after {tool_name}")
                        yield {
                            "event": "cancelled",
                            "data": json.dumps({"message": "Generation cancelled by user"})
                        }
                        return

                    tool_results.append(result)

                    yield {
                        "event": "tool_result",
                        "data": json.dumps({
                            "name": func.get("name"),
                            "result": result
                        })
                    }

                # Add assistant message with tool calls to conversation
                logger.info(f"[Context] Saving assistant message with thinking={len(collected_thinking) if collected_thinking else 0} chars")
                assistant_msg = await conversation_store.add_message(
                    conv_id,
                    role="assistant",
                    content=collected_content,
                    tool_calls=tool_calls,
                    thinking_content=collected_thinking if collected_thinking else None,
                    memories_used=context_metadata.get("memories_used"),
                    tools_available=context_metadata.get("tools_available")
                )

                # Build context with full history plus current tool results
                messages_with_tool = messages.copy()

                # Add assistant's tool call
                messages_with_tool.append({
                    "role": "assistant",
                    "content": collected_content,
                    "tool_calls": tool_calls
                })

                # Add tool results with clear marker for the model
                # IMPORTANT: Sanitize results to prevent context bloat from base64 data
                for tc, result in zip(tool_calls, tool_results):
                    func_name = tc.get("function", {}).get("name", "unknown")
                    # Sanitize result - strips base64, truncates large text
                    sanitized_result = sanitize_tool_result_for_context(result, func_name)
                    # Wrap result with clear context marker
                    tool_content = {
                        "_current_request": True,
                        "tool": func_name,
                        "result": sanitized_result
                    }
                    messages_with_tool.append({
                        "role": "tool",
                        "tool_name": func_name,
                        "content": json.dumps(tool_content)
                    })

                # Add instruction to respond to current results
                messages_with_tool.append({
                    "role": "user",
                    "content": "Based on the tool results above, please answer my question."
                })

                # Log total context size before truncation (helps debug bloat issues)
                total_context_chars = sum(len(json.dumps(m.get("content", ""))) for m in messages_with_tool)
                total_context_tokens_est = total_context_chars // 4
                logger.info(f"[Context] Tool follow-up context: {total_context_chars} chars (~{total_context_tokens_est} tokens) before truncation")
                if total_context_tokens_est > 50000:
                    logger.warning(f"[Context] Large context detected! {total_context_tokens_est} tokens - check for unsanitized data")

                # Apply context window management for follow-up (use simple truncation for tool responses)
                messages_with_tool = truncate_messages_for_context(messages_with_tool, settings.num_ctx)

                # Get follow-up response (disable thinking mode to prevent infinite loops)
                followup_content = ""
                logger.debug(f"Starting follow-up stream with {len(messages_with_tool)} messages")
                thinking_count = 0
                # Track follow-up stream for cleanup
                followup_stream = claude_service.chat_stream(
                    messages=messages_with_tool,
                    model=model_for_conv,
                    options=options,
                    think=False,
                    user_id=user.id,
                    username=user.username,
                    conversation_id=conv_id,
                )
                try:
                    async for chunk in followup_stream:
                        msg = chunk.get("message", {})

                        # Track thinking tokens to detect runaway loops
                        if msg.get("thinking"):
                            thinking_count += 1
                            # Soft limit: warn but continue
                            if thinking_count == THINKING_TOKEN_LIMIT_FOLLOWUP:
                                logger.warning(f"Soft thinking limit reached ({thinking_count} tokens) in followup - continuing")
                            # Hard limit: true runaway detection
                            if thinking_count > THINKING_HARD_LIMIT_FOLLOWUP:
                                logger.error(f"Hard thinking limit reached ({thinking_count} tokens) in followup - breaking")
                                break
                            continue  # Skip thinking tokens

                        if msg.get("content"):
                            content = msg["content"]
                            followup_content += content
                            yield {
                                "event": "token",
                                "data": json.dumps({"content": content})
                            }
                        if chunk.get("done"):
                            logger.debug(f"Follow-up done, content: {len(followup_content)} chars, thinking tokens: {thinking_count}")
                            break
                finally:
                    # Ensure follow-up stream is closed
                    try:
                        await followup_stream.aclose()
                    except Exception:
                        pass

                # Safety: If no content after tool call, send a fallback
                if not followup_content:
                    logger.warning("No content in follow-up response after tool call - sending fallback")
                    followup_content = "I retrieved the information, but couldn't formulate a response. Please try rephrasing your question."
                    yield {
                        "event": "token",
                        "data": json.dumps({"content": followup_content})
                    }

                # Add follow-up to conversation
                if followup_content:
                    followup_msg = await conversation_store.add_message(
                        conv_id,
                        role="assistant",
                        content=followup_content,
                        memories_used=context_metadata.get("memories_used"),
                        tools_available=context_metadata.get("tools_available")
                    )
                    if followup_msg:
                        yield {
                            "event": "message",
                            "data": json.dumps({
                                "id": followup_msg.id,
                                "role": "assistant",
                                "metadata": {
                                    "thinking_content": None,  # Thinking disabled for followups
                                    "memories_used": context_metadata.get("memories_used"),
                                    "tools_available": context_metadata.get("tools_available")
                                }
                            })
                        }

                    # Queue async extraction for followup response (fire-and-forget)
                    try:
                        from app.services.async_extractor import queue_extraction
                        await queue_extraction(
                            user_id=user.id,
                            user_message=chat_request.message,
                            assistant_response=followup_content,
                            conversation_id=conv_id
                        )
                        logger.debug("[AsyncExtract] Queued background extraction for followup")
                    except Exception as e:
                        logger.warning(f"Failed to queue followup extraction: {e}")
                    
                    # Task extraction for non-primary users (creates Deck cards)
                    is_primary = (
                        (OPENCLAW_PRIMARY_USER_ID and user.id == OPENCLAW_PRIMARY_USER_ID) or
                        (OPENCLAW_PRIMARY_USERNAME and user.username.lower() == OPENCLAW_PRIMARY_USERNAME)
                    )
                    if not is_primary and followup_content:
                        try:
                            task_service = get_task_extraction_service()
                            card = await task_service.process_conversation(
                                user_id=user.id,
                                username=user.username,
                                user_message=chat_request.message,
                                assistant_response=followup_content,
                                is_primary_user=False
                            )
                            if card:
                                logger.info(f"[TaskExtract] Created Deck card #{card.get('id')} from {user.username}")
                        except Exception as e:
                            logger.warning(f"Task extraction failed: {e}")

            else:
                # No tool calls - add regular assistant message
                if collected_content:
                    # Strip [MEMORY] and [REMEMBER] tags from response
                    # Tags are extracted for context display but removed from visible response
                    cleaned_content, memories_extracted = strip_memory_tags(collected_content)
                    
                    # If tags were stripped, send content replacement to frontend
                    if memories_extracted and cleaned_content != collected_content:
                        yield {
                            "event": "content_replace",
                            "data": json.dumps({"replace_content": cleaned_content})
                        }
                    
                    # Save extracted memories to BrinChat's memory store
                    if memories_extracted:
                        memory_service = get_memory_service()
                        for mem in memories_extracted:
                            try:
                                await memory_service.add_memory(
                                    user_id=user.id,
                                    content=mem.get("content", ""),
                                    category=mem.get("category", "general"),
                                    importance=mem.get("importance", 5),
                                    source="extracted"
                                )
                            except Exception as e:
                                logger.warning(f"Failed to save extracted memory: {e}")
                    
                    logger.info(f"[Context] Saving assistant message with thinking={len(collected_thinking) if collected_thinking else 0} chars, memories={len(context_metadata.get('memories_used') or [])} items, extracted={len(memories_extracted)}")

                    # If we already created a placeholder assistant message (OpenClaw fast path), update it.
                    if assistant_msg_id:
                        assistant_msg = await conversation_store.update_message(
                            conv_id,
                            assistant_msg_id,
                            cleaned_content,  # Save cleaned content (tags stripped)
                        )
                    else:
                        assistant_msg = await conversation_store.add_message(
                            conv_id,
                            role="assistant",
                            content=cleaned_content,  # Save cleaned content (tags stripped)
                            thinking_content=collected_thinking if collected_thinking else None,
                            memories_used=context_metadata.get("memories_used"),
                            tools_available=context_metadata.get("tools_available")
                        )

                    if assistant_msg:
                        yield {
                            "event": "message",
                            "data": json.dumps({
                                "id": assistant_msg.id,
                                "role": "assistant",
                                "metadata": {
                                    "thinking_content": collected_thinking if collected_thinking else None,
                                    "memories_used": context_metadata.get("memories_used"),
                                    "tools_available": context_metadata.get("tools_available"),
                                    "memories_extracted": memories_extracted if memories_extracted else None
                                }
                            })
                        }

                    # Queue async extraction for memory/profile updates (fire-and-forget)
                    # Uses small model (qwen2.5-coder:3b) in background - doesn't block response
                    # Note: Use cleaned_content since we already extracted explicit [MEMORY] tags above
                    if cleaned_content:
                        try:
                            from app.services.async_extractor import queue_extraction
                            await queue_extraction(
                                user_id=user.id,
                                user_message=chat_request.message,
                                assistant_response=cleaned_content,
                                conversation_id=conv_id
                            )
                            logger.debug("[AsyncExtract] Queued background extraction")
                        except Exception as e:
                            logger.warning(f"Failed to queue extraction: {e}")
                        
                        # Task extraction for non-primary users (creates Deck cards)
                        is_primary = (
                            (OPENCLAW_PRIMARY_USER_ID and user.id == OPENCLAW_PRIMARY_USER_ID) or
                            (OPENCLAW_PRIMARY_USERNAME and user.username.lower() == OPENCLAW_PRIMARY_USERNAME)
                        )
                        if not is_primary:
                            try:
                                task_service = get_task_extraction_service()
                                card = await task_service.process_conversation(
                                    user_id=user.id,
                                    username=user.username,
                                    user_message=chat_request.message,
                                    assistant_response=cleaned_content,
                                    is_primary_user=False
                                )
                                if card:
                                    logger.info(f"[TaskExtract] Created Deck card #{card.get('id')} from {user.username}")
                            except Exception as e:
                                logger.warning(f"Task extraction failed: {e}")

            # === Trigger Evaluation if Needed ===
            try:
                evaluator = get_evaluator_service()
                evaluator.increment_interaction(user.id)
                if await evaluator.should_evaluate(user.id):
                    eval_result = await evaluator.evaluate(user.id)
                    logger.debug(f"Evaluation result: {eval_result.get('session_polarity', 'unknown')}")
            except Exception as e:
                logger.warning(f"Evaluation failed: {e}")

            yield {
                "event": "done",
                "data": json.dumps({"finish_reason": "stop"})
            }

        except (BrokenPipeError, ConnectionError, ConnectionResetError):
            # Client disconnected - exit gracefully without trying to yield
            logger.debug("Client disconnected during SSE stream")
            return
        except asyncio.CancelledError:
            # Request was cancelled - exit gracefully
            logger.debug("SSE stream cancelled")
            return
        except Exception as e:
            logger.error(f"Stream error: {e}")
            try:
                yield {
                    "event": "error",
                    "data": json.dumps({"message": str(e)})
                }
            except (BrokenPipeError, ConnectionError, ConnectionResetError):
                # Even the error yield failed - client is gone
                pass
        finally:
            # Clean up any active Ollama streams
            if active_stream is not None:
                try:
                    await active_stream.aclose()
                except Exception:
                    pass  # Stream may already be closed
            # Clean up context-scoped image registry
            tool_ctx.clear_images()
            # Clear cancellation tracking
            clear_cancellation(conv_id)

    # ping=15 sends SSE comment every 15s to keep mobile connections alive
    return EventSourceResponse(event_generator(), ping=15)


@router.get("/conversations")
async def list_conversations(user: UserResponse = Depends(require_auth)):
    """List conversations for the authenticated user"""
    return {"conversations": conversation_store.list_for_user(user.id)}


@router.post("/conversations")
async def create_conversation(user: UserResponse = Depends(require_auth)):
    """Create a new conversation for the authenticated user"""
    settings = get_settings()
    conv = await conversation_store.create(model=settings.model, user_id=user.id)
    return {"id": conv.id, "title": conv.title}


@router.get("/conversations/search")
async def search_conversations(
    q: str,
    limit: int = 50,
    user: UserResponse = Depends(require_auth)
):
    """Search within message content across all user's conversations.
    
    Args:
        q: Search query (case-insensitive substring match)
        limit: Maximum number of results (default 50, max 100)
    
    Returns:
        List of matching messages with conversation context
    """
    if not q or not q.strip():
        raise HTTPException(status_code=400, detail="Search query required")
    
    limit = min(max(1, limit), 100)  # Clamp to 1-100
    results = conversation_store.search_messages(user.id, q, limit=limit)
    return {"results": results, "query": q, "count": len(results)}


@router.get("/conversations/{conv_id}")
async def get_conversation(conv_id: str, user: UserResponse = Depends(require_auth)):
    """Get a specific conversation with all messages (owned by user)"""
    conv = conversation_store.get(conv_id, user_id=user.id)
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return conv.to_dict()


@router.delete("/conversations/{conv_id}")
async def delete_conversation(conv_id: str, user: UserResponse = Depends(require_auth)):
    """Delete a conversation (must be owned by user)"""
    # Verify ownership first
    conv = conversation_store.get(conv_id, user_id=user.id)
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")
    if await conversation_store.delete(conv_id):
        return {"status": "deleted"}
    raise HTTPException(status_code=404, detail="Conversation not found")


@router.patch("/conversations/{conv_id}")
async def rename_conversation(
    conv_id: str,
    request: RenameConversationRequest,
    user: UserResponse = Depends(require_auth)
):
    """Rename a conversation (must be owned by user)"""
    # Verify ownership first
    conv = conversation_store.get(conv_id, user_id=user.id)
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")
    if await conversation_store.rename(conv_id, request.title):
        return {"status": "renamed", "title": request.title}
    raise HTTPException(status_code=404, detail="Conversation not found")


@router.delete("/conversations/{conv_id}/messages")
async def clear_conversation(conv_id: str, user: UserResponse = Depends(require_auth)):
    """Clear all messages from a conversation (must be owned by user)"""
    # Verify ownership first
    conv = conversation_store.get(conv_id, user_id=user.id)
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")
    if await conversation_store.clear_messages(conv_id):
        return {"status": "cleared"}
    raise HTTPException(status_code=404, detail="Conversation not found")


@router.patch("/conversations/{conv_id}/messages/{msg_id}")
async def edit_message(
    conv_id: str,
    msg_id: str,
    request: EditMessageRequest,
    user: UserResponse = Depends(require_auth)
):
    """Edit a message (in-place, for simple edits)"""
    # Verify ownership first
    conv = conversation_store.get(conv_id, user_id=user.id)
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")
    msg = await conversation_store.update_message(conv_id, msg_id, request.content)
    if msg:
        return {"status": "updated", "id": msg.id}
    raise HTTPException(status_code=404, detail="Message not found")


@router.post("/conversations/{conv_id}/messages/{msg_id}/fork")
async def fork_conversation(
    conv_id: str,
    msg_id: str,
    request: ForkMessageRequest,
    user: UserResponse = Depends(require_auth)
):
    """Fork conversation at a message with new content"""
    # Verify ownership first
    conv = conversation_store.get(conv_id, user_id=user.id)
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")
    new_conv = await conversation_store.fork_at_message(conv_id, msg_id, request.content)
    if new_conv:
        return {
            "status": "forked",
            "id": new_conv.id,
            "title": new_conv.title
        }
    raise HTTPException(status_code=404, detail="Conversation or message not found")


@router.post("/conversations/{conv_id}/regenerate/{msg_id}")
async def regenerate_response(
    conv_id: str,
    msg_id: str,
    request: Request,
    user: UserResponse = Depends(require_auth)
):
    """Regenerate an assistant response by removing it and generating a new one"""
    # Create request-scoped context for tool execution (thread-safe)
    tool_ctx = create_context(user_id=user.id, conversation_id=conv_id)

    conv = conversation_store.get(conv_id, user_id=user.id)
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")

    # Find the message index
    msg_index = None
    for i, msg in enumerate(conv.messages):
        if msg.id == msg_id:
            msg_index = i
            break

    if msg_index is None:
        raise HTTPException(status_code=404, detail="Message not found")

    # The message should be an assistant message
    if conv.messages[msg_index].role != "assistant":
        raise HTTPException(status_code=400, detail="Can only regenerate assistant messages")

    # Find the preceding user message
    user_msg_index = None
    for i in range(msg_index - 1, -1, -1):
        if conv.messages[i].role == "user":
            user_msg_index = i
            break

    if user_msg_index is None:
        raise HTTPException(status_code=400, detail="No preceding user message found")

    user_message = conv.messages[user_msg_index].content
    user_images = conv.messages[user_msg_index].images

    # Remove messages from the assistant message onward
    await conversation_store.truncate_messages(conv_id, msg_index)

    async def event_generator():
        # Set up cancellation tracking for this generation
        cancel_event = get_cancellation_event(conv_id)
        cancel_event.clear()  # Reset in case of previous cancelled request

        settings = get_settings()

        # Use conversation-scoped model if set; fall back to global default
        # (Fixes regenerate path where model_for_conv was previously undefined)
        model_for_conv = conv.model or settings.model

        # === Check Adult Mode to Route to Lexi ===
        profile_service = get_user_profile_service()
        session_id = request.headers.get("X-Session-ID")
        adult_status = await profile_service.get_adult_mode_status(user.id)
        session_unlock_status = await profile_service.get_session_unlock_status(user.id, session_id) if session_id else {"enabled": False}
        
        # Use Lexi when BOTH adult_mode AND session unlock are enabled
        use_lexi = adult_status.get("enabled") and session_unlock_status.get("enabled")
        
        if use_lexi:
            # Lexi mode: simpler capabilities, pure conversation
            is_vision = False
            supports_tools = False
            logger.info(f"Regenerate: LEXI MODE (uncensored)")
        else:
            # Claude supports vision and tools natively
            is_vision = True
            supports_tools = True

        # Get tools for Claude (or none for Lexi)
        tools = get_tools_for_model(supports_tools=supports_tools, supports_vision=is_vision) if supports_tools else None

        # Filter tools based on user's feature flags
        if tools:
            feature_service = get_feature_service()
            tools = feature_service.filter_tools_for_user(tools, user.id)
            logger.debug(f"Regenerate: Filtered tools for user {user.id}: {len(tools)} available")

        # Get updated history (without the removed messages, with user verification)
        history = conversation_store.get_messages_for_api(conv_id, user_id=user.id)

        # === Load User Profile ===
        profile_context = None
        full_unlock_active = use_lexi
        try:
            # Base sections always loaded
            sections_to_load = ["identity", "communication", "persona_preferences", "pet_peeves",
                                "boundaries", "relationship_metrics", "interaction"]

            # Include adult sections when Lexi mode is active
            if use_lexi:
                sections_to_load.extend(["sexual_romantic", "dark_content", "private_self", "substances_health"])
                logger.debug("Regenerate: Lexi mode - including sensitive sections")

            profile_sections = await profile_service.read_sections(
                user.id,
                sections_to_load,
                include_disabled=False
            )
            if profile_sections:
                profile_context = profile_sections
        except Exception as e:
            logger.warning(f"Regenerate profile loading failed: {e}")

        # === Two-Phase Memory Retrieval ===
        memory_service = get_memory_service()
        prompt_builder = get_prompt_builder()
        memory_context = []
        user_name = None

        # Phase 1: Extract search terms (skip for very short messages)
        if len(user_message) > 10:
            try:
                search_terms = await extract_memory_search_terms(
                    user_message,
                    settings.model
                )
                logger.info(f"Regenerate memory search terms: {search_terms}")

                # Query memories with extracted terms
                if search_terms:
                    query = " ".join(search_terms)
                    memory_context = await memory_service.query_memories(
                        user_id=user.id,
                        query=query,
                        top_k=5
                    )
                    logger.info(f"Regenerate retrieved {len(memory_context)} memories")

                    # Check for user's name in memories
                    for mem in memory_context:
                        if mem.get("category") == "personal" and "name" in mem.get("content", "").lower():
                            content = mem.get("content", "")
                            if "name is" in content.lower():
                                # Extract and validate name
                                extracted = content.split("name is")[-1].strip().split()[0]
                                # Validate: names should be simple alphanumeric, no special chars
                                # that could be used for injection
                                if extracted and len(extracted) <= 50 and re.match(r'^[\w\-]+$', extracted):
                                    user_name = extracted
            except Exception as e:
                logger.warning(f"Regenerate memory retrieval failed: {e}")
                # Continue without memory context

        # Phase 2: Build enhanced system prompt with profile
        system_prompt = prompt_builder.build_prompt(
            persona=settings.persona,
            memory_context=memory_context,
            profile_context=profile_context,
            user_name=user_name,
            has_tools=supports_tools,
            has_vision=is_vision,
            full_unlock_enabled=full_unlock_active
        )

        # Build messages - use Lexi service for uncensored mode, Claude otherwise
        if use_lexi:
            messages = lexi_service.build_messages(
                user_message=user_message,
                history=history[:-1] if history else [],
                user_profile=profile_context,
                custom_persona=settings.persona,
                images=user_images if user_images else None
            )
        else:
            messages = claude_service.build_messages_with_system(
                system_prompt=system_prompt,
                user_message=user_message,
                history=history[:-1] if history else [],  # Exclude the last user message as we'll add it fresh
                images=user_images if is_vision else None,
                is_vision_model=is_vision,
                supports_tools=supports_tools
            )

        # Prepare options
        options = {
            "temperature": settings.temperature,
            "top_p": settings.top_p,
            "top_k": settings.top_k,
            "num_ctx": settings.num_ctx,
            "repeat_penalty": settings.repeat_penalty
        }

        collected_content = ""
        tool_calls = []
        regen_stream = None

        # Build context metadata for debugging
        regen_context_metadata = {
            "memories_used": memory_context if memory_context else None,
            "tools_available": [t["function"]["name"] for t in tools] if tools else None
        }

        try:
            # Apply context window management with compaction (with user verification)
            messages = await build_context_with_compaction(
                messages, conv_id, settings, user_id=user.id
            )

            # Store stream for cleanup - use Lexi for uncensored, Claude otherwise
            if use_lexi:
                from app.config import OLLAMA_CHAT_MODEL
                regen_stream = lexi_service.chat_stream(
                    messages=messages,
                    model=OLLAMA_CHAT_MODEL,
                    tools=None,
                    options=options
                )
            else:
                regen_stream = claude_service.chat_stream(
                    messages=messages,
                    model=model_for_conv,
                    tools=tools,
                    options=options,
                    user_id=user.id,
                    username=user.username,
                    conversation_id=conv_id,
                )
            async for chunk in regen_stream:
                if "message" in chunk:
                    msg = chunk["message"]
                    if msg.get("content"):
                        collected_content += msg["content"]
                        yield {
                            "event": "token",
                            "data": json.dumps({"content": msg["content"]})
                        }
                    if msg.get("tool_calls"):
                        tool_calls = msg["tool_calls"]
                if chunk.get("done"):
                    break

            # If no native tool_calls, try parsing text-based function calls
            if not tool_calls and collected_content:
                parsed_calls = parse_text_function_calls(collected_content)
                if parsed_calls:
                    logger.info(f"Regenerate: Parsed {len(parsed_calls)} text-based function call(s)")
                    tool_calls = parsed_calls

            # Handle tool calls if any (simplified version)
            if tool_calls:
                for tc in tool_calls:
                    func = tc.get("function", {})
                    tool_name = func.get("name")

                    # Check for cancellation before executing tool
                    if is_cancelled(conv_id):
                        logger.info(f"[Cancel] Tool execution cancelled before {tool_name}")
                        yield {
                            "event": "cancelled",
                            "data": json.dumps({"message": "Generation cancelled by user"})
                        }
                        return

                    yield {
                        "event": "tool_call",
                        "data": json.dumps({
                            "name": tool_name,
                            "arguments": func.get("arguments")
                        })
                    }
                    result = await tool_executor.execute(tc, user_id=user.id, conversation_id=conv_id)

                    # Check for cancellation after tool execution
                    if is_cancelled(conv_id):
                        logger.info(f"[Cancel] Generation cancelled after {tool_name}")
                        yield {
                            "event": "cancelled",
                            "data": json.dumps({"message": "Generation cancelled by user"})
                        }
                        return

                    yield {
                        "event": "tool_result",
                        "data": json.dumps({
                            "name": tool_name,
                            "result": result
                        })
                    }

            # Save the new assistant message
            if collected_content:
                assistant_msg = await conversation_store.add_message(
                    conv_id,
                    role="assistant",
                    content=collected_content,
                    tool_calls=tool_calls if tool_calls else None,
                    memories_used=regen_context_metadata.get("memories_used"),
                    tools_available=regen_context_metadata.get("tools_available")
                )
                if assistant_msg:
                    yield {
                        "event": "message",
                        "data": json.dumps({
                            "id": assistant_msg.id,
                            "role": "assistant",
                            "metadata": {
                                "thinking_content": None,  # Regenerate doesn't use thinking
                                "memories_used": regen_context_metadata.get("memories_used"),
                                "tools_available": regen_context_metadata.get("tools_available")
                            }
                        })
                    }

            yield {
                "event": "done",
                "data": json.dumps({"finish_reason": "stop"})
            }

        except (BrokenPipeError, ConnectionError, ConnectionResetError):
            logger.debug("Client disconnected during regenerate stream")
            return
        except asyncio.CancelledError:
            logger.debug("Regenerate stream cancelled")
            return
        except Exception as e:
            logger.error(f"Regenerate stream error: {e}")
            try:
                yield {
                    "event": "error",
                    "data": json.dumps({"message": str(e)})
                }
            except (BrokenPipeError, ConnectionError, ConnectionResetError):
                pass
        finally:
            # Clean up stream if active
            if regen_stream is not None:
                try:
                    await regen_stream.aclose()
                except Exception:
                    pass  # Stream may already be closed
            # Clean up context-scoped resources
            tool_ctx.clear_images()
            # Clear cancellation tracking
            clear_cancellation(conv_id)

    # ping=15 sends SSE comment every 15s to keep mobile connections alive
    return EventSourceResponse(event_generator(), ping=15)


# Legacy endpoints for backward compatibility
@router.get("/history")
async def get_chat_history(request: Request, user: UserResponse = Depends(require_auth)):
    """Get chat history for current session (legacy)"""
    conv_id = request.headers.get("X-Conversation-ID", "default")
    conv = conversation_store.get(conv_id, user_id=user.id)
    if not conv:
        return {"history": []}
    return {"history": conversation_store.get_messages_for_api(conv_id, user_id=user.id)}


@router.delete("/history")
async def clear_chat_history(request: Request, user: UserResponse = Depends(require_auth)):
    """Clear chat history for current session (legacy)"""
    conv_id = request.headers.get("X-Conversation-ID")
    if conv_id:
        # Verify ownership before clearing
        conv = conversation_store.get(conv_id, user_id=user.id)
        if conv:
            await conversation_store.clear_messages(conv_id)
    # Clear images from context if available, otherwise from executor
    from app.services.tool_executor import get_current_context
    ctx = get_current_context()
    if ctx:
        ctx.clear_images()
    else:
        tool_executor.clear_images()
    return {"status": "cleared"}
