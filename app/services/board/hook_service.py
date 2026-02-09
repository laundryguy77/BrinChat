"""Hook service for BrinBoard"""
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


def create_hook(name: str, event: str, action_type: str, action_data: Dict,
                project_id: str = None, task_id: str = None, description: str = None,
                enabled: int = 1, position: int = 0) -> Dict:
    """Create a new hook"""
    db = get_database()
    
    if not project_id and not task_id:
        raise HTTPException(status_code=400, detail="Must specify project_id or task_id")
    
    hook_id = str(uuid.uuid4())
    now = datetime.utcnow().isoformat() + "Z"
    action_data_json = json.dumps(action_data or {})
    
    db.execute("""
        INSERT INTO bb_hooks 
        (id, project_id, task_id, name, description, event, action_type, action_data, enabled, position, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (hook_id, project_id, task_id, name, description, event, action_type, 
          action_data_json, enabled, position, now, now))
    
    return get_hook(hook_id)


def get_hook(hook_id: str) -> Optional[Dict]:
    """Get hook by ID"""
    db = get_database()
    
    row = db.fetchone("SELECT * FROM bb_hooks WHERE id = ?", (hook_id,))
    if not row:
        return None
    
    hook = _row_to_dict(row)
    hook['action_data'] = json.loads(hook.get('action_data', '{}'))
    return hook


def list_hooks(project_id: str = None, task_id: str = None, limit: int = 100, offset: int = 0) -> Dict:
    """List hooks with filters"""
    db = get_database()
    
    # Build query
    where_parts = []
    params = []
    
    if project_id:
        where_parts.append("project_id = ?")
        params.append(project_id)
    
    if task_id:
        where_parts.append("task_id = ?")
        params.append(task_id)
    
    where_clause = " AND ".join(where_parts) if where_parts else "1=1"
    
    # Get total count
    count_row = db.fetchone(
        f"SELECT COUNT(*) as total FROM bb_hooks WHERE {where_clause}",
        tuple(params)
    )
    total = count_row['total'] if count_row else 0
    
    # Get items
    rows = db.fetchall(f"""
        SELECT * FROM bb_hooks
        WHERE {where_clause}
        ORDER BY position ASC, created_at ASC
        LIMIT ? OFFSET ?
    """, tuple(params + [limit, offset]))
    
    items = []
    for row in rows:
        hook = _row_to_dict(row)
        hook['action_data'] = json.loads(hook.get('action_data', '{}'))
        items.append(hook)
    
    return {"items": items, "total": total}


def update_hook(hook_id: str, **updates) -> Dict:
    """Update hook fields"""
    db = get_database()
    
    # Verify hook exists
    if not db.fetchone("SELECT 1 FROM bb_hooks WHERE id = ?", (hook_id,)):
        raise HTTPException(status_code=404, detail="Hook not found")
    
    # Build update query
    allowed_fields = ['name', 'description', 'event', 'action_type', 'action_data', 
                     'enabled', 'position']
    set_parts = []
    params = []
    
    for field, value in updates.items():
        if field in allowed_fields and value is not None:
            if field == 'action_data':
                value = json.dumps(value)
            set_parts.append(f"{field} = ?")
            params.append(value)
    
    if not set_parts:
        return get_hook(hook_id)
    
    now = datetime.utcnow().isoformat() + "Z"
    set_parts.append("updated_at = ?")
    params.append(now)
    params.append(hook_id)
    
    db.execute(
        f"UPDATE bb_hooks SET {', '.join(set_parts)} WHERE id = ?",
        tuple(params)
    )
    
    return get_hook(hook_id)


def delete_hook(hook_id: str) -> Dict:
    """Delete hook (hard delete)"""
    db = get_database()
    
    if not db.fetchone("SELECT 1 FROM bb_hooks WHERE id = ?", (hook_id,)):
        raise HTTPException(status_code=404, detail="Hook not found")
    
    db.execute("DELETE FROM bb_hooks WHERE id = ?", (hook_id,))
    
    return {"message": "Hook deleted"}


def toggle_hook(hook_id: str) -> Dict:
    """Toggle hook enabled state"""
    db = get_database()
    
    hook = db.fetchone("SELECT enabled FROM bb_hooks WHERE id = ?", (hook_id,))
    if not hook:
        raise HTTPException(status_code=404, detail="Hook not found")
    
    new_enabled = 0 if hook['enabled'] else 1
    now = datetime.utcnow().isoformat() + "Z"
    
    db.execute(
        "UPDATE bb_hooks SET enabled = ?, updated_at = ? WHERE id = ?",
        (new_enabled, now, hook_id)
    )
    
    return get_hook(hook_id)


def duplicate_hook(hook_id: str) -> Dict:
    """Duplicate an existing hook"""
    db = get_database()
    
    original = db.fetchone("SELECT * FROM bb_hooks WHERE id = ?", (hook_id,))
    if not original:
        raise HTTPException(status_code=404, detail="Hook not found")
    
    new_hook_id = str(uuid.uuid4())
    now = datetime.utcnow().isoformat() + "Z"
    new_name = f"{original['name']} (Copy)"
    
    db.execute("""
        INSERT INTO bb_hooks 
        (id, project_id, task_id, name, description, event, action_type, action_data, enabled, position, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (new_hook_id, original['project_id'], original['task_id'], new_name, 
          original['description'], original['event'], original['action_type'],
          original['action_data'], original['enabled'], original['position'], now, now))
    
    return get_hook(new_hook_id)
