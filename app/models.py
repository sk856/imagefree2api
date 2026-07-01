"""Pydantic models for OpenAI-compatible image generation API."""

from typing import Optional, List
from pydantic import BaseModel, Field


class ImageGenerationRequest(BaseModel):
    """OpenAI-compatible image generation request.

    Reference: https://platform.openai.com/docs/api-reference/images/create
    """
    model: str = Field(default="imagefree", description="Model name")
    prompt: str = Field(..., description="Text description of the desired image")
    n: int = Field(default=1, ge=1, le=4, description="Number of images to generate")
    size: str = Field(
        default="1024x1024",
        pattern=r"^\d+x\d+$",
        description="Image size as WxH, e.g. 1024x1024, 768x1024, 1024x512",
    )
    quality: Optional[str] = Field(default=None, description="Standard or hd")
    response_format: str = Field(
        default="url",
        pattern=r"^(url|b64_json)$",
        description="Return image URL or base64 JSON",
    )
    style: Optional[str] = Field(default=None, description="Image style")
    user: Optional[str] = Field(default=None, description="End-user identifier")


class ImageObject(BaseModel):
    """A generated image."""
    url: Optional[str] = Field(default=None, description="Image URL")
    b64_json: Optional[str] = Field(default=None, description="Base64 encoded image")
    revised_prompt: Optional[str] = Field(default=None, description="The prompt used")


class ImageGenerationResponse(BaseModel):
    """OpenAI-compatible image generation response."""
    created: int = Field(..., description="Unix timestamp of creation")
    data: List[ImageObject] = Field(..., description="Generated images")


class ModelObject(BaseModel):
    """Model listing entry."""
    id: str
    object: str = "model"
    created: int
    owned_by: str = "imagefree2api"


class ModelListResponse(BaseModel):
    """List of available models."""
    object: str = "list"
    data: List[ModelObject]


class ErrorResponse(BaseModel):
    """Error response."""
    error: dict


# ── Chat Completion Models ──────────────────────────────────────────


class ChatMessage(BaseModel):
    """A chat message."""
    role: str = Field(..., description="Role: system, user, or assistant")
    content: str = Field(..., description="Message content")
    name: Optional[str] = Field(default=None, description="Name of the participant")


class ChatCompletionRequest(BaseModel):
    """OpenAI-compatible chat completion request."""
    model: str = Field(default="imagefree", description="Model name")
    messages: Optional[List[ChatMessage]] = Field(default=None, description="List of messages")
    input: Optional[List[dict]] = Field(default=None, description="Alternative input format for sub2api")
    instructions: Optional[str] = Field(default=None, description="System instructions")
    temperature: Optional[float] = Field(default=1.0, ge=0, le=2)
    top_p: Optional[float] = Field(default=1.0, ge=0, le=1)
    n: Optional[int] = Field(default=1, ge=1, le=4)
    stream: Optional[bool] = Field(default=False)
    max_tokens: Optional[int] = Field(default=None)
    size: Optional[str] = Field(
        default="1024x1024",
        description="Image size for generation (e.g., 1024x1024)"
    )
    user: Optional[str] = Field(default=None)


class ChatCompletionChoice(BaseModel):
    """A chat completion choice."""
    index: int
    message: ChatMessage
    finish_reason: str = "stop"


class ChatCompletionUsage(BaseModel):
    """Token usage statistics."""
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0


class ChatCompletionResponse(BaseModel):
    """OpenAI-compatible chat completion response."""
    id: str
    object: str = "chat.completion"
    created: int
    model: str
    choices: List[ChatCompletionChoice]
    usage: ChatCompletionUsage


# ── Streaming Chat Models ──────────────────────────────────────────


class ChatCompletionChunkDelta(BaseModel):
    """Delta content in a streaming chunk."""
    role: Optional[str] = None
    content: Optional[str] = None


class ChatCompletionChunkChoice(BaseModel):
    """A streaming chunk choice."""
    index: int
    delta: ChatCompletionChunkDelta
    finish_reason: Optional[str] = None


class ChatCompletionChunk(BaseModel):
    """OpenAI-compatible streaming chunk."""
    id: str
    object: str = "chat.completion.chunk"
    created: int
    model: str
    choices: List[ChatCompletionChunkChoice]
