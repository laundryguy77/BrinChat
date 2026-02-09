"""Agents router for BrinBoard"""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from typing import Optional, Dict

from app.middleware.auth import require_auth
from app.models.auth_schemas import UserResponse
from app.services.board import agent_service


router = APIRouter()


class AgentRegister(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    status: str = "idle"
    metadata: Optional[Dict] = None


class AgentHeartbeat(BaseModel):
    status: str = "idle"
    metadata: Optional[Dict] = None


@router.get("")
async def list_agents(
    limit: int = 100,
    offset: int = 0,
    user: UserResponse = Depends(require_auth)
):
    """List all agents"""
    try:
        return agent_service.list_agents(limit, offset)
    except Exception as e:
        raise HTTPException(status_code=500, detail={"error": "Failed to list agents", "detail": str(e)})


@router.post("/register", status_code=201)
async def register_agent(
    data: AgentRegister,
    user: UserResponse = Depends(require_auth)
):
    """Register or update agent (upsert)"""
    try:
        return agent_service.register_agent(
            name=data.name,
            status=data.status,
            metadata=data.metadata
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail={"error": "Failed to register agent", "detail": str(e)})


@router.get("/{agent_id}")
async def get_agent(
    agent_id: str,
    user: UserResponse = Depends(require_auth)
):
    """Get agent details"""
    agent = agent_service.get_agent(agent_id)
    if not agent:
        raise HTTPException(status_code=404, detail={"error": "Agent not found"})
    
    return agent


@router.post("/{agent_name}/heartbeat")
async def agent_heartbeat(
    agent_name: str,
    data: AgentHeartbeat,
    user: UserResponse = Depends(require_auth)
):
    """Agent heartbeat - update status and get assignment"""
    try:
        return agent_service.heartbeat(
            agent_name=agent_name,
            status=data.status,
            metadata=data.metadata
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail={"error": "Failed to process heartbeat", "detail": str(e)})


@router.get("/{agent_id}/assignment")
async def get_assignment(
    agent_id: str,
    user: UserResponse = Depends(require_auth)
):
    """Get current assignment for agent"""
    try:
        assignment = agent_service.get_assignment(agent_id)
        if not assignment:
            return {"assignment": None}
        
        return {"assignment": assignment}
    except Exception as e:
        raise HTTPException(status_code=500, detail={"error": "Failed to get assignment", "detail": str(e)})
