import httpx
import logging
from typing import List, Optional
import numpy as np

from app.config import OLLAMA_BASE_URL, KB_EMBEDDING_MODEL

logger = logging.getLogger(__name__)


class EmbeddingService:
    """Service for generating text embeddings using Ollama"""

    def __init__(self, model: str = None):
        self.model = model or KB_EMBEDDING_MODEL
        self.base_url = OLLAMA_BASE_URL
        self.client = httpx.AsyncClient(timeout=120.0)
        self._dimension = None

    async def get_embedding(self, text: str) -> List[float]:
        """Generate embedding for a single text"""
        try:
            response = await self.client.post(
                f"{self.base_url}/api/embed",
                json={
                    "model": self.model,
                    "input": text
                }
            )
            response.raise_for_status()
            data = response.json()

            # Ollama returns embeddings in 'embeddings' array
            embeddings = data.get("embeddings", [])
            if embeddings and len(embeddings) > 0:
                return embeddings[0]

            logger.error(f"No embeddings returned from Ollama")
            return []

        except Exception as e:
            logger.error(f"Embedding generation failed: {e}")
            raise

    async def get_embeddings_batch(self, texts: List[str]) -> List[List[float]]:
        """Generate embeddings for multiple texts"""
        try:
            response = await self.client.post(
                f"{self.base_url}/api/embed",
                json={
                    "model": self.model,
                    "input": texts
                }
            )
            response.raise_for_status()
            data = response.json()

            embeddings = data.get("embeddings", [])
            return embeddings

        except Exception as e:
            logger.error(f"Batch embedding generation failed: {e}")
            raise

    async def get_dimension(self) -> int:
        """Get the embedding dimension by generating a test embedding"""
        if self._dimension is not None:
            return self._dimension

        try:
            embedding = await self.get_embedding("test")
            self._dimension = len(embedding)
            logger.debug(f"Embedding dimension: {self._dimension}")
            return self._dimension
        except Exception as e:
            logger.error(f"Failed to get embedding dimension: {e}")
            return 768  # Default fallback

    def cosine_similarity(self, vec1: List[float], vec2: List[float]) -> float:
        """Calculate cosine similarity between two vectors"""
        if not vec1 or not vec2:
            return 0.0

        a = np.array(vec1)
        b = np.array(vec2)

        dot_product = np.dot(a, b)
        norm_a = np.linalg.norm(a)
        norm_b = np.linalg.norm(b)

        if norm_a == 0 or norm_b == 0:
            return 0.0

        return dot_product / (norm_a * norm_b)

    def find_most_similar(
        self,
        query_embedding: List[float],
        embeddings: List[List[float]],
        top_k: int = 5
    ) -> List[tuple]:
        """
        Find the most similar embeddings to the query.
        Returns list of (index, similarity_score) tuples.
        """
        if not embeddings:
            return []

        similarities = []
        for i, emb in enumerate(embeddings):
            sim = self.cosine_similarity(query_embedding, emb)
            similarities.append((i, sim))

        # Sort by similarity (highest first) and return top_k
        similarities.sort(key=lambda x: x[1], reverse=True)
        return similarities[:top_k]

    async def is_model_available(self) -> bool:
        """Check if the embedding model is available"""
        try:
            response = await self.client.post(
                f"{self.base_url}/api/show",
                json={"name": self.model}
            )
            return response.status_code == 200
        except Exception:
            return False

    async def close(self):
        """Close the HTTP client"""
        await self.client.aclose()


# Global service instance (initialized lazily)
_embedding_service: Optional[EmbeddingService] = None


def get_embedding_service() -> EmbeddingService:
    """Get the global embedding service instance"""
    global _embedding_service
    if _embedding_service is None:
        _embedding_service = EmbeddingService()
    return _embedding_service
