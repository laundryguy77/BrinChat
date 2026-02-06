"""
TTS Backends - Model-swappable text-to-speech implementations.

Follows the same pattern as image_backends.py and video_backends.py.
Add new TTS models by subclassing TTSBackend and registering in TTS_BACKENDS.

Usage:
    backend = get_tts_backend_class("qwen3")("model-name", "cuda:0")
    await backend.ensure_initialized()
    audio = await backend.generate("Hello world", TTSConfig())
"""

import asyncio
import logging
import io
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import AsyncGenerator, Dict, Any, Optional, List

logger = logging.getLogger(__name__)


@dataclass
class TTSConfig:
    """Configuration for TTS generation."""
    voice: str = "default"
    speed: float = 1.0
    format: str = "wav"  # wav, mp3, opus
    sample_rate: int = 24000
    language: str = "en"


@dataclass
class TTSVoice:
    """Represents an available voice."""
    id: str
    name: str
    language: str
    gender: Optional[str] = None
    preview_url: Optional[str] = None


class TTSBackend(ABC):
    """
    Abstract base class for TTS backends.

    Subclass this to add support for new TTS models.
    All backends support lazy initialization - models are loaded on first use.
    """

    name: str = "base"
    supports_streaming: bool = False
    supports_voices: bool = False
    supported_formats: List[str] = ["wav"]

    def __init__(self, model_name: str, device: str = "cuda:0"):
        self.model_name = model_name
        self.device = device
        self.model = None
        self._lock = asyncio.Lock()
        self._initialized = False

    @abstractmethod
    async def initialize(self) -> None:
        """Load model onto device. Called lazily on first use."""
        pass

    @abstractmethod
    async def generate(self, text: str, config: TTSConfig) -> bytes:
        """
        Generate complete audio for text.

        Args:
            text: Text to synthesize
            config: TTS configuration

        Returns:
            Audio bytes in requested format
        """
        pass

    async def generate_stream(
        self,
        text: str,
        config: TTSConfig
    ) -> AsyncGenerator[bytes, None]:
        """
        Stream audio chunks for text.

        Default implementation generates all at once then yields.
        Override for true streaming support.
        """
        audio = await self.generate(text, config)
        yield audio

    async def get_voices(self) -> List[TTSVoice]:
        """Get available voices. Override if backend supports multiple voices."""
        return [TTSVoice(id="default", name="Default", language="en")]

    async def cleanup(self) -> None:
        """Release resources. Override for GPU cleanup."""
        pass

    async def ensure_initialized(self) -> None:
        """Thread-safe initialization check."""
        if not self._initialized:
            async with self._lock:
                if not self._initialized:
                    await self.initialize()
                    self._initialized = True


# =============================================================================
# Piper TTS Backend (CPU-friendly, fast)
# =============================================================================

class PiperTTSBackend(TTSBackend):
    """
    Piper TTS backend - fast, CPU-friendly TTS.

    Good for low-latency, low-resource environments.
    Model: piper-tts voices from https://github.com/rhasspy/piper

    Install: pip install piper-tts
    """

    name = "piper"
    supports_streaming = False
    supports_voices = True
    supported_formats = ["wav"]

    async def initialize(self) -> None:
        """Load Piper voice."""
        logger.info(f"Loading Piper voice {self.model_name}")

        try:
            from piper import PiperVoice
            self.voice = PiperVoice.load(self.model_name)
            logger.info("Piper voice loaded successfully")
        except ImportError:
            logger.error("piper-tts not installed. Install with: pip install piper-tts")
            raise
        except Exception as e:
            logger.error(f"Failed to load Piper voice: {e}")
            raise

    async def generate(self, text: str, config: TTSConfig) -> bytes:
        """Generate audio using Piper."""
        await self.ensure_initialized()

        import wave

        buffer = io.BytesIO()
        with wave.open(buffer, 'wb') as wav_file:
            wav_file.setnchannels(1)
            wav_file.setsampwidth(2)
            wav_file.setframerate(self.voice.config.sample_rate)

            for audio_bytes in self.voice.synthesize_stream_raw(
                text,
                length_scale=1.0 / config.speed if config.speed else 1.0
            ):
                wav_file.writeframes(audio_bytes)

        buffer.seek(0)
        return buffer.read()

    async def get_voices(self) -> List[TTSVoice]:
        return [TTSVoice("default", "Piper Voice", "en")]


# =============================================================================
# Coqui TTS Backend
# =============================================================================

class CoquiTTSBackend(TTSBackend):
    """
    Coqui TTS backend (XTTS, VITS, etc.)

    Model: tts_models/multilingual/multi-dataset/xtts_v2
    VRAM: ~1.5GB

    Install: pip install TTS
    """

    name = "coqui"
    supports_streaming = True
    supports_voices = True
    supported_formats = ["wav", "mp3"]

    async def initialize(self) -> None:
        """Load Coqui TTS model."""
        logger.info(f"Loading Coqui TTS model {self.model_name} on {self.device}")

        try:
            from TTS.api import TTS

            # Map device string to Coqui format
            gpu = "cuda" in self.device

            self.tts = TTS(model_name=self.model_name, gpu=gpu)
            logger.info("Coqui TTS model loaded successfully")
        except ImportError:
            logger.error("TTS not installed. Install with: pip install TTS")
            raise
        except Exception as e:
            logger.error(f"Failed to load Coqui TTS model: {e}")
            raise

    async def generate(self, text: str, config: TTSConfig) -> bytes:
        """Generate audio using Coqui TTS."""
        await self.ensure_initialized()

        import numpy as np
        import soundfile as sf

        # Generate audio
        wav = self.tts.tts(
            text=text,
            speaker=config.voice if config.voice != "default" else None,
            language=config.language,
            speed=config.speed
        )

        # Convert to bytes
        buffer = io.BytesIO()
        wav_array = np.array(wav, dtype=np.float32)
        sf.write(buffer, wav_array, self.tts.synthesizer.output_sample_rate, format='WAV')
        buffer.seek(0)

        return buffer.read()

    async def generate_stream(
        self,
        text: str,
        config: TTSConfig
    ) -> AsyncGenerator[bytes, None]:
        """Stream audio in sentence chunks."""
        await self.ensure_initialized()

        import re

        # Split text into sentences
        sentences = re.split(r'(?<=[.!?])\s+', text)

        for sentence in sentences:
            if sentence.strip():
                audio_chunk = await self.generate(sentence, config)
                yield audio_chunk
                await asyncio.sleep(0)  # Allow other tasks

    async def get_voices(self) -> List[TTSVoice]:
        """Get Coqui speaker list."""
        await self.ensure_initialized()

        voices = []
        if hasattr(self.tts, 'speakers') and self.tts.speakers:
            for speaker in self.tts.speakers:
                voices.append(TTSVoice(
                    id=speaker,
                    name=speaker.replace("_", " ").title(),
                    language="multi"
                ))
        else:
            voices.append(TTSVoice("default", "Default", "en"))

        return voices

    async def cleanup(self) -> None:
        if hasattr(self, 'tts'):
            del self.tts
            self.tts = None
            self._initialized = False


# =============================================================================
# Kokoro TTS Backend
# =============================================================================

class KokoroTTSBackend(TTSBackend):
    """
    Kokoro TTS backend - high quality English TTS.

    Model: hexgrad/Kokoro-82M
    Very fast and high quality.

    Install: pip install kokoro-onnx
    """

    name = "kokoro"
    supports_streaming = True
    supports_voices = True
    supported_formats = ["wav"]

    VOICES = [
        TTSVoice("af", "American Female", "en", "female"),
        TTSVoice("af_bella", "Bella", "en", "female"),
        TTSVoice("af_sarah", "Sarah", "en", "female"),
        TTSVoice("am_adam", "Adam", "en", "male"),
        TTSVoice("am_michael", "Michael", "en", "male"),
        TTSVoice("bf_emma", "Emma (British)", "en", "female"),
        TTSVoice("bm_george", "George (British)", "en", "male"),
    ]

    async def initialize(self) -> None:
        """Load Kokoro model."""
        logger.info(f"Loading Kokoro TTS model")

        try:
            from kokoro_onnx import Kokoro

            self.kokoro = Kokoro(self.model_name)
            logger.info("Kokoro TTS model loaded")
        except ImportError:
            logger.error("kokoro-onnx not installed. Install with: pip install kokoro-onnx")
            raise
        except Exception as e:
            logger.error(f"Failed to load Kokoro model: {e}")
            raise

    async def generate(self, text: str, config: TTSConfig) -> bytes:
        """Generate audio using Kokoro."""
        await self.ensure_initialized()

        import soundfile as sf

        voice = config.voice if config.voice != "default" else "af"

        # Generate audio
        samples, sample_rate = self.kokoro.create(
            text,
            voice=voice,
            speed=config.speed
        )

        # Convert to bytes
        buffer = io.BytesIO()
        sf.write(buffer, samples, sample_rate, format='WAV')
        buffer.seek(0)

        return buffer.read()

    async def generate_stream(
        self,
        text: str,
        config: TTSConfig
    ) -> AsyncGenerator[bytes, None]:
        """Stream audio in sentence chunks."""
        await self.ensure_initialized()

        import re

        sentences = re.split(r'(?<=[.!?])\s+', text)

        for sentence in sentences:
            if sentence.strip():
                audio_chunk = await self.generate(sentence, config)
                yield audio_chunk
                await asyncio.sleep(0)

    async def get_voices(self) -> List[TTSVoice]:
        return self.VOICES.copy()

    async def cleanup(self) -> None:
        if hasattr(self, 'kokoro'):
            del self.kokoro
            self.kokoro = None
            self._initialized = False


# =============================================================================
# Edge TTS Backend (Online, Free)
# =============================================================================

class EdgeTTSBackend(TTSBackend):
    """
    Microsoft Edge TTS backend - free online TTS.

    No model download required, uses Microsoft's online service.
    Many voices available.

    Install: pip install edge-tts
    """

    name = "edge"
    supports_streaming = True
    supports_voices = True
    supported_formats = ["mp3"]

    # Common voices
    VOICES = [
        TTSVoice("en-US-AriaNeural", "Aria (US)", "en", "female"),
        TTSVoice("en-US-GuyNeural", "Guy (US)", "en", "male"),
        TTSVoice("en-US-JennyNeural", "Jenny (US)", "en", "female"),
        TTSVoice("en-GB-SoniaNeural", "Sonia (UK)", "en", "female"),
        TTSVoice("en-GB-RyanNeural", "Ryan (UK)", "en", "male"),
        TTSVoice("en-AU-NatashaNeural", "Natasha (AU)", "en", "female"),
    ]

    async def initialize(self) -> None:
        """Edge TTS doesn't require initialization."""
        logger.info("Edge TTS backend ready (online service)")
        pass

    async def generate(self, text: str, config: TTSConfig) -> bytes:
        """Generate audio using Edge TTS."""
        try:
            import edge_tts

            voice = config.voice if config.voice != "default" else "en-US-AriaNeural"

            # Rate adjustment: edge-tts uses percentage (e.g., "+20%" or "-10%")
            rate_percent = int((config.speed - 1.0) * 100)
            rate = f"+{rate_percent}%" if rate_percent >= 0 else f"{rate_percent}%"

            communicate = edge_tts.Communicate(text, voice, rate=rate)

            buffer = io.BytesIO()
            async for chunk in communicate.stream():
                if chunk["type"] == "audio":
                    buffer.write(chunk["data"])

            buffer.seek(0)
            return buffer.read()

        except ImportError:
            logger.error("edge-tts not installed. Install with: pip install edge-tts")
            raise
        except Exception as e:
            logger.error(f"Edge TTS generation failed: {e}")
            raise

    async def generate_stream(
        self,
        text: str,
        config: TTSConfig
    ) -> AsyncGenerator[bytes, None]:
        """Stream audio from Edge TTS."""
        try:
            import edge_tts

            voice = config.voice if config.voice != "default" else "en-US-AriaNeural"
            rate_percent = int((config.speed - 1.0) * 100)
            rate = f"+{rate_percent}%" if rate_percent >= 0 else f"{rate_percent}%"

            communicate = edge_tts.Communicate(text, voice, rate=rate)

            async for chunk in communicate.stream():
                if chunk["type"] == "audio":
                    yield chunk["data"]

        except ImportError:
            logger.error("edge-tts not installed")
            raise

    async def get_voices(self) -> List[TTSVoice]:
        return self.VOICES.copy()


# =============================================================================
# Qwen3-TTS Backend (High quality, GPU)
# =============================================================================

class Qwen3TTSBackend(TTSBackend):
    """
    Qwen3-TTS backend - high quality multilingual neural TTS.

    Models:
    - Qwen/Qwen3-TTS-12Hz-0.6B-Base (voice cloning)
    - Qwen/Qwen3-TTS-12Hz-0.6B-CustomVoice (preset voices)
    - Qwen/Qwen3-TTS-12Hz-1.7B-* (larger, higher quality)

    VRAM: ~2GB (0.6B) or ~4GB (1.7B)

    Install: pip install qwen-tts
    """

    name = "qwen3"
    supports_streaming = True  # Qwen3-TTS supports streaming
    supports_voices = True
    supported_formats = ["wav"]

    # Voices for CustomVoice models (case-sensitive, must match exactly)
    VOICES = [
        TTSVoice("Vivian", "Vivian - Bright young female (Chinese)", "Chinese", "female"),
        TTSVoice("Serena", "Serena - Warm gentle female (Chinese)", "Chinese", "female"),
        TTSVoice("Ryan", "Ryan - Dynamic male (English)", "English", "male"),
        TTSVoice("Aiden", "Aiden - Sunny American male (English)", "English", "male"),
        TTSVoice("Dylan", "Dylan - Beijing male (Chinese)", "Chinese", "male"),
        TTSVoice("Eric", "Eric - Chengdu male (Chinese/Sichuan)", "Chinese", "male"),
        TTSVoice("Ono_Anna", "Ono Anna - Playful female (Japanese)", "Japanese", "female"),
        TTSVoice("Sohee", "Sohee - Warm female (Korean)", "Korean", "female"),
        TTSVoice("Uncle_Fu", "Uncle Fu - Seasoned male (Chinese)", "Chinese", "male"),
    ]

    async def initialize(self) -> None:
        """Load Qwen3-TTS model."""
        logger.info(f"Loading Qwen3-TTS model {self.model_name} on {self.device}")

        try:
            import torch
            from qwen_tts import Qwen3TTSModel

            # Parse device
            device = self.device if self.device != "cpu" else None

            self.model = Qwen3TTSModel.from_pretrained(
                self.model_name,
                device_map=device,
                dtype=torch.bfloat16,
            )

            # Check if this is a CustomVoice model
            self._is_custom_voice = "CustomVoice" in self.model_name
            self._is_voice_design = "VoiceDesign" in self.model_name

            logger.info(f"Qwen3-TTS model loaded successfully (custom_voice={self._is_custom_voice})")

        except ImportError:
            logger.error("qwen-tts not installed. Install with: pip install qwen-tts")
            raise
        except Exception as e:
            logger.error(f"Failed to load Qwen3-TTS model: {e}")
            raise

    async def generate(self, text: str, config: TTSConfig) -> bytes:
        """Generate audio using Qwen3-TTS."""
        await self.ensure_initialized()

        import soundfile as sf

        try:
            # Map short codes to full language names (Qwen3-TTS uses full names)
            lang_map = {
                "en": "English", "zh": "Chinese", "ja": "Japanese", "ko": "Korean",
                "de": "German", "fr": "French", "ru": "Russian", "pt": "Portuguese",
                "es": "Spanish", "it": "Italian", "auto": "Auto"
            }
            language = lang_map.get(config.language, config.language) if config.language else "Auto"

            if self._is_custom_voice:
                # Use custom voice generation (speaker names are case-sensitive)
                speaker = config.voice if config.voice != "default" else "Vivian"
                audios = self.model.generate_custom_voice(
                    text,
                    speaker=speaker,
                    language=language,
                )
            elif self._is_voice_design:
                # Use voice design (instruction-based)
                audios = self.model.generate_voice_design(
                    text,
                    voice_description="A clear, natural speaking voice",
                    language=language,
                )
            else:
                # Base model - use voice clone with default reference
                # For now, fall back to simple generation
                audios = self.model.generate_custom_voice(
                    text,
                    speaker="Chelsie",
                    language=language,
                )

            # Get first audio result (audios is a list of numpy arrays)
            if isinstance(audios, (list, tuple)):
                audio_array = audios[0]
            else:
                audio_array = audios
            
            # Ensure it's a numpy array
            import numpy as np
            audio_array = np.array(audio_array, dtype=np.float32)
            
            # Flatten if needed
            if audio_array.ndim > 1:
                audio_array = audio_array.flatten()

            # Convert to WAV bytes
            buffer = io.BytesIO()
            sf.write(buffer, audio_array, 24000, format='WAV')
            buffer.seek(0)

            return buffer.read()

        except Exception as e:
            logger.error(f"Qwen3-TTS generation failed: {e}")
            raise

    async def generate_stream(
        self,
        text: str,
        config: TTSConfig
    ) -> AsyncGenerator[bytes, None]:
        """Stream audio using Qwen3-TTS streaming mode."""
        await self.ensure_initialized()

        import soundfile as sf
        import numpy as np

        try:
            language = config.language if config.language != "en" else "auto"
            speaker = config.voice if config.voice != "default" else "Chelsie"

            # Use streaming generation
            for audio_chunk in self.model.generate_custom_voice(
                text,
                speaker=speaker,
                language=language,
                stream=True,
            ):
                # Convert chunk to WAV bytes
                buffer = io.BytesIO()
                sf.write(buffer, audio_chunk, 24000, format='WAV')
                buffer.seek(0)
                yield buffer.read()

        except Exception as e:
            logger.error(f"Qwen3-TTS streaming failed: {e}")
            # Fall back to non-streaming
            audio = await self.generate(text, config)
            yield audio

    async def get_voices(self) -> List[TTSVoice]:
        """Get available voices."""
        if hasattr(self, 'model') and self._is_custom_voice:
            try:
                speakers = self.model.get_supported_speakers()
                languages = self.model.get_supported_languages()
                return [
                    TTSVoice(s, s, ",".join(languages))
                    for s in speakers
                ]
            except Exception:
                pass
        return self.VOICES.copy()

    async def cleanup(self) -> None:
        if hasattr(self, 'model') and self.model is not None:
            import torch
            del self.model
            self.model = None
            torch.cuda.empty_cache()

        if hasattr(self, 'processor'):
            del self.processor
            self.processor = None

        self._initialized = False
        logger.info("Qwen3-TTS model unloaded")


# =============================================================================
# OpenAI-Compatible API Backend (for local TTS server)
# =============================================================================

class OpenAITTSBackend(TTSBackend):
    """
    OpenAI-compatible TTS backend - calls local TTS server.

    Designed for local voice services with OpenAI-compatible API:
    - POST /v1/audio/speech with JSON body
    - Returns audio/wav binary

    Config via model_name: URL of the API endpoint
    Default: http://localhost:5002/v1/audio/speech
    """

    name = "openai"
    supports_streaming = True
    supports_voices = True
    supported_formats = ["wav"]

    VOICES = [
        TTSVoice("brin", "Brin (Maya)", "en", "female"),
        TTSVoice("alloy", "Alloy", "en", "neutral"),
        TTSVoice("echo", "Echo", "en", "male"),
        TTSVoice("fable", "Fable", "en", "neutral"),
        TTSVoice("onyx", "Onyx", "en", "male"),
        TTSVoice("nova", "Nova", "en", "female"),
        TTSVoice("shimmer", "Shimmer", "en", "female"),
    ]

    def __init__(self, model_name: str = "http://localhost:5002", device: str = "cpu"):
        super().__init__(model_name, device)
        # model_name is the base URL
        self.base_url = model_name.rstrip('/')
        self.endpoint = f"{self.base_url}/v1/audio/speech"

    async def initialize(self) -> None:
        """No local model to load - just validate endpoint is reachable."""
        logger.info(f"OpenAI TTS backend configured for {self.endpoint}")
        # Quick health check - try /v1/models first (OpenAI-compatible), then /health
        try:
            import httpx
            async with httpx.AsyncClient(timeout=10.0) as client:
                # Try the models endpoint first (most compatible)
                try:
                    response = await client.get(f"{self.base_url}/v1/models")
                    if response.status_code == 200:
                        logger.info(f"OpenAI TTS backend ready (models endpoint)")
                        return
                except Exception:
                    pass

                # Try health endpoint as fallback
                try:
                    response = await client.get(f"{self.base_url}/health")
                    if response.status_code == 200:
                        logger.info(f"OpenAI TTS backend ready (health endpoint)")
                        return
                except Exception:
                    pass

                # Just log a warning - the actual endpoint may still work
                logger.warning(f"OpenAI TTS health check failed, but generation may still work")
        except Exception as e:
            logger.warning(f"OpenAI TTS endpoint may not be reachable: {e}")

    async def generate(self, text: str, config: TTSConfig) -> bytes:
        """Generate audio using OpenAI-compatible API."""
        import httpx

        try:
            voice = config.voice if config.voice != "default" else "alloy"

            payload = {
                "model": "tts-1",
                "input": text,
                "voice": voice,
                "speed": config.speed,
            }

            async with httpx.AsyncClient(timeout=120.0) as client:
                response = await client.post(
                    self.endpoint,
                    json=payload,
                    headers={"Content-Type": "application/json"}
                )
                response.raise_for_status()
                return response.content

        except httpx.HTTPStatusError as e:
            logger.error(f"OpenAI TTS API error: {e.response.status_code} - {e.response.text}")
            raise
        except Exception as e:
            logger.error(f"OpenAI TTS generation failed: {e}")
            raise

    async def generate_stream(
        self,
        text: str,
        config: TTSConfig
    ) -> AsyncGenerator[bytes, None]:
        """Stream audio - OpenAI API returns full audio, so we yield in chunks."""
        audio = await self.generate(text, config)
        # Yield the whole thing as one chunk since the API doesn't support true streaming
        yield audio

    async def get_voices(self) -> List[TTSVoice]:
        return self.VOICES.copy()

    async def cleanup(self) -> None:
        """No local resources to clean up."""
        pass


# =============================================================================
# Backend Registry
# =============================================================================

TTS_BACKENDS: Dict[str, type] = {
    "piper": PiperTTSBackend,
    "coqui": CoquiTTSBackend,
    "kokoro": KokoroTTSBackend,
    "edge": EdgeTTSBackend,
    "qwen3": Qwen3TTSBackend,
    "openai": OpenAITTSBackend,
}


def get_tts_backend_class(backend_name: str) -> type:
    """Get TTS backend class by name."""
    if backend_name not in TTS_BACKENDS:
        available = list(TTS_BACKENDS.keys())
        raise ValueError(
            f"Unknown TTS backend: {backend_name}. "
            f"Available: {available}"
        )
    return TTS_BACKENDS[backend_name]


def list_available_backends() -> List[str]:
    """List all registered TTS backend names."""
    return list(TTS_BACKENDS.keys())
