"""Diagnose capsolver SDK."""
import capsolver
import os

print(f"SDK file: {capsolver.__file__}")
print(f"SDK version attr: {getattr(capsolver, '__version__', 'N/A')}")

# Check supported task types
from capsolver.check import SUPPORT_TASK_TYPE
print(f"\nSupported task types ({len(SUPPORT_TASK_TYPE)}):")
for t in sorted(SUPPORT_TASK_TYPE):
    print(f"  - {t}")

# Check if AntiTurnstile is there
has_turnstile = any("turnstile" in t.lower() for t in SUPPORT_TASK_TYPE)
has_cloudflare = any("cloudflare" in t.lower() for t in SUPPORT_TASK_TYPE)
print(f"\nHas Turnstile type: {has_turnstile}")
print(f"Has Cloudflare type: {has_cloudflare}")

# Check the env
from dotenv import load_dotenv
load_dotenv()
env_key = os.getenv("CAPSOLVER_API_KEY", "")
print(f"\nEnv CAPSOLVER_API_KEY: {'[SET]' if env_key else '[NOT SET]'}")
print(f"Key length: {len(env_key)}")
if env_key:
    print(f"Key prefix: {env_key[:10]}...")
    print(f"Key suffix: ...{env_key[-8:]}")
