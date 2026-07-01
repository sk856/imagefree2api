"""Test raw API response."""
import asyncio
import httpx


async def test():
    async with httpx.AsyncClient() as c:
        r = await c.get("https://imagefree.org")
        print(f"GET /: HTTP {r.status_code}, {len(r.content)} bytes")
        print(f"  Content-Type: {r.headers.get('content-type')}")
        print(f"  Body preview: {r.content[:100]}")

        form = {
            "action": "generate",
            "prompt": "test",
            "width": "1024",
            "height": "1024",
            "cf-turnstile-response": "test",
        }
        r = await c.post(
            "https://imagefree.org/api/image.php",
            data=form,
            headers={"Accept-Encoding": ""},
        )
        print(f"\nPOST /api/image.php: HTTP {r.status_code}, {len(r.content)} bytes")
        print(f"  Content-Type: {r.headers.get('content-type')}")
        print(f"  Headers: {dict(r.headers)}")
        print(f"  Raw hex (first 60): {r.content[:60].hex()}")
        print(f"  Raw text: {r.content[:200]}")

        # Also try with latin-1 decode
        print(f"  Latin-1: {r.content[:200].decode('latin-1')}")


asyncio.run(test())
