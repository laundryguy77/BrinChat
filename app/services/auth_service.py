import logging
import glob
import os
import secrets
import hashlib
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional, Dict, Any, Tuple
from jose import JWTError, jwt
import bcrypt

from app.config import JWT_SECRET, JWT_ALGORITHM, JWT_EXPIRE_MINUTES
from app.services.database import get_database
from app.models.auth_schemas import UserCreate, UserResponse, UserSettings
from app.services.token_blacklist import get_token_blacklist

logger = logging.getLogger(__name__)


class AuthService:
    """Service for authentication operations"""

    def __init__(self):
        self.db = get_database()

    def verify_password(self, plain_password: str, hashed_password: str) -> bool:
        """Verify a password against its hash"""
        return bcrypt.checkpw(
            plain_password.encode('utf-8'),
            hashed_password.encode('utf-8')
        )

    def hash_password(self, password: str) -> str:
        """Hash a password"""
        salt = bcrypt.gensalt()
        hashed = bcrypt.hashpw(password.encode('utf-8'), salt)
        return hashed.decode('utf-8')

    def create_access_token(self, user_id: int, username: str) -> str:
        """Create a JWT access token with unique JTI for blacklisting support."""
        expire = datetime.now(timezone.utc) + timedelta(minutes=JWT_EXPIRE_MINUTES)
        jti = secrets.token_urlsafe(16)  # Unique token identifier
        to_encode = {
            "sub": str(user_id),
            "username": username,
            "exp": expire,
            "jti": jti
        }
        encoded_jwt = jwt.encode(to_encode, JWT_SECRET, algorithm=JWT_ALGORITHM)
        return encoded_jwt

    def decode_token(self, token: str, check_blacklist: bool = True) -> Optional[Dict[str, Any]]:
        """Decode and validate a JWT token.

        Args:
            token: The JWT token string
            check_blacklist: If True, also check if token is blacklisted

        Returns:
            Token payload dict if valid and not blacklisted, None otherwise
        """
        try:
            payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])

            # Check if token is blacklisted
            if check_blacklist:
                jti = payload.get("jti")
                if jti and get_token_blacklist().is_blacklisted(jti):
                    logger.debug("Token is blacklisted")
                    return None

            return payload
        except JWTError as e:
            logger.debug(f"Token decode error: {e}")
            return None

    def blacklist_token(self, token: str) -> bool:
        """Add a token to the blacklist.

        Args:
            token: The JWT token to blacklist

        Returns:
            True if successfully blacklisted, False if token invalid
        """
        try:
            # Decode without blacklist check to get JTI and expiry
            payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
            jti = payload.get("jti")
            if not jti:
                logger.debug("Token has no JTI, cannot blacklist")
                return False

            # Calculate remaining TTL
            exp = payload.get("exp")
            if exp:
                remaining_ttl = int(exp - datetime.now(timezone.utc).timestamp())
                if remaining_ttl > 0:
                    get_token_blacklist().add(jti, remaining_ttl)
                    return True
            return False
        except JWTError as e:
            logger.debug(f"Cannot blacklist token: {e}")
            return False

    @staticmethod
    def _hash_for_log(value: str) -> str:
        """Hash a value for safe logging."""
        return hashlib.sha256(value.encode()).hexdigest()[:12]

    def create_user(self, user_data: UserCreate) -> Optional[UserResponse]:
        """Create a new user"""
        # Check if username exists
        existing = self.db.fetchone(
            "SELECT id FROM users WHERE username = ?",
            (user_data.username,)
        )
        if existing:
            logger.warning(f"Registration failed: username hash {self._hash_for_log(user_data.username)} already exists")
            return None

        # Check if email exists (if provided)
        if user_data.email:
            existing_email = self.db.fetchone(
                "SELECT id FROM users WHERE email = ?",
                (user_data.email,)
            )
            if existing_email:
                logger.warning(f"Registration failed: email hash {self._hash_for_log(user_data.email)} already exists")
                return None

        # Hash password and create user
        password_hash = self.hash_password(user_data.password)
        created_at = datetime.now(timezone.utc).isoformat()

        cursor = self.db.execute(
            """INSERT INTO users (username, password_hash, email, created_at)
               VALUES (?, ?, ?, ?)""",
            (user_data.username, password_hash, user_data.email, created_at)
        )
        user_id = cursor.lastrowid

        # Create default user settings
        self.db.execute(
            "INSERT INTO user_settings (user_id) VALUES (?)",
            (user_id,)
        )

        logger.info(f"Created new user with id={user_id}")

        return UserResponse(
            id=user_id,
            username=user_data.username,
            email=user_data.email,
            created_at=created_at
        )

    def authenticate_user(self, username: str, password: str) -> Optional[UserResponse]:
        """Authenticate a user by username and password"""
        user = self.db.fetchone(
            "SELECT id, username, password_hash, email, created_at FROM users WHERE username = ?",
            (username,)
        )
        if not user:
            logger.debug(f"Authentication failed: user hash {self._hash_for_log(username)} not found")
            return None

        if not self.verify_password(password, user["password_hash"]):
            logger.debug(f"Authentication failed: invalid password for user id={user['id']}")
            return None

        return UserResponse(
            id=user["id"],
            username=user["username"],
            email=user["email"],
            created_at=user["created_at"]
        )

    def get_user_by_id(self, user_id: int) -> Optional[UserResponse]:
        """Get user by ID"""
        user = self.db.fetchone(
            "SELECT id, username, email, is_admin, is_active, created_at FROM users WHERE id = ?",
            (user_id,)
        )
        if not user:
            return None

        return UserResponse(
            id=user["id"],
            username=user["username"],
            email=user["email"],
            is_admin=bool(user["is_admin"]) if user["is_admin"] is not None else False,
            is_active=bool(user["is_active"]) if user["is_active"] is not None else True,
            created_at=user["created_at"]
        )

    def verify_user_password(self, user_id: int, password: str) -> bool:
        """Verify password for a user by ID.

        Args:
            user_id: The user's ID
            password: The password to verify

        Returns:
            True if password is correct, False otherwise
        """
        user = self.db.fetchone(
            "SELECT password_hash FROM users WHERE id = ?",
            (user_id,)
        )
        if not user:
            return False

        return self.verify_password(password, user["password_hash"])

    def change_password(self, user_id: int, current_password: str, new_password: str) -> bool:
        """Change user's password"""
        if not self.verify_user_password(user_id, current_password):
            return False

        new_hash = self.hash_password(new_password)
        self.db.execute(
            "UPDATE users SET password_hash = ? WHERE id = ?",
            (new_hash, user_id)
        )
        logger.info(f"Password changed for user {user_id}")
        return True

    def get_user_settings(self, user_id: int) -> Optional[UserSettings]:
        """Get user-specific settings"""
        settings = self.db.fetchone(
            """SELECT model, temperature, top_p, top_k, num_ctx, repeat_penalty, persona
               FROM user_settings WHERE user_id = ?""",
            (user_id,)
        )
        if not settings:
            return None

        return UserSettings(
            model=settings["model"],
            temperature=settings["temperature"],
            top_p=settings["top_p"],
            top_k=settings["top_k"],
            num_ctx=settings["num_ctx"],
            repeat_penalty=settings["repeat_penalty"],
            persona=settings["persona"]
        )

    # Whitelist of allowed column names for user_settings table
    _ALLOWED_SETTINGS_FIELDS = frozenset({
        "model", "temperature", "top_p", "top_k",
        "num_ctx", "repeat_penalty", "persona"
    })

    def update_user_settings(self, user_id: int, settings: UserSettings) -> bool:
        """Update user-specific settings"""
        # Build dynamic update query for non-None values
        updates = []
        values = []

        for field, value in settings.model_dump().items():
            if value is not None:
                # Validate field name against whitelist to prevent SQL injection
                if field not in self._ALLOWED_SETTINGS_FIELDS:
                    logger.warning(f"Rejecting invalid settings field: {field}")
                    continue
                updates.append(f"{field} = ?")
                values.append(value)

        if not updates:
            return True

        values.append(user_id)
        query = f"UPDATE user_settings SET {', '.join(updates)} WHERE user_id = ?"

        self.db.execute(query, tuple(values))
        return True

    async def delete_user(self, user_id: int) -> bool:
        """Delete a user and all their associated data.

        Cleans up:
        - Conversation JSON files
        - Avatar images
        - Session unlock state
        - User database record
        """
        from app.services.conversation_store import conversation_store
        from app.services.user_profile_store import get_user_profile_store

        # 1. Delete all conversations for this user
        try:
            deleted_convs = await conversation_store.delete_for_user(user_id)
            logger.info(f"Deleted {deleted_convs} conversations for user {user_id}")
        except Exception as e:
            logger.error(f"Error deleting conversations for user {user_id}: {e}")

        # 2. Delete avatar files matching avatars/{user_id}_*.png
        try:
            avatars_dir = Path("static/avatars")
            if avatars_dir.exists():
                avatar_pattern = f"{user_id}_*.png"
                deleted_avatars = 0
                for avatar_path in avatars_dir.glob(avatar_pattern):
                    avatar_path.unlink()
                    deleted_avatars += 1
                if deleted_avatars > 0:
                    logger.info(f"Deleted {deleted_avatars} avatar(s) for user {user_id}")
        except Exception as e:
            logger.error(f"Error deleting avatars for user {user_id}: {e}")

        # 3. Clear session unlock state
        try:
            profile_store = get_user_profile_store()
            cleared = profile_store.clear_user_sessions(user_id)
            if cleared > 0:
                logger.info(f"Cleared {cleared} session unlock(s) for user {user_id}")
        except Exception as e:
            logger.error(f"Error clearing sessions for user {user_id}: {e}")

        # 4. Delete user record from database
        self.db.execute("DELETE FROM users WHERE id = ?", (user_id,))
        logger.info(f"Deleted user {user_id} from database")

        return True


# Global service instance (initialized lazily)
_auth_service: Optional[AuthService] = None


def get_auth_service() -> AuthService:
    """Get the global auth service instance"""
    global _auth_service
    if _auth_service is None:
        _auth_service = AuthService()
    return _auth_service
