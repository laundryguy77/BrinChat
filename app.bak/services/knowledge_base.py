import logging
import base64
from typing import List, Dict, Any, Optional

from app.services.knowledge_store import get_knowledge_store, KnowledgeStore
from app.services.embedding_service import get_embedding_service, EmbeddingService
from app.services.file_chunker import get_chunker, FileChunker
from app.config import KB_EMBEDDING_MODEL

logger = logging.getLogger(__name__)


class KnowledgeBase:
    """
    Main knowledge base service that orchestrates:
    - Document ingestion (chunking, embedding, storage)
    - Semantic search
    """

    def __init__(self):
        self.store: KnowledgeStore = get_knowledge_store()
        self.embedding_service: EmbeddingService = get_embedding_service()
        self.chunker: FileChunker = get_chunker()

    async def ingest_document(
        self,
        user_id: int,
        filename: str,
        content: bytes,
        file_type: str
    ) -> Dict[str, Any]:
        """
        Ingest a document into the knowledge base.
        Returns document info and chunk count.
        """
        # Compute hash to check for duplicates
        content_hash = KnowledgeStore.compute_hash(content)

        existing_id = self.store.document_exists_by_hash(user_id, content_hash)
        if existing_id:
            logger.info(f"Document already exists: {filename}")
            return {
                "status": "duplicate",
                "document_id": existing_id,
                "message": f"Document '{filename}' already exists in knowledge base"
            }

        # Extract text based on file type
        if file_type == "pdf":
            text = self.chunker.extract_text_from_pdf(content)
        else:
            try:
                text = content.decode('utf-8')
            except UnicodeDecodeError:
                text = content.decode('latin-1', errors='ignore')

        if not text.strip():
            return {
                "status": "error",
                "message": f"Could not extract text from '{filename}'"
            }

        # Chunk the text
        chunks = self.chunker.chunk_text(text, filename)
        if not chunks:
            return {
                "status": "error",
                "message": f"No content chunks generated from '{filename}'"
            }

        logger.info(f"Generated {len(chunks)} chunks from {filename}")

        # Create document record
        document = self.store.create_document(
            user_id=user_id,
            filename=filename,
            file_type=file_type,
            content_hash=content_hash,
            embedding_model=KB_EMBEDDING_MODEL
        )

        # Generate embeddings for all chunks
        chunk_texts = [c[1] for c in chunks]

        try:
            embeddings = await self.embedding_service.get_embeddings_batch(chunk_texts)
        except Exception as e:
            logger.error(f"Embedding generation failed: {e}")
            # Clean up the document record we just created
            self.store.delete_document(document.id, user_id)
            return {
                "status": "error",
                "message": f"Failed to generate embeddings: {str(e)}"
            }

        # Store chunks with embeddings
        chunk_data = []
        for (chunk_index, chunk_content), embedding in zip(chunks, embeddings):
            chunk_data.append((chunk_index, chunk_content, embedding))

        self.store.add_chunks_batch(document.id, chunk_data)
        self.store.update_chunk_count(document.id, len(chunks))

        logger.info(f"Ingested document {filename} with {len(chunks)} chunks")

        return {
            "status": "success",
            "document_id": document.id,
            "filename": filename,
            "chunk_count": len(chunks),
            "message": f"Successfully ingested '{filename}' with {len(chunks)} chunks"
        }

    async def search(
        self,
        user_id: int,
        query: str,
        top_k: int = 5,
        threshold: float = 0.3
    ) -> List[Dict[str, Any]]:
        """
        Search the knowledge base for relevant chunks.
        Returns list of matching chunks with similarity scores.
        """
        # Get query embedding
        try:
            query_embedding = await self.embedding_service.get_embedding(query)
        except Exception as e:
            logger.error(f"Query embedding failed: {e}")
            return []

        # Get all user chunks with embeddings
        all_chunks = self.store.get_all_user_chunks(user_id)

        if not all_chunks:
            return []

        # Prepare embeddings list
        embeddings = []
        valid_chunks = []

        for chunk in all_chunks:
            if chunk["embedding"]:
                embeddings.append(chunk["embedding"])
                valid_chunks.append(chunk)

        if not embeddings:
            return []

        # Find most similar chunks
        similar_indices = self.embedding_service.find_most_similar(
            query_embedding,
            embeddings,
            top_k=top_k
        )

        # Format results
        results = []
        for idx, similarity in similar_indices:
            if similarity < threshold:
                continue

            chunk = valid_chunks[idx]
            results.append({
                "content": chunk["content"],
                "filename": chunk["filename"],
                "file_type": chunk["file_type"],
                "chunk_index": chunk["chunk_index"],
                "similarity": round(similarity, 4),
                "document_id": chunk["document_id"]
            })

        return results

    def get_user_documents(self, user_id: int) -> List[Dict[str, Any]]:
        """Get all documents for a user"""
        documents = self.store.get_user_documents(user_id)
        return [
            {
                "id": doc.id,
                "filename": doc.filename,
                "file_type": doc.file_type,
                "chunk_count": doc.chunk_count,
                "created_at": doc.created_at
            }
            for doc in documents
        ]

    def delete_document(self, user_id: int, document_id: str) -> bool:
        """Delete a document from the knowledge base"""
        return self.store.delete_document(document_id, user_id)

    def get_stats(self, user_id: int) -> Dict[str, int]:
        """Get knowledge base statistics"""
        return self.store.get_user_stats(user_id)


# Global instance
_kb: KnowledgeBase = None


def get_knowledge_base() -> KnowledgeBase:
    """Get the global knowledge base instance"""
    global _kb
    if _kb is None:
        _kb = KnowledgeBase()
    return _kb
