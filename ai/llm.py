"""
Free-tier friendly LLM layer.

Problems this solves
--------------------
* Groq's free tier limits TOKENS PER MINUTE per model (about 6,000-8,000 input tokens for many
  models). A drawing page sent at 1600 px costs ~2,500 tokens, so three pages + the prompt
  (~8,300 tokens) is rejected with HTTP 413 "Request too large ... ITPM".
* One provider is a single point of failure for rate limits.

What it does
------------
1. **Token budgeting** - estimates the request size BEFORE sending and shrinks images (resolution
   first, then number of pages) so a request fits the provider's per-minute budget.
2. **Smart retries** - 413 "too large" -> shrink and retry; 429 "try again in 4.2s" -> wait (if short)
   and retry; model unavailable -> next model.
3. **More free providers** (optional keys): Google Gemini (large free token limits, strong vision)
   and OpenRouter free models, both through their OpenAI-compatible endpoints. If Groq is out of
   budget the request automatically continues on the next provider.

Keys are passed explicitly per call (never stored globally) so users of a shared deployment can
never use each other's keys.
"""
from __future__ import annotations

import base64
import io
import json
import math
import re
import time
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from PIL import Image

import config


# ---------------------------------------------------------------------------
# keys & providers
# ---------------------------------------------------------------------------
@dataclass
class LLMKeys:
    groq: str = ""
    gemini: str = ""
    openrouter: str = ""

    def any(self) -> bool:
        return bool(self.groq or self.gemini or self.openrouter)

    def names(self) -> List[str]:
        return [n for n in ("groq", "gemini", "openrouter") if getattr(self, n)]


def keys_from_mapping(ss: Mapping[str, Any]) -> LLMKeys:
    """Build keys from Streamlit session state (or any mapping)."""
    return LLMKeys(groq=str(ss.get("groq_api_key") or "").strip(),
                   gemini=str(ss.get("gemini_api_key") or "").strip(),
                   openrouter=str(ss.get("openrouter_api_key") or "").strip())


@dataclass
class ProviderSpec:
    name: str
    base_url: str
    vision_model: str
    text_model: str
    tool_model: str
    request_token_budget: int  # max estimated INPUT tokens per request (fits the free per-minute limit)
    image_max_dim: int
    label: str


def provider_specs() -> Dict[str, ProviderSpec]:
    g = config.get_secret
    return {
        "gemini": ProviderSpec(
            "gemini", "https://generativelanguage.googleapis.com/v1beta/openai",
            g("GEMINI_VISION_MODEL", "") or getattr(config, "GEMINI_MODEL", "gemini-2.5-flash"),
            g("GEMINI_TEXT_MODEL", "") or getattr(config, "GEMINI_MODEL", "gemini-2.5-flash"),
            g("GEMINI_TOOL_MODEL", "") or getattr(config, "GEMINI_MODEL", "gemini-2.5-flash"),
            int(getattr(config, "GEMINI_REQUEST_TOKEN_BUDGET", 60000)), 1600, "Google Gemini"),
        "openrouter": ProviderSpec(
            "openrouter", "https://openrouter.ai/api/v1",
            g("OPENROUTER_VISION_MODEL", "") or getattr(config, "OPENROUTER_VISION_MODEL", "qwen/qwen2.5-vl-72b-instruct:free"),
            g("OPENROUTER_TEXT_MODEL", "") or getattr(config, "OPENROUTER_TEXT_MODEL", "meta-llama/llama-3.3-70b-instruct:free"),
            g("OPENROUTER_TOOL_MODEL", "") or getattr(config, "OPENROUTER_TOOL_MODEL", "meta-llama/llama-3.3-70b-instruct:free"),
            int(getattr(config, "OPENROUTER_REQUEST_TOKEN_BUDGET", 12000)), 1280, "OpenRouter (free models)"),
    }


# ---------------------------------------------------------------------------
# token estimation & fitting images into a budget
# ---------------------------------------------------------------------------
def text_tokens(text: str) -> int:
    return int(math.ceil(len(text or "") / 3.5))


def image_tokens(w: int, h: int) -> int:
    """Conservative estimate for vision models that use 28x28-pixel patches (Qwen-VL style);
    also an upper bound for tile-based models at the same resolution."""
    return int(math.ceil(w / 28) * math.ceil(h / 28)) + 20


def _scaled(img: Image.Image, max_dim: int) -> Tuple[int, int]:
    w, h = img.size
    s = min(1.0, max_dim / max(w, h))
    return max(1, int(w * s)), max(1, int(h * s))


MIN_IMAGE_DIM = 640
DIM_STEPS = (1600, 1400, 1280, 1152, 1024, 896, 768, 640)


def fit_images(images: Sequence[Image.Image], prompt_tokens: int, budget: int,
               start_dim: int = 1600, max_images: int = 3) -> Tuple[List[Image.Image], int, str]:
    """Return (images resized to fit, max_dim used, note). Lowers the resolution first (drawings stay readable
    down to ~800-1000 px), then drops the least important (last) pages."""
    imgs = list(images)[:max_images]
    note = ""
    if not imgs:
        return [], start_dim, note
    steps = [d for d in DIM_STEPS if d <= start_dim] or [start_dim]
    while imgs:
        for d in steps:
            total = prompt_tokens + sum(image_tokens(*_scaled(i, d)) for i in imgs)
            if total <= budget:
                if d < start_dim:
                    note = f"images reduced to {d}px to fit the free-tier limit"
                return [_resize(i, d) for i in imgs], d, note + (f"; sent {len(imgs)} of {len(images)} pages" if len(imgs) < len(images) else "")
        if len(imgs) == 1:
            break
        imgs = imgs[:-1]
    d = MIN_IMAGE_DIM
    return [_resize(imgs[0], d)], d, f"only the first page sent at {d}px (free-tier limit)"


def _resize(img: Image.Image, max_dim: int) -> Image.Image:
    img = img.convert("RGB")
    w, h = _scaled(img, max_dim)
    return img if (w, h) == img.size else img.resize((w, h), Image.LANCZOS)


def data_url(img: Image.Image, quality: int = 82) -> str:
    buf = io.BytesIO()
    img.convert("RGB").save(buf, format="JPEG", quality=quality)
    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode("ascii")


# ---------------------------------------------------------------------------
# error classification
# ---------------------------------------------------------------------------
def status_code(exc: Exception) -> Optional[int]:
    code = getattr(exc, "status_code", None)
    if code is None:
        code = getattr(getattr(exc, "response", None), "status_code", None)
    return code


def is_too_large(exc: Exception) -> bool:
    t = str(exc).lower()
    return status_code(exc) == 413 or "request too large" in t or "reduce your message size" in t or \
        ("context" in t and "length" in t and "exceed" in t)


def is_rate_limited(exc: Exception) -> bool:
    t = str(exc).lower()
    return status_code(exc) == 429 or "rate limit" in t or "rate_limit" in t or "quota" in t or "resource_exhausted" in t


def retry_after_seconds(exc: Exception) -> Optional[float]:
    """'Please try again in 4.2s' / '1m30.5s' / Retry-After header -> seconds."""
    t = str(exc)
    m = re.search(r"try again in (?:(\d+)m)?\s*(\d+(?:\.\d+)?)s", t)
    if m:
        return float(m.group(1) or 0) * 60 + float(m.group(2))
    m = re.search(r"try again in (\d+(?:\.\d+)?)ms", t)
    if m:
        return float(m.group(1)) / 1000
    headers = getattr(getattr(exc, "response", None), "headers", None) or {}
    try:
        return float(headers.get("retry-after")) if headers.get("retry-after") else None
    except (TypeError, ValueError):
        return None


def is_auth_error(exc: Exception) -> bool:
    t = str(exc).lower()
    return status_code(exc) in (401, 403) or "invalid api key" in t or "api key not valid" in t


def friendly_error(exc: Exception) -> str:
    if is_too_large(exc):
        return "The request was too large for the free AI limit even after shrinking the images."
    if is_rate_limited(exc):
        w = retry_after_seconds(exc)
        return "The free AI limit has been reached" + (f" - try again in about {int(w) + 1} s." if w else " - try again in a minute.")
    if is_auth_error(exc):
        return "The AI key was rejected - check the key in the sidebar."
    return f"The AI service could not be reached ({str(exc)[:160]})."


class LLMError(Exception):
    pass


# ---------------------------------------------------------------------------
# OpenAI-compatible HTTP client (Gemini, OpenRouter, ...) with the same shape as the Groq SDK
# ---------------------------------------------------------------------------
class _HTTPError(Exception):
    def __init__(self, status: int, text: str, headers=None):
        super().__init__(f"Error code: {status} - {text[:600]}")
        self.status_code = status
        self.response = SimpleNamespace(status_code=status, headers=headers or {})


def _ns(obj):
    if isinstance(obj, dict):
        return SimpleNamespace(**{k: _ns(v) for k, v in obj.items()})
    if isinstance(obj, list):
        return [_ns(x) for x in obj]
    return obj


class OpenAICompatClient:
    """Minimal `client.chat.completions.create(**kwargs)` over HTTP for OpenAI-compatible APIs."""

    GROQ_ONLY = ("reasoning_effort", "reasoning_format")

    def __init__(self, base_url: str, api_key: str, timeout: float = 90.0, extra_headers: Optional[dict] = None):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout = timeout
        self.extra_headers = extra_headers or {}
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        import requests
        body = {k: v for k, v in kwargs.items() if k not in self.GROQ_ONLY and v is not None}
        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json", **self.extra_headers}
        try:
            r = requests.post(f"{self.base_url}/chat/completions", headers=headers, data=json.dumps(body), timeout=self.timeout)
        except requests.RequestException as exc:
            raise _HTTPError(503, f"connection error: {exc}") from exc
        if r.status_code >= 400:
            raise _HTTPError(r.status_code, r.text, dict(r.headers))
        data = r.json()
        if "choices" not in data:
            raise _HTTPError(502, f"unexpected response: {str(data)[:300]}")
        resp = _ns(data)
        for ch in resp.choices:  # normalise: tool_calls may be missing / None
            if not hasattr(ch.message, "tool_calls"):
                ch.message.tool_calls = None
            if not hasattr(ch.message, "content"):
                ch.message.content = ""
        return resp


def compat_client(spec: ProviderSpec, key: str) -> OpenAICompatClient:
    headers = {"HTTP-Referer": "https://costlens.streamlit.app", "X-Title": "CostLens"} if spec.name == "openrouter" else None
    return OpenAICompatClient(spec.base_url, key, extra_headers=headers)


# ---------------------------------------------------------------------------
# generic "send one request with budget + retries" used for non-Groq providers
# ---------------------------------------------------------------------------
def send_with_retries(client, model: str, system_prompt: str, user_prompt: str, images: Sequence[Image.Image],
                      budget: int, start_dim: int, max_tokens: int, json_mode: bool, temperature: float = 0.1,
                      max_wait_s: float = 20.0) -> Tuple[str, str]:
    """Returns (text, note). Shrinks on 413, waits on short 429s. Raises the last exception otherwise."""
    prompt_tok = text_tokens(system_prompt) + text_tokens(user_prompt) + 50
    dim = start_dim
    shrink_budget = budget
    waited = False
    for _attempt in range(4):
        imgs, dim_used, note = fit_images(images, prompt_tok, shrink_budget, dim) if images else ([], dim, "")
        content: Any = user_prompt
        if imgs:
            content = [{"type": "text", "text": user_prompt}] + [{"type": "image_url", "image_url": {"url": data_url(i)}}
                                                                    for i in imgs]
        kwargs = dict(model=model, temperature=temperature, max_tokens=max_tokens,
                      messages=[{"role": "system", "content": system_prompt}, {"role": "user", "content": content}])
        if json_mode:
            kwargs["response_format"] = {"type": "json_object"}
        try:
            resp = client.chat.completions.create(**kwargs)
            return resp.choices[0].message.content or "", note
        except Exception as exc:  # noqa: BLE001
            if is_too_large(exc) and imgs:
                shrink_budget = int(shrink_budget * 0.6)
                dim = max(MIN_IMAGE_DIM, int(dim_used * 0.75))
                continue
            if is_rate_limited(exc) and not waited:
                w = retry_after_seconds(exc)
                if w is not None and w <= max_wait_s:
                    time.sleep(w + 0.5)
                    waited = True
                    continue
            if json_mode and status_code(exc) == 400 and "response_format" in str(exc).lower():
                json_mode = False  # some free models do not support JSON mode
                continue
            raise
    raise LLMError("The AI request could not be made small enough for the free limits.")
