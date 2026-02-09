"""Tag service for BrinBoard"""
import uuid
from typing import Dict, List, Optional
from fastapi import HTTPException

from app.services.database import get_database


def _row_to_dict(row) -> Dict:
    """Convert sqlite3.Row to dict"""
    if row is None:
        return None
    return {key: row[key] for key in row.keys()}


def create_tag(name: str, color: str = "#3b82f6") -> Dict:
    """Create a new tag"""
    db = get_database()
    
    # Check if tag with this name already exists
    existing = db.fetchone("SELECT id FROM bb_tags WHERE name = ?", (name,))
    if existing:
        raise HTTPException(status_code=400, detail="Tag with this name already exists")
    
    tag_id = str(uuid.uuid4())
    
    db.execute(
        "INSERT INTO bb_tags (id, name, color) VALUES (?, ?, ?)",
        (tag_id, name, color)
    )
    
    row = db.fetchone("SELECT * FROM bb_tags WHERE id = ?", (tag_id,))
    return _row_to_dict(row)


def get_tag(tag_id: str) -> Optional[Dict]:
    """Get tag by ID"""
    db = get_database()
    
    row = db.fetchone("SELECT * FROM bb_tags WHERE id = ?", (tag_id,))
    if not row:
        return None
    
    return _row_to_dict(row)


def list_tags() -> List[Dict]:
    """List all tags"""
    db = get_database()
    
    rows = db.fetchall("SELECT * FROM bb_tags ORDER BY name ASC")
    return [_row_to_dict(r) for r in rows]


def delete_tag(tag_id: str) -> Dict:
    """Delete a tag (also removes from all tasks)"""
    db = get_database()
    
    if not db.fetchone("SELECT 1 FROM bb_tags WHERE id = ?", (tag_id,)):
        raise HTTPException(status_code=404, detail="Tag not found")
    
    # Delete tag (cascade will remove from task_tags)
    db.execute("DELETE FROM bb_tags WHERE id = ?", (tag_id,))
    
    return {"message": "Tag deleted"}
