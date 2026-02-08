from pydantic import BaseModel, Field, field_validator, model_validator
from typing import Optional, List


# Limits for input validation
MAX_MESSAGE_LENGTH = 100_000  # 100KB text message
MAX_FILE_CONTENT_LENGTH = 50_000_000  # 50MB per file (base64)
MAX_FILES_PER_REQUEST = 10
MAX_IMAGES_PER_REQUEST = 5
MAX_FILE_NAME_LENGTH = 255


class FileAttachment(BaseModel):
    name: str = Field(..., max_length=MAX_FILE_NAME_LENGTH)
    type: str  # 'pdf', 'zip', 'text', 'code'
    content: str  # base64 for binary, raw text for text files
    is_base64: Optional[bool] = False

    @field_validator('content')
    @classmethod
    def validate_content_length(cls, v):
        if len(v) > MAX_FILE_CONTENT_LENGTH:
            raise ValueError(f'File content exceeds maximum size of {MAX_FILE_CONTENT_LENGTH // 1_000_000}MB')
        return v


class ChatRequest(BaseModel):
    message: str = Field(default="", max_length=MAX_MESSAGE_LENGTH)
    images: Optional[List[str]] = Field(default=None, max_length=MAX_IMAGES_PER_REQUEST)
    think: Optional[bool] = None  # Enable extended reasoning mode
    files: Optional[List[FileAttachment]] = Field(default=None, max_length=MAX_FILES_PER_REQUEST)
    voice_response: bool = False  # Enable sentence-level streaming TTS

    @model_validator(mode='after')
    def validate_has_content(self):
        """Ensure request has either a message, files, or images"""
        has_message = self.message and self.message.strip()
        has_files = self.files and len(self.files) > 0
        has_images = self.images and len(self.images) > 0
        
        if not has_message and not has_files and not has_images:
            raise ValueError('Request must contain a message, files, or images')
        return self

class ChatMessage(BaseModel):
    role: str
    content: str
    images: Optional[List[str]] = None

class ModelSelectRequest(BaseModel):
    model: str

class SettingsUpdate(BaseModel):
    persona: Optional[str] = None
    model: Optional[str] = None
    temperature: Optional[float] = None
    top_p: Optional[float] = None
    top_k: Optional[int] = None
    num_ctx: Optional[int] = None
    repeat_penalty: Optional[float] = None


class OmegaToolCall(BaseModel):
    """Structured output from Omega for tool execution in adult mode.
    
    Omega analyzes user messages and decides if tools are needed:
    - image: Generate an image (NSFW allowed)
    - video: Generate a video
    - websearch: Search the web
    - None: No tool needed, continue with Lexi
    """
    tool: Optional[str] = None  # "image" | "video" | "websearch" | None
    prompt: str = ""  # The prompt to pass to the tool
    style: Optional[str] = None  # "photorealistic" | "anime" | "artistic"
    safe_search: Optional[bool] = False  # For websearch: enable safe search filter
    reason: Optional[str] = None  # Why no tool, if tool is None
