"""Model factory. Local Ollama by default; an optional fallback provider can be configured."""

import io
import os
import sys
from typing import overload

import requests
from dotenv import load_dotenv
from langchain.chat_models import init_chat_model

load_dotenv()


@overload
def env(name: str) -> str | None: ...
@overload
def env(name: str, default: str) -> str: ...
def env(name: str, default: str | None = None) -> str | None:
    return os.getenv(name, default)


def utf8_stdout() -> None:
    """Windows consoles default to cp1252, which cannot print the emoji in the review."""
    if isinstance(sys.stdout, io.TextIOWrapper):
        sys.stdout.reconfigure(encoding="utf-8")


def _make(provider: str, model: str):
    kwargs: dict = {"temperature": 0}
    if provider == "ollama":
        # Ollama's default context window is small and it truncates SILENTLY.
        # Always set num_ctx explicitly or long diffs lose their beginning.
        kwargs["num_ctx"] = int(env("ACR_NUM_CTX", "16384"))
        # Cap the answer length. Without it a 7B model under constrained decoding can loop inside a JSON
        # string (seen: one call ran >10 minutes); a capped, truncated answer fails validation and the
        # agent returns [] instead of hanging the whole review.
        kwargs["num_predict"] = int(env("ACR_NUM_PREDICT", "2048"))
        kwargs["base_url"] = env("ACR_OLLAMA_URL", "http://localhost:11434")
    return init_chat_model(model, model_provider=provider, **kwargs)


def structured_llm(schema, role: str = "ACR"):
    """Return a runnable that outputs `schema` instances.

    role="ACR" -> reviewer model, role="ACR_JUDGE" -> judge model (falls back to reviewer).
    """
    provider = env(f"{role}_PROVIDER") or env("ACR_PROVIDER", "ollama")
    model = env(f"{role}_MODEL") or env("ACR_MODEL", "qwen2.5-coder:7b")
    primary = _make(provider, model).with_structured_output(schema).with_retry(stop_after_attempt=2)

    fb_provider, fb_model = env("ACR_FALLBACK_PROVIDER"), env("ACR_FALLBACK_MODEL")
    if fb_provider and fb_model:
        fallback = _make(fb_provider, fb_model).with_structured_output(schema)
        return primary.with_fallbacks([fallback])
    return primary


def model_label() -> str:
    return f"{env('ACR_PROVIDER', 'ollama')}:{env('ACR_MODEL', 'qwen2.5-coder:7b')}"


def check_model() -> str | None:
    """Fail fast with a clear message instead of a review where every agent call silently failed.
    Returns a problem description, or None when the configured local model is ready."""
    if env("ACR_PROVIDER", "ollama") != "ollama":
        return None
    url = env("ACR_OLLAMA_URL", "http://localhost:11434")
    model = env("ACR_MODEL", "qwen2.5-coder:7b")
    try:
        r = requests.get(f"{url}/api/tags", timeout=5)
        r.raise_for_status()
        names = {m.get("name") for m in r.json().get("models", [])}
    except (requests.RequestException, ValueError) as e:
        return f"Ollama is not reachable at {url} ({type(e).__name__}). Start the Ollama app or run `ollama serve`."
    if model not in names and f"{model}:latest" not in names:
        return f"Model {model} is not pulled. Run: ollama pull {model}"
    return None
