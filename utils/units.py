"""
Unit-system conversion helpers.

DESIGN RULE (mirrors the "AI never calculates" rule in engineering/): every
number is stored and calculated internally in SI/metric units (m, m2/sqm,
m3, kg) exactly as before. Nothing in engineering/calculations.py,
mto_boq/boq_generator.py, or models/schemas.py changes its unit
convention. This module is a pure DISPLAY/INPUT conversion layer:

  - The UI shows values in feet/inches/sqft/cft when the user picks the
    "FPS" unit system, but immediately converts them back to SI before
    storing them in an Estimate or MaterialRate.
  - The MTO/BOQ tables and exports convert SI quantities/rates to
    FPS-equivalent values purely for display; the underlying arithmetic
    (quantity * rate = amount) is unaffected because both quantity and
    rate are converted by the same factor in opposite directions, so
    amounts always come out identical regardless of which unit system is
    selected.

FPS is the DEFAULT unit system (Pakistani practice). When it is selected,
EVERYTHING the user sees is FPS: Step-3 inputs, default dimensions (true
feet-inch round values such as 10'-0" floor height, 9" walls, 4'x4'
footings - see engineering/rules.py DETAILING_FPS and
ai/extraction.default_building_params), MTO/BOQ quantities and rates,
calculation traces (inputs_used keys/values via localize_inputs below),
descriptions/assumptions/validation messages (feet-inch text via
fmt_ftin/fmt_in), and the Excel/PDF exports. Because every conversion here
is exact and linear, each FPS figure equals what a direct feet-inch hand
calculation gives (see tests/test_fps_system.py) - SI is only the
canonical storage format.

Unit conventions used when "FPS" is selected (standard Pakistani
construction practice):
  - length            -> feet (ft)
  - small "thickness"-type dimensions (slab/wall thickness, column &
    beam cross-section) -> inches (in), since these are conventionally
    quoted in inches even in FPS-speaking markets (e.g. "9 inch wall",
    "5 inch slab", "9x18 column")
  - area              -> square feet (sqft)
  - volume            -> cubic feet (cft)
  - weight (steel)    -> kilograms (kg) - unchanged in both systems,
    since Pakistani steel markets always quote/sell by the kg regardless
    of whether the rest of the job is being measured in feet or metres.
  - counts / lump-sum -> unchanged
"""
from __future__ import annotations

import re
from typing import Dict, Optional, Tuple

SI = "SI"
FPS = "FPS"

UNIT_SYSTEM_LABELS = {
    SI: "SI (Metric - m, m², m³)",
    FPS: "FPS (Feet-Inch, Pakistani practice - ft, sqft, cft)",
}

# --------------------------------------------------------------------------
# Base conversion factors
# --------------------------------------------------------------------------
FT_PER_M = 3.280839895
M_PER_FT = 1.0 / FT_PER_M

IN_PER_M = 39.37007874
M_PER_IN = 1.0 / IN_PER_M

SQFT_PER_SQM = FT_PER_M ** 2  # 10.76391...
SQM_PER_SQFT = 1.0 / SQFT_PER_SQM

CFT_PER_CUM = FT_PER_M ** 3  # 35.31467...
CUM_PER_CFT = 1.0 / CFT_PER_CUM


def is_fps(unit_system: str) -> bool:
    return unit_system == FPS


# --------------------------------------------------------------------------
# Scalar dimension conversion (used on the Step-3 edit form). "kind" is one
# of "length", "thickness", "area", or None (no conversion - counts etc.)
# --------------------------------------------------------------------------


def to_display(value_si: float, kind: str | None, unit_system: str) -> float:
    """Convert a canonical SI value to the display value for unit_system."""
    if not is_fps(unit_system) or kind is None:
        return value_si
    if kind == "length":
        return value_si * FT_PER_M
    if kind == "thickness":
        return value_si * IN_PER_M
    if kind == "area":
        return value_si * SQFT_PER_SQM
    return value_si


def to_si(value_display: float, kind: str | None, unit_system: str) -> float:
    """Convert a display value (already in the user's chosen system) back
    to canonical SI for storage in an Estimate."""
    if not is_fps(unit_system) or kind is None:
        return value_display
    if kind == "length":
        return value_display * M_PER_FT
    if kind == "thickness":
        return value_display * M_PER_IN
    if kind == "area":
        return value_display * SQM_PER_SQFT
    return value_display


def dimension_unit_label(kind: str | None, unit_system: str) -> str:
    if kind is None:
        return ""
    if not is_fps(unit_system):
        return {"length": "m", "thickness": "m", "area": "sqm"}.get(kind, "")
    return {"length": "ft", "thickness": "in", "area": "sqft"}.get(kind, "")


# --------------------------------------------------------------------------
# MTO/BOQ line-item unit conversion. Canonical units used throughout
# QuantityLineItem / BOQLineItem / MaterialRate are "m3", "m2", "kg",
# "Nos", "LS" - only "m3" and "m2" have an FPS equivalent.
# --------------------------------------------------------------------------

_CANONICAL_TO_FPS_UNIT = {"m3": "cft", "m2": "sqft"}
_QTY_FACTOR = {"m3": CFT_PER_CUM, "m2": SQFT_PER_SQM}


def display_unit(canonical_unit: str, unit_system: str) -> str:
    """The unit label to show for a given canonical unit + unit system."""
    if not is_fps(unit_system):
        return canonical_unit
    return _CANONICAL_TO_FPS_UNIT.get(canonical_unit, canonical_unit)


def display_quantity(value_si: float, canonical_unit: str, unit_system: str) -> float:
    """Convert an SI quantity (m3/m2/kg/Nos/LS) to its display equivalent."""
    if not is_fps(unit_system):
        return value_si
    factor = _QTY_FACTOR.get(canonical_unit)
    return value_si * factor if factor else value_si


def display_rate(rate_si: float, canonical_unit: str, unit_system: str) -> float:
    """Convert a rate quoted per canonical SI unit (e.g. PKR/m3) into the
    equivalent rate per display unit (e.g. PKR/cft), such that
    display_quantity * display_rate == si_quantity * si_rate (the amount
    is always unit-system-invariant)."""
    if not is_fps(unit_system):
        return rate_si
    factor = _QTY_FACTOR.get(canonical_unit)
    return rate_si / factor if factor else rate_si


def rate_to_si(rate_display: float, canonical_unit: str, unit_system: str) -> float:
    """Inverse of display_rate - used when the user edits a rate in the
    rate-book editor while FPS is selected, to convert back to the
    canonical per-m3/per-m2 rate that generate_boq() expects."""
    if not is_fps(unit_system):
        return rate_display
    factor = _QTY_FACTOR.get(canonical_unit)
    return rate_display * factor if factor else rate_display


def quantity_and_unit_for_display(value_si: float, canonical_unit: str, unit_system: str) -> Tuple[float, str]:
    return display_quantity(value_si, canonical_unit, unit_system), display_unit(canonical_unit, unit_system)


# --------------------------------------------------------------------------
# Wall-material label relabeling. The wall_material STRING itself is a
# lookup key into engineering.rules.MASONRY_UNIT_SIZES_M /
# MASONRY_UNIT_ACTUAL_SIZES_M, so it must never change - only how it's
# displayed in the selectbox. This appends an inch-equivalent to any
# "NxNxNmm" dimension pattern embedded in the label for FPS display.
# --------------------------------------------------------------------------
_MM_DIMS_RE = re.compile(r"(\d+(?:\.\d+)?)x(\d+(?:\.\d+)?)x(\d+(?:\.\d+)?)mm")


def relabel_wall_material(label: str, unit_system: str) -> str:
    if not is_fps(unit_system):
        return label

    def _sub(m: "re.Match[str]") -> str:
        mm_vals = m.groups()
        in_vals = [f"{float(v) / 25.4:.1f}" for v in mm_vals]
        return "x".join(in_vals) + "in (" + "x".join(mm_vals) + "mm)"

    return _MM_DIMS_RE.sub(_sub, label)


# --------------------------------------------------------------------------
# FPS text formatting (feet-inch notation, Pakistani drawing convention)
# --------------------------------------------------------------------------


def _trim(x: float, places: int = 2) -> str:
    text = f"{x:.{places}f}".rstrip("0").rstrip(".")
    return text if text not in ("", "-0") else "0"


def fmt_in(value_m: float) -> str:
    """Small dimension in inches, e.g. 0.1524 -> '6"', 0.0127 -> '0.5"'."""
    return f'{_trim(value_m * IN_PER_M, 3)}"'


def fmt_ftin(value_m: float) -> str:
    """Length in feet-inch notation, e.g. 3.048 -> 10'-0", 1.5 -> 4'-11.06"."""
    total_in = round(value_m * IN_PER_M, 2)
    sign = "-" if total_in < 0 else ""
    total_in = abs(total_in)
    feet = int(total_in // 12)
    inches = round(total_in - feet * 12, 2)
    if inches >= 12:
        feet, inches = feet + 1, inches - 12
    return f"{sign}{feet}'-{_trim(inches)}\""


def small_text(value_m: float, unit_system: str, si_text: str) -> str:
    """`si_text` verbatim in SI (keeps SI output byte-identical); inches in FPS."""
    return fmt_in(value_m) if is_fps(unit_system) else si_text


def length_text(value_m: float, unit_system: str, si_text: str) -> str:
    """`si_text` verbatim in SI; feet-inch notation in FPS."""
    return fmt_ftin(value_m) if is_fps(unit_system) else si_text


def area_text(value_sqm: float, unit_system: str, si_text: str, places: int = 1) -> str:
    return f"{value_sqm * SQFT_PER_SQM:,.{places}f} sqft" if is_fps(unit_system) else si_text


def unit_word(canonical_unit: str, unit_system: str) -> str:
    """'m3' -> 'cft', 'm2' -> 'sqft' in FPS (for use inside sentences)."""
    return display_unit(canonical_unit, unit_system)


def kg_per_volume(value_kg_per_m3: float, unit_system: str) -> float:
    return value_kg_per_m3 / CFT_PER_CUM if is_fps(unit_system) else value_kg_per_m3


def kg_per_volume_unit(unit_system: str) -> str:
    return "kg/cft" if is_fps(unit_system) else "kg/m3"


# --------------------------------------------------------------------------
# Calculation-trace (QuantityLineItem.inputs_used) localisation
# --------------------------------------------------------------------------
# Trace keys ending in "_m" that are SMALL dimensions quoted in inches in
# Pakistani practice (thicknesses, member cross-sections, bearings, ...).
# Every other "_m" key is a length quoted in feet.
SMALL_DIMENSION_KEYS = {
    "pcc_thickness_m", "working_space_m", "pcc_projection_m", "footing_thickness_m",
    "width_b_m", "depth_d_m", "slab_thickness_m", "depth_below_slab_m",
    "bearing_each_side_m", "lintel_depth_m", "wall_thickness_m", "chajja_projection_m",
    "chajja_avg_thickness_m", "waist_m", "riser_m", "thickness_m", "dpc_thickness_m",
    "soling_thickness_m", "pcc_floor_thickness_m",
}
# Keys whose meaning depends on the member: a beam's width/depth are
# cross-section dimensions (inches) but a footing's are plan sizes (feet).
_BEAM_SMALL_KEYS = {"width_m", "depth_m"}


def _localize_key(item_code: str, key: str, value):
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return key, value
    if key.endswith("_kg_per_m3"):
        return key[: -len("_kg_per_m3")] + "_kg_per_cft", round(value / CFT_PER_CUM, 4)
    if key.endswith("_m3"):
        return key[:-3] + "_cft", round(value * CFT_PER_CUM, 4)
    if key.endswith("_sqm"):
        return key[:-4] + "_sqft", round(value * SQFT_PER_SQM, 3)
    if key.endswith("_m2"):
        return key[:-3] + "_sqft", round(value * SQFT_PER_SQM, 3)
    if key.endswith("_m"):
        small = key in SMALL_DIMENSION_KEYS or ("BEAM" in item_code and key in _BEAM_SMALL_KEYS)
        if small:
            return key[:-2] + "_in", round(value * IN_PER_M, 3)
        return key[:-2] + "_ft", round(value * FT_PER_M, 4)
    return key, value


def localize_inputs(item_code: str, inputs: Dict, unit_system: str) -> Dict:
    """Returns the calculation-trace dict in the chosen unit system. SI ->
    returned unchanged (same object). FPS -> keys renamed (_m -> _ft/_in,
    _sqm -> _sqft, _m3 -> _cft, _kg_per_m3 -> _kg_per_cft) and values
    converted exactly."""
    if not is_fps(unit_system):
        return inputs
    out = {}
    for k, v in inputs.items():
        nk, nv = _localize_key(item_code, k, v)
        out[nk] = nv
    return out


def localize_formula(formula: str, unit_system: str) -> str:
    """Unit words that appear inside symbolic formula text."""
    if not is_fps(unit_system):
        return formula
    return (
        formula.replace("kg_per_m3", "kg_per_cft")
        .replace("avg_window_area_sqm", "avg_window_area_sqft")
        .replace("the 10 mm mortar joint", 'the 3/8" (10 mm) mortar joint')
    )


# --------------------------------------------------------------------------
# Step-1 thickness options
# --------------------------------------------------------------------------
# Wall thickness choices (stored in mm). FPS offers the Pakistani brick
# multiples (3", 4.5", 6", 9", 13.5") as exact inch values; SI keeps its
# metric list.
WALL_THICKNESS_OPTIONS_MM = {
    SI: [100, 115, 150, 200, 230],
    FPS: [76.2, 114.3, 152.4, 228.6, 342.9],
}
WALL_THICKNESS_DEFAULT_MM = {SI: 230, FPS: 228.6}
# Plaster thickness defaults (mm): SI 12/18 mm; FPS 1/2" and 3/4".
PLASTER_THICKNESS_DEFAULT_MM = {SI: (12, 18), FPS: (12.7, 19.05)}


def nearest_option(value: float, options: list) -> Optional[float]:
    """Maps a stored value from the other unit system onto the closest
    option of this one (e.g. 230 mm -> 228.6 mm = 9")."""
    if not options:
        return None
    return min(options, key=lambda o: abs(o - value))


def wall_thickness_label(mm: float, unit_system: str) -> str:
    if is_fps(unit_system):
        return f'{_trim(mm / 25.4)}" ({_trim(mm, 1)} mm)'
    return f"{mm:g} mm"


_NICE_STEPS = [0.125, 0.25, 0.5, 1.0, 2.0, 5.0, 10.0, 25.0, 50.0, 100.0]


def display_step(step_si: float, kind: str | None, unit_system: str) -> float:
    """Widget step size in display units. FPS rounds the converted step to
    a practical value (e.g. 0.05 m -> 2", 1 sqm -> 10 sqft, 0.5 m -> 2 ft)
    so the +/- buttons move in sensible feet-inch increments. SI unchanged."""
    converted = to_display(step_si, kind, unit_system) or step_si
    if not is_fps(unit_system) or kind is None:
        return converted
    import math as _math
    return min(_NICE_STEPS, key=lambda s: abs(_math.log(s) - _math.log(converted)))
