"""Hooks router for BrinBoard"""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from typing import Optional, Dict

from app.middleware.auth import require_auth
from app.models.auth_schemas import UserResponse
from app.services.board import hook_service


router = APIRouter()


class HookUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=200)
    description: Optional[str] = None
    event: Optional[str] = None
    action_type: Optional[str] = None
    action_data: Optional[Dict] = None
    enabled: Optional[int] = None
    position: Optional[int] = None


@router.get("/{hook_id}")
async def get_hook(
    hook_id: str,
    user: UserResponse = Depends(require_auth)
):
    """Get hook details"""
    hook = hook_service.get_hook(hook_id)
    if not hook:
        raise HTTPException(status_code=404, detail={"error": "Hook not found"})
    
    return hook


@router.patch("/{hook_id}")
async def update_hook(
    hook_id: str,
    data: HookUpdate,
    user: UserResponse = Depends(require_auth)
):
    """Update hook"""
    try:
        updates = data.dict(exclude_unset=True)
        return hook_service.update_hook(hook_id, **updates)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail={"error": "Failed to update hook", "detail": str(e)})


@router.delete("/{hook_id}")
async def delete_hook(
    hook_id: str,
    user: UserResponse = Depends(require_auth)
):
    """Delete hook"""
    try:
        return hook_service.delete_hook(hook_id)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail={"error": "Failed to delete hook", "detail": str(e)})


@router.post("/{hook_id}/duplicate", status_code=201)
async def duplicate_hook(
    hook_id: str,
    user: UserResponse = Depends(require_auth)
):
    """Duplicate hook"""
    try:
        return hook_service.duplicate_hook(hook_id)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail={"error": "Failed to duplicate hook", "detail": str(e)})


@router.patch("/{hook_id}/toggle")
async def toggle_hook(
    hook_id: str,
    user: UserResponse = Depends(require_auth)
):
    """Toggle hook enabled state"""
    try:
        return hook_service.toggle_hook(hook_id)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail={"error": "Failed to toggle hook", "detail": str(e)})
