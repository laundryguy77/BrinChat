"""Skills router for BrinBoard"""
from fastapi import APIRouter, Depends, HTTPException

from app.middleware.auth import require_auth
from app.models.auth_schemas import UserResponse
from app.services.board import skill_service


router = APIRouter()


@router.get("")
async def list_skills(user: UserResponse = Depends(require_auth)):
    """List available skills from SKILLS_DIR"""
    try:
        return {"items": skill_service.list_skills()}
    except Exception as e:
        raise HTTPException(status_code=500, detail={"error": "Failed to list skills", "detail": str(e)})


@router.get("/{name}")
async def get_skill(
    name: str,
    user: UserResponse = Depends(require_auth)
):
    """Get skill details"""
    try:
        return skill_service.get_skill(name)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail={"error": "Failed to get skill", "detail": str(e)})
