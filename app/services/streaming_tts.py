"""
Streaming TTS Service — Sentence-level TTS for real-time voice playback.

Buffers streaming LLM tokens, detects sentence boundaries, and dispatches
async TTS requests so audio can start playing before the full response is ready.
"""
import asyncio
import logging
import os
import re
import tempfile
import uuid
from typing import Optional, Tuple

import httpx

logger = logging.getLogger(__name__)

# Pattern to strip MEDIA: tags that OpenClaw injects for its own TTS
MEDIA_PATTERN = re.compile(r'\n?MEDIA:/?[\w/._ -]+\.(?:mp3|wav|ogg|m4a|opus)\n?', re.IGNORECASE)

# Directory for streaming TTS temp files
TTS_TEMP_DIR = "/tmp/brinchat-tts"
os.makedirs(TTS_TEMP_DIR, exist_ok=True)


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

    MIN_SENTENCE_LEN = 30
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
    base_url: str = "http://10.10.10.124:5002",
) -> bytes:
    """
    Generate TTS audio for a single sentence via the OpenAI-compatible endpoint.

    Returns:
        WAV audio bytes
    """
    endpoint = f"{base_url.rstrip('/')}/v1/audio/speech"

    payload = {
        "model": "tts-1",
        "input": sentence,
        "voice": voice if voice != "default" else "alloy",
        "speed": speed,
    }

    async with httpx.AsyncClient(timeout=20.0) as client:
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

    async with semaphore:
        logger.info(f"[StreamTTS] Generating audio for sentence #{index}: {cleaned[:60]}...")
        try:
            audio_bytes = await generate_sentence_audio(cleaned, voice, speed, base_url)

            # Write to temp file instead of base64-encoding for SSE
            filename = f"stts-{uuid.uuid4().hex[:8]}-{index}.wav"
            filepath = os.path.join(TTS_TEMP_DIR, filename)
            with open(filepath, 'wb') as f:
                f.write(audio_bytes)

            # URL path that frontend can fetch
            audio_url = f"/api/voice/media/brinchat-tts/{filename}"
            logger.info(f"[StreamTTS] Sentence #{index} done: {len(audio_bytes)} bytes -> {audio_url}")
            return index, audio_url
        except Exception as e:
            logger.error(f"[StreamTTS] Sentence #{index} TTS generation failed: {e}")
            raise
