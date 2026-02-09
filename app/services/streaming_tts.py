"""
Streaming TTS Service — Sentence-level TTS for real-time voice playback.

Buffers streaming LLM tokens, detects sentence boundaries, and dispatches
async TTS requests so audio can start playing before the full response is ready.
"""
import asyncio
import datetime
import logging
import os
import re
import tempfile
import time
import uuid
from pathlib import Path
from typing import Optional, Tuple

import httpx

logger = logging.getLogger(__name__)

# Pattern to strip MEDIA: tags that OpenClaw injects for its own TTS
MEDIA_PATTERN = re.compile(r'\n?MEDIA:/?[\w/._ -]+\.(?:mp3|wav|ogg|m4a|opus)\n?', re.IGNORECASE)

# Directory for streaming TTS temp files
TTS_TEMP_DIR = "/tmp/brinchat-tts"
os.makedirs(TTS_TEMP_DIR, exist_ok=True)

# Cleanup task control
_cleanup_task: Optional[asyncio.Task] = None
_cleanup_stop_event = asyncio.Event()


def clean_for_tts(text: str) -> str:
    """Remove MEDIA: tags and other non-speakable artifacts from text."""
    text = MEDIA_PATTERN.sub('', text)
    # Remove all emoji/symbol Unicode blocks that TTS can't pronounce
    text = re.sub(
        r'[\u2600-\u27BF'           # Misc symbols, Dingbats (✨☀♻➡ etc.)
        r'\U0001f300-\U0001f9ff'    # Misc Symbols & Pictographs, Emoticons, etc.
        r'\U0001fa00-\U0001faff'    # Symbols Extended-A
        r'\u2300-\u23FF'            # Misc Technical (⌚⏰ etc.)
        r'\u2B50-\u2B55'            # Stars, circles
        r'\u200d'                   # Zero-width joiner (emoji sequences)
        r'\ufe0f\ufe0e'            # Variation selectors
        r']+', '', text
    )
    # Collapse leftover whitespace
    text = re.sub(r'\s+', ' ', text).strip()
    # Skip if only punctuation/symbols remain (no actual words)
    if text and not re.search(r'[a-zA-Z0-9]', text):
        return ''
    return text


async def cleanup_old_tts_files():
    """Background task to clean up old TTS temp files."""
    from app.config import TTS_FILE_MAX_AGE_HOURS, TTS_CLEANUP_INTERVAL_MINUTES

    logger.info(f"[TTS Cleanup] Starting cleanup task (interval: {TTS_CLEANUP_INTERVAL_MINUTES}min, max age: {TTS_FILE_MAX_AGE_HOURS}h)")

    while not _cleanup_stop_event.is_set():
        try:
            # Wait for the cleanup interval or stop event
            try:
                await asyncio.wait_for(
                    _cleanup_stop_event.wait(),
                    timeout=TTS_CLEANUP_INTERVAL_MINUTES * 60
                )
                break  # Stop event was set
            except asyncio.TimeoutError:
                pass  # Timeout means it's time to clean

            # Perform cleanup
            now = time.time()
            max_age_seconds = TTS_FILE_MAX_AGE_HOURS * 3600
            deleted_count = 0
            deleted_bytes = 0

            temp_dir = Path(TTS_TEMP_DIR)
            if not temp_dir.exists():
                continue

            for file_path in temp_dir.glob("stts-*.wav"):
                try:
                    file_age = now - file_path.stat().st_mtime
                    if file_age > max_age_seconds:
                        file_size = file_path.stat().st_size
                        file_path.unlink()
                        deleted_count += 1
                        deleted_bytes += file_size
                except Exception as e:
                    logger.error(f"[TTS Cleanup] Failed to delete {file_path}: {e}")

            if deleted_count > 0:
                logger.info(
                    f"[TTS Cleanup] Deleted {deleted_count} files "
                    f"({deleted_bytes / 1024 / 1024:.2f} MB) older than {TTS_FILE_MAX_AGE_HOURS}h"
                )

        except Exception as e:
            logger.error(f"[TTS Cleanup] Error in cleanup loop: {e}")
            await asyncio.sleep(60)  # Wait 1 minute before retrying on error


def start_cleanup_task():
    """Start the background cleanup task."""
    global _cleanup_task
    if _cleanup_task is None:
        _cleanup_stop_event.clear()
        _cleanup_task = asyncio.create_task(cleanup_old_tts_files())
        logger.info("[TTS Cleanup] Cleanup task started")


def stop_cleanup_task():
    """Stop the background cleanup task."""
    global _cleanup_task
    if _cleanup_task is not None:
        _cleanup_stop_event.set()
        logger.info("[TTS Cleanup] Cleanup task stopping")
        _cleanup_task = None


def strip_media_from_token(token: str, buffer: list) -> str:
    """
    Strip MEDIA: tags from streaming content tokens.

    Since MEDIA: tags can span multiple tokens, we use a simple heuristic:
    buffer tokens that look like they're starting a MEDIA: tag, and suppress
    the whole thing once we see the pattern complete.

    Args:
        token: The current content token
        buffer: Mutable list used as accumulator for partial MEDIA: matches

    Returns:
        The token to display (empty string if suppressed)
    """
    combined = "".join(buffer) + token

    # Check if we're in the middle of accumulating a MEDIA: tag
    if buffer:
        if MEDIA_PATTERN.search(combined):
            # Full MEDIA: tag found — suppress it all
            cleaned = MEDIA_PATTERN.sub('', combined)
            buffer.clear()
            return cleaned
        elif len(combined) > 200:
            # Too long without completing — probably not a MEDIA: tag, flush
            buffer.clear()
            return combined
        else:
            # Still accumulating
            buffer.append(token)
            return ""

    # Check if this token starts a MEDIA: tag
    if 'MEDIA:' in token:
        if MEDIA_PATTERN.search(token):
            # Complete MEDIA: tag in single token — strip it
            return MEDIA_PATTERN.sub('', token)
        else:
            # Partial MEDIA: tag — start buffering
            buffer.append(token)
            return ""

    # Check if token ends with "MEDIA" or "\nMEDIA" (partial start)
    if token.rstrip().endswith('MEDIA') or token.rstrip().endswith('\nMEDIA'):
        buffer.append(token)
        return ""

    return token


class SentenceBuffer:
    """
    Accumulates streaming text tokens and yields complete sentences.

    Rules:
    - Split on `. `, `? `, `! `, `.\n`, `?\n`, `!\n` followed by text
    - Do NOT split inside markdown code blocks (``` ... ```)
    - Batch short fragments: don't yield sentences under ~30 chars
    - Ceiling: if a sentence runs past ~250 chars without a boundary, split at nearest word boundary
    - Flush remaining buffer when the stream ends
    """

    MIN_SENTENCE_LEN = 20  # Lowered from 30 for faster first audio chunk
    MAX_SENTENCE_LEN = 250

    # Sentence-ending punctuation followed by whitespace or newline
    SENTENCE_END = re.compile(r'([.!?])(\s+)')

    def __init__(self):
        self.buffer = ""
        self.in_code_block = False
        self._pending_short = ""  # Accumulates short fragments

    def add_token(self, token: str) -> list[str]:
        """
        Add a token to the buffer and return any complete sentences.

        Returns:
            List of complete sentences ready for TTS (may be empty).
        """
        self.buffer += token
        return self._extract_sentences()

    def flush(self) -> Optional[str]:
        """
        Flush any remaining text in the buffer.

        Returns:
            Remaining text, or None if buffer is empty.
        """
        remaining = self._pending_short + self.buffer
        self._pending_short = ""
        self.buffer = ""
        text = remaining.strip()
        return text if text else None

    def _extract_sentences(self) -> list[str]:
        sentences = []

        while self.buffer:
            # Track code block state
            code_fence_count = self.buffer.count("```")
            if code_fence_count > 0:
                last_fence = self.buffer.rfind("```")
                before_last = self.buffer[:last_fence].count("```")
                if before_last % 2 == 0 and code_fence_count % 2 == 1:
                    self.in_code_block = True
                    break
                elif code_fence_count % 2 == 0:
                    self.in_code_block = False
                    after_close = self.buffer[last_fence + 3:]
                    if not after_close.strip():
                        break
                    before = self.buffer[:last_fence + 3]
                    self._pending_short += before
                    self.buffer = after_close
                    continue

            if self.in_code_block:
                break

            match = self.SENTENCE_END.search(self.buffer)

            if match:
                end_pos = match.end()
                sentence_text = self.buffer[:end_pos].strip()
                self.buffer = self.buffer[end_pos:]

                sentence_text = self._pending_short + sentence_text
                self._pending_short = ""

                if len(sentence_text) >= self.MIN_SENTENCE_LEN:
                    sentences.append(sentence_text)
                else:
                    self._pending_short = sentence_text + " "

            elif len(self._pending_short) + len(self.buffer) > self.MAX_SENTENCE_LEN:
                combined = self._pending_short + self.buffer
                split_at = combined.rfind(" ", 0, self.MAX_SENTENCE_LEN)
                if split_at == -1:
                    split_at = self.MAX_SENTENCE_LEN

                sentence_text = combined[:split_at].strip()
                remainder = combined[split_at:].lstrip()

                self._pending_short = ""
                self.buffer = remainder

                if sentence_text:
                    sentences.append(sentence_text)
            else:
                break

        return sentences


async def generate_sentence_audio(
    sentence: str,
    voice: str = "alloy",
    speed: float = 1.0,
    base_url: Optional[str] = None,
) -> bytes:
    """
    Generate TTS audio for a single sentence via the OpenAI-compatible endpoint.

    Returns:
        WAV audio bytes
    """
    if base_url is None:
        raw = os.getenv("OPENAI_TTS_BASE_URL", "")
        if not raw:
            raise ValueError("OPENAI_TTS_BASE_URL environment variable is required for TTS")
        base_url = raw[:-3] if raw.endswith("/v1") else raw
    endpoint = f"{base_url.rstrip('/')}/v1/audio/speech"

    payload = {
        "model": "tts-1",
        "input": sentence,
        "voice": voice if voice != "default" else "alloy",
        "speed": speed,
    }

    async with httpx.AsyncClient(timeout=12.0) as client:  # Balanced: allows 8s generation + 4s headroom
        response = await client.post(
            endpoint,
            json=payload,
            headers={"Content-Type": "application/json"},
        )
        response.raise_for_status()
        return response.content


async def stream_sentence_tts(
    sentence: str,
    index: int,
    voice: str,
    speed: float,
    base_url: str,
    semaphore: asyncio.Semaphore,
) -> Optional[Tuple[int, str]]:
    """
    Generate TTS for a sentence with concurrency control.
    Writes audio to a temp file and returns the URL path.

    Returns:
        Tuple of (sequence index, URL path for audio), or None if sentence was empty.
    """
    # Strip MEDIA: tags and other non-speakable content
    cleaned = clean_for_tts(sentence)
    if not cleaned:
        logger.info(f"[StreamTTS] Sentence #{index} empty after cleaning, skipping")
        return None

    start_time = time.time()
    success = False
    is_timeout = False

    async with semaphore:
        logger.info(f"[StreamTTS] Generating audio for sentence #{index}: {cleaned[:60]}...")
        try:
            audio_bytes = await generate_sentence_audio(cleaned, voice, speed, base_url)

            # Write to temp file instead of base64-encoding for SSE
            timestamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
            filename = f"stts-{timestamp}-{uuid.uuid4().hex[:6]}-{index}.wav"
            filepath = os.path.join(TTS_TEMP_DIR, filename)
            with open(filepath, 'wb') as f:
                f.write(audio_bytes)

            # URL path that frontend can fetch
            audio_url = f"/api/voice/media/brinchat-tts/{filename}"
            logger.info(f"[StreamTTS] Sentence #{index} done: {len(audio_bytes)} bytes -> {audio_url}")
            success = True
            return index, audio_url
        except asyncio.TimeoutError:
            logger.error(f"[StreamTTS] Sentence #{index} TTS generation timed out")
            is_timeout = True
            raise
        except Exception as e:
            logger.error(f"[StreamTTS] Sentence #{index} TTS generation failed: {e}")
            raise
        finally:
            # Record metrics
            latency_ms = (time.time() - start_time) * 1000
            try:
                from app.routers.voice import record_tts_request
                record_tts_request(success, latency_ms, is_timeout)
            except Exception as e:
                logger.warning(f"[StreamTTS] Failed to record metrics: {e}")
