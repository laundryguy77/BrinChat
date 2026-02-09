"""Stats router for BrinBoard dashboard"""
from fastapi import APIRouter, Depends, HTTPException

from app.middleware.auth import require_auth
from app.models.auth_schemas import UserResponse
from app.services.database import get_database


router = APIRouter()


@router.get("")
async def get_stats(user: UserResponse = Depends(require_auth)):
    """Get dashboard statistics"""
    try:
        db = get_database()
        
        # Project count
        project_row = db.fetchone(
            "SELECT COUNT(*) as count FROM bb_projects WHERE owner_id = ? AND archived = 0",
            (user.id,)
        )
        project_count = project_row['count'] if project_row else 0
        
        # Task counts by status
        idle_row = db.fetchone(
            "SELECT COUNT(*) as count FROM bb_tasks WHERE status = 'idle' AND parent_id IS NULL"
        )
        active_row = db.fetchone(
            "SELECT COUNT(*) as count FROM bb_tasks WHERE status = 'active' AND parent_id IS NULL"
        )
        input_needed_row = db.fetchone(
            "SELECT COUNT(*) as count FROM bb_tasks WHERE status = 'user_input_needed' AND parent_id IS NULL"
        )
        finished_row = db.fetchone(
            "SELECT COUNT(*) as count FROM bb_tasks WHERE status = 'finished' AND parent_id IS NULL"
        )
        
        # Agent stats
        agent_count_row = db.fetchone("SELECT COUNT(*) as count FROM bb_agents")
        agent_count = agent_count_row['count'] if agent_count_row else 0
        
        # Average agent health
        health_row = db.fetchone("SELECT AVG(health) as avg_health FROM bb_agents")
        avg_health = int(health_row['avg_health']) if health_row and health_row['avg_health'] else 100
        
        return {
            "project_count": project_count,
            "tasks": {
                "idle": idle_row['count'] if idle_row else 0,
                "active": active_row['count'] if active_row else 0,
                "user_input_needed": input_needed_row['count'] if input_needed_row else 0,
                "finished": finished_row['count'] if finished_row else 0
            },
            "agent_count": agent_count,
            "agent_health_avg": avg_health
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail={"error": "Failed to get stats", "detail": str(e)})
