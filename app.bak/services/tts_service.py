"""
TTS Service - Text-to-speech with swappable backends.

Uses the backend abstraction from tts_backends.py.
Backend selection via TTS_BACKEND environment variable.
"""
import logging
import asyncio
from typing import Optional, AsyncGenerator

from app import config
from app.services.tts_backends import (
    get_tts_backend_class,
    list_available_backends,
    TTSBackend,
    TTSConfig,
)

logger = logging.getLogger(__name__)

# Singleton backend instance
_tts_backend: Optional[TTSBackend] = None
_backend_lock = asyncio.Lock()


async def _get_or_create_backend() -> Optional[TTSBackend]:
    """Get or create the TTS backend based on config."""
    global _tts_backend

    if _tts_backend is not None:
        return _tts_backend

    async with _backend_lock:
        # Double-check after acquiring lock
        if _tts_backend is not None:
            return _tts_backend

        if not config.VOICE_ENABLED:
            logger.warning("Voice features disabled. Set VOICE_ENABLED=true to enable.")
            return None

        backend_name = config.TTS_BACKEND.lower()
        model_name = config.TTS_MODEL
        device = config.TTS_DEVICE

        try:
            # Get backend class
            backend_class = get_tts_backend_class(backend_name)

            # Use backend's default model if "default" specified
            if model_name == "default":
                model_name = _get_default_model(backend_name)

            logger.info(f"Initializing TTS backend: {backend_name} (model={model_name}, device={device})")

            # Create and initialize backend
            backend = backend_class(model_name=model_name, device=device)
            await backend.ensure_initialized()

            _tts_backend = backend
            logger.info(f"TTS backend ready: {backend_name}")
            return _tts_backend

        except ValueError as e:
            logger.error(f"Invalid TTS backend: {e}")
            logger.info(f"Available backends: {list_available_backends()}")
            return None
        except Exception as e:
            logger.error(f"Failed to initialize TTS backend '{backend_name}': {e}")
            return None


def _get_default_model(backend_name: str) -> str:
    """Get default model for each backend."""
    defaults = {
        "edge": "en-US-AriaNeural",
        "piper": "en_US-lessac-medium",
        "coqui": "tts_models/en/ljspeech/tacotron2-DDC",
        "kokoro": "kokoro-v0_19.onnx",
        "qwen3": "Qwen/Qwen3-TTS-12Hz-0.6B-CustomVoice",  # CustomVoice has preset voices
    }
    return defaults.get(backend_name, "default")


class TTSService:
    """
    TTS Service wrapper - maintains backward-compatible API.

    Delegates to the configured backend.
    """

    async def generate_audio(
        self,
        text: str,
        voice: str = "default",
        speed: float = 1.0
    ) -> Optional[bytes]:
        """
        Generate audio from text.

        Args:
            text: Text to synthesize
            voice: Voice ID (backend-specific)
            speed: Playback speed multiplier (0.5-2.0)

        Returns:
            Audio bytes (WAV or MP3 depending on backend) or None on error
        """
        backend = await _get_or_create_backend()
        if backend is None:
            return None

        # Enforce text length limit
        if len(text) > config.VOICE_MAX_TTS_LENGTH:
            text = text[:config.VOICE_MAX_TTS_LENGTH]
            logger.warning(f"TTS text truncated to {config.VOICE_MAX_TTS_LENGTH} chars")

        try:
            tts_config = TTSConfig(
                voice=voice,
                speed=speed,
            )
            return await backend.generate(text, tts_config)
        except Exception as e:
            logger.error(f"TTS generation failed: {e}")
            return None

    async def generate_stream(
        self,
        text: str,
        voice: str = "default",
        speed: float = 1.0,
        chunk_size: int = 4096
    ) -> AsyncGenerator[bytes, None]:
        """
        Generate audio as a stream of chunks.

        Args:
            text: Text to synthesize
            voice: Voice ID
            speed: Playback speed
            chunk_size: Bytes per chunk (for non-streaming backends)

        Yields:
            Audio byte chunks
        """
        backend = await _get_or_create_backend()
        if backend is None:
            return

        if len(text) > config.VOICE_MAX_TTS_LENGTH:
            text = text[:config.VOICE_MAX_TTS_LENGTH]

        tts_config = TTSConfig(voice=voice, speed=speed)

        try:
            if backend.supports_streaming:
                # Use native streaming
                async for chunk in backend.generate_stream(text, tts_config):
                    yield chunk
            else:
                # Fall back to chunked delivery
                audio = await backend.generate(text, tts_config)
                if audio:
                    for i in range(0, len(audio), chunk_size):
                        yield audio[i:i + chunk_size]
        except Exception as e:
            logger.error(f"TTS streaming failed: {e}")

    async def get_voices(self):
        """Get available voices for current backend."""
        backend = await _get_or_create_backend()
        if backend is None:
            return []

        try:
            return await backend.get_voices()
        except Exception as e:
            logger.error(f"Failed to get voices: {e}")
            return []

    async def cleanup(self):
        """Release backend resources."""
        global _tts_backend
        if _tts_backend is not None:
            try:
                await _tts_backend.cleanup()
            except Exception as e:
                logger.error(f"TTS cleanup error: {e}")
            _tts_backend = None
            logger.info("TTS service cleaned up")


# Singleton service instance
_tts_service: Optional[TTSService] = None


def get_tts_service() -> TTSService:
    """Get the global TTS service instance."""
    global _tts_service
    if _tts_service is None:
        _tts_service = TTSService()
    return _tts_service


async def cleanup_tts_service():
    """Clean up the TTS service on shutdown."""
    global _tts_service
    if _tts_service is not None:
        await _tts_service.cleanup()
        _tts_service = None
