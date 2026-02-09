"""Project service for BrinBoard"""
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


def create_project(owner_id: int, name: str, description: str = None, 
                   prompt: str = "", settings: Dict = None) -> Dict:
    """Create a new project"""
    db = get_database()
    project_id = str(uuid.uuid4())
    now = datetime.utcnow().isoformat() + "Z"
    
    settings_json = json.dumps(settings or {
        "priority": "medium",
        "max_subagents": 3,
        "timeout_seconds": 300,
        "auto_compact_threshold": 4000,
        "skills": [],
        "allowed_tools": None,
        "blocked_tools": None
    })
    
    db.execute("""
        INSERT INTO bb_projects (id, name, description, prompt, owner_id, settings, created_at, updated_at, archived)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0)
    """, (project_id, name, description, prompt, owner_id, settings_json, now, now))
    
    return get_project(project_id)


def get_project(project_id: str) -> Optional[Dict]:
    """Get project by ID with task and hook counts"""
    db = get_database()
    
    row = db.fetchone("""
        SELECT p.*, 
               COUNT(DISTINCT t.id) as task_count,
               COUNT(DISTINCT h.id) as hook_count
        FROM bb_projects p
        LEFT JOIN bb_tasks t ON t.project_id = p.id AND t.status != 'archived'
        LEFT JOIN bb_hooks h ON h.project_id = p.id
        WHERE p.id = ?
        GROUP BY p.id
    """, (project_id,))
    
    if not row:
        return None
    
    project = _row_to_dict(row)
    project['settings'] = json.loads(project.get('settings', '{}'))
    return project


def list_projects(owner_id: int, archived: int = 0, limit: int = 20, offset: int = 0) -> Dict:
    """List projects with pagination"""
    db = get_database()
    
    # Get total count
    count_row = db.fetchone(
        "SELECT COUNT(*) as total FROM bb_projects WHERE owner_id = ? AND archived = ?",
        (owner_id, archived)
    )
    total = count_row['total'] if count_row else 0
    
    # Get items
    rows = db.fetchall("""
        SELECT p.*, 
               COUNT(DISTINCT t.id) as task_count,
               COUNT(DISTINCT h.id) as hook_count
        FROM bb_projects p
        LEFT JOIN bb_tasks t ON t.project_id = p.id AND t.status != 'archived'
        LEFT JOIN bb_hooks h ON h.project_id = p.id
        WHERE p.owner_id = ? AND p.archived = ?
        GROUP BY p.id
        ORDER BY p.updated_at DESC
        LIMIT ? OFFSET ?
    """, (owner_id, archived, limit, offset))
    
    items = []
    for row in rows:
        project = _row_to_dict(row)
        project['settings'] = json.loads(project.get('settings', '{}'))
        items.append(project)
    
    return {"items": items, "total": total}


def update_project(project_id: str, owner_id: int, **updates) -> Dict:
    """Update project fields"""
    db = get_database()
    
    # Verify ownership
    existing = db.fetchone("SELECT owner_id FROM bb_projects WHERE id = ?", (project_id,))
    if not existing:
        raise HTTPException(status_code=404, detail="Project not found")
    if existing['owner_id'] != owner_id:
        raise HTTPException(status_code=403, detail="Not authorized")
    
    # Build update query
    allowed_fields = ['name', 'description', 'prompt', 'settings']
    set_parts = []
    params = []
    
    for field, value in updates.items():
        if field in allowed_fields and value is not None:
            if field == 'settings':
                value = json.dumps(value)
            set_parts.append(f"{field} = ?")
            params.append(value)
    
    if not set_parts:
        return get_project(project_id)
    
    now = datetime.utcnow().isoformat() + "Z"
    set_parts.append("updated_at = ?")
    params.append(now)
    params.append(project_id)
    
    db.execute(
        f"UPDATE bb_projects SET {', '.join(set_parts)} WHERE id = ?",
        tuple(params)
    )
    
    return get_project(project_id)


def archive_project(project_id: str, owner_id: int) -> Dict:
    """Archive (soft delete) a project"""
    db = get_database()
    
    # Verify ownership
    existing = db.fetchone("SELECT owner_id FROM bb_projects WHERE id = ?", (project_id,))
    if not existing:
        raise HTTPException(status_code=404, detail="Project not found")
    if existing['owner_id'] != owner_id:
        raise HTTPException(status_code=403, detail="Not authorized")
    
    now = datetime.utcnow().isoformat() + "Z"
    db.execute(
        "UPDATE bb_projects SET archived = 1, updated_at = ? WHERE id = ?",
        (now, project_id)
    )
    
    return {"message": "Project archived"}


def get_project_tasks(project_id: str, limit: int = 100, offset: int = 0) -> Dict:
    """List tasks in a project"""
    db = get_database()
    
    # Verify project exists
    if not db.fetchone("SELECT 1 FROM bb_projects WHERE id = ?", (project_id,)):
        raise HTTPException(status_code=404, detail="Project not found")
    
    # Get total count
    count_row = db.fetchone(
        "SELECT COUNT(*) as total FROM bb_tasks WHERE project_id = ? AND status != 'archived'",
        (project_id,)
    )
    total = count_row['total'] if count_row else 0
    
    # Get items
    rows = db.fetchall("""
        SELECT * FROM bb_tasks
        WHERE project_id = ? AND status != 'archived'
        ORDER BY position ASC, created_at DESC
        LIMIT ? OFFSET ?
    """, (project_id, limit, offset))
    
    items = [_row_to_dict(row) for row in rows]
    for item in items:
        item['settings'] = json.loads(item.get('settings', '{}'))
    
    return {"items": items, "total": total}
