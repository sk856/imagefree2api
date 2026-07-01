"""
Phase 2: 深度抓包 — 发掘 Imagefree 背后 AI API 的线索

可行的方法：
  1. 分析已生成图片的元数据（EXIF、像素特征）
  2. 探测 API 端点，寻找调试接口
  3. 用同一个 prompt 对比已知 AI 模型生成结果
  4. 分析图片 CDN 路径规律（task_id → 可预测性）
  5. 尝试 payload 注入探测内部信息
"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv
from loguru import logger

logger.remove()
logger.add(sys.stdout, format="<green>{time:HH:mm:ss}</green> | <level>{level:^8}</level> | <level>{message}</level>")

load_dotenv()

BASE_URL = "https://imagefree.org"

# ── 1. 图片元数据分析 ─────────────────────────────────────────

async def analyze_image_metadata():
    """分析已生成的图片，寻找 AI 模型线索。"""
    try:
        from PIL import Image, ExifTags
    except ImportError:
        logger.warning("Pillow not installed. Run: pip install Pillow")
        return

    images = sorted(Path("output").glob("*.png"))
    if not images:
        logger.warning("No images found in output/")
        return

    img_path = images[-1]
    size_kb = img_path.stat().st_size / 1024
    logger.info(f"Analyzing: {img_path.name} ({size_kb:.0f} KB)")

    img = Image.open(img_path)
    logger.info(f"  Format: {img.format} | Size: {img.size} | Mode: {img.mode}")

    # EXIF — AI generated images typically have none
    exif = img._getexif()
    if exif:
        logger.info("  EXIF found:")
        for tag_id, v in exif.items():
            name = ExifTags.TAGS.get(tag_id, tag_id)
            if v and not isinstance(v, bytes):
                logger.info(f"    {name}: {v[:80] if isinstance(v, str) else v}")
    else:
        logger.info("  EXIF: none (typical for AI-generated)")

    # Check if image has ICC profile / color space info
    icc = img.info.get("icc_profile")
    logger.info(f"  ICC profile: {'Yes' if icc else 'No'}")
    logger.info(f"  Info keys: {list(img.info.keys())}")

    # Analyze pixel distribution for model fingerprinting
    # Different models produce different noise patterns
    if img.mode == "RGBA":
        r, g, b, a = img.split()
        logger.info(f"  Alpha channel: present")
    elif img.mode == "RGB":
        r, g, b = img.split()
        logger.info(f"  Alpha channel: no")

    # DPI / metadata
    dpi = img.info.get("dpi")
    logger.info(f"  DPI: {dpi}")

    # Check for invisible watermarks (Stable Diffusion / Flux style)
    # Some models embed invisible markers in the pixel data
    # Save first bytes for magic number analysis
    with open(img_path, "rb") as f:
        png_header = f.read(32)
    logger.info(f"  PNG header hex: {png_header.hex()}")

    # PNG chunk analysis - AI tools sometimes embed custom chunks
    logger.info(f"  PNG chunks:")
    with open(img_path, "rb") as f:
        # Skip PNG signature (8 bytes)
        f.read(8)
        while True:
            chunk_len_bytes = f.read(4)
            if len(chunk_len_bytes) < 4:
                break
            chunk_len = int.from_bytes(chunk_len_bytes, "big")
            chunk_type = f.read(4).decode("ascii", errors="replace")
            chunk_data = f.read(chunk_len)
            f.read(4)  # CRC
            if chunk_type not in ("IDAT", "IHDR"):
                try:
                    txt = chunk_data.decode("utf-8", errors="replace")
                    logger.info(f"    {chunk_type} ({chunk_len} bytes): {txt[:100]}")
                except:
                    logger.info(f"    {chunk_type} ({chunk_len} bytes): [binary]")
            if chunk_type == "IEND":
                break

    # Based on file size vs resolution, estimate JPEG quality / compression level
    # Hint at model characteristics
    width, height = img.size
    pixels = width * height
    bytes_per_pixel = size_kb * 1024 / pixels
    logger.info(f"  Bytes per pixel: {bytes_per_pixel:.3f}")


# ── 2. API 端点探测 ─────────────────────────────────────────

async def probe_api_endpoints():
    """扫描可能的调试/管理接口。"""
    import httpx

    interesting = []

    endpoints = [
        (f"{BASE_URL}/api/status", "GET"),
        (f"{BASE_URL}/api/health", "GET"),
        (f"{BASE_URL}/api/debug", "GET"),
        (f"{BASE_URL}/api/info", "GET"),
        (f"{BASE_URL}/api/config", "GET"),
        (f"{BASE_URL}/api/models", "GET"),
        (f"{BASE_URL}/api/admin", "GET"),
        (f"{BASE_URL}/api/phpinfo.php", "GET"),
        (f"{BASE_URL}/api/phpinfo", "GET"),
        (f"{BASE_URL}/api/image.php?action=debug", "GET"),
        (f"{BASE_URL}/api/image.php?action=models", "GET"),
        (f"{BASE_URL}/image.php", "GET"),
        (f"{BASE_URL}/_debug", "GET"),
        (f"{BASE_URL}/.env", "GET"),
        (f"{BASE_URL}/api/.env", "GET"),
    ]

    async with httpx.AsyncClient(timeout=5) as c:
        for url, method in endpoints:
            try:
                if method == "GET":
                    r = await c.get(url)
                else:
                    r = await c.post(url)
                status = r.status_code
                body_len = len(r.content)
                if status not in (404, 405):
                    ct = r.headers.get("content-type", "")
                    interesting.append((status, url, body_len, ct, r.text[:150]))

            except Exception:
                pass

    if interesting:
        logger.info("Interesting endpoints found:")
        for status, url, blen, ct, body in interesting:
            logger.info(f"  [{status}] {url} ({blen} bytes, {ct})")
            logger.info(f"    Body: {body}")
    else:
        logger.info("No interesting endpoints found (all 404).")


# ── 3. 图片 CDN 分析 ─────────────────────────────────────────

async def analyze_cdn():
    """分析图片 CDN 路径规律，尝试发现后端 API。"""
    import httpx

    # 从已生成的图片 URL 推断
    r2_bucket = "pub-89a5b0102174408d8d7f88dcf07eec20.r2.dev"
    base_cdn = f"https://{r2_bucket}"

    async with httpx.AsyncClient(timeout=10) as c:
        # Check if we can list images (unlikely but worth trying)
        for path in ["/", "/images/", "/images/2026/07/"]:
            try:
                r = await c.get(f"{base_cdn}{path}")
                logger.info(f"CDN {path}: HTTP {r.status_code} ({len(r.content)} bytes)")
            except Exception as e:
                logger.info(f"CDN {path}: Error - {e}")


# ── 4. 生成多个图片做对比分析 ─────────────────────────────────

async def generate_analysis_samples():
    """生成多张图片，分析 URL 模式和文件名规律。"""
    from app.captcha_solver import solve_turnstile
    from app.imagefree_client import ImageFreeClient

    test_prompts = [
        "a red apple on a white table",
        "a blue sky with white clouds",
    ]

    logger.info("Generating additional test images for pattern analysis...")
    for prompt in test_prompts:
        token = await asyncio.to_thread(
            solve_turnstile,
            site_key="0x4AAAAAAB_58B_Bds-jVf2h",
            page_url=BASE_URL,
        )
        if not token:
            logger.error(f"Failed to get token for: {prompt}")
            continue

        client = ImageFreeClient()
        result = await client.submit_generation(prompt=prompt, turnstile_token=token)
        if result and result.get("task_id"):
            poll_result = await client.poll_status(result["task_id"], max_attempts=30)
            if poll_result:
                image_url = poll_result.get("image_url", "")
                logger.info(f"  Prompt: '{prompt}'")
                logger.info(f"  Image URL: {image_url}")
                # Extract task_id from URL
                parts = image_url.split("/")
                filename = parts[-1] if parts else "?"
                logger.info(f"  Filename: {filename}")
        await client.close()


# ── 5. AI 模型指纹对比表 ─────────────────────────────────────

MODEL_FINGERPRINTS = {
    "Stable Diffusion 3": {
        "typical_resolution": "1024x1024",
        "strengths": "photorealism, typography",
        "known_watermark": "None visible",
    },
    "Flux.1 Pro": {
        "typical_resolution": "1024x1024",
        "strengths": "anatomy, prompt adherence",
        "known_watermark": "None visible",
    },
    "Midjourney V6": {
        "typical_resolution": "varied (upscaled)",
        "strengths": "artistic, stylized",
        "known_watermark": "Midjourney logo visible",
    },
    "DALL-E 3": {
        "typical_resolution": "1024x1024",
        "strengths": "prompt adherence, text rendering",
        "known_watermark": "Invisible C2PA watermark",
    },
    "Recraft V3": {
        "typical_resolution": "1024x1024",
        "strengths": "vector style, illustration",
        "known_watermark": "None visible",
    },
    "Google Imagen 3": {
        "typical_resolution": "1024x1024",
        "strengths": "photorealism, lighting",
        "known_watermark": "SynthID digital watermark",
    },
    "Replicate (various)": {
        "typical_resolution": "depends on model",
        "strengths": "varied",
        "known_watermark": "No inherent watermark",
    },
}


async def main():
    logger.info("=" * 60)
    logger.info("   Phase 2: Deep Network Sniffing")
    logger.info("=" * 60)

    # Step 1: Analyze existing image
    logger.info("\n─── 1. Image Metadata Analysis ───")
    await analyze_image_metadata()

    # Step 2: Probe API endpoints
    logger.info("\n─── 2. API Endpoint Probing ───")
    await probe_api_endpoints()

    # Step 3: Analyze CDN
    logger.info("\n─── 3. CDN Analysis ───")
    await analyze_cdn()

    # Step 4: Generate more samples for pattern analysis
    logger.info("\n─── 4. Generate Analysis Samples ───")
    await generate_analysis_samples()

    logger.info("\n─── 5. Model Fingerprint Reference ───")
    for model, fp in MODEL_FINGERPRINTS.items():
        logger.info(f"  {model}: {fp['typical_resolution']}, {fp['known_watermark']}")

    logger.info("\n" + "=" * 60)
    logger.info("   Phase 2 Complete — Review findings above")
    logger.info("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
