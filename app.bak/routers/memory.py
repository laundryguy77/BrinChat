"""Memory management API endpoints."""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.middleware.auth import require_auth
from app.models.auth_schemas import UserResponse
from app.services.memory_service import get_memory_service

router = APIRouter(prefix="/api/memory", tags=["memory"])


class AddMemoryRequest(BaseModel):
    content: str
    category: str = "general"
    importance: int = 5


@router.get("")
async def list_memories(user: UserResponse = Depends(require_auth)):
    """List all memories for the authenticated user."""
    service = get_memory_service()
    memories = service.get_all_memories(user.id)
    stats = service.get_stats(user.id)
    return {"memories": memories, "stats": stats}


@router.post("")
async def add_memory(
    request: AddMemoryRequest,
    user: UserResponse = Depends(require_auth)
):
    """Manually add a memory (explicit source)."""
    service = get_memory_service()
    result = await service.add_memory(
        user_id=user.id,
        content=request.content,
        category=request.category,
        importance=request.importance,
        source="explicit"
    )
    if not result.get("success"):
        raise HTTPException(status_code=400, detail=result.get("error"))
    return result


@router.delete("/{memory_id}")
async def delete_memory(
    memory_id: str,
    user: UserResponse = Depends(require_auth)
):
    """Delete a specific memory."""
    service = get_memory_service()
    success = service.delete_memory(user.id, memory_id)
    if not success:
        raise HTTPException(status_code=404, detail="Memory not found")
    return {"success": True}


@router.delete("")
async def clear_all_memories(user: UserResponse = Depends(require_auth)):
    """Clear all memories for the user."""
    service = get_memory_service()
    count = service.clear_all_memories(user.id)
    return {"success": True, "deleted": count}


@router.get("/stats")
async def get_memory_stats(user: UserResponse = Depends(require_auth)):
    """Get memory statistics."""
    service = get_memory_service()
    return service.get_stats(user.id)
