"""Admin router for user management, feature flags, themes, and audit logging."""
import logging
from typing import Optional, List, Dict, Any
from fastapi import APIRouter, Depends, HTTPException, Request, Query
from pydantic import BaseModel, Field

from app.middleware.auth import require_auth
from app.models.auth_schemas import UserResponse
from app.services.database import get_database
from app.services.admin_service import get_admin_service
from app.services.feature_service import get_feature_service
from app.services.theme_service import get_theme_service
from app.services.stats_service import get_stats_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/admin", tags=["admin"])


# === Request/Response Models ===

class CreateUserRequest(BaseModel):
    username: str = Field(..., min_length=3, max_length=50)
    password: str = Field(..., min_length=12)
    is_admin: bool = False


class UpdateUserRequest(BaseModel):
    is_admin: Optional[bool] = None
    is_active: Optional[bool] = None
    mode_restriction: Optional[str] = None  # null, "normal_only", "no_full_unlock"


class ResetPasswordRequest(BaseModel):
    new_password: str = Field(..., min_length=12)


class UpdateFeatureRequest(BaseModel):
    enabled: bool


class SetUserFeatureRequest(BaseModel):
    enabled: Optional[bool] = None  # None = clear override


class CreateThemeRequest(BaseModel):
    name: str = Field(..., min_length=2, max_length=50, pattern=r'^[a-z0-9_-]+$')
    display_name: str = Field(..., min_length=2, max_length=100)
    description: Optional[str] = None
    css_variables: Dict[str, str]


class UpdateThemeRequest(BaseModel):
    display_name: Optional[str] = None
    description: Optional[str] = None
    css_variables: Optional[Dict[str, str]] = None
    is_enabled: Optional[bool] = None


# === Admin Dependency ===

async def require_admin(user: UserResponse = Depends(require_auth)) -> UserResponse:
    """Require admin status for access.

    Checks the is_admin column in the database.
    """
    db = get_database()
    row = db.fetchone("SELECT is_admin FROM users WHERE id = ?", (user.id,))

    if not row or not row["is_admin"]:
        logger.warning(f"Non-admin user {user.id} attempted admin access")
        raise HTTPException(status_code=403, detail="Admin access required")

    return user


def get_client_ip(request: Request) -> Optional[str]:
    """Get client IP from request headers."""
    # Check X-Forwarded-For first (for proxied requests)
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",")[0].strip()
    # Fall back to direct client
    if request.client:
        return request.client.host
    return None


# === User Management Endpoints ===

@router.get("/users")
async def list_users(
    request: Request,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    search: Optional[str] = None,
    include_inactive: bool = False,
    admin: UserResponse = Depends(require_admin)
):
    """List all users with pagination."""
    admin_service = get_admin_service()
    return admin_service.list_users(
        page=page,
        page_size=page_size,
        search=search,
        include_inactive=include_inactive
    )


@router.get("/users/{user_id}")
async def get_user(
    user_id: int,
    admin: UserResponse = Depends(require_admin)
):
    """Get detailed user information."""
    admin_service = get_admin_service()
    user = admin_service.get_user(user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    return user


@router.post("/users")
async def create_user(
    request: Request,
    data: CreateUserRequest,
    admin: UserResponse = Depends(require_admin)
):
    """Create a new user."""
    admin_service = get_admin_service()
    ip = get_client_ip(request)

    user = admin_service.create_user(
        admin_id=admin.id,
        username=data.username,
        password=data.password,
        is_admin=data.is_admin,
        ip_address=ip
    )

    if not user:
        raise HTTPException(status_code=400, detail="Username already exists")

    return user


@router.patch("/users/{user_id}")
async def update_user(
    request: Request,
    user_id: int,
    data: UpdateUserRequest,
    admin: UserResponse = Depends(require_admin)
):
    """Update user attributes."""
    # Prevent self-demotion
    if user_id == admin.id and data.is_admin is False:
        raise HTTPException(status_code=400, detail="Cannot remove your own admin status")

    admin_service = get_admin_service()
    ip = get_client_ip(request)

    updates = data.model_dump(exclude_unset=True)
    user = admin_service.update_user(
        user_id=user_id,
        admin_id=admin.id,
        updates=updates,
        ip_address=ip
    )

    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    return user


@router.delete("/users/{user_id}")
async def delete_user(
    request: Request,
    user_id: int,
    admin: UserResponse = Depends(require_admin)
):
    """Delete a user and all associated data."""
    # Prevent self-deletion
    if user_id == admin.id:
        raise HTTPException(status_code=400, detail="Cannot delete your own account")

    admin_service = get_admin_service()
    ip = get_client_ip(request)

    success = await admin_service.delete_user(
        user_id=user_id,
        admin_id=admin.id,
        ip_address=ip
    )

    if not success:
        raise HTTPException(status_code=404, detail="User not found")

    return {"status": "deleted", "user_id": user_id}


@router.post("/users/{user_id}/reset-password")
async def reset_password(
    request: Request,
    user_id: int,
    data: ResetPasswordRequest,
    admin: UserResponse = Depends(require_admin)
):
    """Reset a user's password."""
    admin_service = get_admin_service()
    ip = get_client_ip(request)

    success = admin_service.reset_password(
        user_id=user_id,
        admin_id=admin.id,
        new_password=data.new_password,
        ip_address=ip
    )

    if not success:
        raise HTTPException(status_code=404, detail="User not found")

    return {"status": "password_reset", "user_id": user_id}


# === Feature Flags Endpoints ===

@router.get("/features")
async def list_features(
    admin: UserResponse = Depends(require_admin)
):
    """List all feature flags with their settings."""
    admin_service = get_admin_service()
    return {"features": admin_service.list_feature_flags()}


@router.patch("/features/{feature_key}")
async def update_feature(
    request: Request,
    feature_key: str,
    data: UpdateFeatureRequest,
    admin: UserResponse = Depends(require_admin)
):
    """Update a feature flag's default enabled state."""
    admin_service = get_admin_service()
    ip = get_client_ip(request)

    feature = admin_service.update_feature_flag(
        admin_id=admin.id,
        feature_key=feature_key,
        enabled=data.enabled,
        ip_address=ip
    )

    if not feature:
        raise HTTPException(status_code=404, detail="Feature not found")

    return feature


@router.get("/users/{user_id}/features")
async def get_user_features(
    user_id: int,
    admin: UserResponse = Depends(require_admin)
):
    """Get effective feature settings for a user."""
    admin_service = get_admin_service()
    return {"features": admin_service.get_user_features(user_id)}


@router.put("/users/{user_id}/features/{feature_key}")
async def set_user_feature(
    request: Request,
    user_id: int,
    feature_key: str,
    data: SetUserFeatureRequest,
    admin: UserResponse = Depends(require_admin)
):
    """Set or clear a feature override for a user."""
    admin_service = get_admin_service()
    ip = get_client_ip(request)

    return {"features": admin_service.set_user_feature_override(
        admin_id=admin.id,
        user_id=user_id,
        feature_key=feature_key,
        enabled=data.enabled,
        ip_address=ip
    )}


# === Theme Endpoints ===

@router.get("/themes")
async def list_themes(
    include_disabled: bool = False,
    admin: UserResponse = Depends(require_admin)
):
    """List all themes."""
    theme_service = get_theme_service()
    return {"themes": theme_service.list_themes(include_disabled=include_disabled)}


@router.get("/themes/{theme_name}")
async def get_theme(
    theme_name: str,
    admin: UserResponse = Depends(require_admin)
):
    """Get a specific theme."""
    theme_service = get_theme_service()
    theme = theme_service.get_theme(theme_name)
    if not theme:
        raise HTTPException(status_code=404, detail="Theme not found")
    return theme


@router.post("/themes")
async def create_theme(
    data: CreateThemeRequest,
    admin: UserResponse = Depends(require_admin)
):
    """Create a new theme."""
    theme_service = get_theme_service()

    theme = theme_service.create_theme(
        name=data.name,
        display_name=data.display_name,
        css_variables=data.css_variables,
        description=data.description,
        created_by=admin.id
    )

    if not theme:
        raise HTTPException(status_code=400, detail="Theme name already exists")

    return theme


@router.patch("/themes/{theme_name}")
async def update_theme(
    theme_name: str,
    data: UpdateThemeRequest,
    admin: UserResponse = Depends(require_admin)
):
    """Update a theme."""
    theme_service = get_theme_service()

    updates = data.model_dump(exclude_unset=True)
    theme = theme_service.update_theme(theme_name, updates)

    if not theme:
        raise HTTPException(status_code=404, detail="Theme not found")

    return theme


@router.delete("/themes/{theme_name}")
async def delete_theme(
    request: Request,
    theme_name: str,
    admin: UserResponse = Depends(require_admin)
):
    """Delete a non-system theme."""
    theme_service = get_theme_service()
    ip = get_client_ip(request)

    # Check if it's a system theme first
    theme = theme_service.get_theme(theme_name)
    if theme and theme["is_system"]:
        raise HTTPException(status_code=400, detail="Cannot delete system themes")

    success = theme_service.delete_theme(
        theme_name=theme_name,
        admin_id=admin.id,
        ip_address=ip
    )

    if not success:
        raise HTTPException(status_code=404, detail="Theme not found")

    return {"status": "deleted", "theme_name": theme_name}


# === Dashboard Endpoints ===

@router.get("/dashboard")
async def get_dashboard(
    admin: UserResponse = Depends(require_admin)
):
    """Get system statistics for the admin dashboard."""
    stats_service = get_stats_service()
    return stats_service.get_dashboard_stats()


@router.get("/dashboard/trends")
async def get_trends(
    days: int = Query(30, ge=1, le=365),
    admin: UserResponse = Depends(require_admin)
):
    """Get activity trends over time."""
    stats_service = get_stats_service()
    return stats_service.get_activity_trends(days=days)


@router.get("/users/{user_id}/activity")
async def get_user_activity(
    user_id: int,
    admin: UserResponse = Depends(require_admin)
):
    """Get activity statistics for a specific user."""
    stats_service = get_stats_service()
    return stats_service.get_user_activity(user_id)


# === Audit Log Endpoints ===

@router.get("/audit-log")
async def get_audit_log(
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    admin_id: Optional[int] = None,
    action: Optional[str] = None,
    admin: UserResponse = Depends(require_admin)
):
    """Get audit log entries."""
    admin_service = get_admin_service()
    return admin_service.get_audit_log(
        page=page,
        page_size=page_size,
        admin_id=admin_id,
        action=action
    )
