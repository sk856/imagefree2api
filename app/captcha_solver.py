"""
Capsolver integration for Cloudflare Turnstile.
"""

import os
import time
from typing import Optional
from loguru import logger

CAPSOLVER_API_KEY = os.getenv("CAPSOLVER_API_KEY", "")


def solve_turnstile(
    site_key: str,
    page_url: str,
    api_key: Optional[str] = None,
    timeout: int = 60,
) -> Optional[str]:
    """
    Solve Cloudflare Turnstile challenge via Capsolver.

    Args:
        site_key: Turnstile site key from the page.
        page_url: Full URL of the page with the Turnstile widget.
        api_key: Capsolver API key (falls back to env CAPSOLVER_API_KEY).
        timeout: Maximum seconds to wait for a solution.

    Returns:
        The Turnstile token string, or None if failed.
    """
    api_key = api_key or CAPSOLVER_API_KEY
    if not api_key:
        logger.error("CAPSOLVER_API_KEY not set. Set it in .env or pass api_key.")
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

    token = solve_turnstile(
        site_key="0x4AAAAAAB_58B_Bds-jVf2h",
        page_url="https://imagefree.org",
    )
    if token:
        print(f"Token ({len(token)} chars): {token[:50]}...")
    else:
        print("Failed to get token")
