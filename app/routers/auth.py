import hashlib
import logging
from fastapi import APIRouter, HTTPException, status, Response, Depends, Request

from app import config
from app.models.auth_schemas import (
    UserCreate, UserLogin, UserResponse, TokenResponse,
    UserSettings, PasswordChange, AccountDelete
)
from app.services.auth_service import get_auth_service
from app.services.rate_limiter import get_login_limiter, get_register_limiter, get_refresh_limiter
from app.middleware.auth import require_auth

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/auth", tags=["auth"])


def _hash_for_log(value: str) -> str:
    """Hash a value for safe logging."""
    return hashlib.sha256(value.encode()).hexdigest()[:12]


def _get_client_ip(request: Request) -> str:
    """Get client IP, respecting trusted proxy configuration.

    If the request comes from a trusted proxy and X-Forwarded-For is present,
    use the first IP in the chain (original client). Otherwise use direct IP.
    """
    direct_ip = request.client.host if request.client else "unknown"

    # Only trust X-Forwarded-For from configured trusted proxies
    if config.TRUSTED_PROXIES and direct_ip in config.TRUSTED_PROXIES:
        forwarded_for = request.headers.get("X-Forwarded-For")
        if forwarded_for:
            # Take the first IP (original client)
            client_ip = forwarded_for.split(",")[0].strip()
            return client_ip

    return direct_ip


def _set_auth_cookie(response: Response, token: str) -> None:
    """Set authentication cookie with secure settings."""
    response.set_cookie(
        key="access_token",
        value=token,
        httponly=True,
        secure=config.COOKIE_SECURE,
        samesite="strict",  # Strict to prevent CSRF
        path="/",
        max_age=60 * 60 * 24  # 24 hours
    )


@router.post("/register", response_model=TokenResponse)
async def register(user_data: UserCreate, request: Request, response: Response):
    """Register a new user"""
    # Rate limiting by IP only for registration
    client_ip = _get_client_ip(request)

    limiter = get_register_limiter()
    allowed, retry_after = limiter.is_allowed(client_ip)

    if not allowed:
        response.headers["Retry-After"] = str(retry_after)
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"Too many registration attempts. Try again in {retry_after} seconds."
        )

    auth_service = get_auth_service()

    user = auth_service.create_user(user_data)
    if not user:
        # Record failed attempt (e.g., duplicate username)
        limiter.record_attempt(client_ip, success=False)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Username or email already exists"
        )

    # Record successful registration
    limiter.record_attempt(client_ip, success=True)

    # Create access token
    access_token = auth_service.create_access_token(user.id, user.username)

    # Set token in cookie for browser sessions
    _set_auth_cookie(response, access_token)

    return TokenResponse(
        access_token=access_token,
        user=user
    )


@router.post("/login", response_model=TokenResponse)
async def login(credentials: UserLogin, request: Request, response: Response):
    """Login with username and password"""
    # Rate limiting: use IP + username as key
    client_ip = _get_client_ip(request)
    rate_key = f"{client_ip}:{credentials.username}"

    limiter = get_login_limiter()
    allowed, retry_after = limiter.is_allowed(rate_key)

    if not allowed:
        response.headers["Retry-After"] = str(retry_after)
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"Too many login attempts. Try again in {retry_after} seconds."
        )

    auth_service = get_auth_service()
    user = auth_service.authenticate_user(credentials.username, credentials.password)

    if not user:
        # Record failed attempt
        limiter.record_attempt(rate_key, success=False)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid username or password"
        )

    # Record successful attempt (clears rate limit tracking)
    limiter.record_attempt(rate_key, success=True)

    # Create access token
    access_token = auth_service.create_access_token(user.id, user.username)

    # Set token in cookie for browser sessions
    _set_auth_cookie(response, access_token)

    logger.info(f"User logged in: id={user.id}")

    return TokenResponse(
        access_token=access_token,
        user=user
    )


@router.post("/logout")
async def logout(request: Request, response: Response):
    """Logout and clear session"""
    # Blacklist the current token to prevent reuse
    token = request.cookies.get("access_token")
    if token:
        auth_service = get_auth_service()
        auth_service.blacklist_token(token)

    response.delete_cookie(key="access_token", path="/", samesite="strict")
    return {"message": "Logged out successfully"}


@router.post("/refresh")
async def refresh_token(request: Request, response: Response):
    """Refresh the access token using existing valid token"""
    from jose import jwt, ExpiredSignatureError, JWTError

    # Rate limiting by IP for refresh
    client_ip = _get_client_ip(request)

    limiter = get_refresh_limiter()
    allowed, retry_after = limiter.is_allowed(client_ip)

    if not allowed:
        response.headers["Retry-After"] = str(retry_after)
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"Too many refresh attempts. Try again in {retry_after} seconds."
        )

    token = request.cookies.get("access_token")
    if not token:
        limiter.record_attempt(client_ip, success=False)
        raise HTTPException(status_code=401, detail="No token provided")

    auth_service = get_auth_service()

    try:
        # Decode and validate token (includes blacklist check)
        payload = auth_service.decode_token(token)
        if not payload:
            limiter.record_attempt(client_ip, success=False)
            raise HTTPException(status_code=401, detail="Invalid or blacklisted token")

        sub = payload.get("sub")
        if not sub:
            limiter.record_attempt(client_ip, success=False)
            raise HTTPException(status_code=401, detail="Invalid token: missing subject")

        user_id = int(sub)
        username = payload.get("username")

        # Blacklist old token before issuing new one
        auth_service.blacklist_token(token)

        # Issue new token with fresh expiration
        new_token = auth_service.create_access_token(user_id, username)

        # Set new token in cookie
        _set_auth_cookie(response, new_token)

        limiter.record_attempt(client_ip, success=True)
        return {"message": "Token refreshed"}
    except ExpiredSignatureError:
        limiter.record_attempt(client_ip, success=False)
        raise HTTPException(status_code=401, detail="Token expired - please login again")
    except JWTError:
        limiter.record_attempt(client_ip, success=False)
        raise HTTPException(status_code=401, detail="Invalid token")


@router.get("/me", response_model=UserResponse)
async def get_current_user_info(user: UserResponse = Depends(require_auth)):
    """Get current authenticated user info"""
    return user


@router.post("/change-password")
async def change_password(
    request: Request,
    password_data: PasswordChange,
    response: Response,
    user: UserResponse = Depends(require_auth)
):
    """Change user's password and invalidate current token"""
    auth_service = get_auth_service()

    success = auth_service.change_password(
        user.id,
        password_data.current_password,
        password_data.new_password
    )

    if not success:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Current password is incorrect"
        )

    # Blacklist current token after password change
    token = request.cookies.get("access_token")
    if token:
        auth_service.blacklist_token(token)

    # Issue new token
    new_token = auth_service.create_access_token(user.id, user.username)
    _set_auth_cookie(response, new_token)

    logger.info(f"Password changed for user id={user.id}")
    return {"message": "Password changed successfully"}


@router.get("/settings", response_model=UserSettings)
async def get_settings(user: UserResponse = Depends(require_auth)):
    """Get user-specific settings"""
    auth_service = get_auth_service()
    settings = auth_service.get_user_settings(user.id)

    if not settings:
        return UserSettings()

    return settings


@router.put("/settings")
async def update_settings(
    settings: UserSettings,
    user: UserResponse = Depends(require_auth)
):
    """Update user-specific settings"""
    auth_service = get_auth_service()
    auth_service.update_user_settings(user.id, settings)
    return {"message": "Settings updated successfully"}


@router.delete("/account")
async def delete_account(
    delete_data: AccountDelete,
    request: Request,
    response: Response,
    user: UserResponse = Depends(require_auth)
):
    """Delete user account and all associated data.

    Requires password confirmation for security.
    """
    auth_service = get_auth_service()

    # Verify password before deletion
    if not auth_service.verify_user_password(user.id, delete_data.password):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Password is incorrect"
        )

    # Blacklist current token
    token = request.cookies.get("access_token")
    if token:
        auth_service.blacklist_token(token)

    await auth_service.delete_user(user.id)
    response.delete_cookie(key="access_token", path="/", samesite="strict")

    logger.info(f"Account deleted: user id={user.id}")
    return {"message": "Account deleted successfully"}
