"""
AI reading of a hand sketch / photo / written description for the guided
("no drawings") route. Uses the same free Groq models as the rest of the app.
The model returns JSON that detailed_mto.brief.apply_ai_result() merges into
the ProjectBrief; the app then asks the remaining questions itself.
"""
from __future__ import annotations

import io
import json
import re
from typing import List, Optional, Tuple

from PIL import Image

import config

SYSTEM_PROMPT = """You are an experienced Pakistani residential architect and quantity surveyor.
You read rough hand sketches, photos of sketches and plain-language descriptions of 5-10 marla houses
(Pakistan) and turn them into structured data. Dimensions are in FEET (Pakistani practice, e.g. 12'-6" = 12.5).
Never invent precise dimensions that are not written or clearly scaled on the sketch - use null instead.
Answer ONLY with a JSON object, no other text."""

USER_PROMPT = """Read the attached sketch(es) and/or the description and return JSON with exactly this structure:
{
  "plot": {"marla": number|null, "width_ft": number|null, "depth_ft": number|null},
  "storeys": number|null,                       // 1, 2 or 3 (ground = 1)
  "structure": "load_bearing" | "rcc_frame" | null,
  "floors": [
    {"floor": "ground" | "first" | "second" | "roof",
     "rooms": [{"name": "text as written", "type": "bedroom|bathroom|drawing room|lounge|kitchen|laundry|staircase|car porch|terrace|store|servant quarter|mumty|lawn",
                "length_ft": number|null, "width_ft": number|null}]}
  ],
  "features": {"corner_plot": bool|null, "septic_tank": bool|null, "mumty": bool|null, "basement": bool|null},
  "notes": "one or two sentences on what you could and could not read",
  "questions": ["up to 6 short, simple questions (plain English, for a house owner) about things that are missing or unclear and that change material quantities"]
}
Rules: list every room you can see or that is described, on the correct floor. Bathrooms attached to bedrooms are separate rooms.
If a size is written like 12x13 or 12'-0"x13'-0", length_ft=12, width_ft=13. Do not ask about things already clear."""


def _json_from(text: str) -> dict:
    t = (text or "").strip()
    t = re.sub(r"^```(?:json)?|```$", "", t, flags=re.MULTILINE).strip()
    try:
        return json.loads(t)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", t, flags=re.DOTALL)
        if m:
            try:
                return json.loads(m.group(0))
            except json.JSONDecodeError:
                pass
    return {}


def files_to_images(files: List[dict], max_images: int = 4) -> List[Image.Image]:
    """Uploaded sketch files (png/jpg/pdf) -> PIL images (PDF pages rendered)."""
    images: List[Image.Image] = []
    for f in files:
        name = (f.get("name") or "").lower()
        data = f.get("bytes") or b""
        try:
            if name.endswith(".pdf"):
                import pymupdf
                doc = pymupdf.open(stream=data, filetype="pdf")
                for page in list(doc)[: max_images]:
                    pix = page.get_pixmap(dpi=110)
                    images.append(Image.open(io.BytesIO(pix.tobytes("png"))).convert("RGB"))
                doc.close()
            else:
                images.append(Image.open(io.BytesIO(data)).convert("RGB"))
        except Exception:
            continue
        if len(images) >= max_images:
            break
    return images[:max_images]


def read_sketch(api_key: str, images: List[Image.Image], description: str = "",
                previous: Optional[dict] = None, answers: Optional[List[Tuple[str, str]]] = None,
                keys=None) -> Tuple[dict, str]:
    """Returns (json_dict, error_message). Uses the vision model when images are given, else the text model.
    `previous` + `answers` refine an earlier reading with the owner's answers to the AI's questions."""
    from ai.groq_client import call_text_model, call_vision_model

    prompt = USER_PROMPT
    if description.strip():
        prompt += "\n\nOwner's description:\n" + description.strip()[:3000]
    if previous:
        prompt += "\n\nYour previous reading (update it, keep what is still right):\n" + json.dumps(previous)[:4000]
    if answers:
        prompt += "\n\nOwner's answers to your questions:\n" + "\n".join(f"Q: {q}\nA: {a}" for q, a in answers if a.strip())
    try:
        if images:
            raw = call_vision_model(api_key, SYSTEM_PROMPT, prompt, images, max_tokens=2500, keys=keys)
        else:
            raw = call_text_model(api_key, SYSTEM_PROMPT, prompt, model=config.GROQ_TEXT_MODEL, max_tokens=2500,
                                  json_mode=True, keys=keys)
    except Exception as exc:  # noqa: BLE001
        return {}, f"The AI could not be reached: {exc}"
    data = _json_from(raw)
    if not data:
        return {}, "The AI answer could not be understood - please answer the questions below yourself."
    return data, ""
