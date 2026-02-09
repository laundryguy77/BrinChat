"""BrinBoard API routers"""
from fastapi import APIRouter

from .projects import router as projects_router
from .tasks import router as tasks_router
from .hooks import router as hooks_router
from .agents import router as agents_router
from .skills import router as skills_router
from .tags import router as tags_router
from .stats import router as stats_router

# Aggregate all board routers
board_router = APIRouter()

board_router.include_router(projects_router, prefix="/projects", tags=["board-projects"])
board_router.include_router(tasks_router, prefix="/tasks", tags=["board-tasks"])
board_router.include_router(hooks_router, prefix="/hooks", tags=["board-hooks"])
board_router.include_router(agents_router, prefix="/agents", tags=["board-agents"])
board_router.include_router(skills_router, prefix="/skills", tags=["board-skills"])
board_router.include_router(tags_router, prefix="/tags", tags=["board-tags"])
board_router.include_router(stats_router, prefix="/stats", tags=["board-stats"])
