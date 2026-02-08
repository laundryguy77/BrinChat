import json
import logging
import hashlib
from datetime import datetime
from typing import List, Optional, Dict, Any
from dataclasses import dataclass
import uuid

from app.services.database import get_database

logger = logging.getLogger(__name__)


@dataclass
class Document:
    """Document metadata"""
    id: str
    user_id: int
    filename: str
    file_type: str
    file_hash: str
    chunk_count: int
    embedding_model: str
    created_at: str


@dataclass
class Chunk:
    """Document chunk with embedding"""
    id: str
    document_id: str
    chunk_index: int
    content: str
    embedding: Optional[List[float]] = None


class KnowledgeStore:
    """SQLite storage for knowledge base documents and chunks"""

    def __init__(self):
        self.db = get_database()

    def create_document(
        self,
        user_id: int,
        filename: str,
        file_type: str,
        content_hash: str,
        embedding_model: str
    ) -> Document:
        """Create a new document record"""
        doc_id = str(uuid.uuid4())
        created_at = datetime.utcnow().isoformat()

        self.db.execute(
            """INSERT INTO documents (id, user_id, filename, file_type, file_hash,
               chunk_count, embedding_model, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (doc_id, user_id, filename, file_type, content_hash, 0, embedding_model, created_at)
        )

        return Document(
            id=doc_id,
            user_id=user_id,
            filename=filename,
            file_type=file_type,
            file_hash=content_hash,
            chunk_count=0,
            embedding_model=embedding_model,
            created_at=created_at
        )

    def update_chunk_count(self, document_id: str, count: int):
        """Update the chunk count for a document"""
        self.db.execute(
            "UPDATE documents SET chunk_count = ? WHERE id = ?",
            (count, document_id)
        )

    def add_chunk(
        self,
        document_id: str,
        chunk_index: int,
        content: str,
        embedding: List[float]
    ) -> str:
        """Add a chunk with its embedding"""
        chunk_id = str(uuid.uuid4())
        # TODO: Consider using BLOB for embeddings instead of JSON string.
        # JSON works but is less efficient for large datasets with many vectors.
        embedding_json = json.dumps(embedding)

        self.db.execute(
            """INSERT INTO chunks (id, document_id, chunk_index, content, embedding)
               VALUES (?, ?, ?, ?, ?)""",
            (chunk_id, document_id, chunk_index, content, embedding_json)
        )

        return chunk_id

    def add_chunks_batch(
        self,
        document_id: str,
        chunks: List[tuple]  # List of (chunk_index, content, embedding)
    ):
        """Add multiple chunks at once"""
        params_list = []
        for chunk_index, content, embedding in chunks:
            chunk_id = str(uuid.uuid4())
            embedding_json = json.dumps(embedding) if embedding else None
            params_list.append((chunk_id, document_id, chunk_index, content, embedding_json))

        self.db.executemany(
            """INSERT INTO chunks (id, document_id, chunk_index, content, embedding)
               VALUES (?, ?, ?, ?, ?)""",
            params_list
        )

    def get_document(self, document_id: str, user_id: int) -> Optional[Document]:
        """Get a document by ID with user ownership validation.

        Args:
            document_id: The document's unique ID
            user_id: The user's ID (required for security - enforces ownership)

        Returns:
            Document if found and owned by user, None otherwise
        """
        row = self.db.fetchone(
            "SELECT * FROM documents WHERE id = ? AND user_id = ?",
            (document_id, user_id)
        )
        if not row:
            return None

        return Document(
            id=row["id"],
            user_id=row["user_id"],
            filename=row["filename"],
            file_type=row["file_type"],
            file_hash=row["file_hash"],
            chunk_count=row["chunk_count"],
            embedding_model=row["embedding_model"],
            created_at=row["created_at"]
        )

    def get_document_unsafe(self, document_id: str) -> Optional[Document]:
        """Get a document by ID WITHOUT user validation.

        WARNING: Only use for internal/admin operations where cross-user
        access is intentional (e.g., cascade deletes, migrations).

        Args:
            document_id: The document's unique ID

        Returns:
            Document if found, None otherwise
        """
        row = self.db.fetchone(
            "SELECT * FROM documents WHERE id = ?",
            (document_id,)
        )
        if not row:
            return None

        return Document(
            id=row["id"],
            user_id=row["user_id"],
            filename=row["filename"],
            file_type=row["file_type"],
            file_hash=row["file_hash"],
            chunk_count=row["chunk_count"],
            embedding_model=row["embedding_model"],
            created_at=row["created_at"]
        )

    def get_user_documents(self, user_id: int) -> List[Document]:
        """Get all documents for a user"""
        rows = self.db.fetchall(
            "SELECT * FROM documents WHERE user_id = ? ORDER BY created_at DESC",
            (user_id,)
        )

        return [
            Document(
                id=row["id"],
                user_id=row["user_id"],
                filename=row["filename"],
                file_type=row["file_type"],
                file_hash=row["file_hash"],
                chunk_count=row["chunk_count"],
                embedding_model=row["embedding_model"],
                created_at=row["created_at"]
            )
            for row in rows
        ]

    def document_exists_by_hash(self, user_id: int, file_hash: str) -> Optional[str]:
        """Check if a document with this hash already exists for the user"""
        row = self.db.fetchone(
            "SELECT id FROM documents WHERE user_id = ? AND file_hash = ?",
            (user_id, file_hash)
        )
        return row["id"] if row else None

    def get_document_chunks(self, document_id: str) -> List[Chunk]:
        """Get all chunks for a document"""
        rows = self.db.fetchall(
            "SELECT * FROM chunks WHERE document_id = ? ORDER BY chunk_index",
            (document_id,)
        )

        chunks = []
        for row in rows:
            embedding = json.loads(row["embedding"]) if row["embedding"] else None
            chunks.append(Chunk(
                id=row["id"],
                document_id=row["document_id"],
                chunk_index=row["chunk_index"],
                content=row["content"],
                embedding=embedding
            ))

        return chunks

    def get_all_user_chunks(self, user_id: int) -> List[Dict[str, Any]]:
        """Get all chunks for a user with document info"""
        rows = self.db.fetchall(
            """SELECT c.*, d.filename, d.file_type
               FROM chunks c
               JOIN documents d ON c.document_id = d.id
               WHERE d.user_id = ?
               ORDER BY d.created_at DESC, c.chunk_index""",
            (user_id,)
        )

        results = []
        for row in rows:
            embedding = json.loads(row["embedding"]) if row["embedding"] else None
            results.append({
                "id": row["id"],
                "document_id": row["document_id"],
                "chunk_index": row["chunk_index"],
                "content": row["content"],
                "embedding": embedding,
                "filename": row["filename"],
                "file_type": row["file_type"]
            })

        return results

    def delete_document(self, document_id: str, user_id: int) -> bool:
        """Delete a document and all its chunks with user ownership validation.

        Args:
            document_id: The document's unique ID
            user_id: The user's ID (required for security - enforces ownership)

        Returns:
            True if document was deleted, False if not found or not owned by user
        """
        # Verify ownership before delete
        doc = self.get_document(document_id, user_id)
        if not doc:
            return False

        # Delete chunks first (due to foreign key)
        self.db.execute("DELETE FROM chunks WHERE document_id = ?", (document_id,))
        # Delete document
        self.db.execute("DELETE FROM documents WHERE id = ? AND user_id = ?", (document_id, user_id))

        logger.info(f"Deleted document {document_id} for user {user_id}")
        return True

    def delete_document_unsafe(self, document_id: str) -> bool:
        """Delete a document WITHOUT user validation.

        WARNING: Only use for internal/admin operations where cross-user
        access is intentional (e.g., cascade deletes, migrations).

        Args:
            document_id: The document's unique ID

        Returns:
            True if document was deleted, False if not found
        """
        doc = self.get_document_unsafe(document_id)
        if not doc:
            return False

        # Delete chunks first (due to foreign key)
        self.db.execute("DELETE FROM chunks WHERE document_id = ?", (document_id,))
        # Delete document
        self.db.execute("DELETE FROM documents WHERE id = ?", (document_id,))

        logger.info(f"Deleted document {document_id} (unsafe)")
        return True

    def get_user_stats(self, user_id: int) -> Dict[str, int]:
        """Get knowledge base statistics for a user"""
        doc_count = self.db.fetchone(
            "SELECT COUNT(*) as count FROM documents WHERE user_id = ?",
            (user_id,)
        )

        chunk_count = self.db.fetchone(
            """SELECT COUNT(*) as count FROM chunks c
               JOIN documents d ON c.document_id = d.id
               WHERE d.user_id = ?""",
            (user_id,)
        )

        return {
            "document_count": doc_count["count"] if doc_count else 0,
            "chunk_count": chunk_count["count"] if chunk_count else 0
        }

    @staticmethod
    def compute_hash(content: bytes) -> str:
        """Compute SHA256 hash of content"""
        return hashlib.sha256(content).hexdigest()


# Global instance
_store: KnowledgeStore = None


def get_knowledge_store() -> KnowledgeStore:
    """Get the global knowledge store instance"""
    global _store
    if _store is None:
        _store = KnowledgeStore()
    return _store
