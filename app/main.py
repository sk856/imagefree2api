"""
Imagefree2API Gateway — OpenAI 兼容接口

将 imagefree.org 的免费 AI 图片生成能力封装为 OpenAI 兼容的 REST API。
"""

import asyncio
import hashlib
import json
import os
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

import httpx
from dotenv import load_dotenv
from fastapi import Depends, FastAPI, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from loguru import logger

from app.auth import get_configured_key, verify_api_key
from app.captcha_solver import solve_turnstile
from app.config import (
    get_api_key,
    get_capsolver_api_key,
    get_generation_config,
    get_imagefree_config,
    get_proxy_config,
    get_server_config,
    reload_config,
)
from app.imagefree_client import ImageFreeClient
from app.models import (
    ChatCompletionChunk,
    ChatCompletionChunkChoice,
    ChatCompletionChunkDelta,
    ChatCompletionRequest,
    ChatCompletionResponse,
    ChatCompletionChoice,
    ChatCompletionUsage,
    ChatMessage,
    ImageGenerationRequest,
    ImageGenerationResponse,
    ImageObject,
    ModelListResponse,
    ModelObject,
)
from app.session_pool import ImagefreeSessionPool

# ── Load config ─────────────────────────────────────────────────────────
load_dotenv()

_cfg_server = get_server_config()
_cfg_imagefree = get_imagefree_config()
_cfg_gen = get_generation_config()
_PROXY = get_proxy_config()

BASE_URL = _cfg_imagefree["base_url"]
SITE_KEY = _cfg_imagefree["site_key"]
MAX_CONCURRENCY = _cfg_gen["max_concurrency"]
REQUEST_INTERVAL = _cfg_gen["request_interval"]
OUTPUT_DIR = Path(_cfg_gen["output_dir"])
SESSION_POOL_CONFIG = _cfg_gen["session_pool"]
SESSION_POOL_ENABLED = SESSION_POOL_CONFIG["enabled"]
CAPTCHA_SOLVE_TIMEOUT = int(os.getenv("IMAGEFREE_CAPTCHA_TIMEOUT_SECONDS", "75"))
GENERATION_TIMEOUT = float(os.getenv("IMAGEFREE_GENERATION_TIMEOUT_SECONDS", "150"))

# ── Lifespan ───────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Starting Imagefree2API Gateway...")
    logger.info(f"API Key configured: {'yes' if get_configured_key() else 'no'}")
    logger.info(f"Max concurrency: {MAX_CONCURRENCY}")
    logger.info(f"Request interval: {REQUEST_INTERVAL}s")
    logger.info(f"Proxy: {_PROXY or 'none'}")
    if SESSION_POOL_ENABLED:
        logger.info(
            "Session pool: "
            f"{_session_pool.session_count} sessions, "
            f"{_session_pool.max_concurrent_per_session} slot(s) each, "
            f"{_session_pool.total_slots} total slot(s)"
        )
    yield
    logger.info("Shutting down Imagefree2API Gateway...")


# ── App ────────────────────────────────────────────────────────────────

app = FastAPI(
    title="Imagefree2API",
    description="OpenAI-compatible API for imagefree.org free AI image generation",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Simple rate limiter / semaphore
_semaphore = asyncio.Semaphore(MAX_CONCURRENCY)
_session_pool = ImagefreeSessionPool(
    state_path=OUTPUT_DIR.parent / "session_pool.json",
    session_count=SESSION_POOL_CONFIG["session_count"],
    max_concurrent_per_session=SESSION_POOL_CONFIG["max_concurrent_per_session"],
    cooldown_seconds=SESSION_POOL_CONFIG["cooldown_seconds"],
    wait_timeout_seconds=SESSION_POOL_CONFIG["wait_timeout_seconds"],
)


# ── Endpoints ──────────────────────────────────────────────────────────

@app.get("/health")
async def health():
    """Health check."""
    return {"status": "ok", "timestamp": int(time.time())}


@app.get("/v1/models", response_model=ModelListResponse)
async def list_models(_auth=Depends(verify_api_key)):
    """List available models (OpenAI-compatible)."""
    return ModelListResponse(
        data=[
            ModelObject(
                id="gpt-image-2",
                created=int(time.time()),
            ),
        ]
    )


@app.post("/v1/images/generations", response_model=ImageGenerationResponse)
async def generate_image(
    request: ImageGenerationRequest,
    _auth=Depends(verify_api_key),
):
    """
    Generate images from text prompts (OpenAI-compatible).

    Accepts the same format as OpenAI's /v1/images/generations.
    """
    # Parse size
    try:
        width_str, height_str = request.size.lower().split("x")
        width = int(width_str)
        height = int(height_str)

        # Validate common aspect ratios
        valid_sizes = {
            (1024, 1024): "1:1",
            (768, 1024): "3:4",
            (1024, 768): "4:3",
            (512, 1024): "9:16",
            (1024, 512): "16:9",
        }
        aspect = valid_sizes.get((width, height))
        if not aspect:
            raise ValueError(f"Unsupported size: {request.size}")
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid size '{request.size}': {e}",
        )

    logger.info(f"Request: prompt='{request.prompt[:60]}...' size={request.size} n={request.n}")

    # Use semaphore for concurrency control
    async with _semaphore:
        images = []
        for i in range(request.n):
            try:
                result = await asyncio.wait_for(
                    _generate_one(
                        prompt=request.prompt,
                        width=width,
                        height=height,
                        response_format=request.response_format,
                    ),
                    timeout=GENERATION_TIMEOUT,
                )
                if result:
                    images.append(result)
                else:
                    logger.error(f"Image {i+1}/{request.n} failed")
            except asyncio.TimeoutError:
                message = f"Generation timed out after {GENERATION_TIMEOUT:.0f}s"
                logger.error(f"Image {i+1}/{request.n} error: {message}")
                raise HTTPException(
                    status_code=status.HTTP_504_GATEWAY_TIMEOUT,
                    detail=message,
                )
            except Exception as e:
                logger.error(f"Image {i+1}/{request.n} error: {e}")
                raise HTTPException(
                    status_code=status.HTTP_502_BAD_GATEWAY,
                    detail=f"Generation failed: {e}",
                )

            # Rate limit between multiple images
            if i < request.n - 1:
                await asyncio.sleep(REQUEST_INTERVAL)

        if not images:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="Image generation failed",
            )

    return ImageGenerationResponse(
        created=int(time.time()),
        data=images,
    )


async def _generate_one(
    prompt: str,
    width: int,
    height: int,
    response_format: str = "url",
) -> Optional[dict]:
    """
    Generate a single image.
    Steps: solve Turnstile → submit → poll → return result.
    """
    lease_context = _session_pool.acquire() if SESSION_POOL_ENABLED else None
    if lease_context is None:
        return await _generate_one_with_session(
            prompt=prompt,
            width=width,
            height=height,
            response_format=response_format,
            lease=None,
        )

    async with await lease_context as lease:
        return await _generate_one_with_session(
            prompt=prompt,
            width=width,
            height=height,
            response_format=response_format,
            lease=lease,
        )


async def _generate_one_with_session(
    prompt: str,
    width: int,
    height: int,
    response_format: str,
    lease,
) -> Optional[dict]:
    """Generate an image using one selected session from the pool."""
    session_name = lease.name if lease else "default"

    # Step 1: Solve Turnstile
    logger.info(f"Solving Turnstile with {session_name}...")
    try:
        token = await asyncio.wait_for(
            asyncio.to_thread(
                solve_turnstile,
                site_key=SITE_KEY,
                page_url=BASE_URL,
            ),
            timeout=CAPTCHA_SOLVE_TIMEOUT,
        )
    except asyncio.TimeoutError as exc:
        raise RuntimeError(f"Turnstile solve timed out after {CAPTCHA_SOLVE_TIMEOUT}s") from exc
    if not token:
        raise RuntimeError("Failed to solve Turnstile")

    # Step 2: Submit generation
    client = ImageFreeClient(
        session_cookies=lease.cookies if lease else None,
        visitor_id=lease.visitor_id if lease else None,
        session_id=lease.session_id if lease else None,
        proxy=_PROXY,
    )
    try:
        result = await client.submit_generation(
            prompt=prompt,
            turnstile_token=token,
            width=width,
            height=height,
        )

        if lease:
            await lease.update_cookies(client.export_cookies())

        if not result:
            raise RuntimeError("Submission failed")

        image_url = None
        if result.get("task_id"):
            poll_result = await client.poll_status(
                task_id=result["task_id"],
                api_node=result.get("api_node"),
                max_attempts=60,
                interval=2.0,
            )
            if poll_result:
                image_url = poll_result.get("image_url")
        elif result.get("image_url"):
            image_url = result["image_url"]

        if not image_url:
            raise RuntimeError("No image URL in response")

        # Step 3: Return result
        if response_format == "b64_json":
            # Download and encode as base64
            async with httpx.AsyncClient(proxy=_PROXY, timeout=60.0) as c:
                try:
                    img_resp = await c.get(image_url)
                    img_resp.raise_for_status()
                except Exception as e:
                    logger.error(f"Failed to download generated image: {e}")
                    raise RuntimeError(f"Failed to download generated image: {e}") from e
                import base64
                b64 = base64.b64encode(img_resp.content).decode("utf-8")
                return {"b64_json": b64}

        return {"url": image_url}

    finally:
        if lease:
            await lease.update_cookies(client.export_cookies())
        await client.close()


def _format_image_text(image_url: str) -> str:
    """Return markdown that chat clients can render as an image."""
    return f"![image]({image_url})\n\n{image_url}"


@app.post("/v1/chat/completions")
async def chat_completions(
    request: ChatCompletionRequest,
    _auth=Depends(verify_api_key),
):
    """
    Chat completions endpoint (OpenAI-compatible).

    Since this is an image generation API, we extract the user's message
    as the prompt and return the generated image URL in the assistant's response.

    Supports both standard OpenAI format (messages) and sub2api format (input).
    """
    # Extract the prompt from either messages or input format
    prompt = None

    # Handle sub2api format (input field)
    if request.input:
        for item in request.input:
            if item.get("role") == "user":
                content = item.get("content", [])
                if isinstance(content, list):
                    for c in content:
                        if c.get("type") == "input_text":
                            prompt = c.get("text")
                            break
                elif isinstance(content, str):
                    prompt = content
                if prompt:
                    break

    # Handle standard OpenAI format (messages field)
    if not prompt and request.messages:
        for message in reversed(request.messages):
            if message.role == "user":
                prompt = message.content
                break

    if not prompt:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No user message found in the conversation",
        )

    # Parse size
    try:
        width_str, height_str = request.size.lower().split("x")
        width = int(width_str)
        height = int(height_str)

        valid_sizes = {
            (1024, 1024): "1:1",
            (768, 1024): "3:4",
            (1024, 768): "4:3",
            (512, 1024): "9:16",
            (1024, 512): "16:9",
        }
        if (width, height) not in valid_sizes:
            raise ValueError(f"Unsupported size: {request.size}")
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid size '{request.size}': {e}",
        )

    logger.info(f"Chat request: prompt='{prompt[:60]}...' size={request.size} stream={request.stream}")

    # Handle streaming
    if request.stream:
        return StreamingResponse(
            _stream_chat_completion(request, prompt, width, height),
            media_type="text/event-stream",
        )

    # Non-streaming response
    async with _semaphore:
        try:
            result = await asyncio.wait_for(
                _generate_one(
                    prompt=prompt,
                    width=width,
                    height=height,
                    response_format="url",
                ),
                timeout=150.0,
            )

            if not result or not result.get("url"):
                raise RuntimeError("Image generation failed")

            image_text = _format_image_text(result["url"])

            # Format as chat completion response - only return image URL for better compatibility
            completion_id = f"chatcmpl-{uuid.uuid4().hex[:24]}"

            return ChatCompletionResponse(
                id=completion_id,
                created=int(time.time()),
                model=request.model,
                choices=[
                    ChatCompletionChoice(
                        index=0,
                        message=ChatMessage(
                            role="assistant",
                            content=image_text,
                        ),
                        finish_reason="stop",
                    )
                ],
                usage=ChatCompletionUsage(
                    prompt_tokens=len(prompt.split()),
                    completion_tokens=1,
                    total_tokens=len(prompt.split()) + 1,
                ),
            )

        except Exception as e:
            logger.error(f"Chat completion error: {e}")
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"Image generation failed: {e}",
            )


async def _stream_chat_completion(
    request: ChatCompletionRequest,
    prompt: str,
    width: int,
    height: int,
):
    """Stream chat completion chunks with keepalive during generation."""
    completion_id = f"chatcmpl-{uuid.uuid4().hex[:24]}"
    created = int(time.time())

    try:
        # Send initial chunk with role
        chunk = ChatCompletionChunk(
            id=completion_id,
            created=created,
            model=request.model,
            choices=[
                ChatCompletionChunkChoice(
                    index=0,
                    delta=ChatCompletionChunkDelta(role="assistant"),
                    finish_reason=None,
                )
            ],
        )
        yield f"data: {chunk.model_dump_json()}\n\n"

        # Generate image with periodic keepalive
        async with _semaphore:
            # Start generation in background
            started_at = time.monotonic()
            generation_task = asyncio.create_task(
                _generate_one(
                    prompt=prompt,
                    width=width,
                    height=height,
                    response_format="url",
                )
            )

            # Send keepalive chunks every 10 seconds while generating
            while not generation_task.done():
                if time.monotonic() - started_at > 150:
                    generation_task.cancel()
                    raise TimeoutError("Image generation timed out")
                try:
                    await asyncio.wait_for(asyncio.shield(generation_task), timeout=10.0)
                except asyncio.TimeoutError:
                    # Send empty keepalive chunk
                    keepalive = ChatCompletionChunk(
                        id=completion_id,
                        created=created,
                        model=request.model,
                        choices=[
                            ChatCompletionChunkChoice(
                                index=0,
                                delta=ChatCompletionChunkDelta(content=""),
                                finish_reason=None,
                            )
                        ],
                    )
                    yield f"data: {keepalive.model_dump_json()}\n\n"
                    continue

            # Get the result
            result = await generation_task

            if not result or not result.get("url"):
                raise RuntimeError("Image generation failed")

            image_text = _format_image_text(result["url"])

            # Send the image URL
            chunk = ChatCompletionChunk(
                id=completion_id,
                created=created,
                model=request.model,
                choices=[
                    ChatCompletionChunkChoice(
                        index=0,
                        delta=ChatCompletionChunkDelta(content=image_text),
                        finish_reason=None,
                    )
                ],
            )
            yield f"data: {chunk.model_dump_json()}\n\n"
            await asyncio.sleep(0.05)

            # Send final chunk
            chunk = ChatCompletionChunk(
                id=completion_id,
                created=created,
                model=request.model,
                choices=[
                    ChatCompletionChunkChoice(
                        index=0,
                        delta=ChatCompletionChunkDelta(),
                        finish_reason="stop",
                    )
                ],
            )
            yield f"data: {chunk.model_dump_json()}\n\n"
            yield "data: [DONE]\n\n"

    except Exception as e:
        logger.error(f"Stream error: {e}")
        error_chunk = {
            "error": {
                "message": f"Image generation failed: {e}",
                "type": "server_error",
            }
        }
        yield f"data: {json.dumps(error_chunk)}\n\n"
        yield "data: [DONE]\n\n"


def _responses_event(event: str, data: dict) -> str:
    """Format a Responses API server-sent event."""
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def _build_response_object(
    response_id: str,
    created: int,
    model: str,
    text: str,
) -> dict:
    item_id = f"msg_{uuid.uuid4().hex[:24]}"
    return {
        "id": response_id,
        "object": "response",
        "created_at": created,
        "status": "completed",
        "model": model,
        "output": [
            {
                "id": item_id,
                "type": "message",
                "status": "completed",
                "role": "assistant",
                "content": [
                    {
                        "type": "output_text",
                        "text": text,
                        "annotations": [],
                    }
                ],
            }
        ],
        "output_text": text,
        "usage": {
            "input_tokens": 0,
            "output_tokens": 1 if text else 0,
            "total_tokens": 1 if text else 0,
        },
    }


def _is_probe_prompt(prompt: str) -> bool:
    prompt_lower = prompt.lower()
    return "probe_ping" in prompt_lower or "acknowledge readiness" in prompt_lower


async def _stream_response_text(request: ChatCompletionRequest, text: str):
    response_id = f"resp_{uuid.uuid4().hex[:24]}"
    created = int(time.time())
    item_id = f"msg_{uuid.uuid4().hex[:24]}"
    created_response = {
        "id": response_id,
        "object": "response",
        "created_at": created,
        "status": "in_progress",
        "model": request.model,
        "output": [],
    }

    yield _responses_event("response.created", {"type": "response.created", "response": created_response})
    yield _responses_event("response.in_progress", {"type": "response.in_progress", "response": created_response})
    yield _responses_event(
        "response.output_item.added",
        {
            "type": "response.output_item.added",
            "output_index": 0,
            "item": {
                "id": item_id,
                "type": "message",
                "status": "in_progress",
                "role": "assistant",
                "content": [],
            },
        },
    )
    yield _responses_event(
        "response.content_part.added",
        {
            "type": "response.content_part.added",
            "item_id": item_id,
            "output_index": 0,
            "content_index": 0,
            "part": {"type": "output_text", "text": "", "annotations": []},
        },
    )
    yield _responses_event(
        "response.output_text.delta",
        {
            "type": "response.output_text.delta",
            "item_id": item_id,
            "output_index": 0,
            "content_index": 0,
            "delta": text,
        },
    )
    yield _responses_event(
        "response.output_text.done",
        {
            "type": "response.output_text.done",
            "item_id": item_id,
            "output_index": 0,
            "content_index": 0,
            "text": text,
        },
    )
    yield _responses_event(
        "response.content_part.done",
        {
            "type": "response.content_part.done",
            "item_id": item_id,
            "output_index": 0,
            "content_index": 0,
            "part": {"type": "output_text", "text": text, "annotations": []},
        },
    )
    yield _responses_event(
        "response.output_item.done",
        {
            "type": "response.output_item.done",
            "output_index": 0,
            "item": {
                "id": item_id,
                "type": "message",
                "status": "completed",
                "role": "assistant",
                "content": [{"type": "output_text", "text": text, "annotations": []}],
            },
        },
    )
    yield _responses_event(
        "response.completed",
        {
            "type": "response.completed",
            "response": _build_response_object(
                response_id=response_id,
                created=created,
                model=request.model,
                text=text,
            ),
        },
    )
    yield "data: [DONE]\n\n"


async def _stream_responses_completion(
    request: ChatCompletionRequest,
    prompt: str,
    width: int,
    height: int,
):
    """Stream a minimal Responses API event sequence for sub2api."""
    response_id = f"resp_{uuid.uuid4().hex[:24]}"
    created = int(time.time())
    item_id = f"msg_{uuid.uuid4().hex[:24]}"

    created_response = {
        "id": response_id,
        "object": "response",
        "created_at": created,
        "status": "in_progress",
        "model": request.model,
        "output": [],
    }
    yield _responses_event(
        "response.created",
        {"type": "response.created", "response": created_response},
    )
    yield _responses_event(
        "response.in_progress",
        {"type": "response.in_progress", "response": created_response},
    )

    image_text = None
    try:
        async with _semaphore:
            started_at = time.monotonic()
            generation_task = asyncio.create_task(
                _generate_one(
                    prompt=prompt,
                    width=width,
                    height=height,
                    response_format="url",
                )
            )

            while not generation_task.done():
                if time.monotonic() - started_at > 150:
                    generation_task.cancel()
                    raise TimeoutError("Image generation timed out")
                try:
                    await asyncio.wait_for(asyncio.shield(generation_task), timeout=10.0)
                except asyncio.TimeoutError:
                    yield _responses_event(
                        "response.in_progress",
                        {"type": "response.in_progress", "response": created_response},
                    )

            result = await generation_task

        if not result or not result.get("url"):
            raise RuntimeError("Image generation failed")

        image_text = _format_image_text(result["url"])
    except Exception as e:
        logger.error(f"Responses stream error: {e}")
        image_text = f"Image generation failed: {e}"

    yield _responses_event(
        "response.output_item.added",
        {
            "type": "response.output_item.added",
            "output_index": 0,
            "item": {
                "id": item_id,
                "type": "message",
                "status": "in_progress",
                "role": "assistant",
                "content": [],
            },
        },
    )
    yield _responses_event(
        "response.content_part.added",
        {
            "type": "response.content_part.added",
            "item_id": item_id,
            "output_index": 0,
            "content_index": 0,
            "part": {"type": "output_text", "text": "", "annotations": []},
        },
    )
    yield _responses_event(
        "response.output_text.delta",
        {
            "type": "response.output_text.delta",
            "item_id": item_id,
            "output_index": 0,
            "content_index": 0,
            "delta": image_text,
        },
    )
    yield _responses_event(
        "response.output_text.done",
        {
            "type": "response.output_text.done",
            "item_id": item_id,
            "output_index": 0,
            "content_index": 0,
            "text": image_text,
        },
    )
    yield _responses_event(
        "response.content_part.done",
        {
            "type": "response.content_part.done",
            "item_id": item_id,
            "output_index": 0,
            "content_index": 0,
            "part": {"type": "output_text", "text": image_text, "annotations": []},
        },
    )
    yield _responses_event(
        "response.output_item.done",
        {
            "type": "response.output_item.done",
            "output_index": 0,
            "item": {
                "id": item_id,
                "type": "message",
                "status": "completed",
                "role": "assistant",
                "content": [
                    {"type": "output_text", "text": image_text, "annotations": []}
                ],
            },
        },
    )
    yield _responses_event(
        "response.completed",
        {
            "type": "response.completed",
            "response": _build_response_object(
                response_id=response_id,
                created=created,
                model=request.model,
                text=image_text,
            ),
        },
    )
    yield "data: [DONE]\n\n"


@app.post("/v1/responses")
async def responses_endpoint(
    request: ChatCompletionRequest,
    _auth=Depends(verify_api_key),
):
    """
    Alternative responses endpoint for compatibility.

    This endpoint emits Responses API events so sub2api can observe
    response.completed instead of treating a chat-completion stream as broken.
    """
    prompt = None
    if request.input:
        for item in request.input:
            if item.get("role") == "user":
                content = item.get("content", [])
                if isinstance(content, list):
                    for c in content:
                        if c.get("type") == "input_text":
                            prompt = c.get("text")
                            break
                elif isinstance(content, str):
                    prompt = content
                if prompt:
                    break

    if not prompt and request.messages:
        for message in reversed(request.messages):
            if message.role == "user":
                prompt = message.content
                break

    if not prompt:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No user message found in the conversation",
        )

    try:
        width_str, height_str = request.size.lower().split("x")
        width = int(width_str)
        height = int(height_str)

        valid_sizes = {
            (1024, 1024): "1:1",
            (768, 1024): "3:4",
            (1024, 768): "4:3",
            (512, 1024): "9:16",
            (1024, 512): "16:9",
        }
        if (width, height) not in valid_sizes:
            raise ValueError(f"Unsupported size: {request.size}")
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid size '{request.size}': {e}",
        )

    logger.info(f"Responses request: prompt='{prompt[:60]}...' size={request.size} stream={request.stream}")

    if _is_probe_prompt(prompt):
        logger.info("Responses probe request acknowledged without image generation")
        if request.stream:
            return StreamingResponse(
                _stream_response_text(request, "ok"),
                media_type="text/event-stream",
            )
        response_id = f"resp_{uuid.uuid4().hex[:24]}"
        return JSONResponse(
            _build_response_object(
                response_id=response_id,
                created=int(time.time()),
                model=request.model,
                text="ok",
            )
        )

    if request.stream:
        return StreamingResponse(
            _stream_responses_completion(request, prompt, width, height),
            media_type="text/event-stream",
        )

    async with _semaphore:
        try:
            result = await asyncio.wait_for(
                _generate_one(
                    prompt=prompt,
                    width=width,
                    height=height,
                    response_format="url",
                ),
                timeout=150.0,
            )
            if not result or not result.get("url"):
                raise RuntimeError("Image generation failed")

            response_id = f"resp_{uuid.uuid4().hex[:24]}"
            return JSONResponse(
                _build_response_object(
                    response_id=response_id,
                    created=int(time.time()),
                    model=request.model,
                    text=result["url"],
                )
            )
        except Exception as e:
            logger.error(f"Responses error: {e}")
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"Image generation failed: {e}",
            )


# ── Main ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn

    port = _cfg_server["port"]
    uvicorn.run(
        "app.main:app",
        host=_cfg_server["host"],
        port=port,
        reload=False,
        log_level="info",
    )
