"""
Centralized configuration for Imagefree2API.

Loads from config.yaml (preferred) or falls back to .env / environment variables.
"""

import os
from pathlib import Path
from typing import Optional

import yaml
from loguru import logger

# Project root
ROOT_DIR = Path(__file__).resolve().parent.parent

# Config file paths (config.yaml is gitignored, config.example.yaml is the template)
CONFIG_PATHS = [
    ROOT_DIR / "config.yaml",
    ROOT_DIR / "config.example.yaml",
]


def _load_config() -> dict:
    """Load config from the first available config file."""
    for path in CONFIG_PATHS:
        if path.exists():
            try:
                with open(path, "r", encoding="utf-8") as f:
                    cfg = yaml.safe_load(f) or {}
                    logger.info(f"Loaded config from {path.name}")
                    return cfg
            except Exception as e:
                logger.warning(f"Failed to load {path.name}: {e}")

    logger.warning("No config.yaml found, falling back to .env / env vars")
    return {}


# Lazy-loaded config
_CONFIG: Optional[dict] = None


def get_config() -> dict:
    global _CONFIG
    if _CONFIG is None:
        _CONFIG = _load_config()
    return _CONFIG


def reload_config():
    """Force reload the config (useful after editing)."""
    global _CONFIG
    _CONFIG = _load_config()


# ── Typed accessors ──

def get_server_config() -> dict:
    c = get_config().get("server", {})
    return {
        "host": c.get("host", os.getenv("HOST", "0.0.0.0")),
        "port": int(c.get("port", os.getenv("PORT", "7860"))),
    }


def get_imagefree_config() -> dict:
    c = get_config().get("imagefree", {})
    return {
        "base_url": c.get("base_url", os.getenv("IMAGEFREE_BASE_URL", "https://imagefree.org")),
        "site_key": c.get("site_key", "0x4AAAAAAB_58B_Bds-jVf2h"),
    }


def get_capsolver_api_key() -> str:
    """Get Capsolver API key from config.yaml or .env."""
    c = get_config().get("capsolver", {})
    key = c.get("api_key") or os.getenv("CAPSOLVER_API_KEY", "")
    return key.strip()


def get_api_key() -> str:
    """Get the API access key from config.yaml or .env."""
    c = get_config().get("api_key", "")
    if c and not c.startswith("sk-imagefree2api-xxxx"):
        return c
    # Fallback to .env
    from dotenv import load_dotenv
    load_dotenv()
    return os.getenv("API_KEY", "")


def get_proxy_config() -> Optional[str]:
    """Get proxy URL if enabled, else None."""
    c = get_config().get("proxy", {})
    if c.get("enabled", False):
        url = c.get("url", "")
        if url:
            return url
    # Fallback: check env vars
    return os.getenv("HTTP_PROXY") or os.getenv("https_proxy") or None


def get_generation_config() -> dict:
    c = get_config().get("generation", {})
    pool = c.get("session_pool", {})
    return {
        "max_concurrency": int(c.get("max_concurrency", os.getenv("MAX_CONCURRENCY", "1"))),
        "request_interval": int(c.get("request_interval", os.getenv("REQUEST_INTERVAL_SECONDS", "30"))),
        "output_dir": c.get("output_dir", os.getenv("OUTPUT_DIR", "/data/images")),
        "session_pool": {
            "enabled": bool(pool.get("enabled", True)),
            "session_count": int(pool.get("session_count", os.getenv("IMAGEFREE_SESSION_COUNT", "1"))),
            "max_concurrent_per_session": int(
                pool.get(
                    "max_concurrent_per_session",
                    os.getenv("IMAGEFREE_MAX_CONCURRENT_PER_SESSION", "1"),
                )
            ),
            "cooldown_seconds": int(pool.get("cooldown_seconds", os.getenv("IMAGEFREE_SESSION_COOLDOWN_SECONDS", "60"))),
            "wait_timeout_seconds": int(
                pool.get("wait_timeout_seconds", os.getenv("IMAGEFREE_SESSION_WAIT_TIMEOUT_SECONDS", "180"))
            ),
        },
    }
