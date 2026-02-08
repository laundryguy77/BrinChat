"""Voice settings service for per-user voice preferences."""
import logging
from typing import Optional
from dataclasses import dataclass

from app.services.database import get_database

logger = logging.getLogger(__name__)


@dataclass
class VoiceSettings:
    """Voice settings for a user."""
    voice_mode: str = "disabled"  # disabled, transcribe_only, tts_only, conversation
    tts_voice: str = "default"
    tts_speed: float = 1.0
    auto_play: bool = False
    auto_send: bool = True  # Auto-send transcription (voice assistant mode)
    stt_language: str = "en"


class VoiceSettingsService:
    """Service for managing per-user voice settings."""

    def __init__(self):
        self.db = get_database()

    def get_settings(self, user_id: int) -> VoiceSettings:
        """Get voice settings for a user."""
        try:
            row = self.db.fetchone(
                """SELECT voice_mode, tts_voice, tts_speed, auto_play, auto_send, stt_language
                   FROM user_settings WHERE user_id = ?""",
                (user_id,)
            )
            if row:
                return VoiceSettings(
                    voice_mode=row["voice_mode"] or "disabled",
                    tts_voice=row["tts_voice"] or "default",
                    tts_speed=row["tts_speed"] or 1.0,
                    auto_play=bool(row["auto_play"]),
                    auto_send=bool(row["auto_send"]) if row["auto_send"] is not None else True,
                    stt_language=row["stt_language"] or "en"
                )
            return VoiceSettings()
        except Exception as e:
            logger.error(f"Failed to get voice settings for user {user_id}: {e}")
            return VoiceSettings()

    def update_settings(self, user_id: int, settings: VoiceSettings) -> bool:
        """Update voice settings for a user."""
        try:
            # Validate voice_mode
            valid_modes = {"disabled", "transcribe_only", "tts_only", "conversation"}
            if settings.voice_mode not in valid_modes:
                logger.warning(f"Invalid voice_mode: {settings.voice_mode}")
                settings.voice_mode = "disabled"

            # Validate tts_speed (0.5 to 2.0)
            settings.tts_speed = max(0.5, min(2.0, settings.tts_speed))

            # Check if user_settings row exists
            existing = self.db.fetchone(
                "SELECT 1 FROM user_settings WHERE user_id = ?",
                (user_id,)
            )

            if existing:
                self.db.execute(
                    """UPDATE user_settings
                       SET voice_mode = ?, tts_voice = ?, tts_speed = ?,
                           auto_play = ?, auto_send = ?, stt_language = ?
                       WHERE user_id = ?""",
                    (
                        settings.voice_mode,
                        settings.tts_voice,
                        settings.tts_speed,
                        1 if settings.auto_play else 0,
                        1 if settings.auto_send else 0,
                        settings.stt_language,
                        user_id
                    )
                )
            else:
                self.db.execute(
                    """INSERT INTO user_settings
                       (user_id, voice_mode, tts_voice, tts_speed, auto_play, auto_send, stt_language)
                       VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (
                        user_id,
                        settings.voice_mode,
                        settings.tts_voice,
                        settings.tts_speed,
                        1 if settings.auto_play else 0,
                        1 if settings.auto_send else 0,
                        settings.stt_language
                    )
                )

            logger.info(f"Updated voice settings for user {user_id}")
            return True

        except Exception as e:
            logger.error(f"Failed to update voice settings for user {user_id}: {e}")
            return False

    def is_tts_enabled(self, user_id: int) -> bool:
        """Check if TTS is enabled for user."""
        settings = self.get_settings(user_id)
        return settings.voice_mode in ("tts_only", "conversation")

    def is_stt_enabled(self, user_id: int) -> bool:
        """Check if STT is enabled for user."""
        settings = self.get_settings(user_id)
        return settings.voice_mode in ("transcribe_only", "conversation")


# Singleton instance
_voice_settings_service: Optional[VoiceSettingsService] = None


def get_voice_settings_service() -> VoiceSettingsService:
    """Get the global voice settings service instance."""
    global _voice_settings_service
    if _voice_settings_service is None:
        _voice_settings_service = VoiceSettingsService()
    return _voice_settings_service
