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
from fastapi.responses import JSONResponse
from loguru import logger

from app.auth import get_api_key, verify_api_key
from app.captcha_solver import solve_turnstile
from app.imagefree_client import ImageFreeClient
from app.models import (
    ImageGenerationRequest,
    ImageGenerationResponse,
    ImageObject,
    ModelListResponse,
    ModelObject,
)

load_dotenv()

# ── Config ─────────────────────────────────────────────────────────────
BASE_URL = os.getenv("IMAGEFREE_BASE_URL", "https://imagefree.org")
SITE_KEY = "0x4AAAAAAB_58B_Bds-jVf2h"
MAX_CONCURRENCY = int(os.getenv("MAX_CONCURRENCY", "1"))
REQUEST_INTERVAL = int(os.getenv("REQUEST_INTERVAL_SECONDS", "30"))
OUTPUT_DIR = Path(os.getenv("OUTPUT_DIR", "/data/images"))
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

# ── Lifespan ───────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Starting Imagefree2API Gateway...")
    logger.info(f"API Key: {get_api_key()}")
    logger.info(f"Max concurrency: {MAX_CONCURRENCY}")
    logger.info(f"Request interval: {REQUEST_INTERVAL}s")
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
                id="imagefree",
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
                result = await _generate_one(
                    prompt=request.prompt,
                    width=width,
                    height=height,
                    response_format=request.response_format,
                )
                if result:
                    images.append(result)
                else:
                    logger.error(f"Image {i+1}/{request.n} failed")
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
    # Step 1: Solve Turnstile
    logger.info("Solving Turnstile...")
    token = await asyncio.to_thread(
        solve_turnstile,
        site_key=SITE_KEY,
        page_url=BASE_URL,
    )
    if not token:
        raise RuntimeError("Failed to solve Turnstile")

    # Step 2: Submit generation
    client = ImageFreeClient()
    try:
        result = await client.submit_generation(
            prompt=prompt,
            turnstile_token=token,
            width=width,
            height=height,
        )

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
            async with httpx.AsyncClient() as c:
                img_resp = await c.get(image_url)
                img_resp.raise_for_status()
                import base64
                b64 = base64.b64encode(img_resp.content).decode("utf-8")
                return {"b64_json": b64}

        return {"url": image_url}

    finally:
        await client.close()


# ── Main ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn

    port = int(os.getenv("PORT", "7860"))
    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",
        port=port,
        reload=False,
        log_level="info",
    )
