"""Tasks router for BrinBoard"""
import os
from pathlib import Path
from fastapi import APIRouter, Depends, HTTPException, File, UploadFile, Form
from pydantic import BaseModel, Field
from typing import Optional, Dict

from app.middleware.auth import require_auth
from app.models.auth_schemas import UserResponse
from app.services.board import task_service


router = APIRouter()


class TaskCreate(BaseModel):
    title: str = Field(..., min_length=1, max_length=500)
    description: Optional[str] = None
    prompt: Optional[str] = None
    project_id: Optional[str] = None
    parent_id: Optional[str] = None
    assignee_id: Optional[str] = None
    status: str = "idle"
    priority: str = "medium"
    settings: Optional[Dict] = None
    due_date: Optional[str] = None


class TaskUpdate(BaseModel):
    title: Optional[str] = Field(None, min_length=1, max_length=500)
    description: Optional[str] = None
    prompt: Optional[str] = None
    status: Optional[str] = None
    priority: Optional[str] = None
    position: Optional[int] = None
    settings: Optional[Dict] = None
    due_date: Optional[str] = None
    assignee_id: Optional[str] = None
    project_id: Optional[str] = None


class SubtaskCreate(BaseModel):
    title: str = Field(..., min_length=1, max_length=500)
    description: Optional[str] = None


class TaskMove(BaseModel):
    status: Optional[str] = None
    position: Optional[int] = None
    project_id: Optional[str] = None


class TaskAssign(BaseModel):
    assignee_id: str


class CommentCreate(BaseModel):
    content: str = Field(..., min_length=1)


class TagAdd(BaseModel):
    tag_id: str


@router.get("")
async def list_tasks(
    status: Optional[str] = None,
    project_id: Optional[str] = None,
    assignee_id: Optional[str] = None,
    limit: int = 20,
    offset: int = 0,
    user: UserResponse = Depends(require_auth)
):
    """List tasks with filters"""
    try:
        return task_service.list_tasks(status, project_id, assignee_id, limit, offset)
    except Exception as e:
        raise HTTPException(status_code=500, detail={"error": "Failed to list tasks", "detail": str(e)})


@router.post("", status_code=201)
async def create_task(
    data: TaskCreate,
    user: UserResponse = Depends(require_auth)
):
    """Create a new task"""
    try:
        return task_service.create_task(
            title=data.title,
            description=data.description,
            prompt=data.prompt,
            project_id=data.project_id,
            parent_id=data.parent_id,
            assignee_id=data.assignee_id,
            status=data.status,
            priority=data.priority,
            settings=data.settings,
            due_date=data.due_date
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail={"error": "Failed to create task", "detail": str(e)})


@router.get("/{task_id}")
async def get_task(
    task_id: str,
    user: UserResponse = Depends(require_auth)
):
    """Get task details"""
    task = task_service.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail={"error": "Task not found"})
    
    return task


@router.patch("/{task_id}")
async def update_task(
    task_id: str,
    data: TaskUpdate,
    user: UserResponse = Depends(require_auth)
):
    """Update task"""
    try:
        updates = data.dict(exclude_unset=True)
        return task_service.update_task(task_id, **updates)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail={"error": "Failed to update task", "detail": str(e)})


@router.delete("/{task_id}")
async def archive_task(
    task_id: str,
    user: UserResponse = Depends(require_auth)
):
    """Archive task"""
    try:
        return task_service.archive_task(task_id)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail={"error": "Failed to archive task", "detail": str(e)})


@router.post("/{task_id}/subtasks", status_code=201)
async def create_subtask(
    task_id: str,
    data: SubtaskCreate,
    user: UserResponse = Depends(require_auth)
):
    """Create subtask"""
    try:
        return task_service.create_subtask(task_id, data.title, data.description)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail={"error": "Failed to create subtask", "detail": str(e)})


@router.patch("/{task_id}/move")
async def move_task(
    task_id: str,
    data: TaskMove,
    user: UserResponse = Depends(require_auth)
):
    """Move task (change status, position, or project)"""
    try:
        return task_service.move_task(
            task_id,
            status=data.status,
            position=data.position,
            project_id=data.project_id
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail={"error": "Failed to move task", "detail": str(e)})


@router.post("/{task_id}/assign")
async def assign_task(
    task_id: str,
    data: TaskAssign,
    user: UserResponse = Depends(require_auth)
):
    """Assign task to agent"""
    try:
        return task_service.assign_task(task_id, data.assignee_id)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail={"error": "Failed to assign task", "detail": str(e)})


@router.post("/{task_id}/attachments", status_code=201)
async def upload_attachment(
    task_id: str,
    file: UploadFile = File(...),
    user: UserResponse = Depends(require_auth)
):
    """Upload attachment to task"""
    try:
        # Create uploads directory if it doesn't exist
        uploads_dir = Path(os.getenv('BB_UPLOADS_DIR', './data/bb_uploads'))
        uploads_dir.mkdir(parents=True, exist_ok=True)
        
        # Save file
        import uuid
        file_id = str(uuid.uuid4())
        file_ext = Path(file.filename).suffix
        filename = file.filename
        filepath = uploads_dir / f"{file_id}{file_ext}"
        
        with filepath.open('wb') as f:
            content = await file.read()
            f.write(content)
        
        # Add attachment record
        return task_service.add_attachment(
            task_id=task_id,
            filename=filename,
            filepath=str(filepath),
            mime_type=file.content_type,
            size_bytes=len(content),
            uploaded_by=user.id
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail={"error": "Failed to upload attachment", "detail": str(e)})


@router.get("/{task_id}/comments")
async def list_comments(
    task_id: str,
    user: UserResponse = Depends(require_auth)
):
    """List comments for task"""
    try:
        return {"items": task_service.list_comments(task_id)}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail={"error": "Failed to list comments", "detail": str(e)})


@router.post("/{task_id}/comments", status_code=201)
async def add_comment(
    task_id: str,
    data: CommentCreate,
    user: UserResponse = Depends(require_auth)
):
    """Add comment to task"""
    try:
        return task_service.add_comment(task_id, data.content, user_id=user.id)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail={"error": "Failed to add comment", "detail": str(e)})


@router.post("/{task_id}/tags")
async def add_tag(
    task_id: str,
    data: TagAdd,
    user: UserResponse = Depends(require_auth)
):
    """Add tag to task"""
    try:
        return task_service.add_tag_to_task(task_id, data.tag_id)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail={"error": "Failed to add tag", "detail": str(e)})


@router.delete("/{task_id}/tags/{tag_id}")
async def remove_tag(
    task_id: str,
    tag_id: str,
    user: UserResponse = Depends(require_auth)
):
    """Remove tag from task"""
    try:
        return task_service.remove_tag_from_task(task_id, tag_id)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail={"error": "Failed to remove tag", "detail": str(e)})
