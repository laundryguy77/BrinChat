"""Projects router for BrinBoard"""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from typing import Optional, Dict, List

from app.middleware.auth import require_auth
from app.models.auth_schemas import UserResponse
from app.services.board import project_service


router = APIRouter()


class ProjectCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    description: Optional[str] = None
    prompt: str = ""
    settings: Optional[Dict] = None


class ProjectUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=200)
    description: Optional[str] = None
    prompt: Optional[str] = None
    settings: Optional[Dict] = None


@router.get("")
async def list_projects(
    archived: int = 0,
    limit: int = 20,
    offset: int = 0,
    user: UserResponse = Depends(require_auth)
):
    """List user's projects"""
    try:
        return project_service.list_projects(user.id, archived, limit, offset)
    except Exception as e:
        raise HTTPException(status_code=500, detail={"error": "Failed to list projects", "detail": str(e)})


@router.post("", status_code=201)
async def create_project(
    data: ProjectCreate,
    user: UserResponse = Depends(require_auth)
):
    """Create a new project"""
    try:
        return project_service.create_project(
            owner_id=user.id,
            name=data.name,
            description=data.description,
            prompt=data.prompt,
            settings=data.settings
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail={"error": "Failed to create project", "detail": str(e)})


@router.get("/{project_id}")
async def get_project(
    project_id: str,
    user: UserResponse = Depends(require_auth)
):
    """Get project details"""
    project = project_service.get_project(project_id)
    if not project:
        raise HTTPException(status_code=404, detail={"error": "Project not found"})
    
    # Verify ownership
    if project['owner_id'] != user.id:
        raise HTTPException(status_code=403, detail={"error": "Not authorized"})
    
    return project


@router.patch("/{project_id}")
async def update_project(
    project_id: str,
    data: ProjectUpdate,
    user: UserResponse = Depends(require_auth)
):
    """Update project"""
    try:
        updates = data.dict(exclude_unset=True)
        return project_service.update_project(project_id, user.id, **updates)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail={"error": "Failed to update project", "detail": str(e)})


@router.delete("/{project_id}")
async def archive_project(
    project_id: str,
    user: UserResponse = Depends(require_auth)
):
    """Archive project"""
    try:
        return project_service.archive_project(project_id, user.id)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail={"error": "Failed to archive project", "detail": str(e)})


@router.get("/{project_id}/tasks")
async def get_project_tasks(
    project_id: str,
    limit: int = 100,
    offset: int = 0,
    user: UserResponse = Depends(require_auth)
):
    """List tasks in project"""
    try:
        # Verify project exists and user owns it
        project = project_service.get_project(project_id)
        if not project:
            raise HTTPException(status_code=404, detail={"error": "Project not found"})
        if project['owner_id'] != user.id:
            raise HTTPException(status_code=403, detail={"error": "Not authorized"})
        
        return project_service.get_project_tasks(project_id, limit, offset)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail={"error": "Failed to list tasks", "detail": str(e)})


@router.get("/{project_id}/hooks")
async def get_project_hooks(
    project_id: str,
    user: UserResponse = Depends(require_auth)
):
    """List hooks for project"""
    from app.services.board import hook_service
    
    try:
        # Verify project exists and user owns it
        project = project_service.get_project(project_id)
        if not project:
            raise HTTPException(status_code=404, detail={"error": "Project not found"})
        if project['owner_id'] != user.id:
            raise HTTPException(status_code=403, detail={"error": "Not authorized"})
        
        return hook_service.list_hooks(project_id=project_id)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail={"error": "Failed to list hooks", "detail": str(e)})


@router.post("/{project_id}/hooks", status_code=201)
async def create_project_hook(
    project_id: str,
    data: dict,
    user: UserResponse = Depends(require_auth)
):
    """Create hook for project"""
    from app.services.board import hook_service
    
    try:
        # Verify project exists and user owns it
        project = project_service.get_project(project_id)
        if not project:
            raise HTTPException(status_code=404, detail={"error": "Project not found"})
        if project['owner_id'] != user.id:
            raise HTTPException(status_code=403, detail={"error": "Not authorized"})
        
        return hook_service.create_hook(
            project_id=project_id,
            name=data.get('name'),
            event=data.get('event'),
            action_type=data.get('action_type'),
            action_data=data.get('action_data', {}),
            description=data.get('description'),
            enabled=data.get('enabled', 1),
            position=data.get('position', 0)
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail={"error": "Failed to create hook", "detail": str(e)})
