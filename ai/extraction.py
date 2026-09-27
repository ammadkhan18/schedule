"""
Orchestrates the AI extraction step:
  images + OCR hints + project context --[Groq vision LLM]--> raw JSON
  raw JSON --[validate/map]--> ExtractedBuildingParams (Pydantic)

If the Groq call fails, times out, or returns malformed JSON, this module
NEVER lets the app crash - it falls back to a fully-defaulted
ExtractedBuildingParams (all fields Low confidence, sourced as
"Default assumption") so the user can still proceed and fill everything in
manually on the verification screen. This fallback path is exercised
whenever `extract_building_params` catches an exception.
"""
from __future__ import annotations

import json
import logging
import math
import re
from typing import List, Optional, Tuple

from PIL import Image
from pydantic import ValidationError

from ai.groq_client import GroqClientError, call_vision_model
from ai.prompts import SYSTEM_PROMPT, build_user_prompt
from engineering import rules
from models.schemas import (
    BeamSpec,
    ColumnSpec,
    ConfidenceLevel,
    Estimate,
    ExtractedBuildingParams,
    FootingSpec,
    OpeningsSpec,
    ServicesSpec,
    Source,
    SlabSpec,
    WallSpec,
)

logger = logging.getLogger(__name__)


FOOTING_TYPES = ["isolated", "strip", "raft", "combined"]
_FOOTING_SYNONYMS = {
    "isolated": "isolated", "pad": "isolated", "spread": "isolated", "column": "isolated", "individual": "isolated",
    "strip": "strip", "wall": "strip", "continuous": "strip",
    "raft": "raft", "mat": "raft",
    "combined": "combined",
}
_NUMBER_RE = re.compile(r"-?\d+(?:\.\d+)?")


def _parse_number(value) -> float:
    """Accepts ints/floats and numeric strings such as "1.2", "1,200",
    "1.2m" or "0.45 m" (first number is taken). Raises ValueError for
    anything non-numeric, negative, NaN or infinite."""
    if isinstance(value, bool):
        raise ValueError(f"Boolean is not a number: {value!r}")
    if isinstance(value, (int, float)):
        number = float(value)
    else:
        match = _NUMBER_RE.search(str(value).replace(",", ""))
        if not match:
            raise ValueError(f"No number found in {value!r}")
        number = float(match.group(0))
    if math.isnan(number) or math.isinf(number) or number < 0:
        raise ValueError(f"Invalid numeric value {value!r}")
    return number


def _to_estimate(d, source: Source = Source.AI_EXTRACTED) -> Estimate:
    """Parses the compact [value, "H|M|L"] array format. Also accepts the
    older {value, confidence, note} object format and a bare number/string
    for robustness against slightly-off model output.
    """
    letter_map = {"H": "High", "M": "Medium", "L": "Low", "HIGH": "High", "MEDIUM": "Medium", "LOW": "Low"}

    if isinstance(d, (list, tuple)) and len(d) >= 1:
        value = d[0]
        conf_raw = str(d[1]).strip().upper() if len(d) > 1 else "M"
        note = ""
    elif isinstance(d, dict) and "value" in d:
        value = d["value"]
        conf_raw = str(d.get("confidence", "M")).strip().upper()
        note = str(d.get("note", ""))[:300]
    elif isinstance(d, (int, float, str)) and not isinstance(d, bool):
        value = d
        conf_raw = "M"
        note = ""
    else:
        raise ValueError(f"Malformed estimate field: {d!r}")

    confidence = ConfidenceLevel(letter_map.get(conf_raw, "Medium"))
    return Estimate(value=_parse_number(value), confidence=confidence, source=source, note=note)


def normalize_footing_type(raw) -> str:
    """Maps free-text/odd-case model output ("Isolated", "pad footing",
    "Mat") onto one of FOOTING_TYPES; unknown values -> "isolated"."""
    text = str(raw or "").strip().lower()
    if text in FOOTING_TYPES:
        return text
    for key, mapped in _FOOTING_SYNONYMS.items():
        if key in text:
            return mapped
    return "isolated"


def resolve_wall_material(ai_text, step1_choice: str) -> str:
    """The AI returns a free-text wall-material guess, but the masonry
    calculation needs an exact key of rules.MASONRY_UNIT_SIZES_M. Returns
    the AI value if it is already a valid key; otherwise maps clearly
    different materials (AAC / concrete block / CSEB) by keyword, and
    falls back to the user's own Step 1 choice for everything else
    (including generic "brick") - so an unrecognised AI string can never
    silently replace the Step 1 selection."""
    keys = list(rules.MASONRY_UNIT_SIZES_M.keys())
    fallback = step1_choice if step1_choice in keys else rules.DEFAULT_WALL_MATERIAL
    text = str(ai_text or "").strip()
    if text in keys:
        return text
    low = text.lower()
    if "aac" in low or "aerated" in low:
        return "AAC block (600x200x200mm)"
    if "cseb" in low or "mud" in low or "stabili" in low:
        return "CSEB / stabilized mud block (300x150x100mm)"
    if "block" in low and "brick" not in low:
        return "Concrete solid block (400x200x200mm)"
    return fallback


class _FieldReader:
    """Reads one estimate at a time from the raw model JSON; a missing or
    malformed field falls back to the matching default (Low confidence)
    and records a warning, instead of discarding the whole extraction."""

    def __init__(self, raw: dict):
        self.raw = raw
        self.warnings: List[str] = []

    def section(self, name: str) -> dict:
        sec = self.raw.get(name)
        return sec if isinstance(sec, dict) else {}

    def get(self, sec: dict, key: str, default: Estimate, label: str) -> Estimate:
        if key not in sec:
            self.warnings.append(f"AI did not return {label}; default used.")
            return default
        try:
            return _to_estimate(sec[key])
        except (ValueError, TypeError) as exc:
            logger.info("Bad AI value for %s: %s", label, exc)
            self.warnings.append(f"AI value for {label} was unreadable; default used.")
            return default


def _map_json_to_params(
    raw: dict,
    wall_thickness_mm: float = 230.0,
    wall_material: str = rules.DEFAULT_WALL_MATERIAL,
    unit_system: str = "SI",
    defaults: Optional[ExtractedBuildingParams] = None,
) -> ExtractedBuildingParams:
    if not isinstance(raw, dict):
        raise ValueError("AI response is not a JSON object")
    # Fields the AI leaves out fall back to `defaults` (e.g. the 5-10 marla
    # plot template) or the project's unit-system defaults.
    d = defaults or default_building_params(wall_thickness_mm, wall_material, unit_system)
    r = _FieldReader(raw)
    top = raw
    f, c, b, s, w, o = (r.section(k) for k in ("footings", "columns", "beams", "slabs", "walls", "openings"))
    svc = r.section("services")

    plinth = r.get(top, "plinth_area_per_floor_sqm", d.plinth_area_per_floor_sqm, "plinth area")
    ext_default = Estimate(
        value=round(4.0 * math.sqrt(max(plinth.value, 0.0)), 2),
        confidence=ConfidenceLevel.LOW,
        source=Source.DEFAULT_ASSUMPTION,
        note="Estimated as 4 x sqrt(plinth area) (square footprint) - please verify",
    )

    params = ExtractedBuildingParams(
        num_floors=r.get(top, "num_floors", d.num_floors, "number of floors"),
        plinth_area_per_floor_sqm=plinth,
        footings=FootingSpec(
            footing_type=normalize_footing_type(f.get("footing_type", "isolated")),
            count=r.get(f, "count", d.footings.count, "footing count"),
            length_m=r.get(f, "length_m", d.footings.length_m, "footing length"),
            width_m=r.get(f, "width_m", d.footings.width_m, "footing width"),
            depth_m=r.get(f, "depth_m", d.footings.depth_m, "footing thickness"),
            founding_depth_m=r.get(f, "founding_depth_m", d.footings.founding_depth_m, "founding depth"),
        ),
        columns=ColumnSpec(
            count=r.get(c, "count", d.columns.count, "column count"),
            width_m=r.get(c, "width_m", d.columns.width_m, "column width"),
            depth_m=r.get(c, "depth_m", d.columns.depth_m, "column depth"),
            height_per_floor_m=r.get(c, "height_per_floor_m", d.columns.height_per_floor_m, "height per floor"),
        ),
        beams=BeamSpec(
            count=r.get(b, "count", d.beams.count, "beam count"),
            avg_length_m=r.get(b, "avg_length_m", d.beams.avg_length_m, "beam length"),
            width_m=r.get(b, "width_m", d.beams.width_m, "beam width"),
            depth_m=r.get(b, "depth_m", d.beams.depth_m, "beam depth"),
        ),
        slabs=SlabSpec(
            area_per_floor_sqm=r.get(s, "area_per_floor_sqm", d.slabs.area_per_floor_sqm, "slab area"),
            thickness_m=r.get(s, "thickness_m", d.slabs.thickness_m, "slab thickness"),
        ),
        walls=WallSpec(
            total_length_per_floor_m=r.get(w, "total_length_per_floor_m", d.walls.total_length_per_floor_m, "total wall length"),
            height_m=r.get(w, "height_m", d.walls.height_m, "wall height"),
            thickness_m=r.get(w, "thickness_m", d.walls.thickness_m, "wall thickness"),
            wall_material=resolve_wall_material(w.get("wall_material"), wall_material),
            external_perimeter_m=r.get(w, "external_perimeter_m", ext_default, "external perimeter"),
        ),
        openings=OpeningsSpec(
            door_count_per_floor=r.get(o, "door_count_per_floor", d.openings.door_count_per_floor, "door count"),
            avg_door_area_sqm=r.get(o, "avg_door_area_sqm", d.openings.avg_door_area_sqm, "door area"),
            window_count_per_floor=r.get(o, "window_count_per_floor", d.openings.window_count_per_floor, "window count"),
            avg_window_area_sqm=r.get(o, "avg_window_area_sqm", d.openings.avg_window_area_sqm, "window area"),
        ),
        services=ServicesSpec(
            bathroom_count_total=r.get(svc, "bathroom_count_total", d.services.bathroom_count_total, "bathroom count"),
            kitchen_count_total=r.get(svc, "kitchen_count_total", d.services.kitchen_count_total, "kitchen count"),
        ),
        overall_notes=str(raw.get("overall_notes", ""))[:1000],
        extraction_warnings=[str(x)[:300] for x in (raw.get("extraction_warnings") or []) if x] if isinstance(raw.get("extraction_warnings", []), list) else [],
    )
    # One grouped message per kind instead of a line per field.
    missing = [w[len("AI did not return "):-len("; default used.")] for w in r.warnings if w.startswith("AI did not return ")]
    unreadable = [w[len("AI value for "):-len(" was unreadable; default used.")] for w in r.warnings if w.startswith("AI value for ")]
    source = "the plot template" if defaults is not None else "standard defaults"
    if missing:
        params.extraction_warnings.append(f"AI did not return: {', '.join(missing)} - {source} used.")
    if unreadable:
        params.extraction_warnings.append(f"AI values were unreadable for: {', '.join(unreadable)} - {source} used.")
    return params


# FPS defaults: the same typical small house as the SI defaults below, but
# expressed in the round feet-inch values Pakistani drawings actually use
# (stored in metres, exact conversions: 1 ft = 0.3048 m, 1 in = 0.0254 m).
_FT = 0.3048
_IN = 0.0254
_SQFT = _FT * _FT


def _default_building_params_fps(wall_thickness_mm: float, wall_material: str) -> ExtractedBuildingParams:
    def est(v: float, note: str = "Standard default - please verify") -> Estimate:
        return Estimate(value=v, confidence=ConfidenceLevel.LOW, source=Source.DEFAULT_ASSUMPTION, note=note)

    det = rules.detailing("FPS")
    return ExtractedBuildingParams(
        num_floors=est(1, "Default: single storey"),
        plinth_area_per_floor_sqm=est(1080 * _SQFT, "Default: 1,080 sqft covered area (30' x 36')"),
        footings=FootingSpec(
            footing_type="isolated",
            count=est(9, "Default: 3x3 column grid"),
            length_m=est(4 * _FT, "Default: 4'-0\" x 4'-0\" isolated footing"),
            width_m=est(4 * _FT, "Default: 4'-0\" x 4'-0\" isolated footing"),
            depth_m=est(det["footing_thickness_m"], "Footing (pad) thickness 1'-6\" - standard default, please verify"),
            founding_depth_m=est(det["founding_depth_m"], "Ground level to underside of footing 5'-0\" - standard default, please verify"),
        ),
        columns=ColumnSpec(
            count=est(9, "Default: 3x3 column grid"),
            width_m=est(9 * _IN, "Default: 9\" x 18\" column"),
            depth_m=est(18 * _IN, "Default: 9\" x 18\" column"),
            height_per_floor_m=est(10 * _FT, "Default: 10'-0\" floor-to-floor height"),
        ),
        beams=BeamSpec(
            count=est(12, "Default: perimeter + a few internal beams"),
            avg_length_m=est(13 * _FT, "Default: 13'-0\" average span"),
            width_m=est(9 * _IN, "Default: 9\" x 18\" beam"),
            depth_m=est(18 * _IN, "Default: 9\" x 18\" beam"),
        ),
        slabs=SlabSpec(
            area_per_floor_sqm=est(1080 * _SQFT, "Default: 1,080 sqft slab"),
            thickness_m=est(5 * _IN, "Default: 5\" RCC slab"),
        ),
        walls=WallSpec(
            total_length_per_floor_m=est(250 * _FT, "Default: 132 ft perimeter + ~118 ft internal partitions for a ~1,080 sqft plan"),
            height_m=est(10 * _FT, "Default: 10'-0\" wall height"),
            thickness_m=est(wall_thickness_mm / 1000.0, "From Step 1 'Wall Thickness' selection"),
            wall_material=wall_material,
            external_perimeter_m=est(132 * _FT, "Default: 30' x 36' footprint perimeter (132 ft)"),
        ),
        openings=OpeningsSpec(
            door_count_per_floor=est(4),
            avg_door_area_sqm=est(21 * _SQFT, "3'-0\" x 7'-0\" standard door (21 sqft)"),
            window_count_per_floor=est(5),
            avg_window_area_sqm=est(16 * _SQFT, "4'-0\" x 4'-0\" standard window (16 sqft)"),
        ),
        services=ServicesSpec(
            bathroom_count_total=est(2, "Default: 2 bathrooms"),
            kitchen_count_total=est(1, "Default: 1 kitchen"),
        ),
        overall_notes="Default assumptions used (no AI extraction performed or extraction failed).",
        extraction_warnings=["All values are generic defaults - please review and edit every field before proceeding."],
    )


def default_building_params(
    wall_thickness_mm: float = 230.0,
    wall_material: str = rules.DEFAULT_WALL_MATERIAL,
    unit_system: str = "SI",
) -> ExtractedBuildingParams:
    """Fully-defaulted fallback used when AI extraction is unavailable or
    fails, and as the starting point for the 'skip AI, enter manually'
    path. All values are standard small-residential defaults, all Low
    confidence, so the UI visibly nudges the user to review every field.

    `wall_thickness_mm`/`wall_material` seed the wall estimate from the
    Step 1 "Wall Thickness"/"Wall Material" selectors: with no drawing to
    read the real wall construction off of, the user's own explicit Step 1
    choice is the best information available. Without this, these fields
    silently defaulted to a fixed 230mm burnt-clay-brick wall regardless of
    what was picked in Step 1 - and since the masonry unit-count/mortar
    calculation in engineering/calculations.py keys off THIS wall_material
    (not project_inputs.wall_material, which is display-only in the
    exports), that mismatch could make the exported BOQ describe and price
    a completely different wall material than the one shown in the
    project's own cover/info table.
    """
    if wall_material not in rules.MASONRY_UNIT_SIZES_M:
        wall_material = rules.DEFAULT_WALL_MATERIAL
    if unit_system == "FPS":
        return _default_building_params_fps(wall_thickness_mm, wall_material)
    def est(v: float, note: str = "Standard default - please verify") -> Estimate:
        return Estimate(value=v, confidence=ConfidenceLevel.LOW, source=Source.DEFAULT_ASSUMPTION, note=note)

    return ExtractedBuildingParams(
        num_floors=est(1, "Default: single storey"),
        plinth_area_per_floor_sqm=est(100.0, "Default: 100 sqm built-up area"),
        footings=FootingSpec(
            footing_type="isolated",
            count=est(9, "Default: 3x3 column grid"),
            length_m=est(1.2),
            width_m=est(1.2),
            depth_m=est(rules.DEFAULT_FOOTING_THICKNESS_M, "Footing (pad) thickness - standard default, please verify"),
            founding_depth_m=est(rules.DEFAULT_FOUNDING_DEPTH_M, "Ground level to underside of footing - standard default, please verify"),
        ),
        columns=ColumnSpec(
            count=est(9, "Default: 3x3 column grid"),
            width_m=est(0.23),
            depth_m=est(0.45),
            height_per_floor_m=est(3.0),
        ),
        beams=BeamSpec(
            count=est(12, "Default: perimeter + a few internal beams"),
            avg_length_m=est(4.0),
            width_m=est(0.23),
            depth_m=est(0.45),
        ),
        slabs=SlabSpec(area_per_floor_sqm=est(100.0), thickness_m=est(0.125)),
        walls=WallSpec(
            total_length_per_floor_m=est(75.0, "Default: 40 m perimeter + ~35 m internal partitions for a ~100 sqm plan"),
            height_m=est(3.0),
            thickness_m=est(wall_thickness_mm / 1000.0, "From Step 1 'Wall Thickness' selection"),
            wall_material=wall_material,
            external_perimeter_m=est(40.0, "Default: 10 m x 10 m footprint perimeter"),
        ),
        openings=OpeningsSpec(
            door_count_per_floor=est(4),
            avg_door_area_sqm=est(1.89, "0.9m x 2.1m standard door"),
            window_count_per_floor=est(5),
            avg_window_area_sqm=est(1.44, "1.2m x 1.2m standard window"),
        ),
        services=ServicesSpec(
            bathroom_count_total=est(2, "Default: 2 bathrooms"),
            kitchen_count_total=est(1, "Default: 1 kitchen"),
        ),
        overall_notes="Default assumptions used (no AI extraction performed or extraction failed).",
        extraction_warnings=["All values are generic defaults - please review and edit every field before proceeding."],
    )


def _strip_code_fences(text: str) -> str:
    """Some models wrap JSON in ```json fences even in JSON mode."""
    t = text.strip()
    if t.startswith("```"):
        t = re.sub(r"^```[a-zA-Z]*\s*", "", t)
        t = re.sub(r"\s*```$", "", t)
    return t.strip()


def extract_building_params(
    api_key: str,
    images: List[Image.Image],
    project_context: str = "",
    ocr_hint: str = "",
    image_labels: List[str] | None = None,
    wall_thickness_mm: float = 230.0,
    wall_material: str = rules.DEFAULT_WALL_MATERIAL,
    unit_system: str = "SI",
    fallback_params: Optional[ExtractedBuildingParams] = None,
    keys=None,
) -> Tuple[ExtractedBuildingParams, str, List[str]]:
    """Returns (params, raw_model_output_text, error_messages).

    `image_labels`, if given, must be the same length/order as `images`
    (e.g. ["Plan", "Section", "Elevation"]) - it's threaded into the
    prompt so the model knows which view to read each kind of dimension
    from (see ai/prompts.py rule 6).

    error_messages is empty on success. On any failure, params falls back
    to `default_building_params()` and error_messages explains why, so the
    UI can show a clear (non-crashing) warning banner.
    """
    errors: List[str] = []
    user_prompt = build_user_prompt(project_context, ocr_hint, image_labels)

    try:
        raw_text = call_vision_model(
            api_key=api_key,
            system_prompt=SYSTEM_PROMPT,
            user_prompt=user_prompt,
            images=images,
            keys=keys,
        )
    except GroqClientError as exc:
        logger.warning("Groq call failed, using defaults: %s", exc)
        return fallback_params or default_building_params(wall_thickness_mm, wall_material, unit_system), "", [str(exc)]

    try:
        raw_json = json.loads(_strip_code_fences(raw_text or ""))
        params = _map_json_to_params(raw_json, wall_thickness_mm, wall_material, unit_system, fallback_params)
        return params, raw_text, errors
    except (json.JSONDecodeError, KeyError, ValueError, TypeError, AttributeError, ValidationError) as exc:
        logger.warning("Failed to parse/validate Groq JSON output, using defaults: %s", exc)
        errors.append(
            "The AI's response could not be parsed into valid building parameters "
            f"({type(exc).__name__}: {exc}). Falling back to standard defaults - "
            "please review and edit every field below."
        )
        return fallback_params or default_building_params(wall_thickness_mm, wall_material, unit_system), raw_text, errors
