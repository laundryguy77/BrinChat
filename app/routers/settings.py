from fastapi import APIRouter, Depends
from typing import Dict, Any
from app.config import get_settings, update_settings, AppSettings
from app.models.schemas import SettingsUpdate
from app.middleware.auth import require_auth
from app.models.auth_schemas import UserResponse

router = APIRouter(prefix="/api/settings", tags=["settings"])


@router.get("")
async def get_all_settings(user: UserResponse = Depends(require_auth)) -> Dict[str, Any]:
    """Get all current settings. Requires authentication."""
    settings = get_settings()
    return settings.model_dump()


@router.put("")
async def update_all_settings(
    updates: SettingsUpdate,
    user: UserResponse = Depends(require_auth)
) -> Dict[str, Any]:
    """Update settings. Requires authentication."""
    current = get_settings()

    # Update only provided fields
    update_dict = updates.model_dump(exclude_unset=True)
    for key, value in update_dict.items():
        if value is not None:
            setattr(current, key, value)

    update_settings(current)

    return current.model_dump()


@router.get("/persona")
async def get_persona(user: UserResponse = Depends(require_auth)) -> Dict[str, Any]:
    """Get current persona. Requires authentication."""
    return {"persona": get_settings().persona}


@router.put("/persona")
async def update_persona(
    data: Dict[str, Any],
    user: UserResponse = Depends(require_auth)
) -> Dict[str, Any]:
    """Update persona. Requires authentication."""
    settings = get_settings()
    settings.persona = data.get("persona")
    update_settings(settings)
    return {"persona": settings.persona, "status": "updated"}
