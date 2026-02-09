"""Chat router — thin relay to OpenClaw with voice support and conversation persistence."""

from fastapi import APIRouter, Request, HTTPException, Depends
from fastapi.responses import JSONResponse
from sse_starlette.sse import EventSourceResponse
import asyncio
import base64
import json
import logging
import re
import time
from typing import Dict, Any, List, Optional
from pydantic import BaseModel

from app.services.claude_service import claude_service
from app.services.rate_limiter import get_chat_limiter
from app.utils.image_utils import compress_images

logger = logging.getLogger(__name__)
from app.services.conversation_store import conversation_store
from app.services.file_processor import file_processor
from app.config import (
    get_settings,
    THINKING_TOKEN_LIMIT_INITIAL, THINKING_TOKEN_LIMIT_FOLLOWUP,
    THINKING_HARD_LIMIT_INITIAL, THINKING_HARD_LIMIT_FOLLOWUP,
    OPENCLAW_PRIMARY_USER_ID, OPENCLAW_PRIMARY_USERNAME
)
from app.models.schemas import ChatRequest
from app.middleware.auth import require_auth
from app.models.auth_schemas import UserResponse
import os
import uuid
from pathlib import Path

# TTS base URL from environment (strip /v1 suffix for internal use)
_tts_url_raw = os.getenv("OPENAI_TTS_BASE_URL", "http://10.10.10.124:5002/v1")
tts_base_url = _tts_url_raw[:-3] if _tts_url_raw.endswith("/v1") else _tts_url_raw

router = APIRouter(prefix="/api/chat", tags=["chat"])

# Module-level TTS semaphore: serialize TTS requests to avoid overloading the V100
_tts_semaphore = asyncio.Semaphore(1)

# Directory for temporary images (OpenClaw image tool can read these)
TEMP_IMAGE_DIR = Path(__file__).parent.parent.parent / "temp_images"
TEMP_IMAGE_DIR.mkdir(exist_ok=True)


def save_images_for_openclaw(images: List[str]) -> List[str]:
    """Save base64 images to temp files for OpenClaw's image tool.

    Returns list of file paths that can be passed to the image tool.
    """
    saved_paths = []
    for i, img_b64 in enumerate(images):
        try:
            # Strip data URL prefix if present
            if img_b64.startswith("data:"):
                img_b64 = img_b64.split(",", 1)[-1]

            # Decode and save
            img_bytes = base64.b64decode(img_b64)
            filename = f"{uuid.uuid4().hex[:12]}.jpg"
            filepath = TEMP_IMAGE_DIR / filename
            filepath.write_bytes(img_bytes)
            saved_paths.append(str(filepath))
            logger.info(f"[OpenClaw] Saved image {i+1} to {filepath} ({len(img_bytes)} bytes)")
        except Exception as e:
            logger.error(f"[OpenClaw] Failed to save image {i+1}: {e}")

    return saved_paths

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
    """Cancel an active generation for a conversation."""
    result = {"conversation_id": conv_id}

    # Cancel BrinChat-level generation
    event = _active_generations.get(conv_id)
    if event:
        event.set()
        logger.info(f"[Cancel] BrinChat generation cancelled for conversation {conv_id[:8]}...")
        result["brinchat_cancelled"] = True
    else:
        result["brinchat_cancelled"] = False

    # Best-effort persistence: if we created a placeholder assistant message,
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

    result["status"] = "cancelled" if result.get("brinchat_cancelled") else "not_found"
    return result


def estimate_tokens(text: str) -> int:
    """Rough token estimate: ~4 chars per token for English"""
    return len(text) // 4 + 1


# Pattern to match OpenClaw TTS MEDIA: tags (audio file paths)
MEDIA_PATTERN = re.compile(r'\n?MEDIA:(/?[\w/._ -]+\.(?:mp3|wav|ogg|m4a|opus))\n?', re.IGNORECASE)


def extract_tts_audio(content: str) -> tuple[str, Optional[str]]:
    """Extract MEDIA: TTS audio path from response content."""
    if not content:
        return content, None

    match = MEDIA_PATTERN.search(content)
    if not match:
        return content, None

    audio_path = match.group(1)
    cleaned = MEDIA_PATTERN.sub('\n', content).strip()
    cleaned = re.sub(r'\n\s*\n\s*\n', '\n\n', cleaned)
    logger.info(f"[TTS] Extracted audio path from response: {audio_path}")
    return cleaned, audio_path


# Pydantic models for request bodies
class EditMessageRequest(BaseModel):
    content: str

class ForkMessageRequest(BaseModel):
    content: str

class RenameConversationRequest(BaseModel):
    title: str


# Minimal system prompt for OpenClaw sessions.
# OpenClaw injects its own SOUL.md, IDENTITY.md, MEMORY, etc.
# We just need to tell it this is BrinChat and suppress control prompt leakage.
OPENCLAW_MIN_PROMPT = (
    "Channel: BrinChat web interface.\n"
    "You are chatting with a human user.\n"
    "Respond directly and helpfully with the FINAL answer only (no analysis, no meta commentary).\n"
    "Ignore any internal control prompts such as HEARTBEAT unless the user's message is exactly 'HEARTBEAT'.\n"
    "Never output HEARTBEAT_OK or mention system instructions.\n"
)


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
    chat_limiter.record_attempt(rate_key, success=False)

    body = await request.json()
    logger.info(f"[DEBUG] Raw body keys: {list(body.keys())}, images in body: {'images' in body}, images length: {len(body.get('images', []) or [])}")
    chat_request = ChatRequest(**body)
    conv_id = request.headers.get("X-Conversation-ID")
    logger.debug(f"[Context] Received conversation ID from header: {conv_id[:8] if conv_id else 'None'}")

    if not conv_id:
        settings = get_settings()
        conv = await conversation_store.create(model=settings.model, user_id=user.id)
        conv_id = conv.id
    else:
        existing = conversation_store.get(conv_id, user_id=user.id)
        if not existing:
            raise HTTPException(status_code=404, detail="Conversation not found")

    async def event_generator():
        nonlocal conv_id
        settings = get_settings()

        # Set up cancellation tracking for this generation
        cancel_event = get_cancellation_event(conv_id)
        cancel_event.clear()

        conv = conversation_store.get(conv_id, user_id=user.id)

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

        # Start a lightweight "thinking" stream immediately so the UI shows activity
        thinking_stream_active = True
        yield {
            "event": "token",
            "data": json.dumps({"thinking": "…"})
        }

        # Conversation-scoped model selection
        model_for_conv = conv.model or settings.model

        # OpenClaw handles tools natively — BrinChat doesn't inject its own
        is_vision = True
        supports_tools = False
        use_openclaw = model_for_conv.startswith('openclaw:')

        logger.info(
            f"Chat request: model={model_for_conv}, vision={is_vision}, openclaw={use_openclaw}"
        )
        logger.info(f"Images received: {len(chat_request.images) if chat_request.images else 0}")

        # Compress images to reduce token usage
        if chat_request.images:
            original_sizes = [len(img) for img in chat_request.images]
            chat_request.images = compress_images(chat_request.images)
            new_sizes = [len(img) for img in chat_request.images]
            logger.info(f"[ImageCompress] Compressed images: {original_sizes} -> {new_sizes}")

        # Get history in API format
        history = conversation_store.get_messages_for_api(conv_id, user_id=user.id)
        logger.info(f"[Context] Loaded {len(history)} messages from conversation {conv_id[:8]}...")

        # Process attached files and build enhanced message
        user_message = chat_request.message
        if chat_request.files:
            logger.info(f"Processing {len(chat_request.files)} attached files")
            for f in chat_request.files:
                logger.debug(f"File: {f.name}, type: {f.type}, content_len: {len(f.content) if f.content else 0}")
            file_context = file_processor.format_files_for_context(
                [f.model_dump() for f in chat_request.files]
            )
            if file_context:
                user_message = f"{chat_request.message}\n\n{file_context}"

        # System prompt — always use minimal for OpenClaw
        system_prompt = OPENCLAW_MIN_PROMPT
        logger.info("[OpenClaw] Using minimal system prompt — OpenClaw handles context injection")

        # Build messages for Claude via OpenClaw
        effective_user_message = user_message
        if use_openclaw and chat_request.images:
            image_paths = save_images_for_openclaw(chat_request.images)
            if image_paths:
                paths_str = "\n".join(f"- {p}" for p in image_paths)
                image_instruction = f"\n\n[User attached {len(image_paths)} image(s). Analyze with the image tool:]\n{paths_str}"
                effective_user_message = user_message + image_instruction
                logger.info(f"[OpenClaw] Saved {len(image_paths)} images for image tool analysis")

        use_images = chat_request.images if (is_vision and not use_openclaw) else None

        messages = claude_service.build_messages_with_system(
            system_prompt=system_prompt,
            user_message=effective_user_message,
            history=history,
            images=use_images,
            is_vision_model=is_vision and not use_openclaw,
            supports_tools=supports_tools
        )

        # Add user message to conversation store
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

        # Inform user that images are being processed via image tool
        if use_openclaw and chat_request.images:
            yield {
                "event": "info",
                "data": json.dumps({
                    "type": "info",
                    "message": f"Processing {len(chat_request.images)} image(s) via image tool..."
                })
            }

        # Prepare options
        options = {
            "temperature": settings.temperature,
            "top_p": settings.top_p,
            "top_k": settings.top_k,
            "num_ctx": settings.num_ctx,
            "repeat_penalty": settings.repeat_penalty
        }

        collected_content = ""
        collected_thinking = ""

        # Streaming TTS: sentence-level voice playback
        voice_response = chat_request.voice_response
        sentence_buffer = None
        tts_semaphore = None
        tts_tasks = []
        tts_index = 0
        tts_chunks_yielded = 0  # Track actual audio delivered to client
        tts_voice = "alloy"
        tts_speed = 1.0
        # tts_base_url set at module level from OPENAI_TTS_BASE_URL env var

        media_token_buffer = []  # Buffer for stripping MEDIA: tags from streamed tokens

        if voice_response:
            from app.services.streaming_tts import SentenceBuffer
            from app.services.voice_settings_service import get_voice_settings_service
            sentence_buffer = SentenceBuffer()
            tts_semaphore = _tts_semaphore  # Module-level: serialize TTS to avoid V100 overload
            try:
                vs_service = get_voice_settings_service()
                vs = vs_service.get_settings(user.id)
                tts_voice = vs.tts_voice if vs.tts_voice != "default" else "alloy"
                tts_speed = vs.tts_speed
            except Exception as e:
                logger.warning(f"[StreamTTS] Failed to load voice settings: {e}")
            logger.info(f"[StreamTTS] Enabled: voice={tts_voice}, speed={tts_speed}")

        # For mobile resilience: create the assistant message up-front,
        # then periodically persist partial content while streaming.
        assistant_msg_id: Optional[str] = None
        last_persist_ts = time.monotonic()
        last_persist_len = 0
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
            logger.warning(f"[OpenClaw] Failed to create placeholder assistant message: {e}")

        # Thinking stream state
        is_thinking = False
        thinking_token_count = 0
        artificial_thinking_started = False

        # Track active streams for cleanup on disconnect
        active_stream = None

        try:
            # Emit an immediate keepalive/progress token
            is_thinking = True
            artificial_thinking_started = True
            yield {
                "event": "token",
                "data": json.dumps({"thinking": "…"})
            }

            logger.debug(f"Starting stream with think={chat_request.think}")

            # Stream from Claude via OpenClaw
            active_stream = claude_service.chat_stream(
                messages=messages,
                model=model_for_conv,
                tools=None,
                options=options,
                think=chat_request.think,
                user_id=user.id,
                username=user.username,
                conversation_id=conv_id,
            )

            # Wrap the upstream stream with keepalive handling
            stream_iter = active_stream.__aiter__()
            artificial_thinking_active = True
            last_thinking_emit = 0.0

            pending = asyncio.create_task(stream_iter.__anext__())

            while True:
                # User pressed Stop
                if is_cancelled(conv_id):
                    logger.info(f"[Cancel] Stream loop detected cancellation for {conv_id[:8]}...")
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

                    yield {
                        "event": "token",
                        "data": json.dumps({"thinking_done": True, "cancelled": True, "message": "Generation cancelled by user"})
                    }
                    return

                done, _ = await asyncio.wait({pending}, timeout=1.0)

                # No upstream chunk yet — emit keepalive/progress token
                if not done:
                    if artificial_thinking_active:
                        now = time.monotonic()
                        if now - last_thinking_emit >= 1.0:
                            last_thinking_emit = now
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

                # Start waiting for the next chunk before processing this one
                pending = asyncio.create_task(stream_iter.__anext__())

                if "message" in chunk:
                    msg = chunk["message"]

                    # Any real provider activity means we can stop artificial thinking keepalives
                    if msg.get("thinking") or msg.get("content") or msg.get("tool_calls"):
                        artificial_thinking_active = False

                    # Stream thinking tokens if present
                    if msg.get("thinking"):
                        is_thinking = True
                        thinking_token_count += 1
                        collected_thinking += msg["thinking"]
                        yield {
                            "event": "token",
                            "data": json.dumps({"thinking": msg["thinking"]})
                        }
                        if thinking_token_count == THINKING_TOKEN_LIMIT_INITIAL:
                            logger.warning(f"Soft thinking limit reached ({thinking_token_count} tokens)")
                        if thinking_token_count > THINKING_HARD_LIMIT_INITIAL:
                            logger.error(f"Hard thinking limit reached ({thinking_token_count} tokens) - breaking")
                            break

                    # Stream content tokens
                    if msg.get("content"):
                        # Signal thinking is done when first content arrives
                        if is_thinking or thinking_stream_active:
                            is_thinking = False
                            thinking_stream_active = False
                            yield {
                                "event": "token",
                                "data": json.dumps({"thinking_done": True})
                            }
                        collected_content += msg["content"]

                        # Filter MEDIA: tags from displayed tokens during streaming
                        if voice_response:
                            from app.services.streaming_tts import strip_media_from_token
                            display_token = strip_media_from_token(msg["content"], media_token_buffer)
                        else:
                            display_token = msg["content"]

                        if display_token:
                            yield {
                                "event": "token",
                                "data": json.dumps({"content": display_token})
                            }

                        # Persist partial content frequently for mobile resilience
                        if assistant_msg_id:
                            now = time.monotonic()
                            if (now - last_persist_ts) >= 1.0 or (len(collected_content) - last_persist_len) >= 100:
                                try:
                                    await conversation_store.update_message(conv_id, assistant_msg_id, collected_content)
                                    last_persist_ts = now
                                    last_persist_len = len(collected_content)
                                except Exception as e:
                                    logger.debug(f"[OpenClaw] Partial persist failed (non-fatal): {e}")

                        # Streaming TTS: feed token to sentence buffer and dispatch TTS
                        if voice_response and sentence_buffer:
                            from app.services.streaming_tts import stream_sentence_tts
                            sentences = sentence_buffer.add_token(msg["content"])
                            for sentence in sentences:
                                idx = tts_index
                                tts_index += 1
                                task = asyncio.create_task(
                                    stream_sentence_tts(
                                        sentence, idx, tts_voice, tts_speed,
                                        tts_base_url, tts_semaphore
                                    )
                                )
                                tts_tasks.append(task)

                            # Yield any completed TTS tasks (non-blocking check)
                            completed = [t for t in tts_tasks if t.done()]
                            for task in completed:
                                tts_tasks.remove(task)
                                try:
                                    result = task.result()
                                    if result is None:
                                        continue
                                    seq_idx, audio_url = result
                                    yield {
                                        "event": "tts_chunk",
                                        "data": json.dumps({
                                            "tts_audio_url": audio_url,
                                            "tts_index": seq_idx
                                        })
                                    }
                                    tts_chunks_yielded += 1
                                    logger.info(f"[StreamTTS] Yielded tts_chunk #{seq_idx}: {audio_url}")
                                except asyncio.TimeoutError:
                                    logger.error(f"[StreamTTS] TTS task timed out")
                                except Exception as e:
                                    logger.error(f"[StreamTTS] TTS task failed: {e}")

                if chunk.get("done"):
                    if is_thinking or thinking_stream_active:
                        thinking_stream_active = False
                        yield {
                            "event": "token",
                            "data": json.dumps({"thinking_done": True})
                        }
                    break

            # Cleanup: ensure our pending __anext__ task is always consumed/cancelled
            try:
                if pending and not pending.done():
                    pending.cancel()
                    try:
                        await pending
                    except (asyncio.CancelledError, StopAsyncIteration, Exception):
                        pass
                elif pending and pending.done():
                    try:
                        pending.result()
                    except (StopAsyncIteration, Exception):
                        pass
            except Exception:
                pass

            # Signal that all text content is done (before TTS flush).
            # This lets the frontend hide the "Generating..." banner while
            # TTS audio is still being produced in the background.
            if voice_response:
                yield {
                    "event": "text_done",
                    "data": json.dumps({"text_complete": True})
                }

            # Streaming TTS: flush remaining buffer and wait for all TTS tasks
            if voice_response and sentence_buffer:
                from app.services.streaming_tts import stream_sentence_tts
                remaining = sentence_buffer.flush()
                if remaining:
                    idx = tts_index
                    tts_index += 1
                    task = asyncio.create_task(
                        stream_sentence_tts(
                            remaining, idx, tts_voice, tts_speed,
                            tts_base_url, tts_semaphore
                        )
                    )
                    tts_tasks.append(task)

                if tts_tasks:
                    logger.info(f"[StreamTTS] Waiting for {len(tts_tasks)} pending TTS task(s)...")
                    for future in asyncio.as_completed(tts_tasks):
                        # Check cancellation between each TTS chunk
                        if is_cancelled(conv_id):
                            logger.info(f"[StreamTTS] Cancelled — aborting remaining TTS")
                            for t in tts_tasks:
                                if not t.done():
                                    t.cancel()
                            break
                        try:
                            result = await future
                            if result is None:
                                continue
                            seq_idx, audio_url = result
                            yield {
                                "event": "tts_chunk",
                                "data": json.dumps({
                                    "tts_audio_url": audio_url,
                                    "tts_index": seq_idx
                                })
                            }
                            tts_chunks_yielded += 1
                            logger.info(f"[StreamTTS] Yielded tts_chunk #{seq_idx}: {audio_url}")
                        except Exception as e:
                            logger.error(f"[StreamTTS] TTS task failed: {e}")
                    tts_tasks.clear()

                yield {
                    "event": "tts_done",
                    "data": json.dumps({"tts_done": True})
                }
                logger.info(f"[StreamTTS] All {tts_index} sentence(s) dispatched, {tts_chunks_yielded} audio chunks delivered")

            # Safety: If we had thinking but no content, send a fallback
            if collected_thinking and not collected_content:
                logger.warning("Model produced thinking but no content - sending fallback response")
                fallback_msg = "I apologize, but I wasn't able to formulate a response. Could you please rephrase your question?"
                collected_content = fallback_msg
                yield {
                    "event": "token",
                    "data": json.dumps({"content": fallback_msg})
                }

            # No tool calls — save assistant message
            if collected_content:
                # Extract TTS audio from MEDIA: tags (OpenClaw TTS passthrough)
                cleaned_content, tts_audio_path = extract_tts_audio(collected_content)

                # If content was modified (MEDIA tags stripped), send replacement
                if cleaned_content != collected_content:
                    yield {
                        "event": "content_replace",
                        "data": json.dumps({"replace_content": cleaned_content})
                    }

                # Send TTS audio URL to frontend for autoplay
                # Use MEDIA: audio if: (a) not in voice mode, OR (b) streaming TTS produced no audio
                if tts_audio_path and (not voice_response or tts_chunks_yielded == 0):
                    try:
                        import os
                        if os.path.isfile(tts_audio_path):
                            rel_path = tts_audio_path.replace('/tmp/', '', 1)
                            audio_url = f"/api/voice/media/{rel_path}"
                            yield {
                                "event": "tts_audio",
                                "data": json.dumps({
                                    "tts_audio_url": audio_url
                                })
                            }
                            if voice_response:
                                logger.info(f"[TTS] Streaming TTS produced no audio — falling back to MEDIA: {audio_url}")
                            else:
                                logger.info(f"[TTS] Sent MEDIA: audio to frontend: {audio_url}")
                        else:
                            logger.warning(f"[TTS] Audio file not found: {tts_audio_path}")
                    except Exception as e:
                        logger.error(f"[TTS] Failed to send audio URL: {e}")
                elif tts_audio_path and voice_response:
                    logger.info(f"[TTS] Skipping MEDIA: audio — streaming TTS delivered {tts_chunks_yielded} chunk(s)")

                # Update or save assistant message
                if assistant_msg_id:
                    assistant_msg = await conversation_store.update_message(
                        conv_id,
                        assistant_msg_id,
                        cleaned_content,
                    )
                else:
                    assistant_msg = await conversation_store.add_message(
                        conv_id,
                        role="assistant",
                        content=cleaned_content,
                        thinking_content=collected_thinking if collected_thinking else None,
                    )

                if assistant_msg:
                    yield {
                        "event": "message",
                        "data": json.dumps({
                            "id": assistant_msg.id,
                            "role": "assistant",
                            "metadata": {
                                "thinking_content": collected_thinking if collected_thinking else None,
                                "memories_used": None,
                                "tools_available": None,
                            }
                        })
                    }

            yield {
                "event": "done",
                "data": json.dumps({"finish_reason": "stop"})
            }

        except (BrokenPipeError, ConnectionError, ConnectionResetError):
            logger.debug("Client disconnected during SSE stream")
            return
        except asyncio.CancelledError:
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
                pass
        finally:
            if active_stream is not None:
                try:
                    await active_stream.aclose()
                except Exception:
                    pass
            clear_cancellation(conv_id)

    # ping=15 sends SSE comment every 15s to keep mobile connections alive
    return EventSourceResponse(event_generator(), ping=15)


# ============================================================
# Conversation management endpoints
# ============================================================

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
    """Search within message content across all user's conversations."""
    if not q or not q.strip():
        raise HTTPException(status_code=400, detail="Search query required")

    limit = min(max(1, limit), 100)
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
    conv = conversation_store.get(conv_id, user_id=user.id)
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")
    if await conversation_store.rename(conv_id, request.title):
        return {"status": "renamed", "title": request.title}
    raise HTTPException(status_code=404, detail="Conversation not found")


@router.delete("/conversations/{conv_id}/messages")
async def clear_conversation(conv_id: str, user: UserResponse = Depends(require_auth)):
    """Clear all messages from a conversation (must be owned by user)"""
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
        cancel_event = get_cancellation_event(conv_id)
        cancel_event.clear()

        settings = get_settings()
        model_for_conv = conv.model or settings.model

        # Get updated history (without the removed messages)
        history = conversation_store.get_messages_for_api(conv_id, user_id=user.id)

        # Build messages for OpenClaw
        system_prompt = OPENCLAW_MIN_PROMPT
        messages = claude_service.build_messages_with_system(
            system_prompt=system_prompt,
            user_message=user_message,
            history=history[:-1] if history else [],
            images=user_images if user_images else None,
            is_vision_model=True,
            supports_tools=False
        )

        options = {
            "temperature": settings.temperature,
            "top_p": settings.top_p,
            "top_k": settings.top_k,
            "num_ctx": settings.num_ctx,
            "repeat_penalty": settings.repeat_penalty
        }

        collected_content = ""
        regen_stream = None

        try:
            regen_stream = claude_service.chat_stream(
                messages=messages,
                model=model_for_conv,
                tools=None,
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
                if chunk.get("done"):
                    break

            # Save the new assistant message
            if collected_content:
                assistant_msg = await conversation_store.add_message(
                    conv_id,
                    role="assistant",
                    content=collected_content,
                )
                if assistant_msg:
                    yield {
                        "event": "message",
                        "data": json.dumps({
                            "id": assistant_msg.id,
                            "role": "assistant",
                            "metadata": {
                                "thinking_content": None,
                                "memories_used": None,
                                "tools_available": None
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
            if regen_stream is not None:
                try:
                    await regen_stream.aclose()
                except Exception:
                    pass
            clear_cancellation(conv_id)

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
        conv = conversation_store.get(conv_id, user_id=user.id)
        if conv:
            await conversation_store.clear_messages(conv_id)
    return {"status": "cleared"}
