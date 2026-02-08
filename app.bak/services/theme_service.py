"""Theme service for managing UI themes."""
import json
import logging
from datetime import datetime, timezone
from typing import Optional, Dict, Any, List
from app.services.database import get_database

logger = logging.getLogger(__name__)


class ThemeService:
    """Service for theme management."""

    def __init__(self):
        self.db = get_database()

    def list_themes(self, include_disabled: bool = False) -> List[Dict[str, Any]]:
        """Get all themes.

        Args:
            include_disabled: Whether to include disabled themes

        Returns:
            List of theme dicts
        """
        if include_disabled:
            rows = self.db.fetchall("""
                SELECT id, name, display_name, description, css_variables,
                       is_system, is_enabled, created_by, created_at, updated_at
                FROM themes
                ORDER BY is_system DESC, display_name
            """)
        else:
            rows = self.db.fetchall("""
                SELECT id, name, display_name, description, css_variables,
                       is_system, is_enabled, created_by, created_at, updated_at
                FROM themes
                WHERE is_enabled = 1
                ORDER BY is_system DESC, display_name
            """)

        themes = []
        for row in rows:
            try:
                css_vars = json.loads(row["css_variables"]) if row["css_variables"] else {}
            except json.JSONDecodeError:
                css_vars = {}

            themes.append({
                "id": row["id"],
                "name": row["name"],
                "display_name": row["display_name"],
                "description": row["description"],
                "css_variables": css_vars,
                "is_system": bool(row["is_system"]),
                "is_enabled": bool(row["is_enabled"]),
                "created_by": row["created_by"],
                "created_at": row["created_at"],
                "updated_at": row["updated_at"]
            })

        return themes

    def get_theme(self, theme_name: str) -> Optional[Dict[str, Any]]:
        """Get a specific theme by name.

        Args:
            theme_name: The theme's unique name

        Returns:
            Theme dict or None if not found
        """
        row = self.db.fetchone("""
            SELECT id, name, display_name, description, css_variables,
                   is_system, is_enabled, created_by, created_at, updated_at
            FROM themes
            WHERE name = ?
        """, (theme_name,))

        if not row:
            return None

        try:
            css_vars = json.loads(row["css_variables"]) if row["css_variables"] else {}
        except json.JSONDecodeError:
            css_vars = {}

        return {
            "id": row["id"],
            "name": row["name"],
            "display_name": row["display_name"],
            "description": row["description"],
            "css_variables": css_vars,
            "is_system": bool(row["is_system"]),
            "is_enabled": bool(row["is_enabled"]),
            "created_by": row["created_by"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"]
        }

    def create_theme(
        self,
        name: str,
        display_name: str,
        css_variables: Dict[str, str],
        description: Optional[str] = None,
        created_by: Optional[int] = None
    ) -> Optional[Dict[str, Any]]:
        """Create a new theme.

        Args:
            name: Unique theme identifier (lowercase, no spaces)
            display_name: Human-readable name
            css_variables: Dict of CSS variable names to values
            description: Optional description
            created_by: Admin user ID who created the theme

        Returns:
            Created theme dict or None if name exists
        """
        # Check if name exists
        existing = self.db.fetchone("SELECT id FROM themes WHERE name = ?", (name,))
        if existing:
            logger.warning(f"Theme with name '{name}' already exists")
            return None

        now = datetime.now(timezone.utc).isoformat()
        css_json = json.dumps(css_variables)

        self.db.execute("""
            INSERT INTO themes (name, display_name, description, css_variables,
                               is_system, is_enabled, created_by, created_at, updated_at)
            VALUES (?, ?, ?, ?, 0, 1, ?, ?, ?)
        """, (name, display_name, description, css_json, created_by, now, now))

        logger.info(f"Created theme '{name}' by admin {created_by}")
        return self.get_theme(name)

    def update_theme(
        self,
        theme_name: str,
        updates: Dict[str, Any]
    ) -> Optional[Dict[str, Any]]:
        """Update a theme.

        Args:
            theme_name: The theme to update
            updates: Dict of fields to update

        Returns:
            Updated theme dict or None if not found
        """
        theme = self.get_theme(theme_name)
        if not theme:
            return None

        # System themes can only have is_enabled updated
        if theme["is_system"]:
            allowed_fields = {"is_enabled"}
        else:
            allowed_fields = {"display_name", "description", "css_variables", "is_enabled"}

        # Filter to allowed fields
        update_fields = {k: v for k, v in updates.items() if k in allowed_fields}

        if not update_fields:
            return theme

        # Build update query
        set_clauses = []
        values = []

        for field, value in update_fields.items():
            set_clauses.append(f"{field} = ?")
            if field == "css_variables":
                values.append(json.dumps(value) if isinstance(value, dict) else value)
            elif field == "is_enabled":
                values.append(1 if value else 0)
            else:
                values.append(value)

        # Add updated_at
        now = datetime.now(timezone.utc).isoformat()
        set_clauses.append("updated_at = ?")
        values.append(now)

        values.append(theme_name)
        query = f"UPDATE themes SET {', '.join(set_clauses)} WHERE name = ?"

        self.db.execute(query, tuple(values))
        logger.info(f"Updated theme '{theme_name}': {list(update_fields.keys())}")

        return self.get_theme(theme_name)

    def delete_theme(
        self,
        theme_name: str,
        admin_id: int,
        ip_address: Optional[str] = None
    ) -> bool:
        """Delete a non-system theme.

        Args:
            theme_name: The theme to delete
            admin_id: Admin performing the deletion
            ip_address: Client IP for audit

        Returns:
            True if deleted, False if not found or is system theme
        """
        theme = self.get_theme(theme_name)
        if not theme:
            return False

        if theme["is_system"]:
            logger.warning(f"Attempted to delete system theme '{theme_name}'")
            return False

        self.db.execute("DELETE FROM themes WHERE name = ?", (theme_name,))

        # Audit log (using admin_service)
        from app.services.admin_service import get_admin_service
        admin_service = get_admin_service()
        admin_service._audit_log(
            admin_id=admin_id,
            action="delete_theme",
            target_type="theme",
            target_id=theme_name,
            details=json.dumps({"display_name": theme["display_name"]}),
            ip_address=ip_address
        )

        logger.info(f"Deleted theme '{theme_name}' by admin {admin_id}")
        return True

    def get_theme_css(self, theme_name: str) -> str:
        """Get CSS variable declarations for a theme.

        Args:
            theme_name: The theme name

        Returns:
            CSS string with :root variables
        """
        theme = self.get_theme(theme_name)
        if not theme:
            return ""

        css_vars = theme.get("css_variables", {})
        if not css_vars:
            return ""

        lines = [":root {"]
        for var_name, value in css_vars.items():
            lines.append(f"  {var_name}: {value};")
        lines.append("}")

        return "\n".join(lines)


# Global service instance
_theme_service: Optional[ThemeService] = None


def get_theme_service() -> ThemeService:
    """Get the global theme service instance."""
    global _theme_service
    if _theme_service is None:
        _theme_service = ThemeService()
    return _theme_service
