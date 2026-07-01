"""Phase 2b: Deep API probing — try to find hidden endpoints and backend info."""

import asyncio
import httpx
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv
from loguru import logger

logger.remove()
logger.add(sys.stdout, format="<green>{time:HH:mm:ss}</green> | <level>{level:^8}</level> | <level>{message}</level>")

load_dotenv()

BASE_URL = "https://imagefree.org"
API = f"{BASE_URL}/api/image.php"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Origin": BASE_URL,
    "Referer": f"{BASE_URL}/",
}


async def probe_action_values():
    """Try different action values to find hidden debug commands."""
    actions = [
        "debug", "info", "status", "health", "version",
        "models", "test", "ping", "echo", "pwd",
        "phpinfo", "config", "env", "system", "shell",
        "raw", "batch", "queue", "tasks", "list",
        "stats", "metrics", "admin", "login", "check",
        "user", "balance", "usage", "limit", "plan",
    ]

    async with httpx.AsyncClient(timeout=5) as c:
        for action in actions:
            try:
                r = await c.post(API, data={"action": action}, headers=HEADERS)
                if r.status_code != 400 and r.status_code != 200:
                    logger.info(f"[{r.status_code}] action={action}: {r.text[:100]}")
                elif r.status_code == 200:
                    logger.info(f"[{r.status_code}] action={action}: {r.text[:150]}")
            except Exception:
                pass
    logger.info("Action probe complete")


async def probe_methods():
    """Try different HTTP methods on the API endpoint."""
    for method in ["GET", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"]:
        async with httpx.AsyncClient(timeout=5) as c:
            try:
                if method == "HEAD":
                    r = await c.head(API)
                    logger.info(f"[{method}] {API}: HTTP {r.status_code}, headers={dict(r.headers)}")
                else:
                    r = await c.request(method, API)
                    logger.info(f"[{method}] {API}: HTTP {r.status_code}, body={r.text[:200]}")
            except Exception as e:
                logger.info(f"[{method}] {API}: Error - {e}")


async def check_server_header_leak():
    """Check response headers for leaked info."""
    async with httpx.AsyncClient(timeout=5) as c:
        r = await c.post(API, data={"action": "generate", "prompt": "test", "width": "1024", "height": "1024", "cf-turnstile-response": "fake"})
        logger.info(f"Response headers:")
        for k, v in r.headers.items():
            logger.info(f"  {k}: {v}")


async def time_based_analysis():
    """
    Time various parts of the process to identify the model.
    Different AI models have different latency profiles.
    """
    from app.captcha_solver import solve_turnstile
    from app.imagefree_client import ImageFreeClient

    # Generate 3 different prompts and measure times
    prompts = [
        "photorealistic cat, highly detailed, studio lighting",
        "oil painting of a mountain landscape, van gogh style",
        "3d render of a futuristic car, octane render, cinematic",
    ]

    times = []
    for prompt in prompts:
        t0 = time.time()

        token = await asyncio.to_thread(
            solve_turnstile,
            site_key="0x4AAAAAAB_58B_Bds-jVf2h",
            page_url=BASE_URL,
        )
        if not token:
            continue
        t1 = time.time()

        client = ImageFreeClient()
        result = await client.submit_generation(prompt=prompt, turnstile_token=token)
        if not result or not result.get("task_id"):
            continue
        t2 = time.time()

        poll_result = await client.poll_status(result["task_id"], max_attempts=30)
        t3 = time.time()
        await client.close()

        if poll_result and poll_result.get("image_url"):
            times.append({
                "prompt": prompt,
                "token_time": t1 - t0,
                "submit_time": t2 - t1,
                "gen_time": t3 - t2,
                "total": t3 - t0,
            })
            logger.info(f"\n  Prompt: {prompt[:40]}...")
            logger.info(f"  Captcha: {t1-t0:.1f}s | Submit: {t2-t1:.1f}s | Generate: {t3-t2:.1f}s | Total: {t3-t0:.1f}s")

    if times:
        avg_gen = sum(t["gen_time"] for t in times) / len(times)
        logger.info(f"\n  Average generation time: {avg_gen:.1f}s")
        logger.info(f"  SD 3.5: ~5-8s | Flux Pro: ~8-15s | Flux Schnell: ~2-4s")
        logger.info(f"  Best guess based on timing: {'Flux.1 Pro' if avg_gen > 8 else 'Flux Schnell/SD 3.5'}")


async def probe_upload_tool(tool_name):
    """Try probing the other tools (background remover, upscaler)."""
    urls = {
        "background_remover": f"{BASE_URL}/tools/background-remover",
        "upscaler": f"{BASE_URL}/tools/image-upscaler",
        "editor": f"{BASE_URL}/tools/photo-editor",
    }
    async with httpx.AsyncClient(timeout=5) as c:
        for name, url in urls.items():
            try:
                r = await c.get(url)
                if "/api/" in r.text or "api" in r.text:
                    # Extract API references from the page
                    import re
                    apis = set(re.findall(r'/api/[a-zA-Z0-9_.?=&]+', r.text))
                    if apis:
                        logger.info(f"[{name}] APIs found: {apis}")
                logger.info(f"[{name}] HTTP {r.status_code}, {len(r.content)} bytes")
            except Exception as e:
                logger.info(f"[{name}] Error: {e}")


async def main():
    logger.info("=" * 60)
    logger.info("   Phase 2b: Deep API Probing")
    logger.info("=" * 60)

    logger.info("\n─── 1. Server Header Leak ───")
    await check_server_header_leak()

    logger.info("\n─── 2. HTTP Method Probe ───")
    await probe_methods()

    logger.info("\n─── 3. Hidden Action Probe ───")
    await probe_action_values()

    logger.info("\n─── 4. Time-Based Analysis ───")
    await time_based_analysis()

    logger.info("\n─── 5. Other Tool API Analysis ───")
    await probe_upload_tool("all")

    logger.info("\n" + "=" * 60)
    logger.info("   Phase 2b Complete")
    logger.info("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
