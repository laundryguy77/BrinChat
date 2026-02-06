"""
File Storage Service - Nextcloud WebDAV Integration

Uploads generated images/videos to Nextcloud for persistent storage.
Used by Adult Mode to store generated content in joel/Lexi/ folder.
"""

import httpx
import logging
import os
from typing import Optional, Dict, Any
from datetime import datetime
import base64

from app.config import NEXTCLOUD_URL, NEXTCLOUD_USER, NEXTCLOUD_PASS

logger = logging.getLogger(__name__)

# Lexi folder for adult mode content
NEXTCLOUD_LEXI_FOLDER = os.getenv("NEXTCLOUD_LEXI_FOLDER", "Lexi")


class FileStorageService:
    """
    Handles file uploads to Nextcloud via WebDAV.
    
    Used by adult mode to store generated images/videos persistently.
    Files are stored in the user's Lexi folder for later access.
    """
    
    def __init__(
        self,
        base_url: str = None,
        username: str = None,
        password: str = None,
        folder: str = None
    ):
        self.base_url = (base_url or NEXTCLOUD_URL).rstrip('/')
        self.username = username or NEXTCLOUD_USER
        self.password = password or NEXTCLOUD_PASS
        self.folder = folder or NEXTCLOUD_LEXI_FOLDER
        
        # WebDAV endpoint
        self.webdav_url = f"{self.base_url}/remote.php/dav/files/{self.username}"
        
        self.client = httpx.AsyncClient(
            auth=(self.username, self.password),
            timeout=60.0
        )
        
        logger.info(f"FileStorageService initialized: {self.webdav_url}/{self.folder}/")
    
    async def ensure_folder_exists(self) -> bool:
        """Ensure the Lexi folder exists in Nextcloud."""
        try:
            folder_url = f"{self.webdav_url}/{self.folder}"
            
            # Check if folder exists (PROPFIND)
            response = await self.client.request(
                "PROPFIND",
                folder_url,
                headers={"Depth": "0"}
            )
            
            if response.status_code == 207:  # Multi-Status = exists
                return True
            
            # Create folder (MKCOL)
            response = await self.client.request("MKCOL", folder_url)
            if response.status_code in (201, 405):  # 201=Created, 405=Already exists
                logger.info(f"Created folder: {self.folder}")
                return True
            
            logger.error(f"Failed to create folder: {response.status_code}")
            return False
            
        except Exception as e:
            logger.exception(f"Error ensuring folder exists: {e}")
            return False
    
    async def upload_from_url(
        self,
        image_url: str,
        filename: str = None,
        subfolder: str = None
    ) -> Optional[Dict[str, Any]]:
        """
        Download an image from URL and upload to Nextcloud.
        
        Args:
            image_url: URL of the image to download
            filename: Optional filename (auto-generated if not provided)
            subfolder: Optional subfolder within Lexi folder
            
        Returns:
            Dict with url, filename, size on success, None on failure
        """
        try:
            # Download the image
            async with httpx.AsyncClient(timeout=120.0) as download_client:
                response = await download_client.get(image_url)
                response.raise_for_status()
                image_data = response.content
                content_type = response.headers.get("content-type", "image/png")
            
            # Generate filename if not provided
            if not filename:
                ext = self._get_extension(content_type)
                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                filename = f"generated_{timestamp}{ext}"
            
            # Build path
            folder_path = self.folder
            if subfolder:
                folder_path = f"{self.folder}/{subfolder}"
            
            # Ensure folder exists
            await self.ensure_folder_exists()
            
            # Upload to Nextcloud
            upload_url = f"{self.webdav_url}/{folder_path}/{filename}"
            
            upload_response = await self.client.put(
                upload_url,
                content=image_data,
                headers={"Content-Type": content_type}
            )
            
            if upload_response.status_code in (201, 204):
                # Build public URL (if sharing is enabled)
                public_url = f"{self.base_url}/remote.php/dav/files/{self.username}/{folder_path}/{filename}"
                
                logger.info(f"Uploaded to Nextcloud: {filename} ({len(image_data)} bytes)")
                
                return {
                    "success": True,
                    "filename": filename,
                    "path": f"{folder_path}/{filename}",
                    "url": public_url,
                    "size": len(image_data),
                    "content_type": content_type
                }
            else:
                logger.error(f"Upload failed: {upload_response.status_code} - {upload_response.text[:200]}")
                return None
                
        except Exception as e:
            logger.exception(f"Error uploading from URL: {e}")
            return None
    
    async def upload_base64(
        self,
        base64_data: str,
        filename: str = None,
        content_type: str = "image/png",
        subfolder: str = None
    ) -> Optional[Dict[str, Any]]:
        """
        Upload base64-encoded image to Nextcloud.
        
        Args:
            base64_data: Base64-encoded image data
            filename: Optional filename
            content_type: MIME type of the image
            subfolder: Optional subfolder within Lexi folder
            
        Returns:
            Dict with url, filename, size on success, None on failure
        """
        try:
            # Decode base64
            if "," in base64_data:  # Handle data URL format
                base64_data = base64_data.split(",", 1)[1]
            
            image_data = base64.b64decode(base64_data)
            
            # Generate filename if not provided
            if not filename:
                ext = self._get_extension(content_type)
                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                filename = f"generated_{timestamp}{ext}"
            
            # Build path
            folder_path = self.folder
            if subfolder:
                folder_path = f"{self.folder}/{subfolder}"
            
            # Ensure folder exists
            await self.ensure_folder_exists()
            
            # Upload to Nextcloud
            upload_url = f"{self.webdav_url}/{folder_path}/{filename}"
            
            response = await self.client.put(
                upload_url,
                content=image_data,
                headers={"Content-Type": content_type}
            )
            
            if response.status_code in (201, 204):
                public_url = f"{self.base_url}/remote.php/dav/files/{self.username}/{folder_path}/{filename}"
                
                logger.info(f"Uploaded base64 to Nextcloud: {filename} ({len(image_data)} bytes)")
                
                return {
                    "success": True,
                    "filename": filename,
                    "path": f"{folder_path}/{filename}",
                    "url": public_url,
                    "size": len(image_data),
                    "content_type": content_type
                }
            else:
                logger.error(f"Upload failed: {response.status_code}")
                return None
                
        except Exception as e:
            logger.exception(f"Error uploading base64: {e}")
            return None
    
    def _get_extension(self, content_type: str) -> str:
        """Get file extension from content type."""
        extensions = {
            "image/png": ".png",
            "image/jpeg": ".jpg",
            "image/gif": ".gif",
            "image/webp": ".webp",
            "video/mp4": ".mp4",
            "video/webm": ".webm",
        }
        return extensions.get(content_type, ".bin")
    
    async def close(self):
        """Close the HTTP client."""
        await self.client.aclose()
    
    async def __aenter__(self):
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.close()


# Singleton instance
_file_storage: Optional[FileStorageService] = None


def get_file_storage() -> FileStorageService:
    """Get or create the global FileStorageService instance."""
    global _file_storage
    if _file_storage is None:
        _file_storage = FileStorageService()
    return _file_storage
