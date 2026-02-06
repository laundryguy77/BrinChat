"""Memory service for semantic memory operations."""
import logging
from typing import List
import numpy as np

from app.services.memory_store import get_memory_store, Memory
from app.services.embedding_service import get_embedding_service

logger = logging.getLogger(__name__)


class MemoryService:
    def __init__(self):
        self.store = get_memory_store()
        self.embedding_service = get_embedding_service()

    async def add_memory(
        self,
        user_id: int,
        content: str,
        category: str = "general",
        importance: int = 5,
        source: str = "inferred"
    ) -> dict:
        """Add a memory with embedding."""
        try:
            # Generate embedding first (needed for semantic duplicate check)
            embedding = await self.embedding_service.get_embedding(content)

            # Check for duplicates using semantic similarity
            existing = self.store.get_memories_with_embeddings(user_id)
            for mem in existing:
                # Exact match check (case-insensitive)
                if mem.content.lower().strip() == content.lower().strip():
                    return {
                        "success": False,
                        "error": "Duplicate memory - already stored",
                        "existing_id": mem.id
                    }
                # Semantic similarity check (threshold 0.85 = very similar)
                if mem.embedding and embedding:
                    similarity = self._cosine_similarity(embedding, mem.embedding)
                    if similarity >= 0.85:
                        return {
                            "success": False,
                            "error": f"Similar memory already exists (similarity: {similarity:.0%})",
                            "existing_id": mem.id,
                            "existing_content": mem.content[:100]
                        }

            # Store memory
            memory = self.store.create_memory(
                user_id=user_id,
                content=content,
                category=category,
                importance=importance,
                embedding=embedding,
                source=source
            )

            logger.info(f"Added memory {memory.id} for user {user_id}: {content[:50]}...")
            return {
                "success": True,
                "id": memory.id,
                "category": category,
                "message": f"Remembered: {content[:100]}..."
            }

        except Exception as e:
            logger.error(f"Failed to add memory: {e}")
            return {"success": False, "error": str(e)}

    async def query_memories(
        self,
        user_id: int,
        query: str,
        top_k: int = 5,
        threshold: float = 0.4
    ) -> List[dict]:
        """Query memories using semantic similarity."""
        try:
            query_embedding = await self.embedding_service.get_embedding(query)
            memories = self.store.get_memories_with_embeddings(user_id)
            if not memories:
                return []

            results = []
            for mem in memories:
                if mem.embedding:
                    similarity = self._cosine_similarity(query_embedding, mem.embedding)
                    if similarity >= threshold:
                        results.append({
                            "id": mem.id,
                            "content": mem.content,
                            "category": mem.category,
                            "importance": mem.importance,
                            "similarity": round(similarity, 3),
                            "created_at": mem.created_at
                        })
                        self.store.update_access(mem.id, user_id)

            # Sort by similarity * importance weighting
            results.sort(key=lambda x: x["similarity"] * (x["importance"] / 10), reverse=True)
            return results[:top_k]

        except Exception as e:
            logger.error(f"Failed to query memories: {e}")
            return []

    def get_all_memories(self, user_id: int) -> List[dict]:
        """Get all memories for a user (for UI display)."""
        memories = self.store.get_user_memories(user_id)
        return [
            {
                "id": m.id,
                "content": m.content,
                "category": m.category,
                "importance": m.importance,
                "source": m.source,
                "created_at": m.created_at,
                "access_count": m.access_count
            }
            for m in memories
        ]

    def delete_memory(self, user_id: int, memory_id: str) -> bool:
        return self.store.delete_memory(memory_id, user_id)

    def clear_all_memories(self, user_id: int) -> int:
        return self.store.clear_user_memories(user_id)

    def get_stats(self, user_id: int) -> dict:
        return self.store.get_memory_stats(user_id)

    def _cosine_similarity(self, a: List[float], b: List[float]) -> float:
        a = np.array(a)
        b = np.array(b)
        return float(np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b)))


_memory_service = None

def get_memory_service() -> MemoryService:
    global _memory_service
    if _memory_service is None:
        _memory_service = MemoryService()
    return _memory_service
