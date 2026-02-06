"""Models router.

Originally simplified for a Claude-only BrinChat setup.
Now supports selecting the OpenClaw agent model (e.g. openclaw:main) and
persisting that selection per-conversation.

Note: In OpenClaw mode, the "model" value primarily selects the agent (openclaw:<agentId>).
"""

from fastapi import APIRouter, Depends, HTTPException
from typing import Dict, Any, List
import logging

from app.config import get_settings, update_settings, CLAUDE_MODEL
from app.middleware.auth import require_auth, optional_auth
from app.models.auth_schemas import UserResponse
from app.services.claude_service import claude_service
from app.services.conversation_store import conversation_store

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/models", tags=["models"])


@router.get("")
async def list_models(user=Depends(optional_auth)) -> Dict[str, Any]:
    """List available models.

    In practice, BrinChat uses OpenClaw via its OpenAI-compatible endpoint.
    The model string typically selects the OpenClaw agent (openclaw:<agentId>).

    We keep this intentionally small and explicit.
    """
    settings = get_settings()

    # Minimal, explicit allowlist (deduped)
    candidates: List[str] = [
        settings.model,
        CLAUDE_MODEL,
        "openclaw:main",
        "openclaw",
    ]
    seen = set()
    model_ids = []
    for m in candidates:
        if m and m not in seen:
            seen.add(m)
            model_ids.append(m)

    def _model_family(mid: str) -> str:
        if mid.startswith("openclaw:") or mid == "openclaw":
            return "openclaw"
        return "other"

    models = []
    for mid in model_ids:
        family = _model_family(mid)
        models.append({
            "name": mid,
            "family": family,
            # Capabilities are best-effort here; OpenClaw may change underlying model.
            "capabilities": ["completion"],
            "supports_tools": False,
            "supports_vision": False,
            "supports_thinking": False,
        })

    return {
        "models": models,
        "current": settings.model,
        "adult_mode": False
    }


@router.post("/select")
async def select_model(request: dict, user: UserResponse = Depends(require_auth)) -> Dict[str, str]:
    """Select a model.

    Supports:
    - Setting the global default model (settings.json)
    - Optionally setting the model for a specific conversation

    Request body:
    {
      "model": "openclaw:main",
      "conversation_id": "abcd1234" (optional),
      "apply_default": true (optional, default true)
    }
    """
    model = (request or {}).get("model")
    if not isinstance(model, str) or not model.strip():
        raise HTTPException(status_code=400, detail="model is required")
    model = model.strip()

    apply_default = (request or {}).get("apply_default", True)
    conv_id = (request or {}).get("conversation_id")

    # Update global default
    if apply_default:
        settings = get_settings()
        settings.model = model
        update_settings(settings)

    # Update conversation model (owned by user)
    if conv_id:
        conv = conversation_store.get(conv_id, user_id=user.id)
        if not conv:
            raise HTTPException(status_code=404, detail="Conversation not found")
        await conversation_store.set_model(conv_id, model)

    return {"model": model, "status": "selected"}


@router.get("/current")
async def get_current_model() -> Dict[str, str]:
    """Get currently selected model (always Claude)."""
    return {"model": get_settings().model}


@router.get("/capabilities")
async def get_model_capabilities() -> Dict[str, Any]:
    """Get capabilities of Claude."""
    settings = get_settings()
    
    return {
        "model": settings.model,
        "capabilities": ["completion", "tools", "vision", "thinking"],
        "is_vision": True,
        "supports_tools": True,
        "supports_thinking": True,
        "tools": [
            {
                "id": "web_search",
                "name": "Web Search",
                "description": "Search the web for information",
                "icon": "search"
            },
            {
                "id": "browse_website",
                "name": "Browse Website",
                "description": "Visit and read a specific URL",
                "icon": "language"
            },
            {
                "id": "search_conversations",
                "name": "Search Conversations",
                "description": "Search past conversations",
                "icon": "history"
            },
            {
                "id": "search_knowledge_base",
                "name": "Knowledge Base",
                "description": "Search your uploaded documents",
                "icon": "folder_open"
            }
        ]
    }


@router.get("/usage")
async def get_usage_stats():
    """Get usage statistics - simplified for Claude."""
    return {
        "vram": {
            "used_mb": 0,
            "total_mb": 0,
            "percent": 0,
            "available": False  # Not applicable for API-based model
        }
    }


@router.get("/capabilities/{model_name:path}")
async def get_model_capabilities_endpoint(model_name: str):
    """Get comprehensive capabilities for Claude."""
    return await claude_service.get_comprehensive_capabilities(model_name)


@router.get("/tools")
async def list_available_tools(user: UserResponse = Depends(require_auth)) -> Dict[str, Any]:
    """List all available tools for Claude."""
    from app.tools.definitions import get_tools_for_model, ALL_TOOLS
    from app.services.feature_service import get_feature_service

    settings = get_settings()

    # Get all tools (Claude supports everything)
    filtered_tools = get_tools_for_model(
        supports_tools=True,
        supports_vision=True
    )

    # Filter tools based on user's feature flags
    if filtered_tools:
        feature_service = get_feature_service()
        filtered_tools = feature_service.filter_tools_for_user(filtered_tools, user.id)

    # Extract tool details for response
    tool_details = []
    for tool in filtered_tools:
        func = tool.get("function", {})
        tool_details.append({
            "name": func.get("name"),
            "description": func.get("description", "")[:100] + "..." if len(func.get("description", "")) > 100 else func.get("description", ""),
            "parameters": list(func.get("parameters", {}).get("properties", {}).keys())
        })

    return {
        "model": settings.model,
        "supports_tools": True,
        "supports_vision": True,
        "total_tools": len(filtered_tools),
        "builtin_tools": len(ALL_TOOLS),
        "mcp_tools": 0,  # MCP removed
        "tools": tool_details,
        "tool_names": [t["name"] for t in tool_details]
    }
