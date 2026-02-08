"""
STT Service - Speech-to-text with swappable backends.

Uses the backend abstraction from stt_backends.py.
Backend selection via STT_BACKEND environment variable.
"""
import logging
import asyncio
from typing import Optional

from app import config
from app.services.stt_backends import (
    get_stt_backend_class,
    list_available_backends,
    STTBackend,
    STTConfig,
    TranscriptionResult,
)

logger = logging.getLogger(__name__)

# Singleton backend instance
_stt_backend: Optional[STTBackend] = None
_backend_lock = asyncio.Lock()


async def _get_or_create_backend() -> Optional[STTBackend]:
    """Get or create the STT backend based on config."""
    global _stt_backend

    if _stt_backend is not None:
        return _stt_backend

    async with _backend_lock:
        # Double-check after acquiring lock
        if _stt_backend is not None:
            return _stt_backend

        if not config.VOICE_ENABLED:
            logger.warning("Voice features disabled. Set VOICE_ENABLED=true to enable.")
            return None

        backend_name = config.STT_BACKEND.lower()
        model_name = config.STT_MODEL
        device = config.STT_DEVICE

        try:
            # Get backend class
            backend_class = get_stt_backend_class(backend_name)

            logger.info(f"Initializing STT backend: {backend_name} (model={model_name}, device={device})")

            # Create and initialize backend
            backend = backend_class(model_name=model_name, device=device)
            await backend.ensure_initialized()

            _stt_backend = backend
            logger.info(f"STT backend ready: {backend_name}")
            return _stt_backend

        except ValueError as e:
            logger.error(f"Invalid STT backend: {e}")
            logger.info(f"Available backends: {list_available_backends()}")
            return None
        except Exception as e:
            logger.error(f"Failed to initialize STT backend '{backend_name}': {e}")
            return None


class STTService:
    """
    STT Service wrapper - maintains backward-compatible API.

    Delegates to the configured backend.
    """

    async def transcribe(
        self,
        audio_data: bytes,
        language: str = "en",
        format: str = "wav"
    ) -> Optional[dict]:
        """
        Transcribe audio data to text.

        Args:
            audio_data: Raw audio bytes
            language: Language code (e.g., "en", "es", "auto" for auto-detect)
            format: Audio format hint ("wav", "mp3", "webm", etc.)

        Returns:
            Dict with 'text', 'language', and 'segments' or None on error
        """
        backend = await _get_or_create_backend()
        if backend is None:
            return None

        # Check audio length (rough estimate: 1 sec of 16kHz 16-bit mono ~ 32KB)
        max_bytes = config.VOICE_MAX_AUDIO_LENGTH * 32000
        if len(audio_data) > max_bytes:
            logger.warning(f"Audio too long, truncating to {config.VOICE_MAX_AUDIO_LENGTH}s")
            audio_data = audio_data[:max_bytes]

        try:
            stt_config = STTConfig(
                language=None if language == "auto" else language,
                task="transcribe",
                word_timestamps=False,
            )

            result: TranscriptionResult = await backend.transcribe(audio_data, stt_config)

            # Convert to dict for backward compatibility
            return {
                "text": result.text,
                "language": result.language,
                "segments": [
                    {
                        "start": seg.get("start", 0),
                        "end": seg.get("end", 0),
                        "text": seg.get("word", seg.get("text", "")),
                    }
                    for seg in (result.segments or [])
                ],
            }

        except Exception as e:
            logger.error(f"STT transcription failed: {e}")
            return None

    async def transcribe_file(
        self,
        file_path: str,
        language: str = "en"
    ) -> Optional[dict]:
        """
        Transcribe an audio file.

        Args:
            file_path: Path to audio file
            language: Language code

        Returns:
            Dict with 'text', 'language', and 'segments' or None on error
        """
        import os

        if not os.path.exists(file_path):
            logger.error(f"Audio file not found: {file_path}")
            return None

        try:
            with open(file_path, "rb") as f:
                audio_data = f.read()
            return await self.transcribe(audio_data, language)
        except Exception as e:
            logger.error(f"Failed to read audio file: {e}")
            return None

    async def cleanup(self):
        """Release backend resources."""
        global _stt_backend
        if _stt_backend is not None:
            try:
                await _stt_backend.cleanup()
            except Exception as e:
                logger.error(f"STT cleanup error: {e}")
            _stt_backend = None
            logger.info("STT service cleaned up")


# Singleton service instance
_stt_service: Optional[STTService] = None


def get_stt_service() -> STTService:
    """Get the global STT service instance."""
    global _stt_service
    if _stt_service is None:
        _stt_service = STTService()
    return _stt_service


async def cleanup_stt_service():
    """Clean up the STT service on shutdown."""
    global _stt_service
    if _stt_service is not None:
        await _stt_service.cleanup()
        _stt_service = None
