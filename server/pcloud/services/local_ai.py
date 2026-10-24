"""Optional local LLM provider (Ollama-compatible HTTP API) - document contents never leave the PC."""
from __future__ import annotations

import httpx

from .. import settings_store


def status() -> dict:
    if not settings_store.get("local_ai_enabled"):
        return {"enabled": False}
    url = settings_store.get("local_ai_url").rstrip("/")
    try:
        r = httpx.get(f"{url}/api/tags", timeout=3)
        r.raise_for_status()
        models = [m["name"] for m in r.json().get("models", [])]
        return {"enabled": True, "reachable": True, "models": models, "model": settings_store.get("local_ai_model")}
    except Exception as e:
        return {"enabled": True, "reachable": False, "error": str(e)}


def _check_url(url: str) -> None:
    host = httpx.URL(url).host
    if host not in {"127.0.0.1", "localhost", "::1"}:
        raise RuntimeError("The local AI provider must run on this PC (localhost)")


def generate(prompt: str, system: str | None = None, timeout: float = 300) -> str:
    if not settings_store.get("local_ai_enabled"):
        raise RuntimeError("Local AI is disabled. Enable it in Settings > AI.")
    url = settings_store.get("local_ai_url").rstrip("/")
    _check_url(url)
    body = {"model": settings_store.get("local_ai_model"), "prompt": prompt, "stream": False}
    if system:
        body["system"] = system
    r = httpx.post(f"{url}/api/generate", json=body, timeout=timeout)
    r.raise_for_status()
    return r.json().get("response", "")


def summarize(text: str, instructions: str = "") -> str:
    text = text[:60_000]
    prompt = (f"{instructions or 'Summarize the following document in clear bullet points, then a one-paragraph overview.'}"
              f"\n\n<document>\n{text}\n</document>")
    return generate(prompt, system="You are a careful assistant that summarizes private documents accurately.")
