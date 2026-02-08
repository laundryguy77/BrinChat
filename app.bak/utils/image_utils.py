"""Image utilities for BrinChat - compression and optimization."""

import base64
import io
import logging
from typing import List, Optional, Tuple
from PIL import Image

logger = logging.getLogger(__name__)

# Max dimensions for images sent to LLM (keeps quality while reducing tokens)
MAX_IMAGE_DIMENSION = 1568  # Claude's recommended max
MAX_IMAGE_BYTES = 500_000   # ~500KB max per image after compression
JPEG_QUALITY = 85           # Good balance of quality vs size


def compress_image_base64(
    base64_data: str,
    max_dimension: int = MAX_IMAGE_DIMENSION,
    max_bytes: int = MAX_IMAGE_BYTES,
    quality: int = JPEG_QUALITY
) -> str:
    """
    Compress a base64-encoded image to reduce token usage.
    
    Args:
        base64_data: Base64 string (with or without data: prefix)
        max_dimension: Maximum width or height
        max_bytes: Maximum size in bytes after compression
        quality: JPEG quality (1-100)
    
    Returns:
        Compressed base64 string (without data: prefix)
    """
    try:
        # Strip data: prefix if present
        if base64_data.startswith("data:"):
            # Extract just the base64 part
            base64_data = base64_data.split(",", 1)[-1]
        
        # Decode
        image_bytes = base64.b64decode(base64_data)
        original_size = len(image_bytes)
        
        # Open with PIL
        img = Image.open(io.BytesIO(image_bytes))
        original_dims = img.size
        
        # Convert to RGB if necessary (for JPEG output)
        if img.mode in ('RGBA', 'LA', 'P'):
            # Create white background for transparency
            background = Image.new('RGB', img.size, (255, 255, 255))
            if img.mode == 'P':
                img = img.convert('RGBA')
            background.paste(img, mask=img.split()[-1] if img.mode == 'RGBA' else None)
            img = background
        elif img.mode != 'RGB':
            img = img.convert('RGB')
        
        # Resize if too large
        width, height = img.size
        if width > max_dimension or height > max_dimension:
            ratio = min(max_dimension / width, max_dimension / height)
            new_size = (int(width * ratio), int(height * ratio))
            img = img.resize(new_size, Image.Resampling.LANCZOS)
            logger.info(f"[ImageCompress] Resized from {original_dims} to {img.size}")
        
        # Compress to JPEG with quality adjustment
        current_quality = quality
        output = io.BytesIO()
        
        while current_quality >= 30:
            output.seek(0)
            output.truncate()
            img.save(output, format='JPEG', quality=current_quality, optimize=True)
            
            if output.tell() <= max_bytes:
                break
            
            # Reduce quality and try again
            current_quality -= 10
        
        # Get final bytes
        compressed_bytes = output.getvalue()
        final_size = len(compressed_bytes)
        
        # Encode back to base64
        compressed_b64 = base64.b64encode(compressed_bytes).decode('utf-8')
        
        reduction = ((original_size - final_size) / original_size) * 100
        logger.info(
            f"[ImageCompress] {original_size:,} -> {final_size:,} bytes "
            f"({reduction:.1f}% reduction, q={current_quality})"
        )
        
        return compressed_b64
        
    except Exception as e:
        logger.error(f"[ImageCompress] Failed to compress image: {e}")
        # Return original if compression fails
        if base64_data.startswith("data:"):
            return base64_data.split(",", 1)[-1]
        return base64_data


def compress_images(images: Optional[List[str]], max_dimension: int = MAX_IMAGE_DIMENSION) -> Optional[List[str]]:
    """
    Compress a list of base64 images.
    
    Args:
        images: List of base64-encoded images
        max_dimension: Maximum width or height
    
    Returns:
        List of compressed base64 strings
    """
    if not images:
        return images
    
    compressed = []
    for i, img in enumerate(images):
        try:
            original_len = len(img)
            compressed_img = compress_image_base64(img, max_dimension=max_dimension)
            compressed.append(compressed_img)
            logger.debug(f"[ImageCompress] Image {i}: {original_len:,} -> {len(compressed_img):,} chars")
        except Exception as e:
            logger.error(f"[ImageCompress] Failed to compress image {i}: {e}")
            # Keep original on failure
            if img.startswith("data:"):
                compressed.append(img.split(",", 1)[-1])
            else:
                compressed.append(img)
    
    return compressed
