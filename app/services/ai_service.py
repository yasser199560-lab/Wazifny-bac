"""Thin AI layer used by job search + recommendations.

Design:
  - Gemini is tried first (primary).
  - If Gemini errors, times out, or isn't configured, Groq is tried
    (fallback) — same prompt, OpenAI-compatible chat endpoint.
  - If BOTH fail, callers get `None` back and are expected to fall back to
    plain (non-AI) keyword logic — search/recommendations must never go
    fully down just because an AI provider is having issues.

Every call logs which provider actually answered (INFO) or why it didn't
(WARNING) — run `python -m scripts.check_ai` any time to get a clear,
one-shot diagnosis of both providers (including listing Groq's currently
valid model names, since free-tier model availability changes over time
and a stale model name is the #1 cause of silent fallback).

Nothing here ever fabricates job listings or company data. It only ever
reasons over documents already in MongoDB (see jobs.py) — its job is
"which of these real jobs match this query/profile, and why", never
"invent a plausible-looking job".
"""

import json
import logging
import re

import httpx

from app.core.config import settings

logger = logging.getLogger("wazifny.ai")

# Gemini occasionally needs longer than 12s on a cold request. Keep the
# fallback, but avoid needlessly abandoning a healthy primary provider.
_TIMEOUT = httpx.Timeout(25.0, connect=5.0)


async def _call_gemini(system_prompt: str, user_prompt: str) -> str | None:
    """Calls Gemini's generateContent endpoint using the new (2026) "Auth"
    key format — sent as an `x-goog-api-key` header, NOT the old `?key=`
    query param that "Standard" keys (AIzaSy...) used. Every key AI Studio
    issues now is this new format (looks like `AQ....`)."""
    if not settings.gemini_api_key:
        return None
    url = (
        f"https://generativelanguage.googleapis.com/v1beta/models/"
        f"{settings.gemini_model}:generateContent"
    )
    payload = {
        "system_instruction": {"parts": [{"text": system_prompt}]},
        "contents": [{"role": "user", "parts": [{"text": user_prompt}]}],
        "generationConfig": {"temperature": 0.2, "maxOutputTokens": 1024},
    }
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.post(
                url,
                json=payload,
                headers={
                    "Content-Type": "application/json",
                    "x-goog-api-key": settings.gemini_api_key,
                },
            )
        if resp.status_code == 429:
            logger.warning(
                "Gemini rate-limited (429) — free-tier quota exceeded for model "
                "'%s'. Falling back to Groq. See https://ai.dev/rate-limit to "
                "check usage, or https://ai.google.dev/gemini-api/docs/models "
                "for a lower-traffic model to switch GEMINI_MODEL to.",
                settings.gemini_model,
            )
            return None
        if resp.status_code != 200:
            logger.warning("Gemini call failed (%s): %s", resp.status_code, resp.text[:300])
            return None
        data = resp.json()
        parts = data["candidates"][0]["content"]["parts"]
        text = "".join(p.get("text", "") for p in parts).strip()
        if text:
            logger.info("AI answered via Gemini (%s).", settings.gemini_model)
        return text
    except Exception as exc:  # noqa: BLE001
        logger.warning("Gemini call raised %s: %s", type(exc).__name__, exc)
        return None


async def _call_groq(system_prompt: str, user_prompt: str) -> str | None:
    if not settings.groq_api_key:
        return None
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.post(
                "https://api.groq.com/openai/v1/chat/completions",
                headers={"Authorization": f"Bearer {settings.groq_api_key}"},
                json={
                    "model": settings.groq_model,
                    "temperature": 0.2,
                    # GPT-OSS is a reasoning model. With its default medium
                    # effort, a short CV extraction can consume the whole
                    # output budget on hidden reasoning and return an empty
                    # final message despite HTTP 200. Low effort is ample
                    # for extraction/ranking and leaves room for the result.
                    "reasoning_effort": "low",
                    "include_reasoning": False,
                    "max_completion_tokens": 2048,
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt},
                    ],
                },
            )
        if resp.status_code == 404:
            logger.warning(
                "Groq call failed (404) — model '%s' doesn't exist or isn't "
                "available on this key. Run `python -m scripts.check_ai` to "
                "list the model names currently valid for your key, then "
                "update GROQ_MODEL in .env.",
                settings.groq_model,
            )
            return None
        if resp.status_code != 200:
            logger.warning("Groq call failed (%s): %s", resp.status_code, resp.text[:300])
            return None
        data = resp.json()
        message = data.get("choices", [{}])[0].get("message", {})
        content = message.get("content")
        # OpenAI-compatible providers normally return a string, but tolerate
        # content-part arrays as well so a valid response is not discarded.
        if isinstance(content, list):
            content = "".join(
                part.get("text", "") if isinstance(part, dict) else str(part)
                for part in content
            )
        text = content.strip() if isinstance(content, str) else ""
        if not text:
            logger.warning(
                "Groq returned HTTP 200 but no final text (finish_reason=%r, model=%r).",
                data.get("choices", [{}])[0].get("finish_reason"), settings.groq_model,
            )
            return None
        if text:
            logger.info("AI answered via Groq (%s) — Gemini fallback.", settings.groq_model)
        return text
    except Exception as exc:  # noqa: BLE001
        logger.warning("Groq call raised %s: %s", type(exc).__name__, exc)
        return None


async def ai_complete(system_prompt: str, user_prompt: str) -> tuple[str | None, str | None]:
    """Returns (text, provider_used). provider_used is 'gemini', 'groq', or
    None if both providers failed / are unconfigured."""
    text = await _call_gemini(system_prompt, user_prompt)
    if text:
        return text, "gemini"

    text = await _call_groq(system_prompt, user_prompt)
    if text:
        return text, "groq"

    logger.warning(
        "Both Gemini and Groq unavailable — using deterministic (non-AI) fallback for this request."
    )
    return None, None


def extract_json(text: str) -> dict | list | None:
    """LLMs love wrapping JSON in ```json fences or a sentence of preamble.
    Pull out the first {...} or [...] block and parse it defensively."""
    if not text:
        return None
    fence_match = re.search(r"```(?:json)?\s*(.*?)```", text, re.DOTALL)
    candidate = fence_match.group(1) if fence_match else text

    start = None
    for i, ch in enumerate(candidate):
        if ch in "{[":
            start = i
            break
    if start is None:
        return None
    end = max(candidate.rfind("}"), candidate.rfind("]"))
    if end == -1 or end < start:
        return None

    try:
        return json.loads(candidate[start : end + 1])
    except (json.JSONDecodeError, ValueError):
        return None
