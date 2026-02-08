"""Stats service for admin dashboard data."""
import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, Any, List, Optional
from collections import defaultdict
from app.services.database import get_database

logger = logging.getLogger(__name__)

# Conversations are stored as JSON files
CONVERSATIONS_DIR = Path("conversations")


class StatsService:
    """Service for gathering system statistics."""

    def __init__(self):
        self.db = get_database()

    def get_dashboard_stats(self) -> Dict[str, Any]:
        """Get comprehensive system statistics for the admin dashboard.

        Returns:
            Dict with various system stats
        """
        stats = {
            "users": self._get_user_stats(),
            "conversations": self._get_conversation_stats(),
            "memories": self._get_memory_stats(),
            "knowledge": self._get_knowledge_stats(),
            "features": self._get_feature_stats(),
        }

        return stats

    def _get_user_stats(self) -> Dict[str, Any]:
        """Get user-related statistics."""
        # Total users
        total = self.db.fetchone("SELECT COUNT(*) as count FROM users")
        total_users = total["count"] if total else 0

        # Active users (is_active = 1 or NULL)
        active = self.db.fetchone(
            "SELECT COUNT(*) as count FROM users WHERE is_active = 1 OR is_active IS NULL"
        )
        active_users = active["count"] if active else 0

        # Admin users
        admins = self.db.fetchone("SELECT COUNT(*) as count FROM users WHERE is_admin = 1")
        admin_count = admins["count"] if admins else 0

        # Users created in last 7 days
        week_ago = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
        recent = self.db.fetchone(
            "SELECT COUNT(*) as count FROM users WHERE created_at >= ?",
            (week_ago,)
        )
        recent_users = recent["count"] if recent else 0

        # Users with adult mode enabled
        adult_mode = self.db.fetchone(
            "SELECT COUNT(*) as count FROM user_profiles WHERE adult_mode_enabled = 1"
        )
        adult_mode_count = adult_mode["count"] if adult_mode else 0

        return {
            "total": total_users,
            "active": active_users,
            "inactive": total_users - active_users,
            "admins": admin_count,
            "recent_signups": recent_users,
            "adult_mode_enabled": adult_mode_count
        }

    def _get_conversation_stats(self) -> Dict[str, Any]:
        """Get conversation-related statistics from JSON files."""
        if not CONVERSATIONS_DIR.exists():
            return {
                "total": 0,
                "total_messages": 0,
                "recent": 0,
                "avg_messages_per_conv": 0
            }

        total_convs = 0
        total_messages = 0
        recent_convs = 0
        week_ago = datetime.now(timezone.utc) - timedelta(days=7)

        for conv_file in CONVERSATIONS_DIR.glob("*.json"):
            try:
                with open(conv_file, 'r') as f:
                    conv = json.load(f)

                total_convs += 1
                messages = conv.get("messages", [])
                total_messages += len(messages)

                # Check if recent
                updated_at = conv.get("updated_at")
                if updated_at:
                    try:
                        updated = datetime.fromisoformat(updated_at.replace("Z", "+00:00"))
                        if updated > week_ago:
                            recent_convs += 1
                    except (ValueError, TypeError):
                        pass

            except (json.JSONDecodeError, IOError) as e:
                logger.warning(f"Error reading conversation file {conv_file}: {e}")
                continue

        avg_messages = total_messages / total_convs if total_convs > 0 else 0

        return {
            "total": total_convs,
            "total_messages": total_messages,
            "recent": recent_convs,
            "avg_messages_per_conv": round(avg_messages, 1)
        }

    def _get_memory_stats(self) -> Dict[str, Any]:
        """Get memory-related statistics."""
        total = self.db.fetchone("SELECT COUNT(*) as count FROM memories")
        total_memories = total["count"] if total else 0

        # Memories by category
        categories = self.db.fetchall("""
            SELECT category, COUNT(*) as count
            FROM memories
            GROUP BY category
            ORDER BY count DESC
        """)
        by_category = {row["category"]: row["count"] for row in categories}

        # Users with memories
        users_with_memories = self.db.fetchone(
            "SELECT COUNT(DISTINCT user_id) as count FROM memories"
        )
        users_count = users_with_memories["count"] if users_with_memories else 0

        return {
            "total": total_memories,
            "users_with_memories": users_count,
            "by_category": by_category
        }

    def _get_knowledge_stats(self) -> Dict[str, Any]:
        """Get knowledge base statistics."""
        # Documents
        docs = self.db.fetchone("SELECT COUNT(*) as count FROM documents")
        doc_count = docs["count"] if docs else 0

        # Chunks
        chunks = self.db.fetchone("SELECT COUNT(*) as count FROM chunks")
        chunk_count = chunks["count"] if chunks else 0

        # Users with documents
        users_with_docs = self.db.fetchone(
            "SELECT COUNT(DISTINCT user_id) as count FROM documents"
        )
        users_count = users_with_docs["count"] if users_with_docs else 0

        return {
            "documents": doc_count,
            "chunks": chunk_count,
            "users_with_knowledge": users_count
        }

    def _get_feature_stats(self) -> Dict[str, Any]:
        """Get feature flag statistics."""
        # Count overrides
        overrides = self.db.fetchone(
            "SELECT COUNT(*) as count FROM user_feature_overrides"
        )
        override_count = overrides["count"] if overrides else 0

        # Features with most overrides
        top_overridden = self.db.fetchall("""
            SELECT feature_key, COUNT(*) as count
            FROM user_feature_overrides
            GROUP BY feature_key
            ORDER BY count DESC
            LIMIT 5
        """)

        return {
            "total_overrides": override_count,
            "top_overridden": [
                {"feature": row["feature_key"], "count": row["count"]}
                for row in top_overridden
            ]
        }

    def get_activity_trends(self, days: int = 30) -> Dict[str, Any]:
        """Get activity trends over time.

        Args:
            days: Number of days to look back

        Returns:
            Dict with daily activity data
        """
        start_date = datetime.now(timezone.utc) - timedelta(days=days)

        # Initialize daily counts
        daily_convs: Dict[str, int] = defaultdict(int)
        daily_messages: Dict[str, int] = defaultdict(int)

        # Scan conversation files for activity
        if CONVERSATIONS_DIR.exists():
            for conv_file in CONVERSATIONS_DIR.glob("*.json"):
                try:
                    with open(conv_file, 'r') as f:
                        conv = json.load(f)

                    # Count conversation creation
                    created_at = conv.get("created_at")
                    if created_at:
                        try:
                            created = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
                            if created > start_date:
                                date_key = created.strftime("%Y-%m-%d")
                                daily_convs[date_key] += 1
                        except (ValueError, TypeError):
                            pass

                    # Count messages by date
                    for msg in conv.get("messages", []):
                        msg_time = msg.get("timestamp")
                        if msg_time:
                            try:
                                ts = datetime.fromisoformat(msg_time.replace("Z", "+00:00"))
                                if ts > start_date:
                                    date_key = ts.strftime("%Y-%m-%d")
                                    daily_messages[date_key] += 1
                            except (ValueError, TypeError):
                                pass

                except (json.JSONDecodeError, IOError) as e:
                    continue

        # Get user signups by date
        daily_signups: Dict[str, int] = defaultdict(int)
        signups = self.db.fetchall("""
            SELECT DATE(created_at) as date, COUNT(*) as count
            FROM users
            WHERE created_at >= ?
            GROUP BY DATE(created_at)
        """, (start_date.isoformat(),))

        for row in signups:
            if row["date"]:
                daily_signups[row["date"]] = row["count"]

        # Build timeline
        timeline = []
        current = start_date.date()
        end = datetime.now(timezone.utc).date()

        while current <= end:
            date_key = current.strftime("%Y-%m-%d")
            timeline.append({
                "date": date_key,
                "conversations": daily_convs.get(date_key, 0),
                "messages": daily_messages.get(date_key, 0),
                "signups": daily_signups.get(date_key, 0)
            })
            current += timedelta(days=1)

        return {
            "period_days": days,
            "timeline": timeline,
            "totals": {
                "conversations": sum(daily_convs.values()),
                "messages": sum(daily_messages.values()),
                "signups": sum(daily_signups.values())
            }
        }

    def get_user_activity(self, user_id: int) -> Dict[str, Any]:
        """Get activity stats for a specific user.

        Args:
            user_id: The user to check

        Returns:
            Dict with user's activity data
        """
        # Count user's conversations
        conv_count = 0
        message_count = 0
        last_active = None

        if CONVERSATIONS_DIR.exists():
            for conv_file in CONVERSATIONS_DIR.glob("*.json"):
                try:
                    with open(conv_file, 'r') as f:
                        conv = json.load(f)

                    if conv.get("user_id") == user_id:
                        conv_count += 1
                        message_count += len(conv.get("messages", []))

                        updated_at = conv.get("updated_at")
                        if updated_at:
                            try:
                                updated = datetime.fromisoformat(updated_at.replace("Z", "+00:00"))
                                if last_active is None or updated > last_active:
                                    last_active = updated
                            except (ValueError, TypeError):
                                pass

                except (json.JSONDecodeError, IOError):
                    continue

        # Count memories
        memories = self.db.fetchone(
            "SELECT COUNT(*) as count FROM memories WHERE user_id = ?",
            (user_id,)
        )
        memory_count = memories["count"] if memories else 0

        # Count documents
        docs = self.db.fetchone(
            "SELECT COUNT(*) as count FROM documents WHERE user_id = ?",
            (user_id,)
        )
        doc_count = docs["count"] if docs else 0

        return {
            "conversations": conv_count,
            "messages": message_count,
            "memories": memory_count,
            "documents": doc_count,
            "last_active": last_active.isoformat() if last_active else None
        }


# Global service instance
_stats_service: Optional[StatsService] = None


def get_stats_service() -> StatsService:
    """Get the global stats service instance."""
    global _stats_service
    if _stats_service is None:
        _stats_service = StatsService()
    return _stats_service
