import logging
from typing import Optional
from fastapi import Depends, HTTPException, status, Request
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

from app.services.auth_service import get_auth_service, AuthService
from app.models.auth_schemas import UserResponse

logger = logging.getLogger(__name__)

# Optional bearer token security (doesn't fail if no token)
security = HTTPBearer(auto_error=False)


async def get_current_user(
    request: Request,
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security)
) -> Optional[UserResponse]:
    """
    Get the current authenticated user from JWT token.
    Returns None if no valid token is provided.
    """
    # Try to get token from Authorization header
    token = None
    if credentials:
        token = credentials.credentials

    # Also check for token in cookie (for browser sessions)
    if not token:
        token = request.cookies.get("access_token")

    if not token:
        return None

    auth_service = get_auth_service()
    payload = auth_service.decode_token(token)

    if not payload:
        return None

    user_id = int(payload.get("sub", 0))
    if not user_id:
        return None

    user = auth_service.get_user_by_id(user_id)
    return user


async def require_auth(
    user: Optional[UserResponse] = Depends(get_current_user)
) -> UserResponse:
    """
    Dependency that requires authentication.
    Raises 401 if not authenticated.
    Raises 403 if account is deactivated.
    """
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
            headers={"WWW-Authenticate": "Bearer"}
        )

    # Check if account is active
    if not user.is_active:
        logger.warning(f"Deactivated user {user.id} attempted access")
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Account deactivated"
        )

    return user


async def optional_auth(
    user: Optional[UserResponse] = Depends(get_current_user)
) -> Optional[UserResponse]:
    """
    Dependency that optionally gets the current user.
    Returns None if not authenticated (doesn't raise error).
    """
    return user
