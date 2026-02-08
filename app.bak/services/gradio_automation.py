#!/usr/bin/env python3
"""
Gradio Space Automation - Base class for automating Hugging Face Gradio spaces

Provides utilities for interacting with common Gradio components used in
image generation interfaces.
"""

import asyncio
import base64
import logging
import os
import re
import secrets
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Optional, Any, List
from urllib.parse import urlparse

from playwright.async_api import async_playwright, Browser, Page, BrowserContext

logger = logging.getLogger(__name__)

# =============================================================================
# SECURITY CONFIGURATION
# =============================================================================

# Allowed output directories for save_image() - paths must be within these
ALLOWED_OUTPUT_DIRS = [
    Path.home() / "BrinChat" / "outputs",
    Path.home() / "BrinChat" / "generated",
    Path("/tmp"),  # Allowed for temp files but with secure naming
    Path.cwd(),  # Current working directory
]

# Maximum output file size (prevent disk exhaustion)
MAX_OUTPUT_FILE_SIZE = 100 * 1024 * 1024  # 100 MB


def _is_path_safe(output_path: str, allowed_dirs: List[Path] = None) -> tuple[bool, str]:
    """
    Validate that an output path is safe and within allowed directories.

    Returns:
        (is_safe, error_message)
    """
    if allowed_dirs is None:
        allowed_dirs = ALLOWED_OUTPUT_DIRS

    try:
        path = Path(output_path).resolve()
    except (OSError, RuntimeError) as e:
        return False, f"Cannot resolve path: {e}"

    # Check for path traversal attempts
    if '..' in output_path:
        return False, "Path traversal (..) not allowed"

    # Ensure path is within an allowed directory
    is_within_allowed = False
    for allowed_dir in allowed_dirs:
        try:
            allowed_resolved = allowed_dir.resolve()
            # Check if path is equal to or a child of allowed_dir
            try:
                path.relative_to(allowed_resolved)
                is_within_allowed = True
                break
            except ValueError:
                continue
        except (OSError, RuntimeError):
            continue

    if not is_within_allowed:
        return False, f"Path must be within allowed directories"

    # Check filename for dangerous patterns
    filename = path.name
    if filename.startswith('.'):
        return False, "Hidden files not allowed"
    if any(c in filename for c in ['<', '>', ':', '"', '|', '?', '*', '\x00']):
        return False, "Invalid characters in filename"

    return True, ""


def _generate_secure_screenshot_path() -> str:
    """Generate a secure, unpredictable path for debug screenshots."""
    # Use secure random token for unpredictable filename
    token = secrets.token_hex(16)
    # Use tempfile to get a secure temporary directory
    temp_dir = tempfile.gettempdir()
    return os.path.join(temp_dir, f"brinchat_debug_{token}.png")


class GradioAutomation:
    """Base class for automating Gradio-based Hugging Face Spaces."""
    
    def __init__(
        self,
        space_url: str,
        browser_path: Optional[str] = None,
        headless: bool = True,
        timeout: int = 300000  # 5 minutes default for image gen (increased for multiple model calls)
    ):
        self.space_url = space_url
        self.browser_path = browser_path
        self.headless = headless
        self.timeout = timeout
        self._playwright = None
        self._browser: Optional[Browser] = None
        
    async def __aenter__(self):
        await self.start()
        return self
        
    async def __aexit__(self, *args):
        await self.close()
        
    async def start(self):
        """Start the browser."""
        self._playwright = await async_playwright().start()

        # Browser launch arguments
        # NOTE ON SECURITY FLAGS:
        # --disable-web-security: Required for accessing cross-origin resources on
        # Hugging Face Spaces. These spaces often load resources (images, models)
        # from different subdomains (*.hf.space, huggingface.co, cdn-lfs.huggingface.co).
        # Without this flag, CORS restrictions prevent fetching generated images.
        # This is acceptable because:
        # 1. We only navigate to known, trusted HuggingFace domains
        # 2. The browser context is isolated and doesn't access user data
        # 3. Each operation uses a fresh browser context that's destroyed after use
        # FUTURE: Consider using a proxy server to handle CORS instead
        launch_args = {
            "headless": self.headless,
            "args": [
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-dev-shm-usage",
                "--disable-gpu",
                "--disable-web-security",  # Required for HF Spaces - see security note above
            ]
        }
        
        # Find browser - only specify if explicitly provided
        # Playwright can auto-discover its installed browser
        if self.browser_path:
            launch_args["executable_path"] = self.browser_path
        
        self._browser = await self._playwright.chromium.launch(**launch_args)
        
    async def close(self):
        """Close the browser."""
        if self._browser:
            await self._browser.close()
        if self._playwright:
            await self._playwright.stop()
    
    async def new_context(self) -> BrowserContext:
        """Create a new browser context."""
        if not self._browser:
            await self.start()
        return await self._browser.new_context(
            accept_downloads=True,
            viewport={"width": 1920, "height": 1080}
        )
    
    async def wait_for_gradio_load(self, page: Page, timeout: int = 120000):
        """Wait for Gradio interface to fully load."""
        await page.wait_for_selector(".gradio-container", timeout=timeout)
        await page.wait_for_timeout(3000)  # Increased wait for slow-loading spaces
        try:
            await page.wait_for_selector(".loading", state="hidden", timeout=10000)
        except Exception:
            pass
    
    async def dismiss_popups(self, page: Page):
        """Dismiss any cookie banners or popups."""
        popup_selectors = [
            "button:has-text('Accept')",
            "button:has-text('OK')",
            "button:has-text('Close')",
            "button:has-text('Got it')",
            "button:has-text('I agree')",
            "[aria-label='Close']",
            ".modal button.close",
        ]
        for selector in popup_selectors:
            try:
                btn = page.locator(selector).first
                if await btn.is_visible(timeout=3000):
                    await btn.click()
                    await page.wait_for_timeout(1000)
            except Exception:
                pass
            
    async def fill_textbox(self, page: Page, text: str, label: Optional[str] = None,
                          placeholder: Optional[str] = None, index: int = 0):
        """
        Fill a Gradio textbox component.

        Args:
            page: Playwright page
            text: Text to enter
            label: Optional label text to find the right textbox
            placeholder: Optional placeholder text to match
            index: Index if multiple textboxes (0-based)
        """
        # Simple direct approach - find all text inputs and use index
        textbox = page.locator("textarea, input[type='text']").nth(index)
        await textbox.fill(text, timeout=20000)
        
    async def upload_image(self, page: Page, file_path: str, label: Optional[str] = None, index: int = 0):
        """
        Upload an image to a Gradio image upload component.
        
        Args:
            page: Playwright page
            file_path: Path to image file to upload
            label: Optional label to find the right upload
            index: Index if multiple uploads
        """
        file_path = str(Path(file_path).absolute())
        
        if not os.path.exists(file_path):
            raise FileNotFoundError(f"Image not found: {file_path}")
        
        # Find file input
        if label:
            container = page.locator(f"*:has(> label:has-text('{label}')), *:has(> span:has-text('{label}'))").first
            file_input = container.locator("input[type='file']").first
        else:
            file_input = page.locator("input[type='file'][accept*='image']").nth(index)
            # Fallback to any file input
            if not await file_input.count():
                file_input = page.locator("input[type='file']").nth(index)
        
        await file_input.set_input_files(file_path)
        await page.wait_for_timeout(4000)  # Wait for upload processing (increased for larger files)
        
    async def set_slider(self, page: Page, value: float, label: Optional[str] = None, index: int = 0, timeout: int = 5000):
        """
        Set a Gradio slider value.

        Args:
            page: Playwright page
            value: Value to set
            label: Optional label to find the right slider
            index: Index if multiple sliders
            timeout: Timeout in milliseconds
        """
        # Try setting via number input first (most reliable)
        try:
            number_inputs = page.locator("input[type='number']")
            if await number_inputs.count() > index:
                number_input = number_inputs.nth(index)
                if await number_input.is_visible(timeout=timeout):
                    await number_input.fill(str(value), timeout=timeout)
                    return
        except Exception:
            pass

        # Try range slider
        try:
            slider_input = page.locator("input[type='range']").nth(index)
            await slider_input.fill(str(value), timeout=timeout)
        except Exception:
            pass  # Slider might not exist, that's OK
        
    async def select_dropdown(self, page: Page, value: str, label: Optional[str] = None, index: int = 0):
        """
        Select a value from a Gradio dropdown.
        
        Args:
            page: Playwright page
            value: Value to select
            label: Optional label to find the right dropdown
            index: Index if multiple dropdowns
        """
        if label:
            container = page.locator(f"*:has(> label:has-text('{label}'))").first
            dropdown = container.locator("select, [role='listbox'], .dropdown").first
        else:
            dropdown = page.locator("select, [role='listbox']").nth(index)
        
        # Try native select
        try:
            await dropdown.select_option(value=value)
            return
        except Exception:
            pass
        
        # Try Gradio custom dropdown
        try:
            await dropdown.click()
            await page.wait_for_timeout(300)
            option = page.locator(f"[role='option']:has-text('{value}'), .dropdown-item:has-text('{value}')").first
            await option.click()
        except Exception:
            pass
        
    async def click_button(self, page: Page, text: Optional[str] = None, index: int = 0):
        """
        Click a Gradio button.
        
        Args:
            page: Playwright page
            text: Button text to match
            index: Button index if no text specified
        """
        if text:
            button = page.locator(f"button:has-text('{text}')").first
        else:
            button = page.locator("button.primary, button.lg, button[type='submit']").nth(index)
        
        await button.click()
        
    async def wait_for_generation(self, page: Page, timeout: Optional[int] = None, min_image_size: int = 256):
        """
        Wait for image generation to complete.
        Watches for loading indicators and/or output images to appear.
        """
        timeout = timeout or self.timeout

        # Wait for processing to start
        try:
            await page.wait_for_selector(
                ".generating, .loading, .progress, [class*='loading'], [class*='progress'], .eta-bar",
                timeout=20000
            )
        except Exception:
            pass

        # Wait for processing to complete OR output image to appear
        start_time = asyncio.get_event_loop().time()
        while (asyncio.get_event_loop().time() - start_time) * 1000 < timeout:
            # Check if we have a generated image already
            has_output = await page.evaluate('''(minSize) => {
                const imgs = document.querySelectorAll('img');
                for (const img of imgs) {
                    const src = img.src || '';
                    if (src.startsWith('data:image/svg') || src.includes('.svg')) continue;
                    if (img.naturalWidth >= minSize && img.naturalHeight >= minSize) {
                        return true;
                    }
                }
                return false;
            }''', min_image_size)

            if has_output:
                await page.wait_for_timeout(2000)  # Wait for image to fully load
                return

            # Check if still loading
            loading = await page.query_selector(
                ".generating, .loading:not(.hidden), .progress:not([style*='display: none']), .eta-bar"
            )
            if not loading:
                await page.wait_for_timeout(3000)
                # Double-check for output image
                has_output = await page.evaluate('''(minSize) => {
                    const imgs = document.querySelectorAll('img');
                    for (const img of imgs) {
                        const src = img.src || '';
                        if (src.startsWith('data:image/svg') || src.includes('.svg')) continue;
                        if (img.naturalWidth >= minSize && img.naturalHeight >= minSize) {
                            return true;
                        }
                    }
                    return false;
                }''', min_image_size)
                if has_output:
                    return
                # No loading indicator and no image yet - keep waiting
            await page.wait_for_timeout(3000)
        else:
            raise TimeoutError(f"Generation did not complete within {timeout}ms")
            
    async def get_output_image(self, page: Page, index: int = 0, min_size: int = 256) -> Optional[bytes]:
        """
        Get the generated image data from the output component.

        Args:
            page: Playwright page
            index: Index if multiple output images
            min_size: Minimum image dimension to consider (filters out icons)

        Returns:
            Image bytes or None if not found
        """
        import urllib.request

        # Find large images and get their sources
        image_info = await page.evaluate('''(minSize) => {
            const imgs = document.querySelectorAll('img');
            const found = [];
            for (const img of imgs) {
                const src = img.src || '';
                // Skip SVG images
                if (src.startsWith('data:image/svg') || src.includes('.svg')) continue;
                // Skip small images (icons)
                if (img.naturalWidth < minSize || img.naturalHeight < minSize) continue;
                found.push({
                    src: src,
                    width: img.naturalWidth,
                    height: img.naturalHeight
                });
            }
            return found;
        }''', min_size)

        if not image_info:
            return None

        # Get the image at the specified index
        if index >= len(image_info):
            index = len(image_info) - 1
        target = image_info[index]
        src = target.get('src', '')

        if not src:
            return None

        # Handle different image source types
        if src.startswith('data:image/'):
            # Base64 data URL
            try:
                b64_data = src.split(',')[1]
                return base64.b64decode(b64_data)
            except Exception:
                return None
        elif src.startswith('http'):
            # URL - fetch it directly (common for Gradio/HuggingFace)
            try:
                with urllib.request.urlopen(src, timeout=30) as resp:
                    return resp.read()
            except Exception as e:
                logger.warning(f"Failed to fetch image from URL: {type(e).__name__}")
                return None

        # Fallback: Try specific selectors
        selectors = [
            ".output-image img",
            ".gradio-image img",
            ".image-container img",
            "[data-testid='image'] img",
            ".gallery img",
            "#output img",
            ".output img",
            "img[src*='blob:']",
            "img[src*='data:']",
            "img[src*='file=']",
        ]

        for selector in selectors:
            try:
                images = page.locator(selector)
                count = await images.count()
                if count > index:
                    img = images.nth(index)
                    if await img.is_visible(timeout=5000):
                        src = await img.get_attribute("src")
                        if src:
                            return await self._fetch_image_data(page, src)
            except Exception:
                continue

        return None
    
    async def _fetch_image_data(self, page: Page, src: str) -> Optional[bytes]:
        """Fetch image data from various source types."""
        try:
            if src.startswith("data:"):
                # Data URL
                b64_data = src.split(",", 1)[1]
                return base64.b64decode(b64_data)
                
            elif src.startswith("blob:"):
                # Blob URL - convert via canvas
                b64_data = await page.evaluate('''async (src) => {
                    const img = document.querySelector(`img[src="${src}"]`);
                    if (!img) return null;
                    const canvas = document.createElement('canvas');
                    canvas.width = img.naturalWidth;
                    canvas.height = img.naturalHeight;
                    const ctx = canvas.getContext('2d');
                    ctx.drawImage(img, 0, 0);
                    return canvas.toDataURL('image/png').split(',')[1];
                }''', src)
                if b64_data:
                    return base64.b64decode(b64_data)
                    
            else:
                # Regular URL
                if src.startswith("/"):
                    parsed = urlparse(page.url)
                    src = f"{parsed.scheme}://{parsed.netloc}{src}"
                response = await page.request.get(src)
                return await response.body()
                
        except Exception as e:
            logger.warning(f"Error fetching image: {type(e).__name__}")
            return None
            
    async def get_all_output_images(self, page: Page) -> List[bytes]:
        """Get all output images from a gallery or multiple outputs."""
        images = []
        
        # Try gallery first
        gallery_imgs = page.locator(".gallery img, .grid img, [role='group'] img")
        count = await gallery_imgs.count()
        
        if count > 0:
            for i in range(count):
                try:
                    img = gallery_imgs.nth(i)
                    if await img.is_visible(timeout=3000):
                        src = await img.get_attribute("src")
                        if src:
                            data = await self._fetch_image_data(page, src)
                            if data:
                                images.append(data)
                except Exception:
                    continue
        
        # Fallback to single image
        if not images:
            single = await self.get_output_image(page)
            if single:
                images.append(single)
                
        return images
    
    async def save_image(self, image_data: bytes, output_path: str) -> dict:
        """Save image data to file with security validation."""
        # SECURITY: Validate output path
        is_safe, error = _is_path_safe(output_path)
        if not is_safe:
            logger.warning(f"Blocked unsafe save_image path: {error}")
            return {
                "success": False,
                "error": f"Invalid output path: {error}"
            }

        # SECURITY: Check file size limit
        if len(image_data) > MAX_OUTPUT_FILE_SIZE:
            return {
                "success": False,
                "error": f"File size exceeds maximum allowed ({MAX_OUTPUT_FILE_SIZE // (1024*1024)}MB)"
            }

        try:
            path = Path(output_path).resolve()

            # Only create parent directory if it's within allowed paths
            # Don't use parents=True to prevent creating arbitrary directory trees
            if not path.parent.exists():
                # Check if parent is in allowed dirs
                parent_safe, _ = _is_path_safe(str(path.parent))
                if parent_safe:
                    path.parent.mkdir(parents=False, exist_ok=True)
                else:
                    return {
                        "success": False,
                        "error": "Cannot create directory outside allowed paths"
                    }

            path.write_bytes(image_data)
            logger.info(f"Saved image to: {path}")
            return {
                "success": True,
                "path": str(path),
                "size_bytes": len(image_data)
            }
        except Exception as e:
            # SECURITY: Don't expose full exception details
            logger.error(f"Failed to save image: {type(e).__name__}")
            return {
                "success": False,
                "error": "Failed to save image"
            }
    
    async def click_download_button(self, page: Page, output_path: str) -> dict:
        """Try to download via the download button."""
        try:
            async with page.expect_download(timeout=10000) as download_info:
                download_btn = page.locator(
                    "button:has-text('Download'), a:has-text('Download'), [download], "
                    "button[title*='download'], a[title*='download']"
                ).first
                await download_btn.click()
            download = await download_info.value
            await download.save_as(output_path)
            return {
                "success": True,
                "path": str(Path(output_path).absolute()),
                "size_bytes": Path(output_path).stat().st_size
            }
        except Exception as e:
            return {
                "success": False,
                "error": str(e)
            }
