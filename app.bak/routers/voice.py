"""Voice router for TTS and STT endpoints."""
import logging
import json
import base64
from typing import Optional
from fastapi import APIRouter, HTTPException, Depends, UploadFile, File, Form
from fastapi.responses import Response
from sse_starlette.sse import EventSourceResponse
from pydantic import BaseModel, Field

from app import config
from app.middleware.auth import require_auth
from app.models.auth_schemas import UserResponse
from app.services.tts_service import get_tts_service
from app.services.stt_service import get_stt_service
from app.services.voice_settings_service import (
    get_voice_settings_service,
    VoiceSettings
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/voice", tags=["voice"])


def require_voice_enabled():
    """Dependency to check if voice features are enabled."""
    if not config.VOICE_ENABLED:
        raise HTTPException(
            status_code=503,
            detail="Voice features are disabled. Set VOICE_ENABLED=true to enable."
        )


class TTSRequest(BaseModel):
    """Request model for TTS endpoint."""
    text: str
    voice: str = "default"
    speed: float = Field(default=1.0, ge=0.5, le=2.0, description="Playback speed (0.5-2.0)")


class VoiceSettingsUpdate(BaseModel):
    """Request model for updating voice settings."""
    voice_mode: str = "disabled"
    tts_voice: str = "default"
    tts_speed: float = Field(default=1.0, ge=0.5, le=2.0)
    auto_play: bool = False
    auto_send: bool = True  # Auto-send transcription (voice assistant mode)
    stt_language: str = "en"


class VoiceSettingsResponse(BaseModel):
    """Response model for voice settings."""
    voice_mode: str
    tts_voice: str
    tts_speed: float
    auto_play: bool
    auto_send: bool  # Auto-send transcription
    stt_language: str
    voice_enabled: bool  # Server-level flag


@router.post("/tts/stream")
async def tts_stream_post(
    request: TTSRequest,
    user: UserResponse = Depends(require_auth),
    _: None = Depends(require_voice_enabled)
):
    """Stream TTS audio via Server-Sent Events (POST version).

    Events:
    - audio: Base64-encoded audio chunk
    - done: Generation complete
    - error: Error occurred
    """
    return await _tts_stream_impl(request.text, request.voice, request.speed)


@router.get("/tts/stream")
async def tts_stream_get(
    text: str,
    voice: str = "default",
    speed: float = 1.0,
    user: UserResponse = Depends(require_auth),
    _: None = Depends(require_voice_enabled)
):
    """Stream TTS audio via Server-Sent Events (GET version for EventSource).

    EventSource only supports GET, so this endpoint mirrors the POST version.
    """
    return await _tts_stream_impl(text, voice, speed)


async def _tts_stream_impl(text: str, voice: str, speed: float):
    """Internal implementation for TTS streaming."""
    async def generate():
        try:
            tts_service = get_tts_service()

            # Validate and truncate text
            text_clean = text.strip()
            if not text_clean:
                yield {
                    "event": "error",
                    "data": json.dumps({"error": "Text is required"})
                }
                return

            if len(text_clean) > config.VOICE_MAX_TTS_LENGTH:
                text_clean = text_clean[:config.VOICE_MAX_TTS_LENGTH]
                yield {
                    "event": "warning",
                    "data": json.dumps({
                        "message": f"Text truncated to {config.VOICE_MAX_TTS_LENGTH} characters"
                    })
                }

            # Generate and stream audio chunks
            chunk_count = 0
            async for chunk in tts_service.generate_stream(
                text=text_clean,
                voice=voice,
                speed=speed
            ):
                chunk_count += 1
                yield {
                    "event": "audio",
                    "data": json.dumps({
                        "chunk": base64.b64encode(chunk).decode("utf-8"),
                        "index": chunk_count
                    })
                }

            yield {
                "event": "done",
                "data": json.dumps({
                    "chunks": chunk_count,
                    "text_length": len(text_clean)
                })
            }

        except Exception as e:
            logger.error(f"TTS stream error: {e}")
            yield {
                "event": "error",
                "data": json.dumps({"error": str(e)})
            }

    return EventSourceResponse(generate())


@router.post("/tts")
async def tts_audio(
    request: TTSRequest,
    user: UserResponse = Depends(require_auth),
    _: None = Depends(require_voice_enabled)
):
    """Generate TTS audio and return as WAV file."""
    tts_service = get_tts_service()

    text = request.text.strip()
    if not text:
        raise HTTPException(status_code=400, detail="Text is required")

    if len(text) > config.VOICE_MAX_TTS_LENGTH:
        text = text[:config.VOICE_MAX_TTS_LENGTH]

    audio_bytes = await tts_service.generate_audio(
        text=text,
        voice=request.voice,
        speed=request.speed
    )

    if audio_bytes is None:
        raise HTTPException(status_code=500, detail="TTS generation failed")

    return Response(
        content=audio_bytes,
        media_type="audio/wav",
        headers={
            "Content-Disposition": "inline; filename=speech.wav"
        }
    )


@router.post("/transcribe")
async def transcribe_audio(
    audio: UploadFile = File(...),
    language: str = Form("en"),
    user: UserResponse = Depends(require_auth),
    _: None = Depends(require_voice_enabled)
):
    """Transcribe audio file to text.

    Args:
        audio: Audio file (WAV, MP3, WebM, etc.)
        language: Language code (e.g., "en", "es", "auto")

    Returns:
        Transcription result with text, language, and segments
    """
    stt_service = get_stt_service()

    # Validate language code (simple validation - just alphanumeric and hyphen)
    import re
    if not re.match(r'^[a-zA-Z]{2,5}(-[a-zA-Z]{2,5})?$|^auto$', language):
        raise HTTPException(status_code=400, detail="Invalid language code")

    # Read audio data
    audio_data = await audio.read()

    if not audio_data:
        raise HTTPException(status_code=400, detail="Empty audio file")

    # Check file size
    max_size = config.VOICE_MAX_AUDIO_LENGTH * 32000  # Rough estimate
    if len(audio_data) > max_size:
        raise HTTPException(
            status_code=400,
            detail=f"Audio too long. Maximum {config.VOICE_MAX_AUDIO_LENGTH} seconds."
        )

    # Determine format from filename
    filename = audio.filename or "audio.wav"
    format = filename.rsplit(".", 1)[-1].lower()
    if format not in ("wav", "mp3", "webm", "ogg", "m4a", "flac"):
        format = "wav"

    result = await stt_service.transcribe(
        audio_data=audio_data,
        language=language,
        format=format
    )

    if result is None:
        raise HTTPException(status_code=500, detail="Transcription failed")

    return {
        "success": True,
        "text": result["text"],
        "language": result["language"],
        "segments": result.get("segments", [])
    }


@router.get("/settings", response_model=VoiceSettingsResponse)
async def get_voice_settings(
    user: UserResponse = Depends(require_auth)
):
    """Get user's voice settings."""
    service = get_voice_settings_service()
    settings = service.get_settings(user.id)

    return VoiceSettingsResponse(
        voice_mode=settings.voice_mode,
        tts_voice=settings.tts_voice,
        tts_speed=settings.tts_speed,
        auto_play=settings.auto_play,
        auto_send=settings.auto_send,
        stt_language=settings.stt_language,
        voice_enabled=config.VOICE_ENABLED
    )


@router.put("/settings")
async def update_voice_settings(
    settings: VoiceSettingsUpdate,
    user: UserResponse = Depends(require_auth)
):
    """Update user's voice settings."""
    service = get_voice_settings_service()

    new_settings = VoiceSettings(
        voice_mode=settings.voice_mode,
        tts_voice=settings.tts_voice,
        tts_speed=settings.tts_speed,
        auto_play=settings.auto_play,
        auto_send=settings.auto_send,
        stt_language=settings.stt_language
    )

    success = service.update_settings(user.id, new_settings)

    if not success:
        raise HTTPException(status_code=500, detail="Failed to update voice settings")

    return {
        "success": True,
        "message": "Voice settings updated",
        "settings": {
            "voice_mode": new_settings.voice_mode,
            "tts_voice": new_settings.tts_voice,
            "tts_speed": new_settings.tts_speed,
            "auto_play": new_settings.auto_play,
            "auto_send": new_settings.auto_send,
            "stt_language": new_settings.stt_language
        }
    }


@router.get("/voices")
async def list_voices(
    user: UserResponse = Depends(require_auth),
    _: None = Depends(require_voice_enabled)
):
    """Get available TTS voices for the current backend."""
    tts_service = get_tts_service()
    
    try:
        voices = await tts_service.get_voices()
        return {
            "success": True,
            "backend": config.TTS_BACKEND,
            "voices": [
                {
                    "id": v.id,
                    "name": v.name,
                    "language": v.language,
                    "gender": v.gender
                }
                for v in voices
            ]
        }
    except Exception as e:
        logger.error(f"Failed to get voices: {e}")
        return {
            "success": False,
            "backend": config.TTS_BACKEND,
            "voices": [],
            "error": str(e)
        }


@router.get("/media/{filename:path}")
async def serve_tts_media(
    filename: str,
    user: UserResponse = Depends(require_auth)
):
    """Serve TTS audio files generated by OpenClaw.

    Only serves files from /tmp/tts-* directories for security.
    """
    import os
    from fastapi.responses import FileResponse as AudioFileResponse

    # Security: only serve from /tmp/tts-* and /tmp/brinchat-tts/ directories
    # Reconstruct the full path from the filename
    # Filename format: tts-XXXXX/voice-YYYYYYY.mp3 or brinchat-tts/stts-XXXX.wav
    full_path = os.path.join("/tmp", filename)
    real_path = os.path.realpath(full_path)

    # Validate path is under allowed directories
    if not (real_path.startswith("/tmp/tts-") or real_path.startswith("/tmp/brinchat-tts/")):
        raise HTTPException(status_code=403, detail="Access denied")
    if not os.path.isfile(real_path):
        raise HTTPException(status_code=404, detail="Audio file not found")

    # Determine MIME type
    ext = real_path.rsplit('.', 1)[-1].lower()
    mime_map = {'mp3': 'audio/mpeg', 'wav': 'audio/wav', 'ogg': 'audio/ogg', 'm4a': 'audio/mp4'}
    media_type = mime_map.get(ext, 'audio/mpeg')

    return AudioFileResponse(
        real_path,
        media_type=media_type,
        headers={"Cache-Control": "no-store"}
    )


@router.get("/status")
async def voice_status(
    user: UserResponse = Depends(require_auth)
):
    """Get voice feature status."""
    return {
        "enabled": config.VOICE_ENABLED,
        "tts_backend": config.TTS_BACKEND if config.VOICE_ENABLED else None,
        "tts_model": config.TTS_MODEL if config.VOICE_ENABLED else None,
        "tts_device": config.TTS_DEVICE if config.VOICE_ENABLED else None,
        "stt_backend": config.STT_BACKEND if config.VOICE_ENABLED else None,
        "stt_model": config.STT_MODEL if config.VOICE_ENABLED else None,
        "stt_device": config.STT_DEVICE if config.VOICE_ENABLED else None,
        "max_audio_length": config.VOICE_MAX_AUDIO_LENGTH,
        "max_tts_length": config.VOICE_MAX_TTS_LENGTH
    }
