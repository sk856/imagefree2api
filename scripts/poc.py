#!/usr/bin/env python3
"""
Phase 1 PoC — 端到端验证 imagefree.org 生成链路。

两种模式：
  auto   — 通过 capsolver 自动求解 Turnstile（需 CAPSOLVER_API_KEY）
  manual — 启动浏览器后用户手动完成验证，适合首次调试

用法：
  python scripts/poc.py --mode auto --prompt "a cute cat"
  python scripts/poc.py --mode manual --prompt "a cute cat"
"""

import asyncio
import json
import os
import sys
import time
import argparse
from pathlib import Path
from urllib.parse import urljoin

# Add project root to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv
from loguru import logger

# Remove default handler, add our own
logger.remove()
logger.add(sys.stdout, format="<green>{time:HH:mm:ss}</green> | <level>{level:^8}</level> | <level>{message}</level>")

load_dotenv()

# ── Config ──────────────────────────────────────────────────────────────
BASE_URL = "https://imagefree.org"
API_ENDPOINT = urljoin(BASE_URL, "/api/image.php")
SITE_KEY = "0x4AAAAAAB_58B_Bds-jVf2h"

# ── Helpers ─────────────────────────────────────────────────────────────

async def extract_cookies(page) -> dict:
    """Extract all cookies from the Playwright page as a dict."""
    cookies = await page.context.cookies()
    return {c["name"]: c["value"] for c in cookies}


async def extract_storage(page) -> dict:
    """Extract localStorage and sessionStorage values."""
    storage = {}
    try:
        storage["local"] = await page.evaluate(
            "() => JSON.parse(JSON.stringify(localStorage))"
        )
    except Exception:
        storage["local"] = {}
    try:
        storage["session"] = await page.evaluate(
            "() => JSON.parse(JSON.stringify(sessionStorage))"
        )
    except Exception:
        storage["session"] = {}
    return storage


async def get_turnstile_token_from_page(page) -> str:
    """Read the Turnstile token from page's JavaScript namespace."""
    token = await page.evaluate("window.ImageFree?.turnstileToken")
    return token


async def wait_for_turnstile_manual(page):
    """
    Manual mode: Open a headed browser and wait for the user to
    complete the Turnstile challenge.
    """
    logger.info("Navigating to imagefree.org...")
    await page.goto(BASE_URL, wait_until="networkidle", timeout=30000)
    logger.info("Page loaded. Waiting for Turnstile widget...")

    # Wait for the Turnstile widget to appear
    try:
        await page.wait_for_selector(".cf-turnstile iframe", timeout=15000)
        logger.info("Turnstile widget detected.")
    except Exception:
        logger.warning("Turnstile iframe not found; checking if already solved...")

    # Check if already solved
    token = await get_turnstile_token_from_page(page)
    if token:
        logger.success("Turnstile already solved!")
        return token

    # Show instructions
    print("\n" + "=" * 60)
    print("  浏览器已打开。请手动完成 'Prove you are human' 验证。")
    print("  完成后，按 Enter 键继续...")
    print("=" * 60 + "\n")

    await asyncio.get_event_loop().run_in_executor(None, input)

    # Extract token
    token = await get_turnstile_token_from_page(page)
    if token:
        logger.success(f"Turnstile token captured ({len(token)} chars)")
    else:
        logger.error("No Turnstile token found. Trying to check callback...")
        # Try via the stored global
        token = await page.evaluate("window.ImageFree?.turnstileToken")
        if token:
            logger.success(f"Token found via window.ImageFree.turnstileToken")
        else:
            logger.error("Still no token. Did you complete the captcha?")

    return token


async def solve_turnstile_auto():
    """
    Auto mode: Use capsolver to solve Turnstile.
    Returns the token string.
    """
    api_key = os.getenv("CAPSOLVER_API_KEY")
    if not api_key:
        logger.error("CAPSOLVER_API_KEY not set in .env")
        return None

    from app.captcha_solver import solve_turnstile

    token = await asyncio.to_thread(
        solve_turnstile,
        site_key=SITE_KEY,
        page_url=BASE_URL,
        api_key=api_key,
    )
    return token


def show_http_submit_guide(token: str):
    """Show the raw curl command for debugging."""
    print("\n── Raw HTTP submit command ──")
    print(f"curl -X POST {API_ENDPOINT} \\")
    print(f"  -d 'action=generate' \\")
    print(f"  -d 'prompt=a test image' \\")
    print(f"  -d 'width=1024' \\")
    print(f"  -d 'height=1024' \\")
    print(f"  -d 'cf-turnstile-response={token[:30]}...'")


async def run_manual_mode(prompt: str, output_dir: Path, enable_sniff: bool):
    """
    Manual mode: browser opens, user solves captcha, then script submits.
    """
    from playwright.async_api import async_playwright

    async with async_playwright() as pw:
        # Launch headed browser with stealth
        browser = await pw.chromium.launch(headless=False)
        context = await browser.new_context(
            viewport={"width": 1280, "height": 900},
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/131.0.0.0 Safari/537.36"
            ),
            locale="en-US",
        )

        # Optional: intercept network requests to discover backend API
        if enable_sniff:
            await sniff_requests(context)

        page = await context.new_page()

        # Step 1: Wait for user to solve Turnstile
        token = await wait_for_turnstile_manual(page)
        if not token:
            logger.error("No token obtained. Aborting.")
            await browser.close()
            return

        # Step 2: Extract cookies and storage for HTTP client
        cookies = await extract_cookies(page)
        storage = await extract_storage(page)
        logger.info(f"Cookies: {len(cookies)} items")
        logger.info(f"localStorage keys: {list(storage['local'].keys())}")
        logger.info(f"sessionStorage keys: {list(storage['session'].keys())}")

        # Step 3: Submit via HTTP client
        visitor_id = storage["local"].get("_vid")
        session_id = storage["session"].get("_sid")
        if visitor_id:
            logger.info(f"Visitor ID: {visitor_id}")
        if session_id:
            logger.info(f"Session ID: {session_id}")

        await submit_and_poll(
            prompt=prompt,
            token=token,
            cookies=cookies,
            visitor_id=visitor_id,
            session_id=session_id,
            output_dir=output_dir,
        )

        await browser.close()


async def run_auto_mode(prompt: str, output_dir: Path):
    """
    Auto mode: Solve Turnstile via capsolver, then pure HTTP.
    No browser needed (after initial capsolver API call).
    """
    from app.captcha_solver import solve_turnstile

    # Step 1: Solve Turnstile
    logger.info("Solving Turnstile via capsolver...")
    api_key = os.getenv("CAPSOLVER_API_KEY")
    if not api_key:
        logger.error("CAPSOLVER_API_KEY not set. Create .env file with your key.")
        return

    token = await asyncio.to_thread(
        solve_turnstile,
        site_key=SITE_KEY,
        page_url=BASE_URL,
        api_key=api_key,
    )
    if not token:
        logger.error("Failed to solve Turnstile. Aborting.")
        return

    logger.success(f"Turnstile token obtained ({len(token)} chars)")

    # Step 2: Submit via HTTP
    await submit_and_poll(
        prompt=prompt,
        token=token,
        cookies=None,
        visitor_id=None,
        session_id=None,
        output_dir=output_dir,
    )


async def submit_and_poll(
    prompt: str,
    token: str,
    cookies: dict | None,
    visitor_id: str | None,
    session_id: str | None,
    output_dir: Path,
):
    """
    Core flow: submit generation -> poll status -> download image.
    """
    from app.imagefree_client import ImageFreeClient

    client = ImageFreeClient(
        session_cookies=cookies,
        visitor_id=visitor_id,
        session_id=session_id,
    )

    try:
        # Submit
        result = await client.submit_generation(
            prompt=prompt,
            turnstile_token=token,
            width=1024,
            height=1024,
        )

        if not result:
            logger.error("Submission returned no result.")
            show_http_submit_guide(token)
            return

        # Poll if task_id returned
        if result.get("task_id"):
            poll_result = await client.poll_status(
                task_id=result["task_id"],
                api_node=result.get("api_node"),
                max_attempts=60,
                interval=2.0,
            )

            if not poll_result:
                logger.error("Polling failed or timed out.")
                return

            image_url = poll_result.get("image_url")
        else:
            image_url = result.get("image_url")

        if not image_url:
            logger.error(f"No image URL in response: {json.dumps(result, indent=2)}")
            return

        # Download
        timestamp = int(time.time())
        prompt_slug = "".join(c if c.isalnum() or c in "-_" else "_" for c in prompt[:20])
        output_path = output_dir / f"{timestamp}_{prompt_slug}.png"

        saved = await client.download_image(image_url, str(output_path))
        if saved:
            logger.success(f"✅ SUCCESS — Image saved to {saved}")

            # Verify file is not empty/trivial
            size = os.path.getsize(saved)
            logger.info(f"File size: {size} bytes ({size/1024:.1f} KB)")

    finally:
        await client.close()


async def sniff_requests(context):
    """
    Intercept ALL HTTP requests from the browser to discover
    the backend AI API that imagefree.org calls.
    """
    from playwright.async_api import Route

    sniffed = set()

    async def on_route(route: Route, request):
        url = request.url
        # Only log outbound requests to external APIs
        if (
            "imagefree" not in url
            and "google" not in url
            and "cloudflare" not in url
            and "challenges" not in url
        ):
            if url not in sniffed:
                sniffed.add(url)
                method = request.method
                logger.info(f"[SNIFF] {method} {url}")

                # If it's a POST, log the post data
                if method.upper() == "POST":
                    try:
                        body = await request.post_data()
                        if body:
                            logger.info(f"[SNIFF]   Body: {body[:300]}")
                    except Exception:
                        pass

        await route.continue_()

    # Register route interceptor
    await context.route("**/*", on_route)
    logger.info("Network sniffing enabled — watching for backend API calls...")


async def main():
    parser = argparse.ArgumentParser(
        description="Imagefree.org PoC — 端到端验证生成链路"
    )
    parser.add_argument(
        "--mode",
        choices=["auto", "manual"],
        default="auto",
        help="auto = capsolver 自动求解 | manual = 浏览器手动验证",
    )
    parser.add_argument(
        "--prompt",
        "-p",
        default="a futuristic cyberpunk cat with neon glowing eyes, digital art, 8k",
        help="图片描述提示词",
    )
    parser.add_argument(
        "--sniff",
        action="store_true",
        help="启用网络抓包，嗅探背后 AI API",
    )
    parser.add_argument(
        "--output",
        "-o",
        default="output",
        help="图片输出目录 (default: output/)",
    )
    args = parser.parse_args()

    output_dir = Path(args.output)
    output_dir.mkdir(parents=True, exist_ok=True)

    logger.info("=== Imagefree.org Phase 1 PoC ===")
    logger.info(f"Mode: {args.mode}")
    logger.info(f"Prompt: {args.prompt}")
    logger.info(f"Output: {output_dir}/")

    start = time.time()

    if args.mode == "manual":
        await run_manual_mode(
            prompt=args.prompt,
            output_dir=output_dir,
            enable_sniff=args.sniff,
        )
    else:
        if args.sniff:
            logger.warning("Sniff mode is only available with --mode manual (needs browser)")
        await run_auto_mode(
            prompt=args.prompt,
            output_dir=output_dir,
        )

    elapsed = time.time() - start
    logger.info(f"Total time: {elapsed:.1f}s")


if __name__ == "__main__":
    asyncio.run(main())
