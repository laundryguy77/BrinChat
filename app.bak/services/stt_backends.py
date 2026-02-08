"""
STT Backends - Model-swappable speech-to-text implementations.

Follows the same pattern as image_backends.py and video_backends.py.
Add new STT models by subclassing STTBackend and registering in STT_BACKENDS.

Usage:
    backend = get_stt_backend_class("faster_whisper")("small", "cuda:0")
    await backend.ensure_initialized()
    result = await backend.transcribe(audio_bytes, STTConfig())
"""

import asyncio
import logging
import tempfile
import os
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Dict, Any, Optional, List

logger = logging.getLogger(__name__)


@dataclass
class TranscriptionResult:
    """Result from STT transcription."""
    text: str
    language: str
    confidence: float
    duration: float
    segments: Optional[List[Dict]] = None  # Word-level timestamps if available


@dataclass
class STTConfig:
    """Configuration for STT transcription."""
    language: Optional[str] = None  # None = auto-detect
    task: str = "transcribe"  # transcribe or translate
    word_timestamps: bool = False


class STTBackend(ABC):
    """
    Abstract base class for STT backends.

    Subclass this to add support for new STT models.
    All backends support lazy initialization - models are loaded on first use.
    """

    name: str = "base"
    supports_streaming: bool = False
    supports_word_timestamps: bool = False
    supported_languages: List[str] = ["en"]

    def __init__(self, model_name: str, device: str = "cuda:0"):
        self.model_name = model_name
        self.device = device
        self.model = None
        self._lock = asyncio.Lock()
        self._initialized = False

    @abstractmethod
    async def initialize(self) -> None:
        """Load model onto device."""
        pass

    @abstractmethod
    async def transcribe(
        self,
        audio_data: bytes,
        config: STTConfig
    ) -> TranscriptionResult:
        """
        Transcribe audio to text.

        Args:
            audio_data: Audio bytes (wav, mp3, webm, etc.)
            config: Transcription configuration

        Returns:
            TranscriptionResult with text and metadata
        """
        pass

    async def cleanup(self) -> None:
        """Release resources."""
        pass

    async def ensure_initialized(self) -> None:
        """Thread-safe initialization."""
        if not self._initialized:
            async with self._lock:
                if not self._initialized:
                    await self.initialize()
                    self._initialized = True


# =============================================================================
# Whisper Backend (OpenAI)
# =============================================================================

class WhisperBackend(STTBackend):
    """
    OpenAI Whisper STT backend.

    Models: tiny, base, small, medium, large, large-v2, large-v3
    VRAM: ~0.5GB (small) to ~10GB (large-v3)

    Install: pip install openai-whisper
    """

    name = "whisper"
    supports_streaming = False
    supports_word_timestamps = True
    supported_languages = [
        "en", "zh", "de", "es", "ru", "ko", "fr", "ja", "pt", "tr",
        "pl", "ca", "nl", "ar", "sv", "it", "id", "hi", "fi", "vi"
    ]

    async def initialize(self) -> None:
        """Load Whisper model."""
        logger.info(f"Loading Whisper model {self.model_name} on {self.device}")

        try:
            import whisper

            # Model name is the size (tiny, base, small, medium, large)
            self.model = whisper.load_model(self.model_name, device=self.device)
            logger.info("Whisper model loaded successfully")
        except ImportError:
            logger.error("openai-whisper not installed. Install with: pip install openai-whisper")
            raise
        except Exception as e:
            logger.error(f"Failed to load Whisper model: {e}")
            raise

    async def transcribe(
        self,
        audio_data: bytes,
        config: STTConfig
    ) -> TranscriptionResult:
        """Transcribe using Whisper."""
        await self.ensure_initialized()

        # Write audio to temp file (Whisper requires file path)
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
            f.write(audio_data)
            temp_path = f.name

        try:
            result = self.model.transcribe(
                temp_path,
                language=config.language,
                task=config.task,
                word_timestamps=config.word_timestamps
            )

            # Calculate duration from segments
            duration = 0.0
            if result.get("segments"):
                duration = result["segments"][-1].get("end", 0.0)

            # Extract segments if word timestamps requested
            segments = None
            if config.word_timestamps and result.get("segments"):
                segments = []
                for seg in result["segments"]:
                    if seg.get("words"):
                        segments.extend(seg["words"])

            return TranscriptionResult(
                text=result["text"].strip(),
                language=result.get("language", config.language or "en"),
                confidence=1.0 - result.get("no_speech_prob", 0.0),
                duration=duration,
                segments=segments
            )

        finally:
            os.unlink(temp_path)

    async def cleanup(self) -> None:
        if self.model is not None:
            del self.model
            self.model = None

            try:
                import torch
                if torch.cuda.is_available():
                    torch.cuda.empty_cache()
            except ImportError:
                pass

            self._initialized = False
            logger.info("Whisper model unloaded")


# =============================================================================
# Faster-Whisper Backend
# =============================================================================

class FasterWhisperBackend(STTBackend):
    """
    Faster-Whisper STT backend (CTranslate2 optimized).

    2-4x faster than OpenAI Whisper with same accuracy.
    Lower VRAM usage.

    Install: pip install faster-whisper
    """

    name = "faster_whisper"
    supports_streaming = True
    supports_word_timestamps = True
    supported_languages = WhisperBackend.supported_languages

    async def initialize(self) -> None:
        """Load Faster-Whisper model."""
        logger.info(f"Loading Faster-Whisper model {self.model_name} on {self.device}")

        try:
            from faster_whisper import WhisperModel

            # Map device string
            device = "cuda" if "cuda" in self.device else "cpu"
            compute_type = "float16" if device == "cuda" else "int8"

            self.model = WhisperModel(
                self.model_name,
                device=device,
                compute_type=compute_type
            )

            logger.info("Faster-Whisper model loaded successfully")
        except ImportError:
            logger.error("faster-whisper not installed. Install with: pip install faster-whisper")
            raise
        except Exception as e:
            logger.error(f"Failed to load Faster-Whisper model: {e}")
            raise

    async def transcribe(
        self,
        audio_data: bytes,
        config: STTConfig
    ) -> TranscriptionResult:
        """Transcribe using Faster-Whisper."""
        await self.ensure_initialized()

        # Write to temp file
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
            f.write(audio_data)
            temp_path = f.name

        try:
            segments, info = self.model.transcribe(
                temp_path,
                language=config.language,
                task=config.task,
                word_timestamps=config.word_timestamps
            )

            # Collect segments
            text_parts = []
            word_segments = []
            duration = 0.0

            for segment in segments:
                text_parts.append(segment.text)
                duration = max(duration, segment.end)

                if config.word_timestamps and segment.words:
                    for word in segment.words:
                        word_segments.append({
                            "word": word.word,
                            "start": word.start,
                            "end": word.end,
                            "probability": word.probability
                        })

            return TranscriptionResult(
                text=" ".join(text_parts).strip(),
                language=info.language,
                confidence=info.language_probability,
                duration=duration,
                segments=word_segments if word_segments else None
            )

        finally:
            os.unlink(temp_path)

    async def cleanup(self) -> None:
        if self.model is not None:
            del self.model
            self.model = None
            self._initialized = False
            logger.info("Faster-Whisper model unloaded")


# =============================================================================
# Vosk Backend (Offline, CPU-friendly)
# =============================================================================

class VoskBackend(STTBackend):
    """
    Vosk STT backend - offline, CPU-friendly.

    Good for low-resource environments.
    Download models from: https://alphacephei.com/vosk/models

    Install: pip install vosk
    """

    name = "vosk"
    supports_streaming = True
    supports_word_timestamps = True
    supported_languages = ["en", "de", "fr", "es", "ru", "zh", "ja"]

    async def initialize(self) -> None:
        """Load Vosk model."""
        logger.info(f"Loading Vosk model {self.model_name}")

        try:
            from vosk import Model, SetLogLevel

            SetLogLevel(-1)  # Suppress Vosk logs
            self.model = Model(self.model_name)
            logger.info("Vosk model loaded successfully")
        except ImportError:
            logger.error("vosk not installed. Install with: pip install vosk")
            raise
        except Exception as e:
            logger.error(f"Failed to load Vosk model: {e}")
            raise

    async def transcribe(
        self,
        audio_data: bytes,
        config: STTConfig
    ) -> TranscriptionResult:
        """Transcribe using Vosk."""
        await self.ensure_initialized()

        import json
        import wave
        from vosk import KaldiRecognizer

        # Write to temp file and convert to proper format
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
            f.write(audio_data)
            temp_path = f.name

        try:
            wf = wave.open(temp_path, "rb")

            rec = KaldiRecognizer(self.model, wf.getframerate())
            rec.SetWords(config.word_timestamps)

            results = []
            while True:
                data = wf.readframes(4000)
                if len(data) == 0:
                    break
                if rec.AcceptWaveform(data):
                    results.append(json.loads(rec.Result()))

            results.append(json.loads(rec.FinalResult()))

            # Combine results
            text_parts = [r.get("text", "") for r in results]
            text = " ".join(text_parts).strip()

            # Extract word timestamps if requested
            segments = None
            if config.word_timestamps:
                segments = []
                for r in results:
                    if "result" in r:
                        for word_info in r["result"]:
                            segments.append({
                                "word": word_info["word"],
                                "start": word_info["start"],
                                "end": word_info["end"],
                                "probability": word_info.get("conf", 1.0)
                            })

            # Calculate duration
            duration = wf.getnframes() / wf.getframerate()
            wf.close()

            return TranscriptionResult(
                text=text,
                language=config.language or "en",
                confidence=1.0,
                duration=duration,
                segments=segments
            )

        finally:
            os.unlink(temp_path)

    async def cleanup(self) -> None:
        if hasattr(self, 'model'):
            del self.model
            self.model = None
            self._initialized = False


# =============================================================================
# OpenAI-Compatible API Backend (for local Whisper server)
# =============================================================================

class OpenAISTTBackend(STTBackend):
    """
    OpenAI-compatible STT backend - calls local Whisper server.

    Designed for local voice services with OpenAI-compatible API:
    - POST /v1/audio/transcriptions with multipart form data
    - Returns JSON with 'text' field

    Config via model_name: URL of the API endpoint
    Default: http://localhost:5001/v1/audio/transcriptions
    """

    name = "openai"
    supports_streaming = False
    supports_word_timestamps = False
    supported_languages = ["en", "es", "fr", "de", "it", "pt", "zh", "ja", "ko", "auto"]

    def __init__(self, model_name: str = "http://localhost:5001", device: str = "cpu"):
        super().__init__(model_name, device)
        # model_name is the base URL
        self.base_url = model_name.rstrip('/')
        self.endpoint = f"{self.base_url}/v1/audio/transcriptions"

    async def initialize(self) -> None:
        """No local model to load - just validate endpoint is reachable."""
        logger.info(f"OpenAI STT backend configured for {self.endpoint}")
        # Quick health check
        try:
            import httpx
            async with httpx.AsyncClient(timeout=5.0) as client:
                # Just check the base URL responds
                response = await client.get(self.base_url)
                # 404 is fine - means server is up but no GET handler
                logger.info(f"OpenAI STT backend ready (status: {response.status_code})")
        except Exception as e:
            logger.warning(f"OpenAI STT endpoint may not be reachable: {e}")

    async def transcribe(
        self,
        audio_data: bytes,
        config: STTConfig
    ) -> TranscriptionResult:
        """Transcribe using OpenAI-compatible API."""
        import httpx

        try:
            # Prepare multipart form data
            files = {
                'file': ('audio.wav', audio_data, 'audio/wav'),
            }
            data = {
                'model': 'whisper-1',  # Standard OpenAI model name
            }
            if config.language and config.language != 'auto':
                data['language'] = config.language

            async with httpx.AsyncClient(timeout=120.0) as client:
                response = await client.post(
                    self.endpoint,
                    files=files,
                    data=data
                )
                response.raise_for_status()
                result = response.json()

            text = result.get('text', '').strip()
            language = result.get('language', config.language or 'en')

            return TranscriptionResult(
                text=text,
                language=language,
                confidence=1.0,
                duration=result.get('duration', 0.0),
                segments=None
            )

        except httpx.HTTPStatusError as e:
            logger.error(f"OpenAI STT API error: {e.response.status_code} - {e.response.text}")
            raise
        except Exception as e:
            logger.error(f"OpenAI STT transcription failed: {e}")
            raise

    async def cleanup(self) -> None:
        """No local resources to clean up."""
        pass


# =============================================================================
# Backend Registry
# =============================================================================

STT_BACKENDS: Dict[str, type] = {
    "whisper": WhisperBackend,
    "faster_whisper": FasterWhisperBackend,
    "vosk": VoskBackend,
    "openai": OpenAISTTBackend,
}


def get_stt_backend_class(backend_name: str) -> type:
    """Get STT backend class by name."""
    if backend_name not in STT_BACKENDS:
        available = list(STT_BACKENDS.keys())
        raise ValueError(
            f"Unknown STT backend: {backend_name}. "
            f"Available: {available}"
        )
    return STT_BACKENDS[backend_name]


def list_available_backends() -> List[str]:
    """List all registered STT backend names."""
    return list(STT_BACKENDS.keys())
