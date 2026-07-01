"""
Capsolver integration for Cloudflare Turnstile.
"""

import os
from typing import Optional
from loguru import logger

from app.config import get_capsolver_api_key, get_imagefree_config


def solve_turnstile(
    site_key: Optional[str] = None,
    page_url: Optional[str] = None,
    api_key: Optional[str] = None,
    timeout: int = 60,
) -> Optional[str]:
    """
    Solve Cloudflare Turnstile challenge via Capsolver.

    Args:
        site_key: Turnstile site key (default from config.yaml).
        page_url: Full URL of the page (default from config.yaml).
        api_key: Capsolver API key (default from config.yaml / .env).
        timeout: Maximum seconds to wait for a solution.

    Returns:
        The Turnstile token string, or None if failed.
    """
    cfg = get_imagefree_config()
    site_key = site_key or cfg["site_key"]
    page_url = page_url or cfg["base_url"]
    api_key = api_key or get_capsolver_api_key()

    if not api_key:
        logger.error("CAPSOLVER_API_KEY not set. Set it in config.yaml or .env.")
        return None

    logger.info(f"Solving Turnstile via Capsolver | site_key={site_key} url={page_url}")

    try:
        import capsolver

        capsolver.api_key = api_key

        solution = capsolver.solve(
            {
                "type": "AntiTurnstileTaskProxyLess",
                "websiteURL": page_url,
                "websiteKey": site_key,
                "metadata": {"action": ""},
            }
        )

        token = solution.get("token")
        if token:
            logger.success(f"Turnstile solved in {solution.get('solveCount', '?')} attempts")
            return token
        else:
            logger.error(f"Capsolver returned no token: {solution}")
            return None

    except ImportError:
        logger.error("capsolver package not installed. Run: pip install capsolver")
        return None
    except Exception as e:
        logger.error(f"Capsolver failed: {e}")
        return None


if __name__ == "__main__":
    # Quick test
    from dotenv import load_dotenv

    load_dotenv()

    token = solve_turnstile()
    if token:
        print(f"Token ({len(token)} chars): {token[:50]}...")
    else:
        print("Failed to get token")
