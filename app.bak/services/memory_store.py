"""Memory storage service for SQLite persistence."""
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import List, Optional
import json

from app.services.database import get_database


@dataclass
class Memory:
    id: str
    user_id: int
    content: str
    category: str  # 'preference', 'personal', 'topic', 'instruction'
    importance: int  # 1-10
    embedding: Optional[List[float]]
    source: str  # 'explicit' or 'inferred'
    created_at: str
    last_accessed: str
    access_count: int


class MemoryStore:
    def __init__(self):
        self.db = get_database()

    def create_memory(
        self,
        user_id: int,
        content: str,
        category: str = "general",
        importance: int = 5,
        embedding: Optional[List[float]] = None,
        source: str = "inferred"
    ) -> Memory:
        """Create a new memory entry."""
        memory_id = str(uuid.uuid4())[:8]
        now = datetime.utcnow().isoformat()
        embedding_json = json.dumps(embedding) if embedding else None

        self.db.execute(
            """INSERT INTO memories (id, user_id, content, category, importance,
               embedding, source, created_at, last_accessed, access_count)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (memory_id, user_id, content, category, importance,
             embedding_json, source, now, now, 0)
        )

        return Memory(
            id=memory_id,
            user_id=user_id,
            content=content,
            category=category,
            importance=importance,
            embedding=embedding,
            source=source,
            created_at=now,
            last_accessed=now,
            access_count=0
        )

    def get_user_memories(self, user_id: int) -> List[Memory]:
        """Get all memories for a user."""
        rows = self.db.fetchall(
            "SELECT * FROM memories WHERE user_id = ? ORDER BY importance DESC, created_at DESC",
            (user_id,)
        )
        return [self._row_to_memory(row) for row in rows]

    def get_memories_with_embeddings(self, user_id: int) -> List[Memory]:
        """Get all memories with embeddings for similarity search."""
        rows = self.db.fetchall(
            "SELECT * FROM memories WHERE user_id = ? AND embedding IS NOT NULL",
            (user_id,)
        )
        return [self._row_to_memory(row) for row in rows]

    def update_access(self, memory_id: str, user_id: int) -> bool:
        """Update last_accessed and increment access_count (with ownership check).

        Returns True if a record was updated, False if not found or not owned by user.
        """
        now = datetime.utcnow().isoformat()
        result = self.db.execute(
            "UPDATE memories SET last_accessed = ?, access_count = access_count + 1 WHERE id = ? AND user_id = ?",
            (now, memory_id, user_id)
        )
        return result.rowcount > 0

    def delete_memory(self, memory_id: str, user_id: int) -> bool:
        """Delete a memory (with ownership check)."""
        result = self.db.execute(
            "DELETE FROM memories WHERE id = ? AND user_id = ?",
            (memory_id, user_id)
        )
        return result.rowcount > 0

    def clear_user_memories(self, user_id: int) -> int:
        """Delete all memories for a user."""
        result = self.db.execute(
            "DELETE FROM memories WHERE user_id = ?",
            (user_id,)
        )
        return result.rowcount

    def get_memory_stats(self, user_id: int) -> dict:
        """Get memory statistics for a user."""
        count = self.db.fetchone(
            "SELECT COUNT(*) as cnt FROM memories WHERE user_id = ?",
            (user_id,)
        )["cnt"]
        categories = self.db.fetchall(
            "SELECT category, COUNT(*) as cnt FROM memories WHERE user_id = ? GROUP BY category",
            (user_id,)
        )
        return {
            "total": count,
            "by_category": {row["category"]: row["cnt"] for row in categories}
        }

    def _row_to_memory(self, row) -> Memory:
        """Convert database row to Memory dataclass."""
        # Uses dict-like access because sqlite3.Row factory is enabled
        return Memory(
            id=row["id"],
            user_id=row["user_id"],
            content=row["content"],
            category=row["category"],
            importance=row["importance"],
            embedding=json.loads(row["embedding"]) if row["embedding"] else None,
            source=row["source"],
            created_at=row["created_at"],
            last_accessed=row["last_accessed"],
            access_count=row["access_count"]
        )


_memory_store = None

def get_memory_store() -> MemoryStore:
    global _memory_store
    if _memory_store is None:
        _memory_store = MemoryStore()
    return _memory_store
