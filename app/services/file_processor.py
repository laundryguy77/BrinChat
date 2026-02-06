"""File processor service for handling PDFs, ZIPs, and other file types."""

import base64
import binascii
import zipfile
import io
import logging
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

# Try to import pypdf, fall back gracefully if not installed
try:
    from pypdf import PdfReader
    PDF_SUPPORT = True
except ImportError:
    PDF_SUPPORT = False
    logger.warning("pypdf not installed. PDF text extraction will be limited.")


class FileProcessor:
    """Process various file types for inclusion in chat context."""

    # Maximum characters to extract from a file
    MAX_TEXT_LENGTH = 50000

    def process_file(self, file_data: Dict) -> Dict:
        """
        Process a file and extract its content.

        Args:
            file_data: Dict with keys: name, type, content, is_base64

        Returns:
            Dict with processed content and metadata
        """
        file_type = file_data.get('type', 'text')
        content = file_data.get('content', '')
        is_base64 = file_data.get('is_base64', False)
        name = file_data.get('name', 'unknown')

        logger.debug(f"Processing file: {name}, type: {file_type}, is_base64: {is_base64}, content_length: {len(content) if content else 0}")

        try:
            if file_type == 'pdf':
                return self._process_pdf(name, content)
            elif file_type == 'zip':
                return self._process_zip(name, content)
            else:
                # Text or code file
                return self._process_text(name, content, file_type)
        except Exception as e:
            return {
                'name': name,
                'type': file_type,
                'error': str(e),
                'content': f"[Error processing file: {e}]"
            }

    def _process_pdf(self, name: str, content_b64: str) -> Dict:
        """Extract text from PDF file."""
        logger.debug(f"_process_pdf called for: {name}, PDF_SUPPORT: {PDF_SUPPORT}")

        if not PDF_SUPPORT:
            return {
                'name': name,
                'type': 'pdf',
                'content': "[PDF text extraction not available. Install pypdf: pip install pypdf]",
                'pages': 0
            }

        try:
            logger.debug(f"Decoding base64 PDF, length: {len(content_b64)}")

            # Validate and decode base64
            try:
                pdf_bytes = base64.b64decode(content_b64, validate=True)
            except binascii.Error as e:
                logger.warning(f"Invalid base64 encoding for PDF {name}: {e}")
                return {
                    'name': name,
                    'type': 'pdf',
                    'error': 'Invalid file encoding',
                    'content': '[Error: Invalid base64 encoding. Please re-upload the file.]'
                }

            logger.debug(f"Decoded PDF to {len(pdf_bytes)} bytes")
            pdf_file = io.BytesIO(pdf_bytes)
            reader = PdfReader(pdf_file)
            logger.debug(f"PDF {name} has {len(reader.pages)} pages")

            text_parts = []
            for i, page in enumerate(reader.pages):
                page_text = page.extract_text()
                logger.debug(f"PDF page {i+1}: {len(page_text) if page_text else 0} chars")
                if page_text:
                    text_parts.append(f"--- Page {i + 1} ---\n{page_text}")

            full_text = "\n\n".join(text_parts)
            logger.debug(f"Total extracted PDF text: {len(full_text)} chars")

            # Truncate if too long
            if len(full_text) > self.MAX_TEXT_LENGTH:
                full_text = full_text[:self.MAX_TEXT_LENGTH] + "\n\n[... content truncated ...]"

            return {
                'name': name,
                'type': 'pdf',
                'content': full_text,
                'pages': len(reader.pages)
            }
        except Exception as e:
            logger.error(f"Error processing PDF {name}: {e}", exc_info=True)
            return {
                'name': name,
                'type': 'pdf',
                'error': str(e),
                'content': f"[Error reading PDF: {e}]"
            }

    def _process_zip(self, name: str, content_b64: str) -> Dict:
        """List and extract text files from ZIP archive."""
        try:
            # Validate and decode base64
            try:
                zip_bytes = base64.b64decode(content_b64, validate=True)
            except binascii.Error as e:
                logger.warning(f"Invalid base64 encoding for ZIP {name}: {e}")
                return {
                    'name': name,
                    'type': 'zip',
                    'error': 'Invalid file encoding',
                    'content': '[Error: Invalid base64 encoding. Please re-upload the file.]'
                }

            zip_file = io.BytesIO(zip_bytes)

            with zipfile.ZipFile(zip_file, 'r') as zf:
                file_list = zf.namelist()

                # Extract text from readable files
                extracted_texts = []
                total_size = 0

                for file_name in file_list:
                    # Skip directories and hidden files
                    if file_name.endswith('/') or file_name.startswith('__'):
                        continue

                    # Check if it's a text file
                    ext = file_name.split('.')[-1].lower() if '.' in file_name else ''
                    text_extensions = ['txt', 'md', 'json', 'xml', 'csv', 'py', 'js', 'ts',
                                       'html', 'css', 'java', 'c', 'cpp', 'h', 'go', 'rs',
                                       'rb', 'php', 'sh', 'yaml', 'yml', 'toml', 'ini', 'cfg']

                    if ext in text_extensions:
                        try:
                            with zf.open(file_name) as f:
                                file_content = f.read().decode('utf-8', errors='ignore')
                                if len(file_content) > 10000:
                                    file_content = file_content[:10000] + "\n[... truncated ...]"
                                extracted_texts.append(f"=== {file_name} ===\n{file_content}")
                                total_size += len(file_content)

                                # Stop if we've extracted too much
                                if total_size > self.MAX_TEXT_LENGTH:
                                    extracted_texts.append("\n[... additional files not extracted ...]")
                                    break
                        except Exception:
                            pass  # Skip files that can't be read

                content = f"ZIP Archive: {name}\nFiles: {len(file_list)}\n\n"
                content += "Contents:\n" + "\n".join(f"  - {f}" for f in file_list[:50])
                if len(file_list) > 50:
                    content += f"\n  ... and {len(file_list) - 50} more files"

                if extracted_texts:
                    content += "\n\n--- Extracted Text Files ---\n\n"
                    content += "\n\n".join(extracted_texts)

                return {
                    'name': name,
                    'type': 'zip',
                    'content': content,
                    'files': len(file_list)
                }
        except Exception as e:
            return {
                'name': name,
                'type': 'zip',
                'error': str(e),
                'content': f"[Error reading ZIP: {e}]"
            }

    def _process_text(self, name: str, content: str, file_type: str) -> Dict:
        """Process text/code file."""
        # Truncate if too long
        if len(content) > self.MAX_TEXT_LENGTH:
            content = content[:self.MAX_TEXT_LENGTH] + "\n\n[... content truncated ...]"

        return {
            'name': name,
            'type': file_type,
            'content': content
        }

    def format_files_for_context(self, files: List[Dict]) -> str:
        """
        Format processed files into a string for inclusion in chat context.

        Args:
            files: List of file dictionaries

        Returns:
            Formatted string with all file contents
        """
        logger.debug(f"format_files_for_context called with {len(files) if files else 0} files")
        if not files:
            return ""

        parts = ["The user has shared the following files:\n"]

        for file_data in files:
            logger.debug(f"Processing file_data: {file_data.get('name', 'unknown')}")
            processed = self.process_file(file_data)
            name = processed.get('name', 'unknown')
            content = processed.get('content', '')
            file_type = processed.get('type', 'text')

            if file_type == 'pdf':
                pages = processed.get('pages', 0)
                parts.append(f"\n📄 **{name}** (PDF, {pages} pages):\n```\n{content}\n```\n")
            elif file_type == 'zip':
                file_count = processed.get('files', 0)
                parts.append(f"\n📦 **{name}** (ZIP, {file_count} files):\n```\n{content}\n```\n")
            elif file_type == 'code':
                # Detect language from extension
                ext = name.split('.')[-1].lower() if '.' in name else ''
                parts.append(f"\n💻 **{name}**:\n```{ext}\n{content}\n```\n")
            else:
                parts.append(f"\n📝 **{name}**:\n```\n{content}\n```\n")

        return "".join(parts)


# Global instance
file_processor = FileProcessor()
