"""Agent service for BrinBoard"""
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


def register_agent(name: str, status: str = "idle", metadata: Dict = None) -> Dict:
    """Register or update an agent (upsert)"""
    db = get_database()
    
    # Check if agent exists
    existing = db.fetchone("SELECT id FROM bb_agents WHERE name = ?", (name,))
    
    if existing:
        # Update existing
        agent_id = existing['id']
        now = datetime.utcnow().isoformat() + "Z"
        metadata_json = json.dumps(metadata or {})
        
        db.execute("""
            UPDATE bb_agents 
            SET status = ?, last_seen = ?, metadata = ?
            WHERE id = ?
        """, (status, now, metadata_json, agent_id))
    else:
        # Create new
        agent_id = str(uuid.uuid4())
        now = datetime.utcnow().isoformat() + "Z"
        metadata_json = json.dumps(metadata or {})
        
        db.execute("""
            INSERT INTO bb_agents (id, name, status, health, last_seen, metadata, created_at)
            VALUES (?, ?, ?, 100, ?, ?, ?)
        """, (agent_id, name, status, now, metadata_json, now))
    
    return get_agent(agent_id)


def get_agent(agent_id: str) -> Optional[Dict]:
    """Get agent by ID"""
    db = get_database()
    
    row = db.fetchone("SELECT * FROM bb_agents WHERE id = ?", (agent_id,))
    if not row:
        return None
    
    agent = _row_to_dict(row)
    agent['metadata'] = json.loads(agent.get('metadata', '{}'))
    return agent


def get_agent_by_name(name: str) -> Optional[Dict]:
    """Get agent by name"""
    db = get_database()
    
    row = db.fetchone("SELECT * FROM bb_agents WHERE name = ?", (name,))
    if not row:
        return None
    
    agent = _row_to_dict(row)
    agent['metadata'] = json.loads(agent.get('metadata', '{}'))
    return agent


def list_agents(limit: int = 100, offset: int = 0) -> Dict:
    """List all agents"""
    db = get_database()
    
    # Get total count
    count_row = db.fetchone("SELECT COUNT(*) as total FROM bb_agents")
    total = count_row['total'] if count_row else 0
    
    # Get items
    rows = db.fetchall("""
        SELECT * FROM bb_agents
        ORDER BY last_seen DESC
        LIMIT ? OFFSET ?
    """, (limit, offset))
    
    items = []
    for row in rows:
        agent = _row_to_dict(row)
        agent['metadata'] = json.loads(agent.get('metadata', '{}'))
        items.append(agent)
    
    return {"items": items, "total": total}


def heartbeat(agent_name: str, status: str = "idle", metadata: Dict = None) -> Dict:
    """Agent heartbeat - update status and return assignment if idle"""
    # Register/update agent
    agent = register_agent(agent_name, status, metadata)
    
    # If agent is idle, check for pending assignments
    assignment = None
    if status == "idle":
        assignment = get_next_assignment(agent['id'])
    
    return {
        "agent_id": agent['id'],
        "status": "acknowledged",
        "assignment": assignment
    }


def get_next_assignment(agent_id: str) -> Optional[Dict]:
    """Get next pending task for agent"""
    db = get_database()
    
    # Find next idle task assigned to this agent
    task_row = db.fetchone("""
        SELECT * FROM bb_tasks
        WHERE assignee_id = ? AND status = 'idle' AND parent_id IS NULL
        ORDER BY priority DESC, created_at ASC
        LIMIT 1
    """, (agent_id,))
    
    if not task_row:
        return None
    
    task = _row_to_dict(task_row)
    
    # Get effective hooks (project-level + task-level)
    hooks = get_effective_hooks(task['project_id'], task['id'])
    
    # Get effective settings (project settings merged with task settings)
    settings = get_effective_settings(task['project_id'], task['id'])
    
    return {
        "task_id": task['id'],
        "project_id": task['project_id'],
        "title": task['title'],
        "prompt": task.get('prompt') or task.get('description', ''),
        "settings": settings,
        "effective_hooks": hooks,
        "skills": settings.get('skills', [])
    }


def get_assignment(agent_id: str) -> Optional[Dict]:
    """Get current assignment for agent"""
    db = get_database()
    
    agent = db.fetchone("SELECT current_task_id FROM bb_agents WHERE id = ?", (agent_id,))
    if not agent or not agent['current_task_id']:
        return None
    
    task_id = agent['current_task_id']
    task_row = db.fetchone("SELECT * FROM bb_tasks WHERE id = ?", (task_id,))
    
    if not task_row:
        return None
    
    task = _row_to_dict(task_row)
    
    # Get effective hooks and settings
    hooks = get_effective_hooks(task['project_id'], task['id'])
    settings = get_effective_settings(task['project_id'], task['id'])
    
    return {
        "task_id": task['id'],
        "project_id": task['project_id'],
        "title": task['title'],
        "prompt": task.get('prompt') or task.get('description', ''),
        "settings": settings,
        "effective_hooks": hooks,
        "skills": settings.get('skills', [])
    }


def get_effective_hooks(project_id: str = None, task_id: str = None) -> List[Dict]:
    """Get merged hooks from project and task"""
    db = get_database()
    
    hooks = []
    
    # Get project hooks
    if project_id:
        project_rows = db.fetchall("""
            SELECT * FROM bb_hooks
            WHERE project_id = ? AND enabled = 1
            ORDER BY position ASC
        """, (project_id,))
        
        for row in project_rows:
            hook = _row_to_dict(row)
            hook['action_data'] = json.loads(hook.get('action_data', '{}'))
            hooks.append(hook)
    
    # Get task hooks
    if task_id:
        task_rows = db.fetchall("""
            SELECT * FROM bb_hooks
            WHERE task_id = ? AND enabled = 1
            ORDER BY position ASC
        """, (task_id,))
        
        for row in task_rows:
            hook = _row_to_dict(row)
            hook['action_data'] = json.loads(hook.get('action_data', '{}'))
            hooks.append(hook)
    
    return hooks


def get_effective_settings(project_id: str = None, task_id: str = None) -> Dict:
    """Get merged settings from project and task (task overrides project)"""
    db = get_database()
    
    # Default settings
    settings = {
        "priority": "medium",
        "max_subagents": 3,
        "timeout_seconds": 300,
        "auto_compact_threshold": 4000,
        "skills": [],
        "allowed_tools": None,
        "blocked_tools": None
    }
    
    # Get project settings
    if project_id:
        project_row = db.fetchone("SELECT settings FROM bb_projects WHERE id = ?", (project_id,))
        if project_row:
            project_settings = json.loads(project_row['settings'] or '{}')
            settings.update(project_settings)
    
    # Get task settings (overrides project)
    if task_id:
        task_row = db.fetchone("SELECT settings FROM bb_tasks WHERE id = ?", (task_id,))
        if task_row:
            task_settings = json.loads(task_row['settings'] or '{}')
            # Merge, task overrides project
            for key, value in task_settings.items():
                if value is not None:
                    settings[key] = value
    
    return settings
