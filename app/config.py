import json
import os
import logging
import sys
from pathlib import Path
from pydantic import BaseModel
from typing import Optional
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

# Logging configuration
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()

def setup_logging():
    """Configure application logging"""
    root_logger = logging.getLogger()
    root_logger.setLevel(getattr(logging, LOG_LEVEL, logging.INFO))

    # Clear existing handlers
    root_logger.handlers.clear()

    # Console handler with formatting
    handler = logging.StreamHandler(sys.stdout)
    handler.setLevel(getattr(logging, LOG_LEVEL, logging.INFO))
    formatter = logging.Formatter(
        '%(asctime)s [%(levelname)s] %(name)s: %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    handler.setFormatter(formatter)
    root_logger.addHandler(handler)

    # Reduce noise from third-party libraries
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)

    return root_logger

# Initialize logging
logger = setup_logging()

SETTINGS_FILE = Path(__file__).parent.parent / "settings.json"

# Server settings
APP_HOST = os.getenv("APP_HOST", "0.0.0.0")
APP_PORT = int(os.getenv("APP_PORT", "8080"))

# OpenClaw API (Claude via OpenAI-compatible endpoint)
OPENCLAW_API_URL = os.getenv("OPENCLAW_API_URL", "http://localhost:18789/v1")
OPENCLAW_API_KEY = os.getenv("OPENCLAW_API_KEY", "")  # Token required for OpenClaw gateway
CLAUDE_MODEL = os.getenv("CLAUDE_MODEL", "ministral-3")

# OpenClaw Session Routing
# Primary user shares the main OpenClaw session (agent:main:main) for unified context
# Other users get isolated sessions via OpenClaw's user-based routing
OPENCLAW_PRIMARY_USER_ID = int(os.getenv("OPENCLAW_PRIMARY_USER_ID", "0"))  # 0 = disabled
OPENCLAW_PRIMARY_USERNAME = os.getenv("OPENCLAW_PRIMARY_USERNAME", "").lower()  # Case-insensitive match
OPENCLAW_MAIN_SESSION_KEY = os.getenv("OPENCLAW_MAIN_SESSION_KEY", "agent:main:main")

# Nextcloud Deck integration for task extraction (non-primary users)
NEXTCLOUD_URL = os.getenv("NEXTCLOUD_URL", "http://10.10.10.140:8080")
NEXTCLOUD_USER = os.getenv("NEXTCLOUD_USER", "admin")
NEXTCLOUD_PASS = os.getenv("NEXTCLOUD_PASS", "")
DECK_BOARD_ID = int(os.getenv("DECK_BOARD_ID", "2"))
DECK_BACKLOG_STACK_ID = int(os.getenv("DECK_BACKLOG_STACK_ID", "5"))

# Legacy: Keep OLLAMA_BASE_URL for embedding service (knowledge base still uses Ollama for embeddings)
OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")

# === Uncensored Mode Models ===
# Generic naming - swap models easily as hardware improves

# Chat model - main conversational AI for uncensored mode
UNCENSORED_CHAT_MODEL = os.getenv(
    "UNCENSORED_CHAT_MODEL",
    os.getenv("LEXI_MODEL", "taozhiyuai/llama-3-8b-lexi-uncensored:v1_q8_0")
)
UNCENSORED_BASE_URL = os.getenv(
    "UNCENSORED_BASE_URL",
    os.getenv("LEXI_BASE_URL", "http://localhost:11434")
)
UNCENSORED_TIMEOUT = int(os.getenv("UNCENSORED_TIMEOUT", "60"))

# Tool model - decides which tool to use (fast, small, doesn't need uncensored)
UNCENSORED_TOOL_MODEL = os.getenv(
    "UNCENSORED_TOOL_MODEL",
    os.getenv("OMEGA_TOOL_MODEL", "ministral-3:latest")
)
UNCENSORED_TOOL_TIMEOUT = int(os.getenv("UNCENSORED_TOOL_TIMEOUT", "30"))

# Vision model - describes images (must have vision capability)
UNCENSORED_VISION_MODEL = os.getenv(
    "UNCENSORED_VISION_MODEL",
    os.getenv("OMEGA_VISION_MODEL", "huihui_ai/qwen3-vl-abliterated:latest")
)
UNCENSORED_VISION_TIMEOUT = int(os.getenv("UNCENSORED_VISION_TIMEOUT", "30"))

# === Legacy Aliases (backwards compatibility) ===
LEXI_MODEL = UNCENSORED_CHAT_MODEL
LEXI_BASE_URL = UNCENSORED_BASE_URL
OMEGA_TOOL_MODEL = UNCENSORED_TOOL_MODEL
OMEGA_TOOL_BASE_URL = UNCENSORED_BASE_URL
OMEGA_TOOL_TIMEOUT = UNCENSORED_TOOL_TIMEOUT
OMEGA_VISION_MODEL = UNCENSORED_VISION_MODEL
OMEGA_VISION_BASE_URL = UNCENSORED_BASE_URL
OMEGA_VISION_TIMEOUT = UNCENSORED_VISION_TIMEOUT
OLLAMA_CHAT_MODEL = UNCENSORED_CHAT_MODEL
OMEGA_MODEL = UNCENSORED_TOOL_MODEL
OMEGA_BASE_URL = UNCENSORED_BASE_URL
OMEGA_TIMEOUT = UNCENSORED_TOOL_TIMEOUT

# Brave Search API
BRAVE_SEARCH_API_KEY = os.getenv("BRAVE_SEARCH_API_KEY", "")

# fal.ai API for image/video generation (Adult Mode)
FAL_KEY = os.getenv("FAL_KEY", "")

# Database settings
DATABASE_PATH = os.getenv("DATABASE_PATH", "brinchat.db")

# JWT Authentication settings
JWT_SECRET = os.getenv("JWT_SECRET", "change-this-in-production-use-a-long-random-string")
JWT_ALGORITHM = os.getenv("JWT_ALGORITHM", "HS256")
JWT_EXPIRE_MINUTES = int(os.getenv("JWT_EXPIRE_MINUTES", "1440"))  # 24 hours default

# Knowledge Base settings
KB_EMBEDDING_MODEL = os.getenv("KB_EMBEDDING_MODEL", "nomic-embed-text")
KB_CHUNK_SIZE = int(os.getenv("KB_CHUNK_SIZE", "512"))
KB_CHUNK_OVERLAP = int(os.getenv("KB_CHUNK_OVERLAP", "50"))

# CORS settings
CORS_ORIGINS = os.getenv("CORS_ORIGINS", "http://localhost:8080").split(",")

# Cookie security (set to true in production with HTTPS)
COOKIE_SECURE = os.getenv("COOKIE_SECURE", "false").lower() == "true"

# Trusted proxy configuration for correct client IP detection
# Set to comma-separated list of trusted proxy IPs (e.g., "127.0.0.1,10.0.0.1")
# When set, X-Forwarded-For header from these proxies will be trusted
TRUSTED_PROXIES = [p.strip() for p in os.getenv("TRUSTED_PROXIES", "").split(",") if p.strip()]

# Hugging Face settings (for video generation)
HF_TOKEN = os.getenv("HF_TOKEN", "")
VIDEO_GENERATION_SPACE = os.getenv("VIDEO_GENERATION_SPACE", "Heartsync/NSFW-Uncensored-video")

# Adult content passcode (MUST be set in environment for security)
ADULT_PASSCODE = os.getenv("ADULT_PASSCODE", "")

# Tool trigger scanner: when enabled, adult mode scans messages for tool-like requests
# (image generation, web search, etc.) and routes them to Brin instead of Lexi
TOOL_TRIGGER_ENABLED = os.getenv("TOOL_TRIGGER_ENABLED", "true").lower() == "true"

# Chat streaming limits
# Soft limits: Log warning but continue streaming (model may need extended thinking for complex problems)
THINKING_TOKEN_LIMIT_INITIAL = int(os.getenv("THINKING_TOKEN_LIMIT_INITIAL", "3000"))
THINKING_TOKEN_LIMIT_FOLLOWUP = int(os.getenv("THINKING_TOKEN_LIMIT_FOLLOWUP", "2000"))
# Hard limits: True runaway detection - break stream only at this threshold (10x soft limit)
THINKING_HARD_LIMIT_INITIAL = int(os.getenv("THINKING_HARD_LIMIT_INITIAL", "30000"))
THINKING_HARD_LIMIT_FOLLOWUP = int(os.getenv("THINKING_HARD_LIMIT_FOLLOWUP", "20000"))
CHAT_REQUEST_TIMEOUT = int(os.getenv("CHAT_REQUEST_TIMEOUT", "300"))  # 5 minutes

# Extraction model for async memory/profile updates (small, fast model)
# This model runs in background after responses to extract memories and profile updates
EXTRACTION_MODEL = os.getenv("EXTRACTION_MODEL", "qwen2.5-coder:3b")

# =============================================================================
# Voice Features (TTS/STT)
# =============================================================================

# Global enable/disable for voice features
VOICE_ENABLED = os.getenv("VOICE_ENABLED", "false").lower() == "true"

# TTS Configuration
# Available backends: qwen3 (high quality, GPU), edge (online, free), piper (offline, fast), coqui, kokoro
TTS_BACKEND = os.getenv("TTS_BACKEND", "edge")
TTS_MODEL = os.getenv("TTS_MODEL", "default")  # "default" uses backend's default, or specify model name
TTS_DEVICE = os.getenv("TTS_DEVICE", "cpu")  # "cuda:0", "cuda:1", or "cpu"

# STT Configuration
# Available backends: faster_whisper (recommended), whisper, vosk (offline)
STT_BACKEND = os.getenv("STT_BACKEND", "faster_whisper")
STT_MODEL = os.getenv("STT_MODEL", "small")  # whisper model size: tiny, base, small, medium, large
STT_DEVICE = os.getenv("STT_DEVICE", "cpu")

# Voice limits
VOICE_MAX_AUDIO_LENGTH = int(os.getenv("VOICE_MAX_AUDIO_LENGTH", "60"))  # seconds for STT
VOICE_MAX_TTS_LENGTH = int(os.getenv("VOICE_MAX_TTS_LENGTH", "5000"))  # characters for TTS

# Feature availability flags (based on API key presence)
WEB_SEARCH_AVAILABLE = bool(BRAVE_SEARCH_API_KEY)
VIDEO_GENERATION_AVAILABLE = bool(HF_TOKEN)
VOICE_AVAILABLE = VOICE_ENABLED  # Voice requires explicit enablement

# Conversations directory (for JSON file storage)
CONVERSATIONS_DIR = os.getenv("CONVERSATIONS_DIR", "conversations")

class AppSettings(BaseModel):
    persona: Optional[str] = None
    model: str = "openclaw:main"  # Claude via OpenClaw
    temperature: float = 0.7
    top_p: float = 0.9
    top_k: int = 40
    num_ctx: int = 4096
    repeat_penalty: float = 1.1
    # Context compaction settings
    compaction_enabled: bool = True
    compaction_buffer_percent: int = 15  # % of context reserved for summaries (5-30)
    compaction_threshold_percent: int = 70  # Trigger compaction at this % of active window (50-90)
    compaction_protected_messages: int = 6  # Recent messages never compacted (4-12)

def load_settings() -> AppSettings:
    if SETTINGS_FILE.exists():
        with open(SETTINGS_FILE) as f:
            return AppSettings(**json.load(f))
    return AppSettings()

def save_settings(settings: AppSettings):
    with open(SETTINGS_FILE, "w") as f:
        json.dump(settings.model_dump(), f, indent=2)

# Global settings instance
_settings: Optional[AppSettings] = None

def get_settings() -> AppSettings:
    global _settings
    if _settings is None:
        _settings = load_settings()
    return _settings

def update_settings(new_settings: AppSettings):
    global _settings
    _settings = new_settings
    save_settings(new_settings)
