#!/usr/bin/env python3
"""
Integration test — 验证 PoC 各组件的连通性。

无需 API Key，只测试：
  1. Playwright 能否打开 imagefree.org 并提取 DOM
  2. 确认 Turnstile sitekey 与预期一致
  3. 测试 API 端点的可访问性（期望 500，因为无有效 token）
"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from loguru import logger

logger.remove()
logger.add(sys.stdout, format="<green>{time:HH:mm:ss}</green> | <level>{level:^8}</level> | <level>{message}</level>")

BASE_URL = "https://imagefree.org"
EXPECTED_SITEKEY = "0x4AAAAAAB_58B_Bds-jVf2h"


async def test_browser():
    """Test: Playwright can open the page and extract Turnstile info."""
    logger.info("===== Test 1: Playwright browser access =====")
    from playwright.async_api import async_playwright

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        context = await browser.new_context(
            viewport={"width": 1280, "height": 900},
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/131.0.0.0 Safari/537.36"
            ),
        )
        page = await context.new_page()

        try:
            await page.goto(BASE_URL, wait_until="domcontentloaded", timeout=30000)
            logger.success(f"Page loaded: {await page.title()}")

            # Extract sitekey from the DOM
            sitekey = await page.evaluate("""
                () => {
                    const el = document.querySelector('.cf-turnstile');
                    return el ? el.getAttribute('data-sitekey') : null;
                }
            """)

            if sitekey:
                logger.info(f"Turnstile sitekey: {sitekey}")
                assert sitekey == EXPECTED_SITEKEY, f"Sitekey mismatch! Expected {EXPECTED_SITEKEY}, got {sitekey}"
                logger.success(f"✅ Sitekey matches expected value")
            else:
                logger.warning("No .cf-turnstile element found on page")

            # Check if callbacks are registered
            has_callbacks = await page.evaluate("""
                () => {
                    const el = document.querySelector('.cf-turnstile');
                    return {
                        hasDataCallback: el ? el.hasAttribute('data-callback') : false,
                        dataCallback: el ? el.getAttribute('data-callback') : null,
                        hasDataErrorCallback: el ? el.hasAttribute('data-error-callback') : false,
                        dataErrorCallback: el ? el.getAttribute('data-error-callback') : null,
                    }
                }
            """)
            logger.info(f"Callbacks: {has_callbacks}")

            # Check for Turnstile script
            has_turnstile_script = await page.evaluate("""
                () => {
                    const scripts = document.querySelectorAll('script');
                    for (const s of scripts) {
                        if (s.src && s.src.includes('turnstile')) return s.src;
                    }
                    return null;
                }
            """)
            if has_turnstile_script:
                logger.info(f"Turnstile script: {has_turnstile_script}")
                logger.success(f"✅ Turnstile script loaded")

            # Check page uses PHP API endpoint
            has_api_ref = await page.evaluate("""
                () => document.body.innerHTML.includes('/api/image.php')
            """)
            logger.info(f"Contains /api/image.php ref: {has_api_ref}")

            # Extract page structure info
            page_info = await page.evaluate("""
                () => ({
                    hasForm: !!document.getElementById('ai-generator-form'),
                    hasPrompt: !!document.getElementById('prompt'),
                    hasGenerateBtn: !!document.getElementById('generate-btn'),
                    hasCaptchaSection: !!document.getElementById('captcha-section'),
                    hasPreview: !!document.getElementById('preview-container'),
                    hasLoadingState: !!document.getElementById('loading-state'),
                })
            """)
            logger.info(f"Page elements: {page_info}")

            all_ok = all(page_info.values())
            if all_ok:
                logger.success(f"✅ All critical page elements found")
            else:
                missing = [k for k, v in page_info.items() if not v]
                logger.warning(f"Missing elements: {missing}")

        except Exception as e:
            logger.error(f"Browser test failed: {e}")
        finally:
            await browser.close()


async def test_api_reachability():
    """Test: Can we reach the API endpoint? (expect 500 without valid token)"""
    logger.info("\n===== Test 2: API endpoint reachability =====")
    import httpx

    async with httpx.AsyncClient() as client:
        try:
            response = await client.post(
                f"{BASE_URL}/api/image.php",
                data={
                    "action": "generate",
                    "prompt": "test",
                    "width": "1024",
                    "height": "1024",
                    "cf-turnstile-response": "invalid_token_for_testing",
                },
                timeout=15,
            )
            logger.info(f"API response: HTTP {response.status_code}")
            logger.info(f"Body: {response.text[:300]}")
            # Expecting 500 (invalid token) or 200 with error
            if response.status_code in (200, 500):
                logger.success(f"✅ API endpoint reachable (HTTP {response.status_code})")
            else:
                logger.warning(f"Unexpected status code")

        except httpx.ConnectError as e:
            logger.error(f"❌ API not reachable: {e}")
        except Exception as e:
            logger.warning(f"API test result: {e}")


async def test_capsolver_config():
    """Test: Check if capsolver is configured."""
    logger.info("\n===== Test 3: Capsolver configuration =====")
    import os
    from dotenv import load_dotenv

    load_dotenv()
    api_key = os.getenv("CAPSOLVER_API_KEY")

    if api_key:
        logger.success(f"✅ CAPSOLVER_API_KEY found: {api_key[:8]}...")
    else:
        logger.warning("⚠️  CAPSOLVER_API_KEY not set — auto mode will not work")
        logger.info("   To test auto mode, add your key to imagefree-2api/.env:")
        logger.info("   CAPSOLVER_API_KEY=your-key-here")


async def main():
    logger.info("=" * 50)
    logger.info("   Imagefree.org PoC — Integration Test")
    logger.info("=" * 50)

    await test_browser()
    await test_api_reachability()
    await test_capsolver_config()

    logger.info("\n" + "=" * 50)
    logger.info("   Test Summary")
    logger.info("=" * 50)
    logger.info("Manual mode:   python scripts/poc.py --mode manual --prompt 'your prompt'")
    logger.info("Auto mode:     python scripts/poc.py --mode auto --prompt 'your prompt'")
    logger.info("With sniff:    python scripts/poc.py --mode manual --prompt 'prompt' --sniff")
    logger.info("=" * 50)


if __name__ == "__main__":
    asyncio.run(main())
