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
