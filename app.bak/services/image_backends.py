#!/usr/bin/env python3
"""
Image Generation Backends

Implements specific image generation workflows for different Hugging Face Spaces.
Supports text-to-image, image-to-image, inpainting, and upscaling.
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
from typing import Optional, List
from abc import ABC, abstractmethod

import aiohttp

from app.services.gradio_automation import GradioAutomation

logger = logging.getLogger(__name__)


def _generate_secure_debug_screenshot_path(prefix: str = "img_error") -> str:
    """Generate a secure, unpredictable path for debug screenshots."""
    token = secrets.token_hex(8)
    temp_dir = tempfile.gettempdir()
    return os.path.join(temp_dir, f"brinchat_{prefix}_{token}.png")


def _sanitize_error_message(error: Exception) -> str:
    """Sanitize error message to prevent leaking sensitive information."""
    error_str = str(error)
    # Remove potential file paths
    error_str = re.sub(r'/[^\s]+', '[path]', error_str)
    # Remove potential URLs with tokens/keys
    error_str = re.sub(r'https?://[^\s]+', '[url]', error_str)
    # Truncate to reasonable length
    if len(error_str) > 200:
        error_str = error_str[:200] + "..."
    return error_str


class ImageGeneratorBackend(ABC):
    """Abstract base class for image generation backends."""
    
    name: str = "base"
    
    @abstractmethod
    async def generate(self, **kwargs) -> dict:
        """Generate image(s). Implementation varies by backend."""
        pass


class TextToImageBackend(GradioAutomation, ImageGeneratorBackend):
    """
    Text-to-Image generation using Hugging Face Spaces.
    
    Supports various models including FLUX, Stable Diffusion, etc.
    """
    
    name = "text_to_image"
    
    # Space URLs to try in order of preference (using direct .hf.space URLs)
    SPACE_URLS = [
        "https://black-forest-labs-flux-1-schnell.hf.space",
        "https://stabilityai-stable-diffusion-3-5-large.hf.space",
        "https://multimodalart-flux-1-merged.hf.space",
        "https://prodia-sdxl-stable-diffusion-xl.hf.space",
    ]

    # Alternative uncensored spaces
    UNCENSORED_SPACE_URLS = [
        "https://yodayo-ai-yodayo-diffusion.hf.space",
        "https://cagliostrolab-animagine-xl-3-0.hf.space",
    ]
    
    def __init__(self, space_url: Optional[str] = None, uncensored: bool = False, **kwargs):
        url = space_url or (self.UNCENSORED_SPACE_URLS[0] if uncensored else self.SPACE_URLS[0])
        super().__init__(space_url=url, **kwargs)
        self.uncensored = uncensored
        
    async def generate(
        self,
        prompt: str,
        negative_prompt: str = "",
        width: int = 1024,
        height: int = 1024,
        num_images: int = 1,
        seed: Optional[int] = None,
        guidance_scale: float = 7.5,
        num_steps: int = 25,
        output_path: Optional[str] = None,
        return_base64: bool = False
    ) -> dict:
        """
        Generate image from text prompt.
        
        Args:
            prompt: Text description of the image
            negative_prompt: Things to avoid in the image
            width: Image width
            height: Image height
            num_images: Number of images to generate
            seed: Random seed for reproducibility
            guidance_scale: How closely to follow the prompt (CFG)
            num_steps: Number of diffusion steps
            output_path: Where to save the image
            return_base64: Return base64 data instead of saving
            
        Returns:
            dict with success status and path/base64/error
        """
        context = await self.new_context()
        page = await context.new_page()
        page.set_default_timeout(self.timeout)
        
        try:
            logger.info(f"Loading space: {self.space_url}")
            await page.goto(self.space_url, wait_until="domcontentloaded")
            await self.wait_for_gradio_load(page)
            await self.dismiss_popups(page)
            
            # Fill in the prompt
            logger.debug("Entering prompt...")
            await self.fill_textbox(page, prompt, index=0)
            
            # Try to fill negative prompt
            if negative_prompt:
                try:
                    await self.fill_textbox(page, negative_prompt, placeholder="negative")
                except Exception:
                    try:
                        await self.fill_textbox(page, negative_prompt, label="Negative")
                    except Exception:
                        pass
            
            # Try to set dimensions
            try:
                await self.set_slider(page, width, label="Width")
                await self.set_slider(page, height, label="Height")
            except Exception:
                pass
            
            # Try to set seed
            if seed is not None:
                try:
                    await self.fill_textbox(page, str(seed), label="Seed")
                except Exception:
                    pass
            
            # Try to set guidance scale
            try:
                await self.set_slider(page, guidance_scale, label="Guidance")
            except Exception:
                pass
            
            # Click generate button
            logger.info("Starting generation...")
            generate_buttons = ["Generate", "Create", "Run", "Submit", "Dream"]
            for btn_text in generate_buttons:
                try:
                    await self.click_button(page, text=btn_text)
                    break
                except Exception:
                    continue
            
            # Wait for generation
            logger.info("Waiting for image generation...")
            await self.wait_for_generation(page)
            
            # Get output image
            image_data = await self.get_output_image(page)
            if not image_data:
                # Try download button as fallback
                if output_path:
                    result = await self.click_download_button(page, output_path)
                    if result["success"]:
                        return result
                raise Exception("Could not retrieve generated image")
            
            # Handle output
            if return_base64:
                return {
                    "success": True,
                    "base64": base64.b64encode(image_data).decode('utf-8'),
                    "size_bytes": len(image_data),
                    "mime_type": "image/png"
                }
            
            # Generate output path if not provided
            if output_path is None:
                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                safe_prompt = re.sub(r'[^\w]', '_', prompt[:30])
                output_path = f"txt2img_{safe_prompt}_{timestamp}.png"
            
            return await self.save_image(image_data, output_path)
                
        except Exception as e:
            # SECURITY: Use secure random path for debug screenshot
            try:
                screenshot_path = _generate_secure_debug_screenshot_path("txt2img")
                await page.screenshot(path=screenshot_path)
                logger.debug(f"Debug screenshot saved to: {screenshot_path}")
            except Exception:
                pass
            # SECURITY: Sanitize error message
            logger.error(f"Text-to-image generation failed: {type(e).__name__}")
            return {"success": False, "error": _sanitize_error_message(e)}

        finally:
            await context.close()


class ImageToImageBackend(GradioAutomation, ImageGeneratorBackend):
    """
    Image-to-Image generation (variations, style transfer).
    
    Takes a source image and transforms it based on a prompt.
    """
    
    name = "image_to_image"
    
    SPACE_URLS = [
        "https://multimodalart-cosxl.hf.space",  # Primary - most reliable
        "https://diffusers-stable-diffusion-xl-img2img.hf.space",
        "https://h1t-ircam.hf.space",
    ]
    
    def __init__(self, space_url: Optional[str] = None, **kwargs):
        super().__init__(space_url=space_url or self.SPACE_URLS[0], **kwargs)
        
    async def generate(
        self,
        image_path: str,
        prompt: str,
        negative_prompt: str = "",
        strength: float = 0.7,
        guidance_scale: float = 7.5,
        seed: Optional[int] = None,
        output_path: Optional[str] = None,
        return_base64: bool = False
    ) -> dict:
        """
        Transform an image based on a prompt.
        
        Args:
            image_path: Path to source image
            prompt: Transformation description
            negative_prompt: Things to avoid
            strength: How much to transform (0.0-1.0, higher = more change)
            guidance_scale: How closely to follow the prompt
            seed: Random seed for reproducibility
            output_path: Where to save the result
            return_base64: Return base64 instead of saving
            
        Returns:
            dict with success status and path/base64/error
        """
        if not os.path.exists(image_path):
            return {"success": False, "error": f"Image not found: {image_path}"}
        
        context = await self.new_context()
        page = await context.new_page()
        page.set_default_timeout(self.timeout)
        
        try:
            logger.info(f"Loading space: {self.space_url}")
            await page.goto(self.space_url, wait_until="domcontentloaded")
            await self.wait_for_gradio_load(page)
            await self.dismiss_popups(page)
            
            # Upload source image
            logger.debug("Uploading source image...")
            await self.upload_image(page, image_path, index=0)
            await page.wait_for_timeout(4000)

            # Fill prompt
            logger.debug("Entering prompt...")
            await self.fill_textbox(page, prompt, index=0)

            # Negative prompt
            if negative_prompt:
                try:
                    await self.fill_textbox(page, negative_prompt, placeholder="negative")
                except Exception:
                    pass
            
            # Set strength
            try:
                await self.set_slider(page, strength, label="Strength")
            except Exception:
                try:
                    await self.set_slider(page, strength, label="Denoise")
                except Exception:
                    pass
            
            # Set guidance
            try:
                await self.set_slider(page, guidance_scale, label="Guidance")
            except Exception:
                pass
            
            # Generate
            logger.info("Starting transformation...")
            for btn_text in ["Generate", "Transform", "Run", "Submit"]:
                try:
                    await self.click_button(page, text=btn_text)
                    break
                except Exception:
                    continue
            
            # Wait
            logger.info("Waiting for image transformation...")
            await self.wait_for_generation(page)
            
            # Get output
            image_data = await self.get_output_image(page)
            if not image_data:
                raise Exception("Could not retrieve transformed image")
            
            if return_base64:
                return {
                    "success": True,
                    "base64": base64.b64encode(image_data).decode('utf-8'),
                    "size_bytes": len(image_data),
                    "mime_type": "image/png"
                }
            
            if output_path is None:
                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                output_path = f"img2img_{timestamp}.png"
            
            return await self.save_image(image_data, output_path)
                
        except Exception as e:
            # SECURITY: Use secure random path for debug screenshot
            try:
                screenshot_path = _generate_secure_debug_screenshot_path("img2img")
                await page.screenshot(path=screenshot_path)
                logger.debug(f"Debug screenshot saved to: {screenshot_path}")
            except Exception:
                pass
            # SECURITY: Sanitize error message
            logger.error(f"Image-to-image generation failed: {type(e).__name__}")
            return {"success": False, "error": _sanitize_error_message(e)}

        finally:
            await context.close()


class InpaintingBackend(GradioAutomation, ImageGeneratorBackend):
    """
    Inpainting - edit specific regions of an image.
    
    Uses a mask to specify which parts to regenerate.
    """
    
    name = "inpainting"
    
    SPACE_URLS = [
        "https://diffusers-stable-diffusion-xl-inpainting.hf.space",
        "https://runwayml-stable-diffusion-inpainting.hf.space",
        "https://multimodalart-cosxl-edit.hf.space",
    ]
    
    def __init__(self, space_url: Optional[str] = None, **kwargs):
        super().__init__(space_url=space_url or self.SPACE_URLS[0], **kwargs)
        
    async def generate(
        self,
        image_path: str,
        mask_path: str,
        prompt: str,
        negative_prompt: str = "",
        guidance_scale: float = 7.5,
        seed: Optional[int] = None,
        output_path: Optional[str] = None,
        return_base64: bool = False
    ) -> dict:
        """
        Inpaint regions of an image.
        
        Args:
            image_path: Path to source image
            mask_path: Path to mask image (white = areas to inpaint)
            prompt: What to generate in masked regions
            negative_prompt: Things to avoid
            guidance_scale: How closely to follow the prompt
            seed: Random seed
            output_path: Where to save result
            return_base64: Return base64 instead of saving
            
        Returns:
            dict with success status and path/base64/error
        """
        if not os.path.exists(image_path):
            return {"success": False, "error": f"Image not found: {image_path}"}
        if not os.path.exists(mask_path):
            return {"success": False, "error": f"Mask not found: {mask_path}"}
        
        context = await self.new_context()
        page = await context.new_page()
        page.set_default_timeout(self.timeout)
        
        try:
            logger.info(f"Loading space: {self.space_url}")
            await page.goto(self.space_url, wait_until="domcontentloaded")
            await self.wait_for_gradio_load(page)
            await self.dismiss_popups(page)
            
            # Upload source image
            logger.debug("Uploading source image...")
            await self.upload_image(page, image_path, index=0)
            await page.wait_for_timeout(3000)

            # Upload mask
            logger.debug("Uploading mask...")
            await self.upload_image(page, mask_path, index=1)
            await page.wait_for_timeout(3000)
            
            # Fill prompt
            logger.debug("Entering prompt...")
            await self.fill_textbox(page, prompt, index=0)
            
            # Negative prompt
            if negative_prompt:
                try:
                    await self.fill_textbox(page, negative_prompt, placeholder="negative")
                except Exception:
                    pass
            
            # Generate
            logger.info("Starting inpainting...")
            for btn_text in ["Inpaint", "Generate", "Run", "Submit"]:
                try:
                    await self.click_button(page, text=btn_text)
                    break
                except Exception:
                    continue
            
            # Wait
            logger.info("Waiting for inpainting to complete...")
            await self.wait_for_generation(page)
            
            # Get output
            image_data = await self.get_output_image(page)
            if not image_data:
                raise Exception("Could not retrieve inpainted image")
            
            if return_base64:
                return {
                    "success": True,
                    "base64": base64.b64encode(image_data).decode('utf-8'),
                    "size_bytes": len(image_data),
                    "mime_type": "image/png"
                }
            
            if output_path is None:
                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                output_path = f"inpaint_{timestamp}.png"
            
            return await self.save_image(image_data, output_path)
                
        except Exception as e:
            # SECURITY: Use secure random path for debug screenshot
            try:
                screenshot_path = _generate_secure_debug_screenshot_path("inpaint")
                await page.screenshot(path=screenshot_path)
                logger.debug(f"Debug screenshot saved to: {screenshot_path}")
            except Exception:
                pass
            # SECURITY: Sanitize error message
            logger.error(f"Inpainting generation failed: {type(e).__name__}")
            return {"success": False, "error": _sanitize_error_message(e)}

        finally:
            await context.close()


class UpscaleBackend(GradioAutomation, ImageGeneratorBackend):
    """
    Image upscaling / enhancement.
    
    Increases resolution and enhances details.
    """
    
    name = "upscale"
    
    SPACE_URLS = [
        "https://finegrain-finegrain-image-enhancer.hf.space",
        "https://kwai-kolors-supir.hf.space",
        "https://kim2091-open-upscaler.hf.space",
    ]
    
    def __init__(self, space_url: Optional[str] = None, **kwargs):
        super().__init__(space_url=space_url or self.SPACE_URLS[0], **kwargs)
        
    async def generate(
        self,
        image_path: str,
        scale: float = 2.0,
        output_path: Optional[str] = None,
        return_base64: bool = False
    ) -> dict:
        """
        Upscale an image.
        
        Args:
            image_path: Path to source image
            scale: Upscale factor (2.0, 4.0, etc.)
            output_path: Where to save result
            return_base64: Return base64 instead of saving
            
        Returns:
            dict with success status and path/base64/error
        """
        if not os.path.exists(image_path):
            return {"success": False, "error": f"Image not found: {image_path}"}
        
        context = await self.new_context()
        page = await context.new_page()
        page.set_default_timeout(self.timeout)
        
        try:
            logger.info(f"Loading space: {self.space_url}")
            await page.goto(self.space_url, wait_until="domcontentloaded")
            await self.wait_for_gradio_load(page)
            await self.dismiss_popups(page)
            
            # Upload image
            logger.debug("Uploading image...")
            await self.upload_image(page, image_path, index=0)
            await page.wait_for_timeout(4000)
            
            # Try to set scale
            try:
                await self.set_slider(page, scale, label="Scale")
            except Exception:
                try:
                    await self.select_dropdown(page, f"{int(scale)}x", label="Scale")
                except Exception:
                    pass
            
            # Generate
            logger.info("Starting upscale...")
            for btn_text in ["Upscale", "Enhance", "Generate", "Run", "Submit"]:
                try:
                    await self.click_button(page, text=btn_text)
                    break
                except Exception:
                    continue
            
            # Wait
            logger.info("Waiting for upscaling to complete...")
            await self.wait_for_generation(page)
            
            # Get output
            image_data = await self.get_output_image(page)
            if not image_data:
                if output_path:
                    result = await self.click_download_button(page, output_path)
                    if result["success"]:
                        return result
                raise Exception("Could not retrieve upscaled image")
            
            if return_base64:
                return {
                    "success": True,
                    "base64": base64.b64encode(image_data).decode('utf-8'),
                    "size_bytes": len(image_data),
                    "mime_type": "image/png"
                }
            
            if output_path is None:
                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                output_path = f"upscale_{timestamp}.png"
            
            return await self.save_image(image_data, output_path)
                
        except Exception as e:
            # SECURITY: Use secure random path for debug screenshot
            try:
                screenshot_path = _generate_secure_debug_screenshot_path("upscale")
                await page.screenshot(path=screenshot_path)
                logger.debug(f"Debug screenshot saved to: {screenshot_path}")
            except Exception:
                pass
            # SECURITY: Sanitize error message
            logger.error(f"Upscale generation failed: {type(e).__name__}")
            return {"success": False, "error": _sanitize_error_message(e)}

        finally:
            await context.close()


class ComfyUIBackend(ImageGeneratorBackend):
    """
    Local ComfyUI text-to-image generation.
    
    Connects to a local ComfyUI instance via HTTP API.
    Much faster than HuggingFace Spaces (no browser automation needed).
    """
    
    name = "comfyui"
    
    DEFAULT_URL = "http://localhost:3457"
    
    def __init__(
        self,
        api_url: Optional[str] = None,
        timeout: int = 120
    ):
        """
        Initialize ComfyUI backend.
        
        Args:
            api_url: ComfyUI HTTP API URL (default: localhost:3457)
            timeout: Request timeout in seconds
        """
        self.api_url = api_url or self.DEFAULT_URL
        self.timeout = timeout
        
    async def health_check(self) -> bool:
        """Check if ComfyUI API is available."""
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    f"{self.api_url}/health",
                    timeout=aiohttp.ClientTimeout(total=5)
                ) as resp:
                    return resp.status == 200
        except Exception:
            return False
    
    async def generate(
        self,
        prompt: str,
        negative_prompt: str = "",
        width: int = 1024,
        height: int = 1024,
        num_steps: int = 20,
        guidance_scale: float = 7.0,
        seed: Optional[int] = None,
        output_path: Optional[str] = None,
        return_base64: bool = False
    ) -> dict:
        """
        Generate image from text prompt via local ComfyUI.
        
        Args:
            prompt: Text description of the image
            negative_prompt: Things to avoid (currently ignored by SDXL workflow)
            width: Image width (default 1024)
            height: Image height (default 1024)
            num_steps: Number of diffusion steps (default 20)
            guidance_scale: CFG scale (default 7.0)
            seed: Random seed (None = random)
            output_path: Where to save the image
            return_base64: Return base64 data instead of saving
            
        Returns:
            dict with success status and path/base64/error
        """
        logger.info(f"ComfyUI generation: {prompt[:80]}...")
        
        try:
            payload = {
                "prompt": prompt,
                "negative_prompt": negative_prompt,
                "width": width,
                "height": height,
                "steps": num_steps,
                "cfg": guidance_scale,
            }
            if seed is not None:
                payload["seed"] = seed
                
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"{self.api_url}/generate",
                    json=payload,
                    timeout=aiohttp.ClientTimeout(total=self.timeout)
                ) as resp:
                    if resp.status != 200:
                        error_text = await resp.text()
                        return {"success": False, "error": f"ComfyUI API error: {error_text[:200]}"}
                    
                    result = await resp.json()
            
            if not result.get("success", False):
                return {"success": False, "error": result.get("error", "Unknown error")}
            
            # Result contains base64 image data
            image_b64 = result.get("base64")
            if not image_b64:
                return {"success": False, "error": "No image data in response"}
            
            if return_base64:
                return {
                    "success": True,
                    "base64": image_b64,
                    "size_bytes": len(base64.b64decode(image_b64)),
                    "mime_type": "image/png",
                    "generation_time": result.get("generation_time")
                }
            
            # Save to file
            image_data = base64.b64decode(image_b64)
            
            if output_path is None:
                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                safe_prompt = re.sub(r'[^\w]', '_', prompt[:30])
                output_path = f"comfyui_{safe_prompt}_{timestamp}.png"
            
            # Ensure directory exists
            output_dir = os.path.dirname(output_path)
            if output_dir:
                os.makedirs(output_dir, exist_ok=True)
            
            with open(output_path, "wb") as f:
                f.write(image_data)
            
            return {
                "success": True,
                "path": output_path,
                "size_bytes": len(image_data),
                "generation_time": result.get("generation_time")
            }
            
        except asyncio.TimeoutError:
            logger.error("ComfyUI generation timed out")
            return {"success": False, "error": "Generation timed out"}
        except aiohttp.ClientError as e:
            logger.error(f"ComfyUI connection error: {e}")
            return {"success": False, "error": f"Connection error: {_sanitize_error_message(e)}"}
        except Exception as e:
            logger.error(f"ComfyUI generation failed: {e}")
            return {"success": False, "error": _sanitize_error_message(e)}


class UnifiedImageGenerator:
    """
    Unified image generator supporting all image operations.
    
    - text_to_image: Generate from text description
    - image_to_image: Transform existing image
    - inpaint: Edit specific regions
    - upscale: Enhance resolution
    
    Supports multiple backends:
    - "huggingface" (default): Uses HuggingFace Spaces via browser automation
    - "comfyui": Uses local ComfyUI instance via HTTP API (faster, no browser)
    """
    
    def __init__(
        self,
        text_to_image_url: Optional[str] = None,
        image_to_image_url: Optional[str] = None,
        inpainting_url: Optional[str] = None,
        upscale_url: Optional[str] = None,
        comfyui_url: Optional[str] = None,
        preferred_backend: str = "huggingface",
        headless: bool = True,
        timeout: int = 300000  # 5 minutes (increased for multiple model calls)
    ):
        """
        Initialize unified image generator.
        
        Args:
            text_to_image_url: HuggingFace Space URL for text-to-image
            image_to_image_url: HuggingFace Space URL for img2img
            inpainting_url: HuggingFace Space URL for inpainting
            upscale_url: HuggingFace Space URL for upscaling
            comfyui_url: Local ComfyUI API URL (default: localhost:3457)
            preferred_backend: "huggingface" or "comfyui" for text_to_image
            headless: Run browser automation headless
            timeout: Timeout in milliseconds
        """
        self.headless = headless
        self.timeout = timeout
        self.preferred_backend = preferred_backend
        
        # Initialize backends (lazy loading)
        self._txt2img: Optional[TextToImageBackend] = None
        self._img2img: Optional[ImageToImageBackend] = None
        self._inpaint: Optional[InpaintingBackend] = None
        self._upscale: Optional[UpscaleBackend] = None
        self._comfyui: Optional[ComfyUIBackend] = None
        
        # Store URLs for lazy init
        self._txt2img_url = text_to_image_url
        self._img2img_url = image_to_image_url
        self._inpaint_url = inpainting_url
        self._upscale_url = upscale_url
        self._comfyui_url = comfyui_url
        
    async def _get_comfyui(self) -> ComfyUIBackend:
        if self._comfyui is None:
            self._comfyui = ComfyUIBackend(
                api_url=self._comfyui_url,
                timeout=int(self.timeout / 1000)  # Convert ms to seconds
            )
        return self._comfyui
        
    async def _get_txt2img(self) -> TextToImageBackend:
        if self._txt2img is None:
            self._txt2img = TextToImageBackend(
                space_url=self._txt2img_url,
                headless=self.headless,
                timeout=self.timeout
            )
            await self._txt2img.start()
        return self._txt2img
    
    async def _get_img2img(self) -> ImageToImageBackend:
        if self._img2img is None:
            self._img2img = ImageToImageBackend(
                space_url=self._img2img_url,
                headless=self.headless,
                timeout=self.timeout
            )
            await self._img2img.start()
        return self._img2img
    
    async def _get_inpaint(self) -> InpaintingBackend:
        if self._inpaint is None:
            self._inpaint = InpaintingBackend(
                space_url=self._inpaint_url,
                headless=self.headless,
                timeout=self.timeout
            )
            await self._inpaint.start()
        return self._inpaint
    
    async def _get_upscale(self) -> UpscaleBackend:
        if self._upscale is None:
            self._upscale = UpscaleBackend(
                space_url=self._upscale_url,
                headless=self.headless,
                timeout=self.timeout
            )
            await self._upscale.start()
        return self._upscale
        
    async def close(self):
        """Close all backends."""
        for backend in [self._txt2img, self._img2img, self._inpaint, self._upscale]:
            if backend:
                await backend.close()
        # ComfyUI backend doesn't need closing (HTTP only)
        
    async def __aenter__(self):
        return self
        
    async def __aexit__(self, *args):
        await self.close()
        
    async def text_to_image(self, backend: Optional[str] = None, **kwargs) -> dict:
        """
        Generate image from text.
        
        Args:
            backend: "huggingface" or "comfyui" (overrides preferred_backend)
            **kwargs: See TextToImageBackend.generate or ComfyUIBackend.generate
            
        Returns:
            dict with success status and base64/path/error
        """
        use_backend = backend or self.preferred_backend
        
        if use_backend == "comfyui":
            # Try ComfyUI first
            comfyui = await self._get_comfyui()
            if await comfyui.health_check():
                logger.info("Using ComfyUI backend for text-to-image")
                return await comfyui.generate(**kwargs)
            else:
                logger.warning("ComfyUI unavailable, falling back to HuggingFace")
        
        # Default to HuggingFace
        hf_backend = await self._get_txt2img()
        return await hf_backend.generate(**kwargs)
        
    async def image_to_image(self, **kwargs) -> dict:
        """Transform image. See ImageToImageBackend.generate for args."""
        backend = await self._get_img2img()
        return await backend.generate(**kwargs)
        
    async def inpaint(self, **kwargs) -> dict:
        """Inpaint image regions. See InpaintingBackend.generate for args."""
        backend = await self._get_inpaint()
        return await backend.generate(**kwargs)
        
    async def upscale(self, **kwargs) -> dict:
        """Upscale image. See UpscaleBackend.generate for args."""
        backend = await self._get_upscale()
        return await backend.generate(**kwargs)


# CLI for testing
async def main():
    import argparse
    
    parser = argparse.ArgumentParser(description="Generate images with AI")
    subparsers = parser.add_subparsers(dest="command", help="Generation mode")
    
    # Text to image
    txt_parser = subparsers.add_parser("text", help="Text to image")
    txt_parser.add_argument("prompt", help="Text prompt")
    txt_parser.add_argument("-o", "--output", help="Output path")
    txt_parser.add_argument("-n", "--negative", default="", help="Negative prompt")
    txt_parser.add_argument("--width", type=int, default=1024)
    txt_parser.add_argument("--height", type=int, default=1024)
    
    # Image to image
    img_parser = subparsers.add_parser("img2img", help="Image to image")
    img_parser.add_argument("image", help="Source image path")
    img_parser.add_argument("prompt", help="Transformation prompt")
    img_parser.add_argument("-o", "--output", help="Output path")
    img_parser.add_argument("-s", "--strength", type=float, default=0.7)
    
    # Inpainting
    inp_parser = subparsers.add_parser("inpaint", help="Inpainting")
    inp_parser.add_argument("image", help="Source image path")
    inp_parser.add_argument("mask", help="Mask image path")
    inp_parser.add_argument("prompt", help="Inpainting prompt")
    inp_parser.add_argument("-o", "--output", help="Output path")
    
    # Upscale
    up_parser = subparsers.add_parser("upscale", help="Upscale image")
    up_parser.add_argument("image", help="Source image path")
    up_parser.add_argument("-o", "--output", help="Output path")
    up_parser.add_argument("-s", "--scale", type=float, default=2.0)
    
    args = parser.parse_args()
    
    if not args.command:
        parser.print_help()
        return 1
    
    async with UnifiedImageGenerator() as gen:
        if args.command == "text":
            result = await gen.text_to_image(
                prompt=args.prompt,
                negative_prompt=args.negative,
                width=args.width,
                height=args.height,
                output_path=args.output
            )
        elif args.command == "img2img":
            result = await gen.image_to_image(
                image_path=args.image,
                prompt=args.prompt,
                strength=args.strength,
                output_path=args.output
            )
        elif args.command == "inpaint":
            result = await gen.inpaint(
                image_path=args.image,
                mask_path=args.mask,
                prompt=args.prompt,
                output_path=args.output
            )
        else:  # upscale
            result = await gen.upscale(
                image_path=args.image,
                scale=args.scale,
                output_path=args.output
            )
    
    if result.get("success"):
        print(f"✓ Image saved to: {result.get('path')}")
        if "size_bytes" in result:
            print(f"  Size: {result['size_bytes']:,} bytes")
        return 0
    else:
        print(f"✗ Error: {result.get('error')}")
        return 1


if __name__ == "__main__":
    import sys
    sys.exit(asyncio.run(main()))
