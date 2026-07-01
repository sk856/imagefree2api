"""API key authentication for the 2API gateway."""
import os
import secrets
import hashlib
from typing import Optional
from fastapi import Header, HTTPException, status

# Generate a random API key if not set in env
_API_KEY_ENV = "API_KEY"
DEFAULT_API_KEY = os.getenv(_API_KEY_ENV, f"sk-imagefree2api-{secrets.token_hex(16)}")


def get_api_key() -> str:
    """Return the configured API key."""
    return DEFAULT_API_KEY


async def verify_api_key(authorization: Optional[str] = Header(None)):
    """
    Verify the Bearer token in the Authorization header.

    Usage in FastAPI:
        @app.post("/v1/images/generations")
        async def generate(_auth=Depends(verify_api_key), ...):
    """
    if not authorization:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing Authorization header",
            headers={"WWW-Authenticate": "Bearer"},
        )

    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid Authorization format. Use: Bearer <token>",
            headers={"WWW-Authenticate": "Bearer"},
        )

    if not secrets.compare_digest(token, DEFAULT_API_KEY):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid API key",
        )
    return token
