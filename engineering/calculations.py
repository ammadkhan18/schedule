"""
Deterministic engineering calculations.

CRITICAL DESIGN RULE: every function in this file is a pure Python
function of plain numbers/Pydantic models. No LLM call happens here or is
ever allowed to happen here. The AI is only used upstream (ai/extraction.py)
to *guess* the input parameters (dimensions/counts); everything downstream
of that guess is standard, auditable civil-engineering arithmetic.

Every public "compute_*" function returns one or more `QuantityLineItem`
objects that carry:
  - the numeric quantity
  - the formula name (human readable)
  - the exact inputs used (so a user/engineer can hand-verify it)
  - a confidence level inherited from the weakest input Estimate
  - a list of assumption strings shown in the UI's traceability panel

User-editable thumb rules / key default dimensions arrive as an
`EngineeringAssumptions` object (Step 3 "Engineering assumptions" panel);
every function that uses one accepts `assumptions=None` and falls back to
the defaults (which mirror engineering/rules.py).
"""
from __future__ import annotations

import contextvars
import math
from typing import List, Optional

from engineering import rules
from models.schemas import (
    BeamSpec,
    ColumnSpec,
    ConfidenceLevel,
    EngineeringAssumptions,
    Estimate,
    ExtractedBuildingParams,
    FootingSpec,
    OpeningsSpec,
    ProjectInputs,
    QuantityLineItem,
    SlabSpec,
    WallSpec,
)
from utils import units
from utils.helpers import combine_confidence


# In FPS projects, m3/m2 quantities keep full precision so they are rounded
# in cft/sqft at display time (rounding to 0.001 m3 first and converting
# afterwards would show e.g. 45.556 cft instead of the exact 45.563 cft).
# SI rounding is unchanged. Set by generate_all_quantities().
_FPS_PRECISION = contextvars.ContextVar("fps_precision", default=False)


def _q(value: float, places: int, unit: str) -> float:
    if _FPS_PRECISION.get() and unit in ("m3", "m2"):
        return round(value, places + 6)
    return round(value, places)


def _assump(assumptions: Optional[EngineeringAssumptions]) -> EngineeringAssumptions:
    return assumptions if assumptions is not None else EngineeringAssumptions()


def founding_depth_estimate(footings: FootingSpec, unit_system: str = units.SI) -> Estimate:
    """Founding depth (ground level -> underside of footing). Falls back to
    the unit system's standard default (Low confidence) for params created
    before this field existed."""
    if footings.founding_depth_m is not None:
        return footings.founding_depth_m
    return Estimate(
        value=rules.detailing(unit_system)["founding_depth_m"],
        confidence=ConfidenceLevel.LOW,
        note="Default founding depth - please verify",
    )


def external_perimeter_estimate(params: ExtractedBuildingParams) -> Estimate:
    """External wall perimeter per floor. Falls back to 4 x sqrt(plinth
    area) - the perimeter of a square footprint, i.e. the MINIMUM possible
    for a rectangular plan - with Low confidence."""
    if params.walls.external_perimeter_m is not None:
        return params.walls.external_perimeter_m
    return Estimate(
        value=round(4.0 * math.sqrt(max(params.plinth_area_per_floor_sqm.value, 0.0)), 2),
        confidence=ConfidenceLevel.LOW,
        note="Estimated as 4 x sqrt(plinth area) (square footprint) - please verify",
    )


def column_stub_below_ground_m(footings: FootingSpec, unit_system: str = units.SI) -> float:
    """Column/pedestal height from the TOP of the footing up to natural
    ground level = founding depth - footing thickness (never negative)."""
    return max(founding_depth_estimate(footings, unit_system).value - footings.depth_m.value, 0.0)


# --------------------------------------------------------------------------
# Excavation
# --------------------------------------------------------------------------


def compute_excavation(
    footings: FootingSpec,
    soil_type: str,
    assumptions: Optional[EngineeringAssumptions] = None,
    unit_system: str = units.SI,
) -> QuantityLineItem:
    A = _assump(assumptions)
    slope_factor = rules.SOIL_SIDE_SLOPE_FACTOR.get(soil_type, 1.05)
    ws = A.excavation_working_space_m
    fd = founding_depth_estimate(footings, unit_system)

    L = footings.length_m.value + 2 * ws
    W = footings.width_m.value + 2 * ws
    D = fd.value + A.pcc_thickness_m  # dig down to the underside of the PCC bed
    n = footings.count.value

    volume = n * L * W * D * slope_factor

    return QuantityLineItem(
        item_code="EXC-01",
        description=f"Earthwork excavation in {soil_type.lower()} for footings (incl. working space)",
        category="Excavation",
        unit="m3",
        quantity=_q(volume, 3, "m3"),
        confidence=combine_confidence(footings.count, footings.length_m, footings.width_m, fd),
        formula="V = n × (L + 2×working_space) × (W + 2×working_space) × (founding_depth + PCC_thickness) × soil_side_slope_factor",
        inputs_used={
            "footing_count": n,
            "footing_length_m": footings.length_m.value,
            "footing_width_m": footings.width_m.value,
            "founding_depth_m": fd.value,
            "pcc_thickness_m": A.pcc_thickness_m,
            "working_space_m": ws,
            "soil_side_slope_factor": slope_factor,
        },
        assumptions=[
            f"Isolated pad footing excavation assumed ({footings.footing_type}).",
            "Excavation depth = founding depth (ground level to underside of footing) + PCC bed thickness - "
            "NOT the footing's own thickness.",
            f"{units.small_text(ws, unit_system, f'{ws*1000:.0f} mm')} working space added on each side for shuttering.",
            f"Soil type '{soil_type}' side-slope/bulking factor = {slope_factor}.",
            "Backfill of the excavated earth around the footings is quantified separately (BACKFILL-01).",
        ],
    )


# --------------------------------------------------------------------------
# Cement / sand / aggregate procurement breakdown
# --------------------------------------------------------------------------
# These functions turn a cast volume (concrete OR mortar) into the raw
# materials someone actually has to go buy: cement bags, sand, and (for
# concrete only) coarse aggregate/crush. They are called right after each
# concrete/mortar QuantityLineItem is built (see the helper functions below
# and generate_all_quantities()) and return QuantityLineItems flagged
# `informational=True` - their cost is already inside the parent item's
# composite BOQ rate (e.g. FTG-CONC-01's PKR/m3 rate already covers its own
# cement+sand+aggregate+labour), so generate_boq() skips pricing them to
# avoid double-counting. They exist purely so the MTO can answer "how many
# bags of cement / how much sand / how much crush do I need to buy."


def _cement_density_text(unit_system: str) -> str:
    if units.is_fps(unit_system):
        return f"{units.kg_per_volume(rules.CEMENT_DENSITY_KG_PER_M3, unit_system):.1f} kg/cft"
    return f"{rules.CEMENT_DENSITY_KG_PER_M3:.0f} kg/m3"


def concrete_material_breakdown(wet_volume_m3: float, grade_label: str) -> dict:
    """Standard nominal-mix, dry-volume-factor method: dry_volume =
    wet_volume x 1.54, then split by the grade's cement:sand:aggregate
    ratio parts. Indicative preliminary-estimation practice, not a lab mix
    design - see engineering/rules.py for the full method note."""
    cement_parts, sand_parts, agg_parts = rules.resolve_nominal_mix(grade_label)
    total_parts = cement_parts + sand_parts + agg_parts
    dry_volume_m3 = wet_volume_m3 * rules.DRY_VOLUME_FACTOR
    cement_volume_m3 = dry_volume_m3 * (cement_parts / total_parts)
    sand_volume_m3 = dry_volume_m3 * (sand_parts / total_parts)
    aggregate_volume_m3 = dry_volume_m3 * (agg_parts / total_parts)
    cement_bags = (cement_volume_m3 * rules.CEMENT_DENSITY_KG_PER_M3) / rules.CEMENT_BAG_WEIGHT_KG
    return {
        "mix_ratio": (cement_parts, sand_parts, agg_parts),
        "dry_volume_m3": dry_volume_m3,
        "cement_volume_m3": cement_volume_m3,
        "cement_bags": cement_bags,
        "sand_volume_m3": sand_volume_m3,
        "aggregate_volume_m3": aggregate_volume_m3,
    }


def mortar_material_breakdown(mortar_volume_m3: float, mix_ratio: tuple[float, float]) -> dict:
    """Same idea for cement:sand-only mortar (masonry bedding/jointing,
    plaster) - no coarse aggregate, and mortar's own (lower) dry-volume
    factor since there's no coarse material to bulk the loose mix up."""
    cement_parts, sand_parts = mix_ratio
    total_parts = cement_parts + sand_parts
    dry_volume_m3 = mortar_volume_m3 * rules.MORTAR_DRY_VOLUME_FACTOR
    cement_volume_m3 = dry_volume_m3 * (cement_parts / total_parts)
    sand_volume_m3 = dry_volume_m3 * (sand_parts / total_parts)
    cement_bags = (cement_volume_m3 * rules.CEMENT_DENSITY_KG_PER_M3) / rules.CEMENT_BAG_WEIGHT_KG
    return {
        "mix_ratio": (cement_parts, sand_parts),
        "dry_volume_m3": dry_volume_m3,
        "cement_volume_m3": cement_volume_m3,
        "cement_bags": cement_bags,
        "sand_volume_m3": sand_volume_m3,
    }


def _concrete_material_items(
    parent: QuantityLineItem, grade_label: str, unit_system: str = units.SI
) -> List[QuantityLineItem]:
    bd = concrete_material_breakdown(parent.quantity, grade_label)
    c, s, a = bd["mix_ratio"]
    mix_str = f"{c:g}:{s:g}:{a:g}"
    common_inputs = {
        "wet_volume_m3": parent.quantity,
        "dry_volume_factor": rules.DRY_VOLUME_FACTOR,
        "mix_ratio_cement": c,
        "mix_ratio_sand": s,
        "mix_ratio_aggregate": a,
    }
    procurement_note = (
        "Procurement reference quantity only - its cost is already included in "
        f"{parent.item_code}'s composite rate above; do NOT price it again separately."
    )
    wastage_note = (
        "Net theoretical requirement (no site wastage/spillage margin added) - "
        "order a few percent extra for handling losses."
    )
    return [
        QuantityLineItem(
            item_code=f"{parent.item_code}-CEMENT",
            description=f"Cement for {parent.description} (nominal mix {mix_str})",
            category="Concrete Materials",
            unit="bags",
            quantity=_q(bd["cement_bags"], 1, "bags"),
            confidence=parent.confidence,
            formula="bags = wet_volume × dry_volume_factor × cement_ratio/total_ratio × cement_density ÷ bag_weight",
            inputs_used={**common_inputs, "cement_density_kg_per_m3": rules.CEMENT_DENSITY_KG_PER_M3, "bag_weight_kg": rules.CEMENT_BAG_WEIGHT_KG},
            assumptions=[f"Nominal mix {mix_str} (cement:sand:aggregate) assumed for grade {grade_label}.", f"{rules.CEMENT_BAG_WEIGHT_KG:.0f}kg bags @ {_cement_density_text(unit_system)} loose cement density.", procurement_note, wastage_note],
            informational=True,
            parent_item_code=parent.item_code,
        ),
        QuantityLineItem(
            item_code=f"{parent.item_code}-SAND",
            description=f"Sand for {parent.description} (nominal mix {mix_str})",
            category="Concrete Materials",
            unit="m3",
            quantity=_q(bd["sand_volume_m3"], 3, "m3"),
            confidence=parent.confidence,
            formula="sand_volume = wet_volume × dry_volume_factor × sand_ratio/total_ratio",
            inputs_used=dict(common_inputs),
            assumptions=[f"Nominal mix {mix_str} (cement:sand:aggregate) assumed for grade {grade_label}.", procurement_note, wastage_note],
            informational=True,
            parent_item_code=parent.item_code,
        ),
        QuantityLineItem(
            item_code=f"{parent.item_code}-AGG",
            description=f"Aggregate/crush for {parent.description} (nominal mix {mix_str})",
            category="Concrete Materials",
            unit="m3",
            quantity=_q(bd["aggregate_volume_m3"], 3, "m3"),
            confidence=parent.confidence,
            formula="aggregate_volume = wet_volume × dry_volume_factor × aggregate_ratio/total_ratio",
            inputs_used=dict(common_inputs),
            assumptions=[f"Nominal mix {mix_str} (cement:sand:aggregate) assumed for grade {grade_label}.", procurement_note, wastage_note],
            informational=True,
            parent_item_code=parent.item_code,
        ),
    ]


def _mortar_material_items(
    parent: QuantityLineItem, mortar_volume_m3: float, mix_ratio: tuple[float, float], mix_purpose: str
) -> List[QuantityLineItem]:
    """`mortar_volume_m3` is passed explicitly rather than read off
    `parent.quantity`, because `parent` isn't always a volume: masonry
    mortar (MAS-02) IS a volume, but plaster (PLAS-01/02) is stored as an
    AREA (m2) - the caller must convert area x thickness to a volume
    before calling this, or the cement/sand figures come out ~80x too
    high (plaster area treated as if it were already a cast volume)."""
    bd = mortar_material_breakdown(mortar_volume_m3, mix_ratio)
    c, s = bd["mix_ratio"]
    mix_str = f"{c:g}:{s:g}"
    common_inputs = {
        "mortar_volume_m3": mortar_volume_m3,
        "dry_volume_factor": rules.MORTAR_DRY_VOLUME_FACTOR,
        "mix_ratio_cement": c,
        "mix_ratio_sand": s,
    }
    procurement_note = (
        "Procurement reference quantity only - its cost is already included in "
        f"{parent.item_code}'s composite rate above; do NOT price it again separately."
    )
    wastage_note = "Net theoretical requirement (no site wastage/spillage margin added)."
    return [
        QuantityLineItem(
            item_code=f"{parent.item_code}-CEMENT",
            description=f"Cement for {mix_purpose} (mix {mix_str})",
            category="Concrete Materials",
            unit="bags",
            quantity=_q(bd["cement_bags"], 1, "bags"),
            confidence=parent.confidence,
            formula="bags = mortar_volume × dry_volume_factor × cement_ratio/total_ratio × cement_density ÷ bag_weight",
            inputs_used={**common_inputs, "cement_density_kg_per_m3": rules.CEMENT_DENSITY_KG_PER_M3, "bag_weight_kg": rules.CEMENT_BAG_WEIGHT_KG},
            assumptions=[f"Standard {mix_str} (cement:sand) mortar mix assumed for {mix_purpose}.", procurement_note, wastage_note],
            informational=True,
            parent_item_code=parent.item_code,
        ),
        QuantityLineItem(
            item_code=f"{parent.item_code}-SAND",
            description=f"Sand for {mix_purpose} (mix {mix_str})",
            category="Concrete Materials",
            unit="m3",
            quantity=_q(bd["sand_volume_m3"], 3, "m3"),
            confidence=parent.confidence,
            formula="sand_volume = mortar_volume × dry_volume_factor × sand_ratio/total_ratio",
            inputs_used=dict(common_inputs),
            assumptions=[f"Standard {mix_str} (cement:sand) mortar mix assumed for {mix_purpose}.", procurement_note, wastage_note],
            informational=True,
            parent_item_code=parent.item_code,
        ),
    ]


def compute_procurement_summary(items: List[QuantityLineItem]) -> List[QuantityLineItem]:
    """Rolls every per-member cement/sand/aggregate breakdown line up into
    one project-wide total each - the numbers most useful to actually hand
    to a supplier ("I need X bags of cement, Y m3 of sand, Z m3 of crush")."""
    cement_bags = sum(i.quantity for i in items if i.item_code.endswith("-CEMENT"))
    sand_m3 = sum(i.quantity for i in items if i.item_code.endswith("-SAND"))
    agg_m3 = sum(i.quantity for i in items if i.item_code.endswith("-AGG"))
    conf = ConfidenceLevel.LOW  # a sum of many thumb-rule-derived figures inherits the weakest confidence
    return [
        QuantityLineItem(
            item_code="SUMMARY-CEMENT",
            description="TOTAL cement required (all concrete + masonry mortar + plaster, project-wide)",
            category="Procurement Summary",
            unit="bags",
            quantity=_q(cement_bags, 0, "bags"),
            confidence=conf,
            formula="sum of every *-CEMENT line above",
            inputs_used={"line_items_summed": float(sum(1 for i in items if i.item_code.endswith("-CEMENT")))},
            assumptions=["Net theoretical requirement - add your own site wastage margin (commonly 3-5%) before ordering."],
            informational=True,
        ),
        QuantityLineItem(
            item_code="SUMMARY-SAND",
            description="TOTAL sand required (all concrete + masonry mortar + plaster, project-wide)",
            category="Procurement Summary",
            unit="m3",
            quantity=_q(sand_m3, 2, "m3"),
            confidence=conf,
            formula="sum of every *-SAND line above",
            inputs_used={"line_items_summed": float(sum(1 for i in items if i.item_code.endswith("-SAND")))},
            assumptions=["Net theoretical requirement - add your own site wastage margin (commonly 5-10%) before ordering."],
            informational=True,
        ),
        QuantityLineItem(
            item_code="SUMMARY-AGG",
            description="TOTAL aggregate/crush required (concrete only, project-wide)",
            category="Procurement Summary",
            unit="m3",
            quantity=_q(agg_m3, 2, "m3"),
            confidence=conf,
            formula="sum of every *-AGG line above",
            inputs_used={"line_items_summed": float(sum(1 for i in items if i.item_code.endswith("-AGG")))},
            assumptions=["Net theoretical requirement - add your own site wastage margin (commonly 5-10%) before ordering."],
            informational=True,
        ),
    ]


# --------------------------------------------------------------------------
# PCC (lean concrete) below footings
# --------------------------------------------------------------------------


def compute_pcc(
    footings: FootingSpec,
    pcc_grade: str,
    assumptions: Optional[EngineeringAssumptions] = None,
    unit_system: str = units.SI,
) -> QuantityLineItem:
    A = _assump(assumptions)
    proj = rules.detailing(unit_system)["pcc_projection_m"]
    t = A.pcc_thickness_m

    L = footings.length_m.value + 2 * proj
    W = footings.width_m.value + 2 * proj
    n = footings.count.value

    volume = n * L * W * t

    return QuantityLineItem(
        item_code="PCC-01",
        description=f"PCC ({pcc_grade}) bedding below footings, {units.small_text(t, unit_system, f'{t*1000:.0f}mm')} thick",
        category="PCC",
        unit="m3",
        quantity=_q(volume, 3, "m3"),
        confidence=combine_confidence(footings.count, footings.length_m, footings.width_m),
        formula="V = n × (L + 2×projection) × (W + 2×projection) × thickness",
        inputs_used={
            "footing_count": n,
            "footing_length_m": footings.length_m.value,
            "footing_width_m": footings.width_m.value,
            "pcc_projection_m": proj,
            "pcc_thickness_m": t,
        },
        assumptions=[
            f"PCC projects {units.small_text(proj, unit_system, f'{proj*1000:.0f} mm')} beyond footing edge on all sides.",
            f"PCC thickness {units.small_text(t, unit_system, f'{t*1000:.0f} mm')} (editable in Step 3 'Engineering assumptions').",
            f"Grade {pcc_grade} assumed (nominal mix, not structurally designed).",
        ],
    )


# --------------------------------------------------------------------------
# Footings — concrete + reinforcement
# --------------------------------------------------------------------------


def compute_footing_concrete(footings: FootingSpec, grade: str) -> QuantityLineItem:
    n = footings.count.value
    L = footings.length_m.value
    W = footings.width_m.value
    t = footings.depth_m.value  # footing THICKNESS
    volume = n * L * W * t

    return QuantityLineItem(
        item_code="FTG-CONC-01",
        description=f"RCC {grade} in isolated footings",
        category="Footing",
        unit="m3",
        quantity=_q(volume, 3, "m3"),
        confidence=combine_confidence(footings.count, footings.length_m, footings.width_m, footings.depth_m),
        formula="V = n × L × W × t_footing  (simple rectangular pad; stepped/sloped footings not modelled in MVP)",
        inputs_used={"footing_count": n, "length_m": L, "width_m": W, "footing_thickness_m": t},
        assumptions=[
            "Rectangular (non-stepped) pad footing shape assumed for volume calc.",
            "Uses the footing THICKNESS (pad depth), not the founding/excavation depth.",
            f"Concrete grade {grade} assumed as declared by user; not structurally verified.",
        ],
    )


def _steel_item(
    item_code: str,
    description: str,
    concrete_item: QuantityLineItem,
    member_key: str,
    kg_per_m3: float,
    steel_grade: str,
    extra_assumptions: Optional[List[str]] = None,
    unit_system: str = units.SI,
) -> QuantityLineItem:
    """Thumb-rule steel = concrete volume × kg/m3 × steel-grade quantity factor."""
    rate_min, _rate_mid, rate_max = rules.STEEL_THUMB_RULE_KG_PER_M3[member_key]
    grade_factor = rules.steel_grade_qty_factor(steel_grade)
    weight = concrete_item.quantity * kg_per_m3 * grade_factor
    if units.is_fps(unit_system):
        kv = lambda x: units.kg_per_volume(x, unit_system)  # noqa: E731
        range_text = (
            f"Preliminary allowance only: {kv(rate_min):.2f}-{kv(rate_max):.2f} kg/cft typical range, "
            f"{kv(kg_per_m3):.2f} kg/cft used (editable in Step 3 'Engineering assumptions')."
        )
    else:
        range_text = (
            f"Preliminary allowance only: {rate_min:g}-{rate_max:g} kg/m3 typical range, "
            f"{kg_per_m3:g} kg/m3 used (editable in Step 3 'Engineering assumptions')."
        )
    assumptions = [
        range_text,
        f"Steel grade '{steel_grade}' quantity factor = {grade_factor:.2f} "
        + ("(thumb rules are calibrated for Grade 60)." if units.is_fps(unit_system) else "(thumb rules are calibrated for Grade 60 / Fe415)."),
    ]
    assumptions.extend(extra_assumptions or [])
    return QuantityLineItem(
        item_code=item_code,
        description=description,
        category="Reinforcement",
        unit="kg",
        quantity=_q(weight, 1, "kg"),
        confidence=ConfidenceLevel.LOW,  # thumb-rule steel is always Low-confidence by nature
        formula=f"Weight = {member_key}_concrete_volume × thumb_rule_kg_per_m3 × steel_grade_factor",
        inputs_used={
            f"{member_key}_concrete_volume_m3": concrete_item.quantity,
            "thumb_rule_kg_per_m3": kg_per_m3,
            "steel_grade_factor": grade_factor,
        },
        assumptions=assumptions,
    )


def compute_footing_steel(
    footing_concrete_qty: QuantityLineItem,
    footings: FootingSpec,
    assumptions: Optional[EngineeringAssumptions] = None,
    steel_grade: str = "Fe415",
    unit_system: str = units.SI,
) -> QuantityLineItem:
    A = _assump(assumptions)
    return _steel_item(
        "FTG-STEEL-01",
        "Reinforcement steel in footings (thumb-rule allowance)",
        footing_concrete_qty,
        "footing",
        A.steel_kg_per_m3_footing,
        steel_grade,
        ["NOT a substitute for a structural design / bar bending schedule."],
        unit_system=unit_system,
    )


# --------------------------------------------------------------------------
# Columns — concrete + reinforcement
# --------------------------------------------------------------------------


def total_column_height_m(
    columns: ColumnSpec,
    num_floors: float,
    footings: Optional[FootingSpec] = None,
    assumptions: Optional[EngineeringAssumptions] = None,
    unit_system: str = units.SI,
) -> float:
    """Top of footing -> roof: below-ground stub + plinth height + storeys."""
    A = _assump(assumptions)
    stub = column_stub_below_ground_m(footings, unit_system) if footings is not None else 0.0
    return columns.height_per_floor_m.value * num_floors + A.plinth_height_m + stub


def compute_column_concrete(
    columns: ColumnSpec,
    num_floors: float,
    grade: str,
    footings: Optional[FootingSpec] = None,
    assumptions: Optional[EngineeringAssumptions] = None,
    unit_system: str = units.SI,
) -> QuantityLineItem:
    A = _assump(assumptions)
    n = columns.count.value
    b = columns.width_m.value
    d = columns.depth_m.value
    stub = column_stub_below_ground_m(footings, unit_system) if footings is not None else 0.0
    total_height = total_column_height_m(columns, num_floors, footings, A, unit_system)
    volume = n * b * d * total_height

    return QuantityLineItem(
        item_code="COL-CONC-01",
        description=f"RCC {grade} in columns",
        category="Column",
        unit="m3",
        quantity=_q(volume, 3, "m3"),
        confidence=combine_confidence(columns.count, columns.width_m, columns.depth_m, columns.height_per_floor_m),
        formula="V = n × b × d × [(height_per_floor × num_floors) + plinth_height + below_ground_stub]",
        inputs_used={
            "column_count": n,
            "width_b_m": b,
            "depth_d_m": d,
            "height_per_floor_m": columns.height_per_floor_m.value,
            "num_floors": num_floors,
            "plinth_height_m": A.plinth_height_m,
            "below_ground_stub_m": round(stub, 3),
        },
        assumptions=[
            "Uniform column cross-section assumed across all floors (no staggered/tapering sections).",
            f"Plinth height {units.length_text(A.plinth_height_m, unit_system, f'{A.plinth_height_m} m')} (editable in Step 3 'Engineering assumptions').",
            f"Below-ground column stub = founding depth - footing thickness = {units.length_text(stub, unit_system, f'{stub:.2f} m')} "
            "(top of footing up to natural ground level).",
        ],
    )


def compute_column_steel(
    column_concrete_qty: QuantityLineItem,
    assumptions: Optional[EngineeringAssumptions] = None,
    steel_grade: str = "Fe415",
    unit_system: str = units.SI,
) -> QuantityLineItem:
    A = _assump(assumptions)
    return _steel_item(
        "COL-STEEL-01",
        "Reinforcement steel in columns (main bars + ties, thumb-rule allowance)",
        column_concrete_qty,
        "column",
        A.steel_kg_per_m3_column,
        steel_grade,
        unit_system=unit_system,
    )


# --------------------------------------------------------------------------
# Beams — concrete + reinforcement
# --------------------------------------------------------------------------


def _beam_depth_below_slab(beams: BeamSpec, slab_thickness_m: float) -> float:
    return max(beams.depth_m.value - slab_thickness_m, 0.0)


def compute_beam_concrete(
    beams: BeamSpec, num_floors: float, grade: str, slab_thickness_m: float = 0.0
) -> QuantityLineItem:
    """One plinth-beam level (full depth - no slab above it) plus one beam
    level under every RCC slab. At slab levels only the part of the beam
    BELOW the slab is counted: the top `slab_thickness` of the beam is
    already inside the slab volume, so counting the full depth there would
    double-count that concrete (and its thumb-rule steel)."""
    L = beams.avg_length_m.value
    w = beams.width_m.value
    d = beams.depth_m.value
    d_below = _beam_depth_below_slab(beams, slab_thickness_m)
    n = beams.count.value
    volume = n * L * w * (d + num_floors * d_below)

    return QuantityLineItem(
        item_code="BEAM-CONC-01",
        description=f"RCC {grade} in beams (incl. plinth & roof-level beams)",
        category="Beam",
        unit="m3",
        quantity=_q(volume, 3, "m3"),
        confidence=combine_confidence(beams.count, beams.avg_length_m, beams.width_m, beams.depth_m),
        formula="V = beam_count_per_level × avg_length × width × [depth (plinth level) + num_floors × (depth − slab_thickness)]",
        inputs_used={
            "beam_count_per_level": n,
            "plinth_levels": 1,
            "slab_levels": num_floors,
            "avg_length_m": L,
            "width_m": w,
            "depth_m": d,
            "slab_thickness_m": slab_thickness_m,
            "depth_below_slab_m": round(d_below, 3),
        },
        assumptions=[
            "Same beam layout (count/size) repeated at the plinth level and under every slab level.",
            "At slab levels only the beam depth below the slab is counted (slab portion is in SLAB-CONC-01).",
            "Does not distinguish plinth beam vs floor beam vs roof beam sizes separately in MVP.",
        ],
    )


def compute_beam_steel(
    beam_concrete_qty: QuantityLineItem,
    assumptions: Optional[EngineeringAssumptions] = None,
    steel_grade: str = "Fe415",
    unit_system: str = units.SI,
) -> QuantityLineItem:
    A = _assump(assumptions)
    return _steel_item(
        "BEAM-STEEL-01",
        "Reinforcement steel in beams (thumb-rule allowance)",
        beam_concrete_qty,
        "beam",
        A.steel_kg_per_m3_beam,
        steel_grade,
        unit_system=unit_system,
    )


# --------------------------------------------------------------------------
# Slabs — concrete + reinforcement
# --------------------------------------------------------------------------


def compute_slab_concrete(slabs: SlabSpec, num_floors: float, grade: str) -> QuantityLineItem:
    area = slabs.area_per_floor_sqm.value
    t = slabs.thickness_m.value
    volume = area * t * num_floors

    return QuantityLineItem(
        item_code="SLAB-CONC-01",
        description=f"RCC {grade} in slabs (all floor slabs incl. roof)",
        category="Slab",
        unit="m3",
        quantity=_q(volume, 3, "m3"),
        confidence=combine_confidence(slabs.area_per_floor_sqm, slabs.thickness_m),
        formula="V = area_per_floor × thickness × num_floors",
        inputs_used={"area_per_floor_sqm": area, "thickness_m": t, "num_floors": num_floors},
        assumptions=[
            "'num_floors' here represents the number of RCC slab levels above plinth, including the roof slab.",
            "One-way vs two-way slab action not distinguished for the concrete volume calc.",
        ],
    )


def compute_slab_steel(
    slab_concrete_qty: QuantityLineItem,
    assumptions: Optional[EngineeringAssumptions] = None,
    steel_grade: str = "Fe415",
    unit_system: str = units.SI,
) -> QuantityLineItem:
    A = _assump(assumptions)
    return _steel_item(
        "SLAB-STEEL-01",
        "Reinforcement steel in slabs (thumb-rule allowance)",
        slab_concrete_qty,
        "slab",
        A.steel_kg_per_m3_slab,
        steel_grade,
        unit_system=unit_system,
    )


# --------------------------------------------------------------------------
# Backfill, plinth filling, ground-floor base
# --------------------------------------------------------------------------


def compute_backfill(
    excavation: QuantityLineItem,
    pcc: QuantityLineItem,
    footing_concrete: QuantityLineItem,
    columns: ColumnSpec,
    footings: FootingSpec,
    unit_system: str = units.SI,
) -> QuantityLineItem:
    stub = column_stub_below_ground_m(footings, unit_system)
    stub_volume = columns.count.value * columns.width_m.value * columns.depth_m.value * stub
    volume = max(excavation.quantity - pcc.quantity - footing_concrete.quantity - stub_volume, 0.0)
    return QuantityLineItem(
        item_code="BACKFILL-01",
        description="Backfilling around footings with excavated earth, in layers, watered & compacted",
        category="Earthwork",
        unit="m3",
        quantity=_q(volume, 3, "m3"),
        confidence=combine_confidence(footings.count, footings.length_m, footings.width_m, founding_depth_estimate(footings, unit_system)),
        formula="V = excavation − PCC − footing concrete − below-ground column stubs",
        inputs_used={
            "excavation_m3": excavation.quantity,
            "pcc_m3": pcc.quantity,
            "footing_concrete_m3": footing_concrete.quantity,
            "column_stub_volume_m3": round(stub_volume, 3),
        },
        assumptions=["Excavated earth re-used for backfill (no imported fill); surplus disposal not priced."],
    )


def _ground_floor_fill_area(params: ExtractedBuildingParams) -> float:
    wall_footprint = params.walls.total_length_per_floor_m.value * params.walls.thickness_m.value
    return max(params.plinth_area_per_floor_sqm.value - wall_footprint, 0.0)


def compute_plinth_filling(
    params: ExtractedBuildingParams,
    assumptions: Optional[EngineeringAssumptions] = None,
    unit_system: str = units.SI,
) -> QuantityLineItem:
    A = _assump(assumptions)
    det = rules.detailing(unit_system)
    area = _ground_floor_fill_area(params)
    depth = max(A.plinth_height_m - det["gf_soling_thickness_m"] - det["gf_pcc_floor_thickness_m"], 0.0)
    return QuantityLineItem(
        item_code="PLINTH-FILL-01",
        description="Earth filling in plinth (imported earth, in layers, watered & compacted)",
        category="Earthwork",
        unit="m3",
        quantity=_q(area * depth, 3, "m3"),
        confidence=combine_confidence(params.plinth_area_per_floor_sqm, params.walls.total_length_per_floor_m),
        formula="V = (plinth_area − wall_footprint) × (plinth_height − soling − PCC floor base)",
        inputs_used={
            "plinth_area_sqm": params.plinth_area_per_floor_sqm.value,
            "wall_footprint_sqm": round(params.walls.total_length_per_floor_m.value * params.walls.thickness_m.value, 3),
            "plinth_height_m": A.plinth_height_m,
            "soling_thickness_m": det["gf_soling_thickness_m"],
            "pcc_floor_thickness_m": det["gf_pcc_floor_thickness_m"],
        },
        assumptions=["Filling from natural ground level up to the underside of the ground-floor soling."],
    )


def compute_ground_floor_base(params: ExtractedBuildingParams, unit_system: str = units.SI) -> List[QuantityLineItem]:
    det = rules.detailing(unit_system)
    soling_t = det["gf_soling_thickness_m"]
    pcc_t = det["gf_pcc_floor_thickness_m"]
    area = _ground_floor_fill_area(params)
    conf = combine_confidence(params.plinth_area_per_floor_sqm, params.walls.total_length_per_floor_m)
    return [
        QuantityLineItem(
            item_code="GF-SOL-01",
            description="Brick soling (one layer, flat) under ground-floor PCC floor base",
            category="Ground Floor Base",
            unit="m2",
            quantity=_q(area, 2, "m2"),
            confidence=conf,
            formula="A = plinth_area − wall_footprint",
            inputs_used={"fill_area_sqm": round(area, 2)},
            assumptions=[f"{units.small_text(soling_t, unit_system, f'{soling_t*1000:.0f} mm')} flat brick soling over compacted plinth fill."],
        ),
        QuantityLineItem(
            item_code="GF-PCC-01",
            description=f"PCC floor base over soling, {units.small_text(pcc_t, unit_system, f'{pcc_t*1000:.0f}mm')} thick",
            category="Ground Floor Base",
            unit="m3",
            quantity=_q(area * pcc_t, 3, "m3"),
            confidence=conf,
            formula="V = (plinth_area − wall_footprint) × PCC_floor_thickness",
            inputs_used={"fill_area_sqm": round(area, 2), "thickness_m": pcc_t},
            assumptions=["Uses the project's PCC grade; ground-floor tiling is in FLR-01."],
        ),
    ]


# --------------------------------------------------------------------------
# Staircase
# --------------------------------------------------------------------------


def stair_geometry(floor_height_m: float, unit_system: str = units.SI) -> dict:
    """RCC dog-legged stair (2 flights + mid-landing) for one storey height."""
    det = rules.detailing(unit_system)
    H = max(floor_height_m, 0.0)
    n_risers = max(int(math.ceil(H / det["stair_max_riser_m"])), 2) if H > 0 else 0
    if n_risers % 2:
        n_risers += 1  # two equal flights
    riser = H / n_risers if n_risers else 0.0
    risers_per_flight = n_risers // 2
    going = max(risers_per_flight - 1, 0) * det["stair_tread_m"]
    inclined = math.sqrt(going ** 2 + (H / 2) ** 2)
    w = det["stair_width_m"]
    waist_vol = 2 * inclined * w * det["stair_waist_m"]
    steps_vol = n_risers * 0.5 * riser * det["stair_tread_m"] * w
    landing_len = 2 * w + det["stair_landing_gap_m"]
    landing_vol = landing_len * w * det["stair_waist_m"]
    formwork = 2 * inclined * w + landing_len * w + n_risers * riser * w
    return {
        "n_risers": n_risers,
        "riser_m": riser,
        "inclined_length_m": inclined,
        "volume_m3": waist_vol + steps_vol + landing_vol,
        "formwork_m2": formwork,
    }


def compute_staircase(
    columns: ColumnSpec, num_floors: float, grade: str, unit_system: str = units.SI
) -> List[QuantityLineItem]:
    """One stair (two flights) per slab level reached, i.e. `num_floors`
    sets: each storey plus roof access (the usual Pakistani 'mumty')."""
    det = rules.detailing(unit_system)
    w, waist, tread = det["stair_width_m"], det["stair_waist_m"], det["stair_tread_m"]
    geo = stair_geometry(columns.height_per_floor_m.value, unit_system)
    sets = max(num_floors, 0)
    conf = combine_confidence(columns.height_per_floor_m)
    inputs = {
        "stair_sets": sets,
        "floor_height_m": columns.height_per_floor_m.value,
        "risers_per_set": geo["n_risers"],
        "riser_m": round(geo["riser_m"], 3) if not units.is_fps(unit_system) else geo["riser_m"],
        "stair_width_m": w,
        "waist_m": waist,
    }
    common = [
        "RCC dog-legged stair: 2 flights + mid-landing per storey, "
        f"{units.length_text(w, unit_system, f'{w:g} m')} wide, "
        f"{units.small_text(waist, unit_system, f'{waist*1000:.0f} mm')} waist, "
        f"{units.small_text(tread, unit_system, f'{tread*1000:.0f} mm')} treads.",
        "One stair set per slab level reached (each storey + roof access). Untick 'Staircase' in Step 1 to exclude.",
        "Stair finishes (treads/risers cladding, railing) not included.",
    ]
    return [
        QuantityLineItem(
            item_code="STAIR-CONC-01",
            description=f"RCC {grade} in staircase (waist slab, steps, mid-landing)",
            category="Staircase",
            unit="m3",
            quantity=_q(sets * geo["volume_m3"], 3, "m3"),
            confidence=conf,
            formula="V = sets × [2 × inclined_length × width × waist + risers × ½ × riser × tread × width + landing]",
            inputs_used=inputs,
            assumptions=common,
        ),
        QuantityLineItem(
            item_code="FORM-STAIR-01",
            description="Formwork to staircase (soffit, landing, riser faces)",
            category="Formwork",
            unit="m2",
            quantity=_q(sets * geo["formwork_m2"], 2, "m2"),
            confidence=conf,
            formula="A = sets × [2 × inclined_length × width + landing_area + risers × riser × width]",
            inputs_used=inputs,
            assumptions=common,
        ),
    ]


def compute_stair_steel(
    stair_concrete: QuantityLineItem,
    assumptions: Optional[EngineeringAssumptions] = None,
    steel_grade: str = "Fe415",
    unit_system: str = units.SI,
) -> QuantityLineItem:
    A = _assump(assumptions)
    return _steel_item(
        "STAIR-STEEL-01",
        "Reinforcement steel in staircase (thumb-rule allowance)",
        stair_concrete,
        "stair",
        A.steel_kg_per_m3_stair,
        steel_grade,
        unit_system=unit_system,
    )


# --------------------------------------------------------------------------
# Lintels & chajjas
# --------------------------------------------------------------------------


def _opening_widths(openings: OpeningsSpec, unit_system: str = units.SI) -> tuple[float, float]:
    door_height = rules.detailing(unit_system)["door_height_m"]
    door_w = openings.avg_door_area_sqm.value / door_height if door_height else 0.0
    window_w = math.sqrt(max(openings.avg_window_area_sqm.value, 0.0))  # square window assumption
    return door_w, window_w


def lintel_volume_m3(walls: WallSpec, openings: OpeningsSpec, num_floors: float, unit_system: str = units.SI) -> float:
    det = rules.detailing(unit_system)
    door_w, window_w = _opening_widths(openings, unit_system)
    bearing = 2 * det["lintel_bearing_m"]
    total_len = num_floors * (
        openings.door_count_per_floor.value * (door_w + bearing)
        + openings.window_count_per_floor.value * (window_w + bearing)
    )
    return total_len * walls.thickness_m.value * det["lintel_depth_m"]


def compute_lintels(
    walls: WallSpec, openings: OpeningsSpec, num_floors: float, grade: str, unit_system: str = units.SI
) -> List[QuantityLineItem]:
    det = rules.detailing(unit_system)
    lintel_depth, bearing_each = det["lintel_depth_m"], det["lintel_bearing_m"]
    chajja_proj, chajja_t, door_h = det["chajja_projection_m"], det["chajja_avg_thickness_m"], det["door_height_m"]
    door_w, window_w = _opening_widths(openings, unit_system)
    bearing = 2 * bearing_each
    n_doors = openings.door_count_per_floor.value * num_floors
    n_windows = openings.window_count_per_floor.value * num_floors
    lintel_len = n_doors * (door_w + bearing) + n_windows * (window_w + bearing)
    lintel_vol = lintel_len * walls.thickness_m.value * lintel_depth
    chajja_len = n_windows * (window_w + bearing)
    chajja_vol = chajja_len * chajja_proj * chajja_t
    formwork = (
        lintel_len * (walls.thickness_m.value + 2 * lintel_depth)
        + chajja_len * chajja_proj
    )
    conf = combine_confidence(
        openings.door_count_per_floor, openings.window_count_per_floor, openings.avg_door_area_sqm, openings.avg_window_area_sqm
    )
    inputs = {
        "door_count_total": n_doors,
        "window_count_total": n_windows,
        "door_width_m": round(door_w, 3) if not units.is_fps(unit_system) else door_w,
        "window_width_m": round(window_w, 3) if not units.is_fps(unit_system) else window_w,
        "bearing_each_side_m": bearing_each,
        "lintel_depth_m": lintel_depth,
        "wall_thickness_m": walls.thickness_m.value,
        "chajja_projection_m": chajja_proj,
        "chajja_avg_thickness_m": chajja_t,
    }
    sm = lambda v: units.small_text(v, unit_system, f"{v*1000:.0f} mm")  # noqa: E731
    common = [
        f"Door width = avg door area / {units.length_text(door_h, unit_system, f'{door_h} m')} door height; "
        "window width = sqrt(avg window area).",
        f"Lintel = full wall thickness × {sm(lintel_depth)} deep, {sm(bearing_each)} bearing each side.",
        f"Chajja (sunshade) over every window: {sm(chajja_proj)} projection, "
        f"{sm(chajja_t)} average thickness.",
        "Lintel volume is deducted from the masonry volume.",
    ]
    return [
        QuantityLineItem(
            item_code="LINTEL-CONC-01",
            description=f"RCC {grade} in lintels over openings and chajjas over windows",
            category="Lintels",
            unit="m3",
            quantity=_q(lintel_vol + chajja_vol, 3, "m3"),
            confidence=conf,
            formula="V = Σ(opening_width + 2×bearing) × wall_thickness × lintel_depth + Σ window chajjas",
            inputs_used={
                **inputs,
                "lintel_volume_m3": round(lintel_vol, 3) if not units.is_fps(unit_system) else lintel_vol,
                "chajja_volume_m3": round(chajja_vol, 3) if not units.is_fps(unit_system) else chajja_vol,
            },
            assumptions=common,
        ),
        QuantityLineItem(
            item_code="FORM-LINTEL-01",
            description="Formwork to lintels (soffit + 2 sides) and chajja soffits",
            category="Formwork",
            unit="m2",
            quantity=_q(formwork, 2, "m2"),
            confidence=conf,
            formula="A = lintel_length × (wall_thickness + 2×lintel_depth) + chajja_length × projection",
            inputs_used=inputs,
            assumptions=common,
        ),
    ]


def compute_lintel_steel(
    lintel_concrete: QuantityLineItem,
    assumptions: Optional[EngineeringAssumptions] = None,
    steel_grade: str = "Fe415",
    unit_system: str = units.SI,
) -> QuantityLineItem:
    A = _assump(assumptions)
    return _steel_item(
        "LINTEL-STEEL-01",
        "Reinforcement steel in lintels & chajjas (thumb-rule allowance)",
        lintel_concrete,
        "lintel",
        A.steel_kg_per_m3_lintel,
        steel_grade,
        unit_system=unit_system,
    )


# --------------------------------------------------------------------------
# Formwork / shuttering
# --------------------------------------------------------------------------


def compute_formwork(
    footings: FootingSpec,
    columns: ColumnSpec,
    beams: BeamSpec,
    slabs: SlabSpec,
    num_floors: float,
    assumptions: Optional[EngineeringAssumptions] = None,
    unit_system: str = units.SI,
) -> List[QuantityLineItem]:
    A = _assump(assumptions)
    items: List[QuantityLineItem] = []

    # Footings: 4 side faces over the footing THICKNESS
    ftg_area = footings.count.value * 2 * (footings.length_m.value + footings.width_m.value) * footings.depth_m.value
    items.append(
        QuantityLineItem(
            item_code="FORM-FTG-01",
            description="Formwork/shuttering to sides of footings",
            category="Formwork",
            unit="m2",
            quantity=_q(ftg_area, 2, "m2"),
            confidence=combine_confidence(footings.count, footings.length_m, footings.width_m, footings.depth_m),
            formula="A = n × 2×(L+W) × t_footing  (side faces only)",
            inputs_used={
                "count": footings.count.value,
                "length_m": footings.length_m.value,
                "width_m": footings.width_m.value,
                "footing_thickness_m": footings.depth_m.value,
            },
            assumptions=["Only vertical side faces of footing formed; base cast on PCC (no bottom formwork)."],
        )
    )

    # Columns: perimeter x total height (incl. below-ground stub)
    total_col_height = total_column_height_m(columns, num_floors, footings, A, unit_system)
    col_area = columns.count.value * 2 * (columns.width_m.value + columns.depth_m.value) * total_col_height
    items.append(
        QuantityLineItem(
            item_code="FORM-COL-01",
            description="Formwork/shuttering to columns (4 faces)",
            category="Formwork",
            unit="m2",
            quantity=_q(col_area, 2, "m2"),
            confidence=combine_confidence(columns.count, columns.width_m, columns.depth_m, columns.height_per_floor_m),
            formula="A = n × 2×(b+d) × total_height (top of footing to roof)",
            inputs_used={
                "count": columns.count.value,
                "width_b_m": columns.width_m.value,
                "depth_d_m": columns.depth_m.value,
                "total_height_m": round(total_col_height, 3) if not units.is_fps(unit_system) else total_col_height,
            },
            assumptions=["Full column perimeter formed on all 4 faces for the entire height."],
        )
    )

    # Beams: soffit + 2 sides. At slab levels the sides are only formed for
    # the depth below the slab (the slab soffit formwork covers the rest).
    d = beams.depth_m.value
    d_below = _beam_depth_below_slab(beams, slabs.thickness_m.value)
    w = beams.width_m.value
    beam_area = beams.count.value * beams.avg_length_m.value * ((w + 2 * d) + num_floors * (w + 2 * d_below))
    items.append(
        QuantityLineItem(
            item_code="FORM-BEAM-01",
            description="Formwork/shuttering to beams (soffit + 2 sides)",
            category="Formwork",
            unit="m2",
            quantity=_q(beam_area, 2, "m2"),
            confidence=combine_confidence(beams.count, beams.avg_length_m, beams.width_m, beams.depth_m),
            formula="A = count × L × [(width + 2×depth) (plinth) + num_floors × (width + 2×(depth − slab_thickness))]",
            inputs_used={
                "beam_count_per_level": beams.count.value,
                "slab_levels": num_floors,
                "avg_length_m": beams.avg_length_m.value,
                "width_m": w,
                "depth_m": d,
                "depth_below_slab_m": round(d_below, 3),
            },
            assumptions=["Beam top face cast monolithically with slab; sides formed only below the slab at slab levels."],
        )
    )

    # Slabs: soffit only
    slab_area = slabs.area_per_floor_sqm.value * num_floors
    items.append(
        QuantityLineItem(
            item_code="FORM-SLAB-01",
            description="Formwork/shuttering (centering + soffit) to slabs",
            category="Formwork",
            unit="m2",
            quantity=_q(slab_area, 2, "m2"),
            confidence=combine_confidence(slabs.area_per_floor_sqm),
            formula="A = area_per_floor × num_floors  (soffit area ≈ slab plan area)",
            inputs_used={"area_per_floor_sqm": slabs.area_per_floor_sqm.value, "num_floors": num_floors},
            assumptions=["Slab soffit formwork area approximated equal to slab plan area (edge strips ignored)."],
        )
    )

    return items


# --------------------------------------------------------------------------
# Masonry / blockwork
# --------------------------------------------------------------------------


def _unit_size_text(kind: str, size_m: tuple, suffix: str, unit_system: str) -> str:
    """'Nominal unit size 200x100x100 mm (incl. joint) ' (SI, unchanged) or
    'Nominal unit size 7.874x3.937x3.937" (200x100x100 mm) (incl. joint) ' (FPS)."""
    mm = f"{size_m[0]*1000:.0f}x{size_m[1]*1000:.0f}x{size_m[2]*1000:.0f} mm"
    if units.is_fps(unit_system):
        inches = "x".join(units._trim(v * units.IN_PER_M, 3) for v in size_m) + '"'
        text = f"{kind} unit size {inches} ({mm})"
    else:
        text = f"{kind} unit size {mm}"
    return f"{text} {suffix} " if suffix else f"{text} "


def compute_masonry(
    walls: WallSpec,
    openings: OpeningsSpec,
    num_floors: float,
    lintel_volume: float = 0.0,
    parapet_length_m: float = 0.0,
    parapet_height_m: float = 0.0,
    unit_system: str = units.SI,
) -> tuple[List[QuantityLineItem], float]:
    """Returns (items, net_wall_area). net_wall_area is ONE face of the
    storey walls (openings deducted, parapet excluded) - reused by the
    plaster/paint face-area split."""
    items: List[QuantityLineItem] = []

    gross_wall_area = walls.total_length_per_floor_m.value * walls.height_m.value * num_floors
    opening_area = (
        openings.door_count_per_floor.value * openings.avg_door_area_sqm.value
        + openings.window_count_per_floor.value * openings.avg_window_area_sqm.value
    ) * num_floors
    net_wall_area = max(gross_wall_area - opening_area, 0.0)
    storey_wall_volume = max(net_wall_area * walls.thickness_m.value - lintel_volume, 0.0)
    parapet_volume = parapet_length_m * parapet_height_m * walls.thickness_m.value
    wall_volume = storey_wall_volume + parapet_volume

    material = walls.wall_material if walls.wall_material in rules.MASONRY_UNIT_SIZES_M else rules.DEFAULT_WALL_MATERIAL
    nominal = rules.MASONRY_UNIT_SIZES_M[material]
    actual = rules.MASONRY_UNIT_ACTUAL_SIZES_M[material]
    nominal_volume = nominal[0] * nominal[1] * nominal[2]
    actual_volume = actual[0] * actual[1] * actual[2]

    unit_count = wall_volume / nominal_volume if nominal_volume else 0.0
    mortar_volume = max(wall_volume - unit_count * actual_volume, 0.0)
    units_per_m3 = 1.0 / nominal_volume if nominal_volume else 0.0
    mortar_per_m3 = 1.0 - units_per_m3 * actual_volume

    conf = combine_confidence(
        walls.total_length_per_floor_m,
        walls.height_m,
        openings.door_count_per_floor,
        openings.window_count_per_floor,
    )
    common_inputs = {
        "gross_wall_area_sqm": round(gross_wall_area, 3),
        "opening_area_sqm": round(opening_area, 3),
        "wall_thickness_m": walls.thickness_m.value,
        "lintel_volume_deducted_m3": round(lintel_volume, 3),
        "parapet_volume_m3": round(parapet_volume, 3),
        "masonry_volume_m3": round(wall_volume, 3),
    }

    items.append(
        QuantityLineItem(
            item_code="MAS-01",
            description=f"Masonry/blockwork units - {units.relabel_wall_material(material, unit_system)}",
            category="Masonry",
            unit="Nos",
            quantity=round(unit_count),
            confidence=conf,
            formula="units = masonry_volume / nominal_unit_volume  (nominal size includes the 10 mm mortar joint)",
            inputs_used={**common_inputs, "nominal_unit_volume_m3": nominal_volume},
            assumptions=[
                _unit_size_text("Nominal", nominal, "(incl. joint)", unit_system)
                + (
                    f"-> {units_per_m3 / units.CFT_PER_CUM:.2f} units per cft of masonry "
                    f"({units_per_m3:.0f} per m3, before wastage)."
                    if units.is_fps(unit_system)
                    else f"-> {units_per_m3:.0f} units per m3 of masonry (before wastage)."
                ),
                "masonry_volume = (gross wall area − openings) × thickness − lintels + parapet.",
                "Wall openings deducted using average door/window area × count; not per-opening schedule.",
            ],
        )
    )
    items.append(
        QuantityLineItem(
            item_code="MAS-02",
            description="Masonry mortar (bedding/jointing)",
            category="Masonry",
            unit="m3",
            quantity=_q(mortar_volume, 3, "m3"),
            confidence=conf,
            formula="mortar_volume = masonry_volume − units × actual_unit_volume",
            inputs_used={**common_inputs, "actual_unit_volume_m3": actual_volume, "unit_count": round(unit_count)},
            assumptions=[
                _unit_size_text("Actual", actual, "", unit_system)
                + (
                    f"-> {mortar_per_m3:.3f} cft wet mortar per cft of masonry."
                    if units.is_fps(unit_system)
                    else f"-> {mortar_per_m3:.3f} m3 wet mortar per m3 of masonry."
                ),
            ],
        )
    )
    items.append(
        QuantityLineItem(
            item_code="MAS-03",
            description="Masonry laying labour (mason + helpers, incl. scaffolding & curing)",
            category="Masonry",
            unit="m3",
            quantity=_q(wall_volume, 3, "m3"),
            confidence=conf,
            formula="labour quantity = masonry_volume",
            inputs_used=dict(common_inputs),
            assumptions=["Units (MAS-01) and mortar (MAS-02) are priced as materials; this line prices the laying labour."],
        )
    )
    return items, net_wall_area


# --------------------------------------------------------------------------
# Plaster (internal / external / ceiling) & painting
# --------------------------------------------------------------------------


def compute_wall_face_areas(
    params: ExtractedBuildingParams,
    net_wall_area: float,
    num_floors: float,
    parapet_length_m: float = 0.0,
    parapet_height_m: float = 0.0,
) -> dict:
    """Splits the plasterable/paintable faces:
    - every wall has TWO faces -> total wall faces = 2 × net_wall_area
    - external faces = external perimeter × height × floors − external
      openings (all windows + one main entrance door), plus both faces of
      the roof parapet
    - internal faces = total wall faces − external storey faces
    - ceilings = slab soffit area on every slab level"""
    walls, openings = params.walls, params.openings
    perimeter = external_perimeter_estimate(params)
    ext_gross = perimeter.value * walls.height_m.value * num_floors
    ext_openings = (
        num_floors * openings.window_count_per_floor.value * openings.avg_window_area_sqm.value
        + (openings.avg_door_area_sqm.value if num_floors > 0 and openings.door_count_per_floor.value >= 1 else 0.0)
    )
    ext_storey = max(ext_gross - ext_openings, 0.0)
    internal = max(2 * net_wall_area - ext_storey, 0.0)
    parapet_faces = 2 * parapet_length_m * parapet_height_m
    ceiling = params.slabs.area_per_floor_sqm.value * num_floors
    return {
        "internal_sqm": internal,
        "external_sqm": ext_storey + parapet_faces,
        "ceiling_sqm": ceiling,
        "external_perimeter_m": perimeter.value,
        "external_storey_faces_sqm": ext_storey,
        "external_openings_sqm": ext_openings,
        "parapet_faces_sqm": parapet_faces,
        "total_wall_faces_sqm": 2 * net_wall_area,
        "wall_confidence": combine_confidence(perimeter, walls.total_length_per_floor_m, walls.height_m),
        "ceiling_confidence": combine_confidence(params.slabs.area_per_floor_sqm),
    }


def compute_plaster(face_areas: dict, project_inputs: ProjectInputs) -> List[QuantityLineItem]:
    us = project_inputs.unit_system
    t_int = project_inputs.plaster_thickness_internal_mm / 1000.0
    t_ext = project_inputs.plaster_thickness_external_mm / 1000.0
    t_ceil = rules.detailing(us)["ceiling_plaster_thickness_m"]
    split_inputs = {
        "total_wall_faces_sqm": round(face_areas["total_wall_faces_sqm"], 2),
        "external_perimeter_m": face_areas["external_perimeter_m"],
        "external_storey_faces_sqm": round(face_areas["external_storey_faces_sqm"], 2),
        "external_openings_sqm": round(face_areas["external_openings_sqm"], 2),
        "parapet_faces_sqm": round(face_areas["parapet_faces_sqm"], 2),
    }
    return [
        QuantityLineItem(
            item_code="PLAS-01",
            description=f"Internal cement plaster to walls, {units.small_text(t_int, us, f'{project_inputs.plaster_thickness_internal_mm}mm')} thick",
            category="Plaster",
            unit="m2",
            quantity=_q(face_areas["internal_sqm"], 2, "m2"),
            confidence=face_areas["wall_confidence"],
            formula="area = 2 × net_wall_area − external storey faces  (both faces of every wall, minus the outside face)",
            inputs_used={**split_inputs, "thickness_m": t_int},
            assumptions=["Internal walls plastered on both faces; external walls on their inside face."],
        ),
        QuantityLineItem(
            item_code="PLAS-02",
            description=f"External cement plaster, {units.small_text(t_ext, us, f'{project_inputs.plaster_thickness_external_mm}mm')} thick",
            category="Plaster",
            unit="m2",
            quantity=_q(face_areas["external_sqm"], 2, "m2"),
            confidence=face_areas["wall_confidence"],
            formula="area = external_perimeter × wall_height × floors − (all windows + main door) + 2 × parapet faces",
            inputs_used={**split_inputs, "thickness_m": t_ext},
            assumptions=[
                "All windows assumed to be in external walls; one main entrance door deducted from the external face.",
                "Roof parapet plastered on both faces.",
            ],
        ),
        QuantityLineItem(
            item_code="PLAS-03",
            description=f"Ceiling plaster to slab soffits, {units.small_text(t_ceil, us, f'{rules.CEILING_PLASTER_THICKNESS_MM}mm')} thick",
            category="Plaster",
            unit="m2",
            quantity=_q(face_areas["ceiling_sqm"], 2, "m2"),
            confidence=face_areas["ceiling_confidence"],
            formula="area = slab_area_per_floor × num_floors",
            inputs_used={"ceiling_sqm": round(face_areas["ceiling_sqm"], 2), "thickness_m": t_ceil},
            assumptions=["Beam soffits/sides not added separately (approximately offset by stair/void areas)."],
        ),
    ]


def compute_flooring(slabs: SlabSpec, num_floors: float) -> QuantityLineItem:
    area = slabs.area_per_floor_sqm.value * num_floors * rules.FLOORING_COVERAGE_FACTOR
    return QuantityLineItem(
        item_code="FLR-01",
        description="Flooring (tiling/finish) allowance",
        category="Flooring",
        unit="m2",
        quantity=_q(area, 2, "m2"),
        confidence=combine_confidence(slabs.area_per_floor_sqm),
        formula="area = plinth_area_per_floor × num_floors",
        inputs_used={"area_per_floor_sqm": slabs.area_per_floor_sqm.value, "num_floors": num_floors},
        assumptions=["Flooring area assumed equal to built-up floor area on every level (staircase/void areas not deducted in MVP)."],
    )


def compute_waterproofing(slabs: SlabSpec) -> QuantityLineItem:
    area = slabs.area_per_floor_sqm.value
    return QuantityLineItem(
        item_code="WP-01",
        description="Terrace/roof waterproofing",
        category="Waterproofing",
        unit="m2",
        quantity=_q(area, 2, "m2"),
        confidence=ConfidenceLevel.MEDIUM,
        formula="area = roof (top floor) plan area",
        inputs_used={"roof_area_sqm": area},
        assumptions=["Roof area assumed equal to the typical floor plate area; parapet/overhang not added in MVP."],
    )


def compute_roof_treatment(slabs: SlabSpec) -> QuantityLineItem:
    area = slabs.area_per_floor_sqm.value
    return QuantityLineItem(
        item_code="ROOF-01",
        description="Roof insulation & tiling (mud/earth fill + brick or tuff tiles over waterproofing)",
        category="Roof Treatment",
        unit="m2",
        quantity=_q(area, 2, "m2"),
        confidence=combine_confidence(slabs.area_per_floor_sqm),
        formula="area = roof (top floor) plan area",
        inputs_used={"roof_area_sqm": area},
        assumptions=["Typical Pakistani roof build-up over the RCC roof slab; untick 'Roof insulation/tiles' in Step 1 to exclude."],
    )


def compute_painting(face_areas: dict) -> List[QuantityLineItem]:
    return [
        QuantityLineItem(
            item_code="PAINT-01",
            description="Internal wall painting (2 coats over primer)",
            category="Painting",
            unit="m2",
            quantity=_q(face_areas["internal_sqm"], 2, "m2"),
            confidence=face_areas["wall_confidence"],
            formula="area = internal plaster area (PLAS-01)",
            inputs_used={"internal_plaster_area_sqm": round(face_areas["internal_sqm"], 2)},
            assumptions=["Paint area tied 1:1 to internal wall plaster area."],
        ),
        QuantityLineItem(
            item_code="PAINT-02",
            description="External painting/texture (weatherproof, 2 coats)",
            category="Painting",
            unit="m2",
            quantity=_q(face_areas["external_sqm"], 2, "m2"),
            confidence=face_areas["wall_confidence"],
            formula="area = external plaster area (PLAS-02)",
            inputs_used={"external_plaster_area_sqm": round(face_areas["external_sqm"], 2)},
            assumptions=["Paint area tied 1:1 to external plaster area (incl. parapet)."],
        ),
        QuantityLineItem(
            item_code="PAINT-03",
            description="Ceiling painting (2 coats over primer)",
            category="Painting",
            unit="m2",
            quantity=_q(face_areas["ceiling_sqm"], 2, "m2"),
            confidence=face_areas["ceiling_confidence"],
            formula="area = ceiling plaster area (PLAS-03)",
            inputs_used={"ceiling_area_sqm": round(face_areas["ceiling_sqm"], 2)},
            assumptions=["Paint area tied 1:1 to ceiling plaster area."],
        ),
    ]


def compute_dpc(walls: WallSpec, unit_system: str = units.SI) -> QuantityLineItem:
    dpc_t = rules.detailing(unit_system)["dpc_thickness_m"]
    volume = walls.total_length_per_floor_m.value * walls.thickness_m.value * dpc_t
    return QuantityLineItem(
        item_code="DPC-01",
        description="Damp Proof Course (DPC) at plinth level",
        category="DPC",
        unit="m3",
        quantity=_q(volume, 3, "m3"),
        confidence=combine_confidence(walls.total_length_per_floor_m),
        formula="V = ground_floor_wall_length × wall_thickness × DPC_thickness",
        inputs_used={
            "wall_length_m": walls.total_length_per_floor_m.value,
            "wall_thickness_m": walls.thickness_m.value,
            "dpc_thickness_m": dpc_t,
        },
        assumptions=["DPC applied once at plinth level along ground-floor wall footprint only."],
    )


def compute_anti_termite(plinth_area_per_floor: Estimate) -> QuantityLineItem:
    area = plinth_area_per_floor.value
    return QuantityLineItem(
        item_code="AT-01",
        description="Anti-termite chemical treatment",
        category="Anti-termite",
        unit="m2",
        quantity=_q(area, 2, "m2"),
        confidence=plinth_area_per_floor.confidence,
        formula="area = ground floor plinth area (soil treatment under building footprint + periphery)",
        inputs_used={"plinth_area_sqm": area},
        assumptions=["Applied once to ground-floor footprint; peripheral trench treatment not separately quantified in MVP."],
    )


# --------------------------------------------------------------------------
# MEP (parametric): electrical, plumbing & sanitary, kitchen, external services
# --------------------------------------------------------------------------


def compute_mep(params: ExtractedBuildingParams, num_floors: float, unit_system: str = units.SI) -> List[QuantityLineItem]:
    covered = params.plinth_area_per_floor_sqm.value * num_floors
    baths = params.services.bathroom_count_total
    kitchens = params.services.kitchen_count_total
    return [
        QuantityLineItem(
            item_code="ELEC-01",
            description="Electrical works complete: conduits, wiring, DB/breakers, earthing, switches/sockets, basic lights & fans",
            category="Electrical",
            unit="m2",
            quantity=_q(covered, 2, "m2"),
            confidence=combine_confidence(params.plinth_area_per_floor_sqm),
            formula="area = plinth_area_per_floor × num_floors (covered area)",
            inputs_used={"plinth_area_per_floor_sqm": params.plinth_area_per_floor_sqm.value, "num_floors": num_floors},
            assumptions=[
                f"Priced per {units.unit_word('m2', unit_system)} of covered area (typical residential point density); "
                "solar/UPS/HVAC not included."
            ],
        ),
        QuantityLineItem(
            item_code="PLUMB-01",
            description="Bathroom plumbing & sanitary: supply/drain piping, WC, basin, shower/mixer, accessories",
            category="Plumbing & Sanitary",
            unit="Nos",
            quantity=round(baths.value),
            confidence=baths.confidence,
            formula="count = total bathrooms",
            inputs_used={"bathroom_count_total": baths.value},
            assumptions=["Priced per complete bathroom; geysers and wall/floor tiling of wet areas not included."],
        ),
        QuantityLineItem(
            item_code="KITCH-01",
            description="Kitchen: plumbing, sink & mixer, basic cabinets and counter top",
            category="Kitchen",
            unit="Nos",
            quantity=round(kitchens.value),
            confidence=kitchens.confidence,
            formula="count = total kitchens",
            inputs_used={"kitchen_count_total": kitchens.value},
            assumptions=["Kitchen cost varies hugely with cabinetry - override the rate with an actual quotation."],
        ),
        QuantityLineItem(
            item_code="WSD-01",
            description="External water supply & drainage: underground + overhead tanks, pump, sewer/septic connection",
            category="External Services",
            unit="Nos",
            quantity=1,
            confidence=ConfidenceLevel.LOW,
            formula="1 set per building",
            inputs_used={"sets": 1},
            assumptions=["One set per house; boundary wall and main gate are NOT included."],
        ),
    ]


# --------------------------------------------------------------------------
# Doors & windows (supply + installation)
# --------------------------------------------------------------------------


def compute_doors_windows(openings: OpeningsSpec, num_floors: float, unit_system: str = units.SI) -> List[QuantityLineItem]:
    """Doors/windows are already captured (count + avg area) for the wall-
    opening deduction in compute_masonry() - this prices them as their own
    procurable BOQ items, since a real residential BOQ has to budget for
    them (a door/window schedule is one of the biggest single procurement
    line items on a house). Priced per DOOR (a fixed-size assumption is
    reasonable for cost since door sizes vary little) but per m2 of WINDOW
    area (window cost scales with size, unlike doors)."""
    door_count = openings.door_count_per_floor.value * num_floors
    window_count = openings.window_count_per_floor.value * num_floors
    window_area = window_count * openings.avg_window_area_sqm.value

    return [
        QuantityLineItem(
            item_code="DOOR-01",
            description="Doors - supply & installation (frame/chaukhat + hardware, standard residential size)",
            category="Doors",
            unit="Nos",
            quantity=round(door_count),
            confidence=combine_confidence(openings.door_count_per_floor),
            formula="count = door_count_per_floor × num_floors",
            inputs_used={"door_count_per_floor": openings.door_count_per_floor.value, "num_floors": num_floors},
            assumptions=[
                (
                    "Rate assumes a standard-size residential door (~3'-0\" x 7'-0\"); unusually large/oversized doors will cost more per unit."
                    if units.is_fps(unit_system)
                    else "Rate assumes a standard-size residential door (~0.9m x 2.1m); unusually large/oversized doors will cost more per unit."
                ),
                "Finish Level (Step 1) scales the door quality/cost tier - see engineering/rules.py FINISH_LEVEL_RATE_MULTIPLIERS.",
            ],
        ),
        QuantityLineItem(
            item_code="WINDOW-01",
            description="Windows - supply & installation (aluminium frame, glazing, hardware)",
            category="Windows",
            unit="m2",
            quantity=_q(window_area, 2, "m2"),
            confidence=combine_confidence(openings.window_count_per_floor, openings.avg_window_area_sqm),
            formula="area = window_count_per_floor × avg_window_area_sqm × num_floors",
            inputs_used={
                "window_count_per_floor": openings.window_count_per_floor.value,
                "avg_window_area_sqm": openings.avg_window_area_sqm.value,
                "num_floors": num_floors,
                "total_window_count": window_count,
            },
            assumptions=[
                f"Priced per {units.unit_word('m2', unit_system)} of window area (standard aluminium sliding/casement) - UPVC or specialty glazing costs more.",
                "Finish Level (Step 1) scales the window quality/cost tier - see engineering/rules.py FINISH_LEVEL_RATE_MULTIPLIERS.",
            ],
        ),
    ]


# --------------------------------------------------------------------------
# Reinforcement sanity cross-check (not a BOQ line, just a diagnostic)
# --------------------------------------------------------------------------


def steel_sanity_check(structural_concrete_m3: float, total_steel_kg: float, unit_system: str = units.SI) -> dict:
    """The check itself is unit-independent (75-160 kg/m3 = 2.12-4.53
    kg/cft); only the message is shown in the project's unit system."""
    if structural_concrete_m3 <= 0:
        return {"kg_per_m3": 0.0, "status": "n/a", "message": "No structural concrete computed yet."}
    kg_per_m3 = total_steel_kg / structural_concrete_m3
    ok = 75 <= kg_per_m3 <= 160
    if units.is_fps(unit_system):
        kv = lambda x: units.kg_per_volume(x, unit_system)  # noqa: E731
        ratio, rng = f"{kv(kg_per_m3):.2f} kg/cft", f"{kv(75):.2f}-{kv(160):.2f} kg/cft"
    else:
        ratio, rng = f"{kg_per_m3:.0f} kg/m3", "75-160 kg/m3"
    if ok:
        status = "OK"
        message = f"Overall steel ratio {ratio} is within the typical residential RCC range ({rng})."
    else:
        status = "Review"
        message = (
            f"Overall steel ratio {ratio} is OUTSIDE the typical residential RCC range "
            f"({rng}). Re-check extracted dimensions/counts and thumb-rule settings."
        )
    return {"kg_per_m3": round(kg_per_m3, 1), "status": status, "message": message}


# --------------------------------------------------------------------------
# Strip (load-bearing wall) foundations
# --------------------------------------------------------------------------


def strip_footing_section(
    trench_width_m: float, wall_thickness_m: float, height_m: float, unit_system: str = units.SI
) -> dict:
    """Cross-section of a stepped brick strip footing of total brick height
    `height_m` (top of PCC bed -> plinth/DPC). Bottom step = PCC width - 2 x
    projection; each step is `offset` narrower per side and `step_height`
    tall, until the wall thickness is reached; the wall continues above."""
    det = rules.detailing(unit_system)
    bottom = max(trench_width_m - 2 * det["strip_pcc_projection_m"], wall_thickness_m)
    area, h_left, width, steps = 0.0, max(height_m, 0.0), bottom, []
    while width > wall_thickness_m + 1e-9 and h_left > 1e-9:
        h = min(det["strip_step_height_m"], h_left)
        area += width * h
        steps.append(width)
        h_left -= h
        width = max(width - 2 * det["strip_step_offset_m"], wall_thickness_m)
    area += wall_thickness_m * h_left
    return {"area_m2": area, "bottom_width_m": bottom, "steps": steps}


def compute_strip_foundation(
    params: ExtractedBuildingParams,
    project_inputs: ProjectInputs,
    assumptions: Optional[EngineeringAssumptions] = None,
    unit_system: str = units.SI,
) -> List[QuantityLineItem]:
    """Load-bearing wall foundations (typical 5-10 marla houses): a trench
    under every ground-floor wall, a PCC bed, and stepped brickwork up to
    the plinth. footings.width_m = trench/PCC width, footings.depth_m = PCC
    bed thickness, founding depth = ground level -> underside of PCC."""
    A = _assump(assumptions)
    f, w = params.footings, params.walls
    det = rules.detailing(unit_system)
    L = w.total_length_per_floor_m.value
    B = f.width_m.value
    t_pcc = f.depth_m.value
    fd = founding_depth_estimate(f, unit_system)
    D = fd.value
    t_wall = w.thickness_m.value
    slope = rules.SOIL_SIDE_SLOPE_FACTOR.get(project_inputs.soil_type, 1.05)
    brick_h = max(D - t_pcc, 0.0) + A.plinth_height_m
    sec = strip_footing_section(B, t_wall, brick_h, unit_system)
    below = strip_footing_section(B, t_wall, max(D - t_pcc, 0.0), unit_system)
    exc_v = L * B * D * slope
    pcc_v = L * B * t_pcc
    mas_v = L * sec["area_m2"]
    below_v = L * below["area_m2"]
    backfill_v = max(exc_v - pcc_v - below_v, 0.0)
    conf = combine_confidence(w.total_length_per_floor_m, f.width_m, fd)
    sm = lambda v: units.small_text(v, unit_system, f"{v * 1000:.0f} mm")  # noqa: E731
    ln = lambda v: units.length_text(v, unit_system, f"{v:.2f} m")  # noqa: E731
    base = {
        "foundation_wall_length_m": L,
        "trench_width_m": B,
        "founding_depth_m": D,
        "pcc_thickness_m": t_pcc,
        "wall_thickness_m": t_wall,
    }
    common = [
        "Load-bearing walls on strip foundations: a trench under every ground-floor wall "
        "(foundation length = ground-floor total wall length).",
        f"Trench / PCC bed {ln(B)} wide, PCC {sm(t_pcc)} thick, founding depth {ln(D)} below ground.",
    ]
    return [
        QuantityLineItem(
            item_code="EXC-01",
            description=f"Earthwork excavation in {project_inputs.soil_type.lower()} in trenches for strip foundations",
            category="Excavation", unit="m3", quantity=_q(exc_v, 3, "m3"), confidence=conf,
            formula="V = foundation_wall_length × trench_width × founding_depth × soil_side_slope_factor",
            inputs_used={**base, "soil_side_slope_factor": slope},
            assumptions=common + [f"Soil type '{project_inputs.soil_type}' side-slope/bulking factor = {slope}."],
        ),
        QuantityLineItem(
            item_code="PCC-01",
            description=f"PCC ({project_inputs.pcc_grade}) bed under strip foundations, {sm(t_pcc)} thick",
            category="PCC", unit="m3", quantity=_q(pcc_v, 3, "m3"), confidence=conf,
            formula="V = foundation_wall_length × trench_width × PCC_thickness",
            inputs_used=dict(base), assumptions=list(common),
        ),
        QuantityLineItem(
            item_code="FDN-MAS-01",
            description="Brick masonry in stepped strip foundations up to plinth (bricks, mortar & labour)",
            category="Foundation Masonry", unit="m3", quantity=_q(mas_v, 3, "m3"), confidence=conf,
            formula="V = foundation_wall_length × [Σ(step_width × step_height) + wall_thickness × remaining height]",
            inputs_used={**base, "brick_height_m": brick_h, "bottom_step_width_m": sec["bottom_width_m"],
                         "steps": len(sec["steps"]), "section_area_sqm": sec["area_m2"]},
            assumptions=common + [
                f"Bottom step = PCC width - 2 × {sm(det['strip_pcc_projection_m'])}; each step "
                f"{sm(det['strip_step_offset_m'])} narrower per side and {sm(det['strip_step_height_m'])} tall, "
                "until the wall thickness.",
                "Brickwork runs from the top of the PCC bed to plinth (DPC) level; walls above are in MAS-01..03.",
            ],
        ),
        QuantityLineItem(
            item_code="BACKFILL-01",
            description="Backfilling foundation trenches with excavated earth, in layers, watered & compacted",
            category="Earthwork", unit="m3", quantity=_q(backfill_v, 3, "m3"), confidence=conf,
            formula="V = excavation − PCC − foundation brickwork below ground",
            inputs_used={"excavation_m3": exc_v, "pcc_m3": pcc_v, "brickwork_below_ground_m3": below_v},
            assumptions=["Excavated earth re-used for backfill (no imported fill); surplus disposal not priced."],
        ),
    ]


# --------------------------------------------------------------------------
# Master orchestrator
# --------------------------------------------------------------------------


def generate_all_quantities(
    params: ExtractedBuildingParams,
    project_inputs: ProjectInputs,
    assumptions: Optional[EngineeringAssumptions] = None,
) -> List[QuantityLineItem]:
    """Run every calculation function and return the full flat MTO list.

    Every concrete/mortar-producing item is immediately followed by its own
    cement/sand/(aggregate) procurement breakdown (informational=True lines
    - see _concrete_material_items/_mortar_material_items) so the two never
    drift apart, and a project-wide cement/sand/aggregate rollup is added
    at the end (compute_procurement_summary).

    Inputs should be checked with engineering.validation.validate_params()
    first - the UI blocks calculation while it reports errors."""
    token = _FPS_PRECISION.set(units.is_fps(project_inputs.unit_system))
    try:
        return _generate_all_quantities(params, project_inputs, assumptions)
    finally:
        _FPS_PRECISION.reset(token)


def _generate_all_quantities(
    params: ExtractedBuildingParams,
    project_inputs: ProjectInputs,
    assumptions: Optional[EngineeringAssumptions] = None,
) -> List[QuantityLineItem]:
    A = _assump(assumptions)
    num_floors = params.num_floors.value
    steel_grade = project_inputs.steel_grade
    us = project_inputs.unit_system
    items: List[QuantityLineItem] = []

    # --- Substructure -----------------------------------------------------
    strip = params.footings.footing_type == "strip"
    if strip:
        # load-bearing walls on stepped brick strip foundations
        for it in compute_strip_foundation(params, project_inputs, A, us):
            items.append(it)
            if it.item_code == "PCC-01":
                items.extend(_concrete_material_items(it, project_inputs.pcc_grade, us))
            elif it.item_code == "FDN-MAS-01":
                mat = params.walls.wall_material if params.walls.wall_material in rules.MASONRY_UNIT_SIZES_M else rules.DEFAULT_WALL_MATERIAL
                nom, act = rules.MASONRY_UNIT_SIZES_M[mat], rules.MASONRY_UNIT_ACTUAL_SIZES_M[mat]
                mortar_frac = 1.0 - (act[0] * act[1] * act[2]) / (nom[0] * nom[1] * nom[2])
                items.extend(_mortar_material_items(it, it.quantity * mortar_frac, rules.MASONRY_MORTAR_MIX_RATIO, "foundation brickwork mortar"))
    else:
        exc = compute_excavation(params.footings, project_inputs.soil_type, A, us)
        items.append(exc)

        pcc = compute_pcc(params.footings, project_inputs.pcc_grade, A, us)
        items.append(pcc)
        items.extend(_concrete_material_items(pcc, project_inputs.pcc_grade, us))

        ftg_conc = compute_footing_concrete(params.footings, project_inputs.concrete_grade_footing)
        items.append(ftg_conc)
        items.extend(_concrete_material_items(ftg_conc, project_inputs.concrete_grade_footing, us))
        items.append(compute_footing_steel(ftg_conc, params.footings, A, steel_grade, us))

        items.append(compute_backfill(exc, pcc, ftg_conc, params.columns, params.footings, us))
    items.append(compute_plinth_filling(params, A, us))
    gf_items = compute_ground_floor_base(params, us)
    for gf in gf_items:
        items.append(gf)
        if gf.item_code == "GF-PCC-01":
            items.extend(_concrete_material_items(gf, project_inputs.pcc_grade, us))

    # --- Superstructure frame --------------------------------------------
    col_conc = compute_column_concrete(
        params.columns, num_floors, project_inputs.concrete_grade_column, params.footings, A, us
    )
    items.append(col_conc)
    items.extend(_concrete_material_items(col_conc, project_inputs.concrete_grade_column, us))
    items.append(compute_column_steel(col_conc, A, steel_grade, us))

    beam_conc = compute_beam_concrete(
        params.beams, num_floors, project_inputs.concrete_grade_beam, params.slabs.thickness_m.value
    )
    items.append(beam_conc)
    items.extend(_concrete_material_items(beam_conc, project_inputs.concrete_grade_beam, us))
    items.append(compute_beam_steel(beam_conc, A, steel_grade, us))

    slab_conc = compute_slab_concrete(params.slabs, num_floors, project_inputs.concrete_grade_slab)
    items.append(slab_conc)
    items.extend(_concrete_material_items(slab_conc, project_inputs.concrete_grade_slab, us))
    items.append(compute_slab_steel(slab_conc, A, steel_grade, us))

    stair_formwork: List[QuantityLineItem] = []
    if project_inputs.include_staircase:
        stair_items = compute_staircase(params.columns, num_floors, project_inputs.concrete_grade_slab, us)
        stair_conc = next(i for i in stair_items if i.item_code == "STAIR-CONC-01")
        items.append(stair_conc)
        items.extend(_concrete_material_items(stair_conc, project_inputs.concrete_grade_slab, us))
        items.append(compute_stair_steel(stair_conc, A, steel_grade, us))
        stair_formwork = [i for i in stair_items if i.category == "Formwork"]

    lintel_items = compute_lintels(params.walls, params.openings, num_floors, project_inputs.concrete_grade_beam, us)
    lintel_conc = next(i for i in lintel_items if i.item_code == "LINTEL-CONC-01")
    items.append(lintel_conc)
    items.extend(_concrete_material_items(lintel_conc, project_inputs.concrete_grade_beam, us))
    items.append(compute_lintel_steel(lintel_conc, A, steel_grade, us))

    formwork = compute_formwork(params.footings, params.columns, params.beams, params.slabs, num_floors, A, us)
    if strip:
        formwork = [i for i in formwork if i.item_code != "FORM-FTG-01"]  # brick footings need no shuttering
    items.extend(formwork)
    items.extend(stair_formwork)
    items.extend(i for i in lintel_items if i.category == "Formwork")

    # --- Masonry ------------------------------------------------------------
    parapet_len = external_perimeter_estimate(params).value if project_inputs.include_parapet else 0.0
    parapet_h = A.parapet_height_m if project_inputs.include_parapet else 0.0
    masonry_items, net_wall_area = compute_masonry(
        params.walls,
        params.openings,
        num_floors,
        lintel_volume=lintel_volume_m3(params.walls, params.openings, num_floors, us),
        parapet_length_m=parapet_len,
        parapet_height_m=parapet_h,
        unit_system=us,
    )
    items.extend(masonry_items)
    mortar_item = next((i for i in masonry_items if i.item_code == "MAS-02"), None)
    if mortar_item is not None:
        # MAS-02's quantity IS already a volume (m3) - use it directly.
        items.extend(
            _mortar_material_items(mortar_item, mortar_item.quantity, rules.MASONRY_MORTAR_MIX_RATIO, "masonry mortar (bedding/jointing)")
        )

    # --- Plaster ------------------------------------------------------------
    face_areas = compute_wall_face_areas(params, net_wall_area, num_floors, parapet_len, parapet_h)
    plaster_items = compute_plaster(face_areas, project_inputs)
    plaster_thickness = {
        "PLAS-01": ("internal plaster", project_inputs.plaster_thickness_internal_mm / 1000.0),
        "PLAS-02": ("external plaster", project_inputs.plaster_thickness_external_mm / 1000.0),
        "PLAS-03": ("ceiling plaster", rules.detailing(us)["ceiling_plaster_thickness_m"]),
    }
    for p_item in plaster_items:
        items.append(p_item)
        # PLAS-0x quantity is an AREA (m2), not a volume - convert
        # area x thickness to get the actual mortar volume cast.
        purpose, thickness_m = plaster_thickness[p_item.item_code]
        plaster_volume_m3 = p_item.quantity * thickness_m
        items.extend(_mortar_material_items(p_item, plaster_volume_m3, rules.PLASTER_MORTAR_MIX_RATIO, purpose))

    # --- Openings, finishes, roof, services ---------------------------------
    items.extend(compute_doors_windows(params.openings, num_floors, us))

    if project_inputs.include_flooring:
        items.append(compute_flooring(params.slabs, num_floors))
    if project_inputs.include_waterproofing:
        items.append(compute_waterproofing(params.slabs))
    if project_inputs.include_roof_treatment:
        items.append(compute_roof_treatment(params.slabs))
    if project_inputs.include_painting:
        items.extend(compute_painting(face_areas))
    if project_inputs.include_dpc:
        dpc = compute_dpc(params.walls, us)
        items.append(dpc)
        dpc_grade = rules.grade_label_for_system(rules.DPC_ASSUMED_GRADE, rules.CONCRETE_GRADE_OPTIONS, us)
        items.extend(_concrete_material_items(dpc, dpc_grade, us))
    if project_inputs.include_anti_termite:
        items.append(compute_anti_termite(params.plinth_area_per_floor_sqm))
    if project_inputs.include_mep:
        items.extend(compute_mep(params, num_floors, us))

    items.extend(compute_procurement_summary(items))

    # FPS: show every calculation trace in feet/inch/sqft/cft (keys renamed
    # and values converted exactly - see utils/units.localize_inputs). The
    # item QUANTITIES stay canonical; they are converted for display by the
    # same exact factors, so the traces and quantities always agree.
    if units.is_fps(us):
        for item in items:
            item.inputs_used = units.localize_inputs(item.item_code, item.inputs_used, us)
            item.formula = units.localize_formula(item.formula, us)

    return items
