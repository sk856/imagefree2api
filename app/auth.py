"""API key authentication for the 2API gateway."""
from typing import Optional

from fastapi import Header, HTTPException, status

from app.config import get_api_key


def get_configured_key() -> str:
    """Return the API key from config.yaml."""
    key = get_api_key()
    if not key:
        raise RuntimeError("API key not configured! Set api_key in config.yaml")
    return key


async def verify_api_key(authorization: Optional[str] = Header(None)):
    """
    Verify the Bearer token in the Authorization header.
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

    from secrets import compare_digest
    if not compare_digest(token, get_configured_key()):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid API key",
        )
    return token
