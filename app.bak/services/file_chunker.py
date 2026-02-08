import logging
from typing import List, Tuple
import re

from app.config import KB_CHUNK_SIZE, KB_CHUNK_OVERLAP

logger = logging.getLogger(__name__)


class FileChunker:
    """Service for chunking text documents into smaller pieces"""

    def __init__(self, chunk_size: int = None, chunk_overlap: int = None):
        self.chunk_size = chunk_size or KB_CHUNK_SIZE
        self.chunk_overlap = chunk_overlap or KB_CHUNK_OVERLAP

    def chunk_text(self, text: str, filename: str = "") -> List[Tuple[int, str]]:
        """
        Split text into chunks with overlap.
        Returns list of (chunk_index, chunk_content) tuples.
        """
        if not text.strip():
            return []

        # Determine chunking strategy based on file type
        ext = filename.split('.')[-1].lower() if filename else ""

        if ext in ['py', 'js', 'ts', 'java', 'go', 'rs', 'c', 'cpp', 'h']:
            return self._chunk_code(text)
        elif ext in ['md', 'markdown']:
            return self._chunk_markdown(text)
        else:
            return self._chunk_plain_text(text)

    def _chunk_plain_text(self, text: str) -> List[Tuple[int, str]]:
        """Chunk plain text by paragraphs/sentences"""
        # Split into paragraphs first
        paragraphs = re.split(r'\n\s*\n', text)
        chunks = []
        current_chunk = []
        current_size = 0
        chunk_index = 0

        for para in paragraphs:
            para = para.strip()
            if not para:
                continue

            para_size = len(para)

            if current_size + para_size > self.chunk_size and current_chunk:
                # Save current chunk
                chunk_text = '\n\n'.join(current_chunk)
                chunks.append((chunk_index, chunk_text))
                chunk_index += 1

                # Start new chunk with overlap
                if self.chunk_overlap > 0 and len(current_chunk) > 0:
                    overlap_text = current_chunk[-1]
                    current_chunk = [overlap_text]
                    current_size = len(overlap_text)
                else:
                    current_chunk = []
                    current_size = 0

            current_chunk.append(para)
            current_size += para_size

        # Don't forget the last chunk
        if current_chunk:
            chunk_text = '\n\n'.join(current_chunk)
            chunks.append((chunk_index, chunk_text))

        return chunks

    def _chunk_code(self, text: str) -> List[Tuple[int, str]]:
        """
        Chunk code by logical blocks (functions, classes).
        Falls back to line-based chunking if needed.
        """
        lines = text.split('\n')
        chunks = []
        current_chunk = []
        current_size = 0
        chunk_index = 0

        for line in lines:
            line_size = len(line) + 1  # +1 for newline

            if current_size + line_size > self.chunk_size and current_chunk:
                # Save current chunk
                chunk_text = '\n'.join(current_chunk)
                chunks.append((chunk_index, chunk_text))
                chunk_index += 1

                # Calculate overlap in lines
                overlap_lines = max(1, self.chunk_overlap // 50)  # ~50 chars per line avg
                current_chunk = current_chunk[-overlap_lines:]
                current_size = sum(len(l) + 1 for l in current_chunk)

            current_chunk.append(line)
            current_size += line_size

        # Last chunk
        if current_chunk:
            chunk_text = '\n'.join(current_chunk)
            chunks.append((chunk_index, chunk_text))

        return chunks

    def _chunk_markdown(self, text: str) -> List[Tuple[int, str]]:
        """Chunk markdown by headers and sections"""
        # Split by headers
        sections = re.split(r'(^#{1,6}\s+.+$)', text, flags=re.MULTILINE)

        chunks = []
        current_chunk = []
        current_size = 0
        chunk_index = 0
        current_header = ""

        for section in sections:
            if not section.strip():
                continue

            # Check if this is a header
            if re.match(r'^#{1,6}\s+', section):
                current_header = section.strip()
                continue

            # Add header to content
            content = f"{current_header}\n{section}" if current_header else section
            content_size = len(content)

            if current_size + content_size > self.chunk_size and current_chunk:
                # Save current chunk
                chunk_text = '\n\n'.join(current_chunk)
                chunks.append((chunk_index, chunk_text))
                chunk_index += 1
                current_chunk = []
                current_size = 0

            current_chunk.append(content)
            current_size += content_size

        # Last chunk
        if current_chunk:
            chunk_text = '\n\n'.join(current_chunk)
            chunks.append((chunk_index, chunk_text))

        return chunks

    def extract_text_from_pdf(self, pdf_content: bytes) -> str:
        """Extract text from PDF bytes"""
        try:
            from pypdf import PdfReader
            from io import BytesIO

            reader = PdfReader(BytesIO(pdf_content))
            text_parts = []

            for page in reader.pages:
                text = page.extract_text()
                if text:
                    text_parts.append(text)

            return '\n\n'.join(text_parts)

        except Exception as e:
            logger.error(f"PDF extraction failed: {e}")
            return ""


# Global instance
_chunker: FileChunker = None


def get_chunker() -> FileChunker:
    """Get the global chunker instance"""
    global _chunker
    if _chunker is None:
        _chunker = FileChunker()
    return _chunker
