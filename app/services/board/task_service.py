"""Task service for BrinBoard"""
import json
import uuid
from datetime import datetime
from typing import Optional, Dict, List
from fastapi import HTTPException

from app.services.database import get_database


def _row_to_dict(row) -> Dict:
    """Convert sqlite3.Row to dict"""
    if row is None:
        return None
    return {key: row[key] for key in row.keys()}


def create_task(title: str, description: str = None, prompt: str = None,
                project_id: str = None, parent_id: str = None, assignee_id: str = None,
                status: str = "idle", priority: str = "medium", settings: Dict = None,
                due_date: str = None) -> Dict:
    """Create a new task"""
    db = get_database()
    task_id = str(uuid.uuid4())
    now = datetime.utcnow().isoformat() + "Z"
    
    settings_json = json.dumps(settings or {})
    
    db.execute("""
        INSERT INTO bb_tasks 
        (id, title, description, prompt, project_id, parent_id, assignee_id, 
         status, priority, position, settings, due_date, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?, ?, ?)
    """, (task_id, title, description, prompt, project_id, parent_id, assignee_id,
          status, priority, settings_json, due_date, now, now))
    
    return get_task(task_id)


def get_task(task_id: str) -> Optional[Dict]:
    """Get task by ID with subtasks, attachments, comments, and tags"""
    db = get_database()
    
    # Get task
    row = db.fetchone("SELECT * FROM bb_tasks WHERE id = ?", (task_id,))
    if not row:
        return None
    
    task = _row_to_dict(row)
    task['settings'] = json.loads(task.get('settings', '{}'))
    
    # Get subtasks
    subtask_rows = db.fetchall(
        "SELECT * FROM bb_tasks WHERE parent_id = ? ORDER BY position ASC",
        (task_id,)
    )
    task['subtasks'] = [_row_to_dict(r) for r in subtask_rows]
    
    # Get attachments
    attachment_rows = db.fetchall(
        "SELECT * FROM bb_attachments WHERE task_id = ? ORDER BY created_at DESC",
        (task_id,)
    )
    task['attachments'] = [_row_to_dict(r) for r in attachment_rows]
    
    # Get comments
    comment_rows = db.fetchall(
        "SELECT * FROM bb_comments WHERE task_id = ? ORDER BY created_at ASC",
        (task_id,)
    )
    task['comments'] = [_row_to_dict(r) for r in comment_rows]
    
    # Get tags
    tag_rows = db.fetchall("""
        SELECT t.* FROM bb_tags t
        JOIN bb_task_tags tt ON tt.tag_id = t.id
        WHERE tt.task_id = ?
    """, (task_id,))
    task['tags'] = [_row_to_dict(r) for r in tag_rows]
    
    return task


def list_tasks(status: str = None, project_id: str = None, assignee_id: str = None,
               limit: int = 20, offset: int = 0) -> Dict:
    """List tasks with filters and pagination"""
    db = get_database()
    
    # Build query
    where_parts = ["parent_id IS NULL"]  # Only top-level tasks
    params = []
    
    if status:
        where_parts.append("status = ?")
        params.append(status)
    else:
        where_parts.append("status != 'archived'")
    
    if project_id:
        where_parts.append("project_id = ?")
        params.append(project_id)
    
    if assignee_id:
        where_parts.append("assignee_id = ?")
        params.append(assignee_id)
    
    where_clause = " AND ".join(where_parts)
    
    # Get total count
    count_row = db.fetchone(
        f"SELECT COUNT(*) as total FROM bb_tasks WHERE {where_clause}",
        tuple(params)
    )
    total = count_row['total'] if count_row else 0
    
    # Get items
    rows = db.fetchall(f"""
        SELECT * FROM bb_tasks
        WHERE {where_clause}
        ORDER BY position ASC, created_at DESC
        LIMIT ? OFFSET ?
    """, tuple(params + [limit, offset]))
    
    items = []
    for row in rows:
        task = _row_to_dict(row)
        task['settings'] = json.loads(task.get('settings', '{}'))
        items.append(task)
    
    return {"items": items, "total": total}


def update_task(task_id: str, **updates) -> Dict:
    """Update task fields"""
    db = get_database()
    
    # Verify task exists
    if not db.fetchone("SELECT 1 FROM bb_tasks WHERE id = ?", (task_id,)):
        raise HTTPException(status_code=404, detail="Task not found")
    
    # Build update query
    allowed_fields = ['title', 'description', 'prompt', 'status', 'priority', 
                     'position', 'settings', 'due_date', 'assignee_id', 'project_id']
    set_parts = []
    params = []
    
    for field, value in updates.items():
        if field in allowed_fields and value is not None:
            if field == 'settings':
                value = json.dumps(value)
            set_parts.append(f"{field} = ?")
            params.append(value)
    
    if not set_parts:
        return get_task(task_id)
    
    now = datetime.utcnow().isoformat() + "Z"
    set_parts.append("updated_at = ?")
    params.append(now)
    params.append(task_id)
    
    db.execute(
        f"UPDATE bb_tasks SET {', '.join(set_parts)} WHERE id = ?",
        tuple(params)
    )
    
    return get_task(task_id)


def archive_task(task_id: str) -> Dict:
    """Archive (soft delete) a task"""
    db = get_database()
    
    if not db.fetchone("SELECT 1 FROM bb_tasks WHERE id = ?", (task_id,)):
        raise HTTPException(status_code=404, detail="Task not found")
    
    now = datetime.utcnow().isoformat() + "Z"
    db.execute(
        "UPDATE bb_tasks SET status = 'archived', updated_at = ? WHERE id = ?",
        (now, task_id)
    )
    
    return {"message": "Task archived"}


def create_subtask(parent_id: str, title: str, description: str = None) -> Dict:
    """Create a subtask under a parent task"""
    db = get_database()
    
    # Verify parent exists
    parent = db.fetchone("SELECT project_id FROM bb_tasks WHERE id = ?", (parent_id,))
    if not parent:
        raise HTTPException(status_code=404, detail="Parent task not found")
    
    return create_task(
        title=title,
        description=description,
        parent_id=parent_id,
        project_id=parent['project_id']
    )


def move_task(task_id: str, status: str = None, position: int = None, 
              project_id: str = None) -> Dict:
    """Move task (change status, position, or project)"""
    updates = {}
    if status is not None:
        updates['status'] = status
    if position is not None:
        updates['position'] = position
    if project_id is not None:
        updates['project_id'] = project_id
    
    return update_task(task_id, **updates)


def assign_task(task_id: str, assignee_id: str) -> Dict:
    """Assign task to an agent"""
    return update_task(task_id, assignee_id=assignee_id)


def add_attachment(task_id: str, filename: str, filepath: str, mime_type: str = None,
                   size_bytes: int = 0, uploaded_by: int = None) -> Dict:
    """Add attachment to task"""
    db = get_database()
    
    # Verify task exists
    if not db.fetchone("SELECT 1 FROM bb_tasks WHERE id = ?", (task_id,)):
        raise HTTPException(status_code=404, detail="Task not found")
    
    attachment_id = str(uuid.uuid4())
    now = datetime.utcnow().isoformat() + "Z"
    
    db.execute("""
        INSERT INTO bb_attachments 
        (id, task_id, filename, filepath, mime_type, size_bytes, uploaded_by, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """, (attachment_id, task_id, filename, filepath, mime_type, size_bytes, uploaded_by, now))
    
    row = db.fetchone("SELECT * FROM bb_attachments WHERE id = ?", (attachment_id,))
    return _row_to_dict(row)


def add_comment(task_id: str, content: str, user_id: int = None, agent_id: str = None) -> Dict:
    """Add comment to task"""
    db = get_database()
    
    # Verify task exists
    if not db.fetchone("SELECT 1 FROM bb_tasks WHERE id = ?", (task_id,)):
        raise HTTPException(status_code=404, detail="Task not found")
    
    comment_id = str(uuid.uuid4())
    now = datetime.utcnow().isoformat() + "Z"
    
    db.execute("""
        INSERT INTO bb_comments (id, task_id, user_id, agent_id, content, created_at)
        VALUES (?, ?, ?, ?, ?, ?)
    """, (comment_id, task_id, user_id, agent_id, content, now))
    
    row = db.fetchone("SELECT * FROM bb_comments WHERE id = ?", (comment_id,))
    return _row_to_dict(row)


def list_comments(task_id: str) -> List[Dict]:
    """List comments for a task"""
    db = get_database()
    
    # Verify task exists
    if not db.fetchone("SELECT 1 FROM bb_tasks WHERE id = ?", (task_id,)):
        raise HTTPException(status_code=404, detail="Task not found")
    
    rows = db.fetchall(
        "SELECT * FROM bb_comments WHERE task_id = ? ORDER BY created_at ASC",
        (task_id,)
    )
    return [_row_to_dict(r) for r in rows]


def add_tag_to_task(task_id: str, tag_id: str) -> Dict:
    """Add tag to task"""
    db = get_database()
    
    # Verify task and tag exist
    if not db.fetchone("SELECT 1 FROM bb_tasks WHERE id = ?", (task_id,)):
        raise HTTPException(status_code=404, detail="Task not found")
    if not db.fetchone("SELECT 1 FROM bb_tags WHERE id = ?", (tag_id,)):
        raise HTTPException(status_code=404, detail="Tag not found")
    
    try:
        db.execute(
            "INSERT INTO bb_task_tags (task_id, tag_id) VALUES (?, ?)",
            (task_id, tag_id)
        )
    except Exception:
        # Already exists, ignore
        pass
    
    return {"message": "Tag added"}


def remove_tag_from_task(task_id: str, tag_id: str) -> Dict:
    """Remove tag from task"""
    db = get_database()
    
    db.execute(
        "DELETE FROM bb_task_tags WHERE task_id = ? AND tag_id = ?",
        (task_id, tag_id)
    )
    
    return {"message": "Tag removed"}
