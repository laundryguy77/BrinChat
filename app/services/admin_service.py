"""Admin service for user management, feature flags, and audit logging."""
import json
import logging
from datetime import datetime, timezone
from typing import Optional, Dict, Any, List
from app.services.database import get_database
from app.services.auth_service import get_auth_service

logger = logging.getLogger(__name__)


class AdminService:
    """Service for admin operations."""

    def __init__(self):
        self.db = get_database()
        self.auth_service = get_auth_service()

    # === User Management ===

    def list_users(
        self,
        page: int = 1,
        page_size: int = 20,
        search: Optional[str] = None,
        include_inactive: bool = False
    ) -> Dict[str, Any]:
        """List users with pagination and optional search.

        Args:
            page: Page number (1-indexed)
            page_size: Number of users per page
            search: Optional search term for username/email
            include_inactive: Whether to include deactivated users

        Returns:
            Dict with users list and pagination info
        """
        offset = (page - 1) * page_size

        # Build WHERE clause
        conditions = []
        params = []

        if not include_inactive:
            conditions.append("(is_active = 1 OR is_active IS NULL)")

        if search:
            conditions.append("(username LIKE ? OR email LIKE ?)")
            params.extend([f"%{search}%", f"%{search}%"])

        where_clause = ""
        if conditions:
            where_clause = "WHERE " + " AND ".join(conditions)

        # Get total count
        count_query = f"SELECT COUNT(*) FROM users {where_clause}"
        count_row = self.db.fetchone(count_query, tuple(params))
        total = count_row[0] if count_row else 0

        # Get users
        query = f"""
            SELECT id, username, email, is_admin, is_active, mode_restriction, created_at
            FROM users
            {where_clause}
            ORDER BY created_at DESC
            LIMIT ? OFFSET ?
        """
        params.extend([page_size, offset])
        rows = self.db.fetchall(query, tuple(params))

        users = []
        for row in rows:
            users.append({
                "id": row["id"],
                "username": row["username"],
                "email": row["email"],
                "is_admin": bool(row["is_admin"]) if row["is_admin"] is not None else False,
                "is_active": bool(row["is_active"]) if row["is_active"] is not None else True,
                "mode_restriction": row["mode_restriction"],
                "created_at": row["created_at"]
            })

        return {
            "users": users,
            "total": total,
            "page": page,
            "page_size": page_size,
            "total_pages": (total + page_size - 1) // page_size
        }

    def get_user(self, user_id: int) -> Optional[Dict[str, Any]]:
        """Get detailed user information including profile data.

        Args:
            user_id: The user's ID

        Returns:
            User dict with profile data or None if not found
        """
        row = self.db.fetchone("""
            SELECT id, username, email, is_admin, is_active, mode_restriction, created_at
            FROM users WHERE id = ?
        """, (user_id,))

        if not row:
            return None

        user = {
            "id": row["id"],
            "username": row["username"],
            "email": row["email"],
            "is_admin": bool(row["is_admin"]) if row["is_admin"] is not None else False,
            "is_active": bool(row["is_active"]) if row["is_active"] is not None else True,
            "mode_restriction": row["mode_restriction"],
            "created_at": row["created_at"]
        }

        # Get profile data
        profile_row = self.db.fetchone("""
            SELECT profile_data, adult_mode_enabled, full_unlock_enabled
            FROM user_profiles WHERE user_id = ?
        """, (user_id,))

        if profile_row:
            try:
                profile_data = json.loads(profile_row["profile_data"]) if profile_row["profile_data"] else {}
                user["profile"] = {
                    "data": profile_data,
                    "adult_mode_enabled": bool(profile_row["adult_mode_enabled"]),
                    "full_unlock_enabled": bool(profile_row["full_unlock_enabled"])
                }
            except json.JSONDecodeError:
                user["profile"] = None
        else:
            user["profile"] = None

        # Get feature overrides for this user
        overrides = self.db.fetchall("""
            SELECT feature_key, enabled FROM user_feature_overrides WHERE user_id = ?
        """, (user_id,))
        user["feature_overrides"] = {row["feature_key"]: bool(row["enabled"]) for row in overrides}

        return user

    def update_user(
        self,
        user_id: int,
        admin_id: int,
        updates: Dict[str, Any],
        ip_address: Optional[str] = None
    ) -> Optional[Dict[str, Any]]:
        """Update user attributes.

        Args:
            user_id: The user to update
            admin_id: The admin performing the update
            updates: Dict of fields to update (is_admin, is_active, mode_restriction)
            ip_address: Optional IP for audit logging

        Returns:
            Updated user dict or None if not found
        """
        # Validate allowed fields
        allowed_fields = {"is_admin", "is_active", "mode_restriction"}
        update_fields = {k: v for k, v in updates.items() if k in allowed_fields}

        if not update_fields:
            return self.get_user(user_id)

        # Build update query
        set_clauses = []
        values = []
        for field, value in update_fields.items():
            set_clauses.append(f"{field} = ?")
            if field in ("is_admin", "is_active"):
                values.append(1 if value else 0)
            else:
                values.append(value)

        values.append(user_id)
        query = f"UPDATE users SET {', '.join(set_clauses)} WHERE id = ?"

        self.db.execute(query, tuple(values))

        # Audit log
        self._audit_log(
            admin_id=admin_id,
            action="update_user",
            target_type="user",
            target_id=str(user_id),
            details=json.dumps(update_fields),
            ip_address=ip_address
        )

        logger.info(f"Admin {admin_id} updated user {user_id}: {update_fields}")
        return self.get_user(user_id)

    def create_user(
        self,
        admin_id: int,
        username: str,
        password: str,
        is_admin: bool = False,
        ip_address: Optional[str] = None
    ) -> Optional[Dict[str, Any]]:
        """Create a new user.

        Args:
            admin_id: The admin creating the user
            username: New user's username
            password: New user's password
            is_admin: Whether the new user should be an admin
            ip_address: Optional IP for audit logging

        Returns:
            Created user dict or None if username exists
        """
        from app.models.auth_schemas import UserCreate

        # Use auth service to create user
        user_data = UserCreate(username=username, password=password)
        user = self.auth_service.create_user(user_data)

        if not user:
            return None

        # Update admin status if needed
        if is_admin:
            self.db.execute("UPDATE users SET is_admin = 1 WHERE id = ?", (user.id,))

        # Audit log
        self._audit_log(
            admin_id=admin_id,
            action="create_user",
            target_type="user",
            target_id=str(user.id),
            details=json.dumps({"username": username, "is_admin": is_admin}),
            ip_address=ip_address
        )

        logger.info(f"Admin {admin_id} created user {username} (id={user.id}, admin={is_admin})")
        return self.get_user(user.id)

    async def delete_user(
        self,
        user_id: int,
        admin_id: int,
        ip_address: Optional[str] = None
    ) -> bool:
        """Delete a user and all associated data.

        Args:
            user_id: The user to delete
            admin_id: The admin performing the deletion
            ip_address: Optional IP for audit logging

        Returns:
            True if deleted, False if user not found
        """
        # Get username for audit log before deletion
        user = self.get_user(user_id)
        if not user:
            return False

        username = user["username"]

        # Use auth service to delete (handles cleanup)
        await self.auth_service.delete_user(user_id)

        # Audit log
        self._audit_log(
            admin_id=admin_id,
            action="delete_user",
            target_type="user",
            target_id=str(user_id),
            details=json.dumps({"username": username}),
            ip_address=ip_address
        )

        logger.info(f"Admin {admin_id} deleted user {username} (id={user_id})")
        return True

    def reset_password(
        self,
        user_id: int,
        admin_id: int,
        new_password: str,
        ip_address: Optional[str] = None
    ) -> bool:
        """Reset a user's password.

        Args:
            user_id: The user whose password to reset
            admin_id: The admin performing the reset
            new_password: The new password
            ip_address: Optional IP for audit logging

        Returns:
            True if reset, False if user not found
        """
        user = self.db.fetchone("SELECT id, username FROM users WHERE id = ?", (user_id,))
        if not user:
            return False

        # Hash and update password
        password_hash = self.auth_service.hash_password(new_password)
        self.db.execute("UPDATE users SET password_hash = ? WHERE id = ?", (password_hash, user_id))

        # Audit log
        self._audit_log(
            admin_id=admin_id,
            action="reset_password",
            target_type="user",
            target_id=str(user_id),
            details=json.dumps({"username": user["username"]}),
            ip_address=ip_address
        )

        logger.info(f"Admin {admin_id} reset password for user {user['username']} (id={user_id})")
        return True

    # === Feature Flags ===

    def list_feature_flags(self) -> List[Dict[str, Any]]:
        """Get all feature flags with their settings.

        Returns:
            List of feature flag dicts
        """
        rows = self.db.fetchall("""
            SELECT id, feature_key, display_name, description, default_enabled, category,
                   created_at, updated_at
            FROM feature_flags
            ORDER BY category, display_name
        """)

        return [
            {
                "id": row["id"],
                "feature_key": row["feature_key"],
                "display_name": row["display_name"],
                "description": row["description"],
                "default_enabled": bool(row["default_enabled"]),
                "category": row["category"],
                "created_at": row["created_at"],
                "updated_at": row["updated_at"]
            }
            for row in rows
        ]

    def update_feature_flag(
        self,
        admin_id: int,
        feature_key: str,
        enabled: bool,
        ip_address: Optional[str] = None
    ) -> Optional[Dict[str, Any]]:
        """Update a feature flag's default enabled state.

        Args:
            admin_id: The admin making the change
            feature_key: The feature to update
            enabled: New default enabled state
            ip_address: Optional IP for audit logging

        Returns:
            Updated feature dict or None if not found
        """
        # Check if feature exists
        row = self.db.fetchone("SELECT id FROM feature_flags WHERE feature_key = ?", (feature_key,))
        if not row:
            return None

        now = datetime.now(timezone.utc).isoformat()
        self.db.execute("""
            UPDATE feature_flags
            SET default_enabled = ?, updated_at = ?
            WHERE feature_key = ?
        """, (1 if enabled else 0, now, feature_key))

        # Audit log
        self._audit_log(
            admin_id=admin_id,
            action="update_feature",
            target_type="feature",
            target_id=feature_key,
            details=json.dumps({"enabled": enabled}),
            ip_address=ip_address
        )

        logger.info(f"Admin {admin_id} set feature {feature_key} default_enabled={enabled}")

        # Return updated feature
        updated = self.db.fetchone("""
            SELECT id, feature_key, display_name, description, default_enabled, category,
                   created_at, updated_at
            FROM feature_flags WHERE feature_key = ?
        """, (feature_key,))

        return {
            "id": updated["id"],
            "feature_key": updated["feature_key"],
            "display_name": updated["display_name"],
            "description": updated["description"],
            "default_enabled": bool(updated["default_enabled"]),
            "category": updated["category"],
            "created_at": updated["created_at"],
            "updated_at": updated["updated_at"]
        } if updated else None

    def get_user_features(self, user_id: int) -> Dict[str, Any]:
        """Get effective feature settings for a user.

        Combines global defaults with per-user overrides.

        Args:
            user_id: The user to check

        Returns:
            Dict with feature settings
        """
        # Get all features
        features = self.db.fetchall("""
            SELECT feature_key, display_name, default_enabled, category
            FROM feature_flags
            ORDER BY category, display_name
        """)

        # Get user overrides
        overrides = self.db.fetchall("""
            SELECT feature_key, enabled FROM user_feature_overrides WHERE user_id = ?
        """, (user_id,))
        override_map = {row["feature_key"]: bool(row["enabled"]) for row in overrides}

        result = {}
        for feature in features:
            key = feature["feature_key"]
            default = bool(feature["default_enabled"])
            effective = override_map.get(key, default)
            result[key] = {
                "display_name": feature["display_name"],
                "category": feature["category"],
                "default": default,
                "override": override_map.get(key),
                "effective": effective
            }

        return result

    def set_user_feature_override(
        self,
        admin_id: int,
        user_id: int,
        feature_key: str,
        enabled: Optional[bool],
        ip_address: Optional[str] = None
    ) -> Dict[str, Any]:
        """Set or clear a feature override for a user.

        Args:
            admin_id: The admin making the change
            user_id: The user to update
            feature_key: The feature to override
            enabled: True/False to set override, None to clear override
            ip_address: Optional IP for audit logging

        Returns:
            Updated feature status for user
        """
        if enabled is None:
            # Clear override
            self.db.execute("""
                DELETE FROM user_feature_overrides
                WHERE user_id = ? AND feature_key = ?
            """, (user_id, feature_key))
            action = "clear_feature_override"
        else:
            # Set override
            now = datetime.now(timezone.utc).isoformat()
            self.db.execute("""
                INSERT INTO user_feature_overrides (user_id, feature_key, enabled, created_at, created_by)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(user_id, feature_key) DO UPDATE SET enabled = ?, created_at = ?, created_by = ?
            """, (user_id, feature_key, 1 if enabled else 0, now, admin_id,
                  1 if enabled else 0, now, admin_id))
            action = "set_feature_override"

        # Audit log
        self._audit_log(
            admin_id=admin_id,
            action=action,
            target_type="user_feature",
            target_id=f"{user_id}:{feature_key}",
            details=json.dumps({"user_id": user_id, "feature_key": feature_key, "enabled": enabled}),
            ip_address=ip_address
        )

        logger.info(f"Admin {admin_id} {action} for user {user_id}: {feature_key}={enabled}")
        return self.get_user_features(user_id)

    # === Audit Log ===

    def _audit_log(
        self,
        admin_id: int,
        action: str,
        target_type: str,
        target_id: Optional[str] = None,
        details: Optional[str] = None,
        ip_address: Optional[str] = None
    ) -> None:
        """Log an admin action.

        Args:
            admin_id: The admin performing the action
            action: Action type (e.g., 'create_user', 'update_feature')
            target_type: Type of target (e.g., 'user', 'feature')
            target_id: ID of the target
            details: JSON string with additional details
            ip_address: Client IP address
        """
        now = datetime.now(timezone.utc).isoformat()
        self.db.execute("""
            INSERT INTO admin_audit_log (admin_id, action, target_type, target_id, details, ip_address, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (admin_id, action, target_type, target_id, details, ip_address, now))

    def get_audit_log(
        self,
        page: int = 1,
        page_size: int = 50,
        admin_id: Optional[int] = None,
        action: Optional[str] = None
    ) -> Dict[str, Any]:
        """Get audit log entries with pagination.

        Args:
            page: Page number (1-indexed)
            page_size: Entries per page
            admin_id: Filter by specific admin
            action: Filter by action type

        Returns:
            Dict with audit entries and pagination info
        """
        offset = (page - 1) * page_size

        # Build WHERE clause
        conditions = []
        params = []

        if admin_id:
            conditions.append("l.admin_id = ?")
            params.append(admin_id)

        if action:
            conditions.append("l.action = ?")
            params.append(action)

        where_clause = ""
        if conditions:
            where_clause = "WHERE " + " AND ".join(conditions)

        # Get total count
        count_query = f"SELECT COUNT(*) FROM admin_audit_log l {where_clause}"
        count_row = self.db.fetchone(count_query, tuple(params))
        total = count_row[0] if count_row else 0

        # Get entries with admin username
        query = f"""
            SELECT l.id, l.admin_id, u.username as admin_username,
                   l.action, l.target_type, l.target_id, l.details,
                   l.ip_address, l.created_at
            FROM admin_audit_log l
            LEFT JOIN users u ON l.admin_id = u.id
            {where_clause}
            ORDER BY l.created_at DESC
            LIMIT ? OFFSET ?
        """
        params.extend([page_size, offset])
        rows = self.db.fetchall(query, tuple(params))

        entries = []
        for row in rows:
            try:
                details = json.loads(row["details"]) if row["details"] else None
            except json.JSONDecodeError:
                details = row["details"]

            entries.append({
                "id": row["id"],
                "admin_id": row["admin_id"],
                "admin_username": row["admin_username"],
                "action": row["action"],
                "target_type": row["target_type"],
                "target_id": row["target_id"],
                "details": details,
                "ip_address": row["ip_address"],
                "created_at": row["created_at"]
            })

        return {
            "entries": entries,
            "total": total,
            "page": page,
            "page_size": page_size,
            "total_pages": (total + page_size - 1) // page_size
        }


# Global service instance
_admin_service: Optional[AdminService] = None


def get_admin_service() -> AdminService:
    """Get the global admin service instance."""
    global _admin_service
    if _admin_service is None:
        _admin_service = AdminService()
    return _admin_service
