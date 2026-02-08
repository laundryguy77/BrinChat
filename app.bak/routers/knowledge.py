import logging
import base64
from fastapi import APIRouter, HTTPException, status, Depends, UploadFile, File
from pydantic import BaseModel
from typing import List, Optional

from app.services.knowledge_base import get_knowledge_base
from app.middleware.auth import require_auth
from app.models.auth_schemas import UserResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/knowledge", tags=["knowledge"])


class SearchQuery(BaseModel):
    query: str
    top_k: int = 5
    threshold: float = 0.3


class SearchResult(BaseModel):
    content: str
    filename: str
    file_type: str
    chunk_index: int
    similarity: float
    document_id: str


class DocumentInfo(BaseModel):
    id: str
    filename: str
    file_type: str
    chunk_count: int
    created_at: str


class KnowledgeStats(BaseModel):
    document_count: int
    chunk_count: int


@router.post("/upload")
async def upload_document(
    file: UploadFile = File(...),
    user: UserResponse = Depends(require_auth)
):
    """Upload a document to the knowledge base"""
    kb = get_knowledge_base()

    # Determine file type
    filename = file.filename or "unknown"
    ext = filename.split('.')[-1].lower() if '.' in filename else ""

    file_type_map = {
        'pdf': 'pdf',
        'txt': 'text', 'md': 'text', 'markdown': 'text',
        'csv': 'text', 'log': 'text', 'ini': 'text', 'cfg': 'text',
        'py': 'code', 'js': 'code', 'ts': 'code', 'jsx': 'code', 'tsx': 'code',
        'java': 'code', 'go': 'code', 'rs': 'code', 'c': 'code', 'cpp': 'code',
        'h': 'code', 'rb': 'code', 'php': 'code', 'sh': 'code',
        'html': 'code', 'css': 'code', 'json': 'code', 'xml': 'code',
        'yaml': 'code', 'yml': 'code', 'toml': 'code'
    }

    file_type = file_type_map.get(ext, 'text')

    # Read file content
    content = await file.read()

    # Size limit (150MB)
    if len(content) > 150 * 1024 * 1024:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail="File size exceeds 150MB limit"
        )

    # Ingest document
    result = await kb.ingest_document(
        user_id=user.id,
        filename=filename,
        content=content,
        file_type=file_type
    )

    if result["status"] == "error":
        logger.error(f"Upload failed for {filename}: {result['message']}")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=result["message"]
        )

    return result


@router.post("/search", response_model=List[SearchResult])
async def search_knowledge(
    query: SearchQuery,
    user: UserResponse = Depends(require_auth)
):
    """Search the knowledge base"""
    kb = get_knowledge_base()

    results = await kb.search(
        user_id=user.id,
        query=query.query,
        top_k=query.top_k,
        threshold=query.threshold
    )

    return results


@router.get("/documents", response_model=List[DocumentInfo])
async def list_documents(user: UserResponse = Depends(require_auth)):
    """List all documents in the knowledge base"""
    kb = get_knowledge_base()
    return kb.get_user_documents(user.id)


@router.delete("/documents/{document_id}")
async def delete_document(
    document_id: str,
    user: UserResponse = Depends(require_auth)
):
    """Delete a document from the knowledge base"""
    kb = get_knowledge_base()

    success = kb.delete_document(user.id, document_id)
    if not success:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Document not found"
        )

    return {"message": "Document deleted successfully"}


@router.get("/stats", response_model=KnowledgeStats)
async def get_stats(user: UserResponse = Depends(require_auth)):
    """Get knowledge base statistics"""
    kb = get_knowledge_base()
    return kb.get_stats(user.id)
