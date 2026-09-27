"""
App-wide configuration and constants.

Reads secrets in this order of priority:
1. Streamlit secrets (st.secrets) - used on Streamlit Community Cloud
2. Environment variable
3. User-entered value in the sidebar at runtime (handled in app.py, not here)
"""
from __future__ import annotations

import os
from pathlib import Path

APP_NAME = "CostLens"
APP_TAGLINE = "From plans to materials."
APP_FULL_NAME = "CostLens - Drawing-based Material Take-Off for 5-10 marla houses"
APP_VERSION = "0.8.1"

# Brand assets (see assets/generate_logo.py to regenerate/tweak).
ASSETS_DIR = Path(__file__).resolve().parent / "assets"
# Local project library (saved *.costlens.json files). On Streamlit Community Cloud this folder is
# not permanent - users should also download their project file.
PROJECTS_DIR = Path(__file__).resolve().parent / "projects"
LOGO_ICON_PATH = ASSETS_DIR / "costlens_icon.png"
LOGO_HORIZONTAL_PATH = ASSETS_DIR / "costlens_logo.png"
# White wordmark variant for use on the app's dark-navy sidebar - the
# regular lockup above has navy text and would be invisible there.
LOGO_HORIZONTAL_ON_DARK_PATH = ASSETS_DIR / "costlens_logo_on_dark.png"

# Brand palette - also mirrored in .streamlit/config.toml and the CSS
# injected by ui.theme, kept here as the single source of truth for any
# Python code that needs a hex value (e.g. chart colors).
#
BRAND_NAVY = "#0B1E3D"
BRAND_NAVY_LIGHT = "#123A5A"
BRAND_TEAL = "#0D9488"
BRAND_TEAL_BRIGHT = "#2DD4BF"
BRAND_GOLD = "#FBBF24"
BRAND_BG = "#F4F7FB"

# Groq model IDs. Kept in one place so they're easy to bump as Groq
# updates its free-tier vision-capable model lineup. Groq has deprecated
# qwen/qwen3.6-27b in favour of its direct successor qwen/qwen3.8-27b, so
# the successor is the primary model and 3.6 is kept only as a fallback
# while it is still served. Either can be overridden without a code change
# via a GROQ_VISION_MODEL / GROQ_VISION_MODEL_FALLBACK secret or env var
# (see get_secret below) when Groq retires a model.
GROQ_VISION_MODEL_DEFAULT = "qwen/qwen3.8-27b"
GROQ_VISION_MODEL_FALLBACK_DEFAULT = "qwen/qwen3.6-27b"
GROQ_TEXT_MODEL = "openai/gpt-oss-120b"
GROQ_TOOL_MODEL = "openai/gpt-oss-120b"  # copilot (tool calling); falls back to llama-3.3-70b-versatile

# Max images accepted in ONE Groq vision request. qwen/qwen3.8-27b accepts
# at most 3 images per request (qwen3.6-27b: 5) - sending more returns a
# 400 error, so the upload pipeline and the client both cap at this value.
MAX_IMAGES_PER_REQUEST = 3

# Retries for transient Groq errors (429 rate limit, 5xx, connection).
GROQ_MAX_RETRIES = 2
GROQ_RETRY_BASE_DELAY_S = 2.0

# Max dimension (px) we resize any page/image to before sending to Groq.
# Keeps base64 payloads small and inference fast/cheap.
MAX_IMAGE_DIMENSION = 1600
# Free-tier token budgeting (see ai/llm.py). Groq's free tier allows roughly 6,000-8,000 input tokens per
# minute per model; requests are shrunk (image resolution, then number of pages) to stay below this.
GROQ_REQUEST_TOKEN_BUDGET = 5500
# Optional extra free providers (keys entered in the sidebar or set as secrets GEMINI_API_KEY / OPENROUTER_API_KEY)
GEMINI_MODEL = "gemini-2.5-flash"
GEMINI_REQUEST_TOKEN_BUDGET = 60000
OPENROUTER_VISION_MODEL = "qwen/qwen2.5-vl-72b-instruct:free"
OPENROUTER_TEXT_MODEL = "meta-llama/llama-3.3-70b-instruct:free"
OPENROUTER_TOOL_MODEL = "meta-llama/llama-3.3-70b-instruct:free"
OPENROUTER_REQUEST_TOKEN_BUDGET = 12000

# Default currency for the rate book / cost estimate.
DEFAULT_CURRENCY = "PKR"
DEFAULT_CURRENCY_SYMBOL = "PKR "

# Default unit system shown on a fresh project ("SI" or "FPS" - see
# models.schemas.UnitSystem). The user can switch this per-project in
# Step 1; every internal calculation stays in SI regardless of this
# setting (see utils/units.py).
DEFAULT_UNIT_SYSTEM = "SI"

# Rate book provenance note shown in the UI (Step 5) and in exports, so
# users know how current/local the shipped default rates are.
RATE_BOOK_AS_OF = "September 2026"
RATE_BOOK_NOTE = (
    "Default rates are indicative Pakistani market rates (as of "
    f"{RATE_BOOK_AS_OF}), built up from published material prices "
    "(cement, steel, sand, crush, bricks, plaster, paint) plus standard "
    "nominal-mix/labour allowances - NOT a live feed and NOT city- or "
    "supplier-specific. Always override with your own current, local "
    "quotations before relying on the cost estimate."
)

# Supported upload types
SUPPORTED_FILE_TYPES = ["pdf", "png", "jpg", "jpeg"]

# --------------------------------------------------------------------------
# Multi-file drawing upload (Plan / Elevation / Section, etc.)
# --------------------------------------------------------------------------
# The AI call already supports sending several images in one request (that's
# how a multi-page PDF works today) - these limits exist purely to keep a
# multi-FILE upload (e.g. separate Plan + Elevation + Section PDFs) from
# growing the per-request image count (and therefore token usage) without
# bound, so a free/low-tier Groq API key doesn't get rate-limited.
MAX_DRAWING_FILES = 10         # a full drawing set: plans, sections, elevations, structural...
MAX_PAGES_PER_FILE = 20        # pages analysed (free, no AI) from any one PDF; only the best
                               # MAX_TOTAL_IMAGES pages are ever sent to the AI
MAX_TOTAL_IMAGES = MAX_IMAGES_PER_REQUEST  # hard cap across ALL files combined (Groq per-request image limit)

# Max characters of PDF text-layer (vector PDF dimension strings, notes)
# passed to the AI as an extra hint alongside the images.
MAX_PDF_TEXT_HINT_CHARS = 3000

DRAWING_VIEW_TYPES = ["Plan", "Elevation", "Section", "Other / not sure"]

# Confidence levels used throughout the app (AI extraction + calculations)
CONFIDENCE_LEVELS = ["High", "Medium", "Low"]

# Full disclaimer - used in the Excel/PDF exports, where a complete,
# unambiguous legal caveat belongs in the deliverable itself.
DISCLAIMER_TEXT = (
    "This tool generates a PRELIMINARY, indicative Material Take-Off (MTO), "
    "Bill of Quantities (BOQ), and cost estimate using standard engineering "
    "thumb rules applied to AI-assisted drawing interpretation. It is NOT a "
    "structural design, NOT a certified quantity surveyor's estimate, and "
    "MUST NOT be used for tendering, construction, financing, or legal "
    "purposes without independent review and sign-off by a licensed "
    "structural engineer / qualified quantity surveyor. Always verify "
    "AI-extracted dimensions against the actual approved drawings."
)

# Short version shown in the Streamlit UI itself (sidebar / Step 5) as a
# low-key caption rather than a large warning banner - the full legal
# text above still appears in every exported Excel/PDF.
DISCLAIMER_TEXT_SHORT = (
    "Preliminary, drawing-based material take-off - not a structural design or "
    "certified QS takeoff. Verify quantities before procurement or construction."
)


def get_secret(key: str, default: str | None = None) -> str | None:
    """Fetch a secret from Streamlit secrets first, then environment vars."""
    try:
        import streamlit as st

        if hasattr(st, "secrets") and key in st.secrets:
            return st.secrets[key]
    except Exception:
        pass
    return os.environ.get(key, default)


GROQ_VISION_MODEL = get_secret("GROQ_VISION_MODEL", GROQ_VISION_MODEL_DEFAULT) or GROQ_VISION_MODEL_DEFAULT
GROQ_VISION_MODEL_FALLBACK = (
    get_secret("GROQ_VISION_MODEL_FALLBACK", GROQ_VISION_MODEL_FALLBACK_DEFAULT) or GROQ_VISION_MODEL_FALLBACK_DEFAULT
)
