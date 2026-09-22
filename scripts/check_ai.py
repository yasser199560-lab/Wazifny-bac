"""Quick, standalone diagnostic for both AI providers.

Run this any time search/matching seems to be silently falling back to
non-AI results — it tells you exactly which provider works, which
doesn't, and why (bad key, wrong model name, rate limit, etc.), including
listing the Groq model names your key actually has access to (free-tier
model availability changes over time, and a stale GROQ_MODEL is the most
common cause of a silent fallback).

    cd backend
    python -m scripts.check_ai
"""

import asyncio
import logging
import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

from app.core.config import settings  # noqa: E402
from app.services.ai_service import _call_gemini, _call_groq  # noqa: E402


async def check_gemini() -> None:
    print(f"\n[Gemini] model={settings.gemini_model!r}")
    if not settings.gemini_api_key:
        print("  SKIPPED — GEMINI_API_KEY not set in .env")
        return
    text = await _call_gemini("Reply with exactly: OK", "ping")
    if text:
        print(f"  SUCCESS — Gemini responded: {text[:80]!r}")
    else:
        print("  FAILED — see the WARNING log line above for the exact reason.")


async def list_groq_models() -> None:
    if not settings.groq_api_key:
        return
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(
                "https://api.groq.com/openai/v1/models",
                headers={"Authorization": f"Bearer {settings.groq_api_key}"},
            )
        if resp.status_code == 200:
            models = [m["id"] for m in resp.json().get("data", [])]
            print(f"  Models available to your Groq key ({len(models)}):")
            for m in sorted(models):
                marker = "  <- currently configured" if m == settings.groq_model else ""
                print(f"    - {m}{marker}")
        else:
            print(f"  Could not list Groq models ({resp.status_code}): {resp.text[:200]}")
    except Exception as exc:  # noqa: BLE001
        print(f"  Could not list Groq models: {exc}")


async def check_groq() -> None:
    print(f"\n[Groq] model={settings.groq_model!r}")
    if not settings.groq_api_key:
        print("  SKIPPED — GROQ_API_KEY not set in .env")
        return
    text = await _call_groq("Reply with exactly: OK", "ping")
    if text:
        print(f"  SUCCESS — Groq responded: {text[:80]!r}")
    else:
        print("  FAILED — see the WARNING log line above for the exact reason.")
        await list_groq_models()


async def main() -> None:
    print("Checking AI providers (Gemini primary, Groq fallback)...")
    await check_gemini()
    await check_groq()
    print(
        "\nIf both failed: search/matching will still work, just with the "
        "non-AI keyword/category fallback — nothing breaks, results are "
        "just less precise until this is fixed."
    )


if __name__ == "__main__":
    asyncio.run(main())
