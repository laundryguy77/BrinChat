"""Tags router for BrinBoard"""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.middleware.auth import require_auth
from app.models.auth_schemas import UserResponse
from app.services.board import tag_service


router = APIRouter()


class TagCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=50)
    color: str = "#3b82f6"


@router.get("")
async def list_tags(user: UserResponse = Depends(require_auth)):
    """List all tags"""
    try:
        return {"items": tag_service.list_tags()}
    except Exception as e:
        raise HTTPException(status_code=500, detail={"error": "Failed to list tags", "detail": str(e)})


@router.post("", status_code=201)
async def create_tag(
    data: TagCreate,
    user: UserResponse = Depends(require_auth)
):
    """Create a new tag"""
    try:
        return tag_service.create_tag(data.name, data.color)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail={"error": "Failed to create tag", "detail": str(e)})


@router.delete("/{tag_id}")
async def delete_tag(
    tag_id: str,
    user: UserResponse = Depends(require_auth)
):
    """Delete tag"""
    try:
        return tag_service.delete_tag(tag_id)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail={"error": "Failed to delete tag", "detail": str(e)})
