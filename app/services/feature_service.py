"""Feature service for checking feature flags and gating tools."""
import logging
from typing import Optional, Set, List
from app.services.database import get_database

logger = logging.getLogger(__name__)

# Mapping of feature flags to tool names
FEATURE_TO_TOOLS = {
    "web_search": ["web_search"],
    "browse_website": ["browse_website"],
    "memory_system": ["add_memory", "query_memory"],
    "knowledge_base": ["search_knowledge", "add_to_knowledge"],
    "image_generation": ["image"],
    "video_generation": ["video"],
}

# All tools that require specific feature flags
GATED_TOOLS = set()
for tools in FEATURE_TO_TOOLS.values():
    GATED_TOOLS.update(tools)


class FeatureService:
    """Service for checking feature flags."""

    def __init__(self):
        self.db = get_database()

    def is_feature_enabled(self, feature_key: str, user_id: int) -> bool:
        """Check if a specific feature is enabled for a user.

        Considers both global defaults and per-user overrides.

        Args:
            feature_key: The feature to check
            user_id: The user to check for

        Returns:
            True if feature is enabled, False otherwise
        """
        # First check for user-specific override
        override = self.db.fetchone("""
            SELECT enabled FROM user_feature_overrides
            WHERE user_id = ? AND feature_key = ?
        """, (user_id, feature_key))

        if override:
            return bool(override["enabled"])

        # Fall back to global default
        feature = self.db.fetchone("""
            SELECT default_enabled FROM feature_flags WHERE feature_key = ?
        """, (feature_key,))

        if feature:
            return bool(feature["default_enabled"])

        # If feature not in database, default to enabled
        logger.warning(f"Feature {feature_key} not found in database, defaulting to enabled")
        return True

    def get_enabled_features(self, user_id: int) -> Set[str]:
        """Get all enabled feature keys for a user.

        Args:
            user_id: The user to check

        Returns:
            Set of enabled feature keys
        """
        # Get all features with defaults
        features = self.db.fetchall("""
            SELECT feature_key, default_enabled FROM feature_flags
        """)

        # Get user overrides
        overrides = self.db.fetchall("""
            SELECT feature_key, enabled FROM user_feature_overrides WHERE user_id = ?
        """, (user_id,))
        override_map = {row["feature_key"]: bool(row["enabled"]) for row in overrides}

        enabled = set()
        for feature in features:
            key = feature["feature_key"]
            # Use override if exists, otherwise use default
            if key in override_map:
                if override_map[key]:
                    enabled.add(key)
            elif feature["default_enabled"]:
                enabled.add(key)

        return enabled

    def get_available_tools(self, user_id: int) -> Set[str]:
        """Get the set of tool names available to a user based on their features.

        Tools not in the GATED_TOOLS list are always available.
        Tools in GATED_TOOLS require their corresponding feature to be enabled.

        Args:
            user_id: The user to check

        Returns:
            Set of available tool names
        """
        enabled_features = self.get_enabled_features(user_id)

        # Check if tool_use is enabled at all
        if "tool_use" not in enabled_features:
            return set()

        available = set()

        # Add tools for each enabled feature
        for feature_key, tools in FEATURE_TO_TOOLS.items():
            if feature_key in enabled_features:
                available.update(tools)

        return available

    def filter_tools_for_user(self, tools: List[dict], user_id: int) -> List[dict]:
        """Filter a list of tool definitions based on user's enabled features.

        Tools not in GATED_TOOLS are always included (e.g., built-in tools).
        Tools in GATED_TOOLS are included only if their feature is enabled.

        Args:
            tools: List of tool definitions (OpenAI format with function.name)
            user_id: The user to filter for

        Returns:
            Filtered list of tool definitions
        """
        if not tools:
            return tools

        enabled_features = self.get_enabled_features(user_id)

        # If tool_use is disabled, return empty list
        if "tool_use" not in enabled_features:
            logger.debug(f"Tool use disabled for user {user_id}")
            return []

        available_tools = self.get_available_tools(user_id)

        filtered = []
        for tool in tools:
            tool_name = tool.get("function", {}).get("name", "")

            # If tool is not gated, always include it
            if tool_name not in GATED_TOOLS:
                filtered.append(tool)
                continue

            # If tool is gated, check if it's available
            if tool_name in available_tools:
                filtered.append(tool)
            else:
                logger.debug(f"Tool {tool_name} filtered out for user {user_id}")

        return filtered

    def check_thinking_mode_enabled(self, user_id: int) -> bool:
        """Check if thinking mode is enabled for a user.

        Args:
            user_id: The user to check

        Returns:
            True if thinking mode is enabled
        """
        return self.is_feature_enabled("thinking_mode", user_id)

    def check_voice_features_enabled(self, user_id: int) -> dict:
        """Check voice feature status for a user.

        Args:
            user_id: The user to check

        Returns:
            Dict with tts and stt enabled status
        """
        return {
            "tts": self.is_feature_enabled("tts", user_id),
            "stt": self.is_feature_enabled("stt", user_id)
        }


# Global service instance
_feature_service: Optional[FeatureService] = None


def get_feature_service() -> FeatureService:
    """Get the global feature service instance."""
    global _feature_service
    if _feature_service is None:
        _feature_service = FeatureService()
    return _feature_service
