import logging
import re
from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.base import BaseHTTPMiddleware
from pathlib import Path

from app import config


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Add security headers to all responses."""

    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)

        # Prevent clickjacking
        response.headers["X-Frame-Options"] = "DENY"

        # Prevent MIME type sniffing
        response.headers["X-Content-Type-Options"] = "nosniff"

        # Enable XSS filter in browsers that support it
        response.headers["X-XSS-Protection"] = "1; mode=block"

        # Control referrer information
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"

        # Prevent caching of sensitive responses (API endpoints and JS files)
        if request.url.path.startswith("/api/") or request.url.path.endswith(".js"):
            response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate"
            response.headers["Pragma"] = "no-cache"

        return response
from app.routers import admin, auth, chat, knowledge, models, settings, user_profile, voice
from app.routers.board import board_router
from app.services.claude_service import claude_service

logger = logging.getLogger(__name__)

# Get the project root directory
PROJECT_ROOT = Path(__file__).parent.parent

app = FastAPI(
    title="BrinChat",
    description="Chat with Claude via OpenClaw - an AI that can search the web",
    version="2.0.0"
)

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=config.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Security headers middleware
app.add_middleware(SecurityHeadersMiddleware)

# Dynamic cache-busting for ES module imports in app.js
# CRITICAL: This route must be registered BEFORE app.mount("/static", ...) or the
# static mount will swallow the request and this handler will never fire.
@app.get("/static/js/app.js")
async def serve_app_js():
    """Serve app.js with dynamic cache-busting for ES module imports."""
    import time as _time
    from fastapi.responses import Response
    js_path = PROJECT_ROOT / "static" / "js" / "app.js"
    content = js_path.read_text()
    cache_buster = str(int(_time.time()))
    # Rewrite all import version params so sub-modules are never stale
    content = re.sub(r'\?v=[^\'\"]+', f'?v={cache_buster}', content)
    return Response(content, media_type="application/javascript",
                    headers={"Cache-Control": "no-store"})

# Include routers (BEFORE static mounts)
app.include_router(admin.router)
app.include_router(auth.router)
app.include_router(chat.router)
app.include_router(knowledge.router)
app.include_router(models.router)
app.include_router(settings.router)
app.include_router(user_profile.router)
app.include_router(voice.router)
app.include_router(board_router, prefix="/api/board")

# Mount static files
app.mount("/static", StaticFiles(directory=PROJECT_ROOT / "static"), name="static")

# Mount avatars directory for serving generated avatar images
import os
avatars_dir = PROJECT_ROOT / "avatars"
os.makedirs(avatars_dir, exist_ok=True)
app.mount("/avatars", StaticFiles(directory=avatars_dir), name="avatars")

@app.get("/")
async def index():
    """Serve the main HTML page with dynamic cache-busting for JS assets."""
    import time as _time
    html_path = PROJECT_ROOT / "static" / "index.html"
    content = html_path.read_text()
    # Replace static version params with current timestamp so JS is never stale
    cache_buster = str(int(_time.time()))
    content = re.sub(r'(app\.js)\?v=[^"]+', rf'\1?v={cache_buster}', content)
    from fastapi.responses import HTMLResponse
    return HTMLResponse(content, headers={"Cache-Control": "no-store"})

@app.get("/health")
async def health_check():
    """Health check endpoint"""
    return {"status": "healthy"}


@app.websocket("/")
async def websocket_root(websocket: WebSocket):
    """
    WebSocket endpoint for the root path.
    
    Accepts connections from proxies (e.g., Apache) that attempt WebSocket upgrades.
    This prevents 403 errors in logs and can be used for future real-time features.
    
    Idle connections are closed after 5 minutes to prevent resource leaks.
    """
    import asyncio
    
    # Get client IP, respecting trusted proxies
    direct_ip = websocket.client.host if websocket.client else "unknown"
    client_ip = direct_ip
    
    # Trust X-Forwarded-For from configured trusted proxies
    if config.TRUSTED_PROXIES and direct_ip in config.TRUSTED_PROXIES:
        # Get the actual client IP from X-Forwarded-For header
        forwarded_for = websocket.headers.get("X-Forwarded-For")
        if forwarded_for:
            # X-Forwarded-For can be a comma-separated list; first one is the original client
            client_ip = forwarded_for.split(",")[0].strip()
    
    logger.debug(f"WebSocket connection attempt from {client_ip}")
    
    try:
        await websocket.accept()
        logger.info(f"WebSocket connected from {client_ip}")
        
        # Keep connection alive with idle timeout
        idle_timeout = 300  # 5 minutes
        while True:
            try:
                # Wait for incoming messages with timeout
                data = await asyncio.wait_for(
                    websocket.receive_text(),
                    timeout=idle_timeout
                )
                # Echo back or handle messages as needed
                await websocket.send_json({"status": "ok", "echo": data})
            except asyncio.TimeoutError:
                # Connection idle too long, close gracefully
                logger.debug(f"WebSocket idle timeout from {client_ip}")
                await websocket.close(code=1000, reason="Idle timeout")
                break
            except WebSocketDisconnect:
                logger.debug(f"WebSocket disconnected from {client_ip}")
                break
            except Exception as e:
                logger.debug(f"WebSocket error from {client_ip}: {e}")
                break
    except Exception as e:
        logger.debug(f"WebSocket failed to accept from {client_ip}: {e}")


@app.on_event("startup")
async def startup_security_check():
    """Verify security configuration on startup."""
    if config.JWT_SECRET == "change-this-in-production-use-a-long-random-string":
        logger.critical("SECURITY ERROR: Default JWT_SECRET detected! Set JWT_SECRET in .env to a secure random value (32+ characters).")
        raise RuntimeError("Application cannot start with default JWT_SECRET. Set JWT_SECRET environment variable.")

    if len(config.JWT_SECRET) < 32:
        logger.warning("SECURITY WARNING: JWT_SECRET is shorter than 32 characters. Consider using a longer secret.")

    # Feature availability warnings
    if not config.WEB_SEARCH_AVAILABLE:
        logger.warning("BRAVE_SEARCH_API_KEY not set - web search feature disabled")
    if not config.VIDEO_GENERATION_AVAILABLE:
        logger.warning("HF_TOKEN not set - video generation feature disabled")
    if not config.VOICE_AVAILABLE:
        logger.info("VOICE_ENABLED not set - voice features disabled")
    else:
        logger.info(f"Voice features enabled - TTS: {config.TTS_MODEL}, STT: whisper-{config.STT_MODEL}")

    # Start TTS cleanup task if voice is enabled
    if config.VOICE_ENABLED:
        from app.services.streaming_tts import start_cleanup_task
        start_cleanup_task()


@app.on_event("shutdown")
async def shutdown_cleanup():
    """Clean up resources on shutdown"""
    await claude_service.close()

    # Clean up voice services if enabled
    if config.VOICE_ENABLED:
        from app.services.tts_service import cleanup_tts_service
        from app.services.stt_service import cleanup_stt_service
        from app.services.streaming_tts import stop_cleanup_task
        await cleanup_tts_service()
        await cleanup_stt_service()
        stop_cleanup_task()
