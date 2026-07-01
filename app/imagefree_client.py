"""
Core imagefree.org API client.

Handles generation submission, status polling, and result downloading
without requiring a browser (pure HTTP, needs Turnstile token provided).
"""

import time
import json
from typing import Optional, Dict, Any
from urllib.parse import urljoin

import httpx
from loguru import logger

from app.config import get_imagefree_config, get_proxy_config

# Import configuration
_cfg = get_imagefree_config()
BASE_URL = _cfg["base_url"]
API_ENDPOINT = urljoin(BASE_URL, "/api/image.php")
_PROXY = get_proxy_config()

# Default headers to mimic a real browser
DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9,zh-CN;q=0.8,zh;q=0.7",
    "Origin": BASE_URL,
    "Referer": BASE_URL + "/",
    "DNT": "1",
}


def _decode_response(response: httpx.Response) -> Optional[Dict[str, Any]]:
    """Decode the JSON response from the PHP backend."""
    raw = response.content
    # Try UTF-8 first, fallback to latin-1 if it fails
    for enc in ["utf-8", "latin-1"]:
        try:
            text = raw.decode(enc)
            return json.loads(text)
        except (UnicodeDecodeError, LookupError):
            continue
        except json.JSONDecodeError as e:
            logger.error(f"JSON decode failed: {e} | body: {raw[:200]}")
            return None
    logger.error(f"Cannot decode response: {raw[:200]}")
    return None


class ImageFreeClient:
    """HTTP client for imagefree.org."""

    def __init__(
        self,
        session_cookies: Optional[Dict[str, str]] = None,
        visitor_id: Optional[str] = None,
        session_id: Optional[str] = None,
        proxy: Optional[str] = None,
    ):
        client_kwargs = {
            "headers": DEFAULT_HEADERS,
            "cookies": session_cookies or {},
            "follow_redirects": True,
            "timeout": httpx.Timeout(30.0, connect=15.0),
        }
        proxy = proxy or _PROXY
        if proxy:
            client_kwargs["proxy"] = proxy
            logger.info(f"Using proxy: {proxy}")

        self.client = httpx.AsyncClient(**client_kwargs)
        self.visitor_id = visitor_id
        self.session_id = session_id

    async def close(self):
        await self.client.aclose()

    def _build_visitor_cookies(self) -> Dict[str, str]:
        """Return visitor tracking cookies if set."""
        cookies = {}
        if self.visitor_id:
            cookies["_vid"] = self.visitor_id
        if self.session_id:
            cookies["_sid"] = self.session_id
        return cookies

    async def submit_generation(
        self,
        prompt: str,
        turnstile_token: str,
        width: int = 1024,
        height: int = 1024,
    ) -> Optional[Dict[str, Any]]:
        """
        Submit an image generation request.

        Args:
            prompt: Text description for the image.
            turnstile_token: Solved Turnstile token.
            width: Image width (default 1024).
            height: Image height (default 1024).

        Returns:
            Response dict with keys like task_id, image_url, or None on failure.
        """
        form_data = {
            "action": "generate",
            "prompt": prompt,
            "width": str(width),
            "height": str(height),
            "cf-turnstile-response": turnstile_token,
        }

        logger.info(f"Submitting generation | prompt='{prompt[:60]}...' size={width}x{height}")

        try:
            response = await self.client.post(
                API_ENDPOINT,
                data=form_data,
                cookies=self._build_visitor_cookies(),
            )
            result = _decode_response(response)
            if result is None:
                return None

            if result.get("success") is False:
                logger.error(f"Generation rejected: {result.get('error', 'unknown')}")
                return None

            if result.get("task_id"):
                logger.info(f"Generation queued | task_id={result['task_id']}")
                return result
            elif result.get("image_url"):
                logger.success("Generation completed instantly")
                return result
            else:
                logger.warning(f"Unexpected response: {result}")
                return result

        except httpx.HTTPStatusError as e:
            logger.error(f"HTTP {e.response.status_code}: {e.response.text[:200]}")
            return None
        except Exception as e:
            logger.error(f"Submit failed: {e}")
            return None

    async def poll_status(
        self,
        task_id: str,
        api_node: Optional[str] = None,
        max_attempts: int = 60,
        interval: float = 2.0,
    ) -> Optional[Dict[str, Any]]:
        """
        Poll for generation result.

        Args:
            task_id: The task ID returned by submit_generation.
            api_node: Optional API node hint.
            max_attempts: Max polling rounds (default 60 = 2 min).
            interval: Seconds between polls (default 2.0).

        Returns:
            Dict with status='completed' + image_url, or None on failure/timeout.
        """
        logger.info(f"Polling task_id={task_id} | max_attempts={max_attempts} interval={interval}s")

        for attempt in range(1, max_attempts + 1):
            try:
                form_data = {"action": "status", "task_id": task_id}
                if api_node:
                    form_data["api_node"] = api_node

                response = await self.client.post(
                    API_ENDPOINT,
                    data=form_data,
                    cookies=self._build_visitor_cookies(),
                )
                response.raise_for_status()
                raw_text = response.text
                logger.debug(f"Poll response (attempt {attempt}): {raw_text[:300]}")
                result = _decode_response(response)
                if result is None:
                    logger.warning(f"Poll decode failed (attempt {attempt})")
                    continue

                status = result.get("status")
                if status == "completed":
                    image_url = result.get("image_url")
                    if image_url:
                        # Make absolute URL if relative
                        if image_url.startswith("/"):
                            image_url = urljoin(BASE_URL, image_url)
                        logger.success(f"Generation complete | image_url={image_url}")
                        return {"status": "completed", "image_url": image_url}
                    else:
                        logger.warning(f"Completed but no image_url: {result}")
                        return result

                elif status == "failed":
                    logger.error(f"Generation failed: {result.get('error', 'unknown')}")
                    return result

                # Still processing — log periodically
                if attempt % 10 == 0:
                    logger.info(f"Still waiting... ({attempt * interval:.0f}s)")

            except httpx.HTTPStatusError as e:
                logger.warning(f"Poll error (attempt {attempt}): HTTP {e.response.status_code}")
            except Exception as e:
                logger.warning(f"Poll error (attempt {attempt}): {e}")

            await _async_sleep(interval)

        logger.error(f"Polling timed out after {max_attempts * interval:.0f}s")
        return None

    async def download_image(
        self, image_url: str, output_path: str
    ) -> Optional[str]:
        """
        Download the generated image to a local file.

        Returns:
            The output path if successful, None otherwise.
        """
        try:
            response = await self.client.get(image_url)
            response.raise_for_status()

            with open(output_path, "wb") as f:
                f.write(response.content)

            logger.success(f"Image saved to {output_path} ({len(response.content)} bytes)")
            return output_path

        except Exception as e:
            logger.error(f"Download failed: {e}")
            return None


async def _async_sleep(seconds: float):
    """Async sleep helper."""
    import asyncio

    await asyncio.sleep(seconds)
