"""User profile router — stub endpoints (profile management moved to OpenClaw)."""

import logging
from typing import Dict, Any
from fastapi import APIRouter, Depends
from app.middleware.auth import require_auth
from app.models.auth_schemas import UserResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/profile", tags=["profile"])


@router.get("")
async def get_profile(user: UserResponse = Depends(require_auth)) -> Dict[str, Any]:
    """Get user profile — returns empty defaults (profile managed by OpenClaw)."""
    return {"success": True, "profile": {}, "message": "Profile management moved to OpenClaw"}


@router.put("")
async def update_profile(user: UserResponse = Depends(require_auth)) -> Dict[str, Any]:
    """Update profile — no-op (profile managed by OpenClaw)."""
    return {"success": True}


@router.delete("")
async def delete_profile(user: UserResponse = Depends(require_auth)) -> Dict[str, Any]:
    """Reset profile — no-op (profile managed by OpenClaw)."""
    return {"success": True}


@router.post("/sections/read")
async def read_sections(user: UserResponse = Depends(require_auth)) -> Dict[str, Any]:
    """Read profile sections — returns empty defaults."""
    return {"success": True, "sections": {}}


@router.get("/adult-mode/status")
async def get_adult_mode_status(user: UserResponse = Depends(require_auth)) -> Dict[str, Any]:
    """Adult mode status — always disabled (handled by OpenClaw)."""
    return {"enabled": False, "message": "Adult mode managed by OpenClaw"}


@router.post("/adult-mode/unlock")
async def unlock_adult_mode(user: UserResponse = Depends(require_auth)) -> Dict[str, Any]:
    """Adult mode unlock — disabled (handled by OpenClaw)."""
    return {"success": False, "message": "Adult mode managed by OpenClaw"}


@router.post("/adult-mode/disable")
async def disable_adult_mode(user: UserResponse = Depends(require_auth)) -> Dict[str, Any]:
    """Adult mode disable — no-op."""
    return {"success": True}


@router.post("/enable-section")
async def enable_section(user: UserResponse = Depends(require_auth)) -> Dict[str, Any]:
    """Enable profile section — no-op."""
    return {"success": True}


@router.post("/log-event")
async def log_event(user: UserResponse = Depends(require_auth)) -> Dict[str, Any]:
    """Log interaction event — no-op."""
    return {"success": True}


@router.get("/export")
async def export_profile(user: UserResponse = Depends(require_auth)) -> Dict[str, Any]:
    """Export profile — returns empty."""
    return {"success": True, "profile": {}}


@router.post("/export")
async def export_profile_full(user: UserResponse = Depends(require_auth)) -> Dict[str, Any]:
    """Export full profile — returns empty."""
    return {"success": True, "profile": {}}


@router.post("/onboarding/start")
async def start_onboarding(user: UserResponse = Depends(require_auth)) -> Dict[str, Any]:
    """Start onboarding — no-op."""
    return {"success": True, "questions": [], "total": 0}


@router.post("/onboarding/answer")
async def submit_onboarding(user: UserResponse = Depends(require_auth)) -> Dict[str, Any]:
    """Submit onboarding answer — no-op."""
    return {"success": True}
