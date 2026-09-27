"""
BOQ (Bill of Quantities) generation.

Takes the deterministic MTO quantities and applies:
1. Category-specific wastage/allowance percentages (editable)
2. Unit rates (editable rate book)
3. Concrete-grade rate adjustment: the rate book prices RCC at M20 and
   PCC at M10; another grade adjusts the composite rate by the difference
   in cement/sand/aggregate material cost per m3 (see
   concrete_grade_rate_delta below).
4. A "Preliminaries & site overheads" lump-sum line (% of the civil
   subtotal, editable in Step 5). MEP (electrical, plumbing & sanitary,
   kitchen, external services) is now priced as its own parametric BOQ
   lines rather than hidden inside this percentage.
5. Overall contingency percentage -> grand total

Quantity items flagged `informational=True` (the cement/sand/aggregate
breakdown of each concrete/mortar pour - see engineering/calculations.py)
are skipped entirely here: their cost already lives inside their parent
item's composite rate, so pricing them again would double-count. They're
still fully visible in the MTO (Step 4) - that's a deliberate MTO-vs-BOQ
split: MTO is the full procurement-grade material list, BOQ is the priced
summary using composite pay items.

No AI involvement here either - pure arithmetic over user-approved inputs.
"""
from __future__ import annotations

from typing import Dict, List

import pandas as pd

from engineering import rules
from engineering.calculations import concrete_material_breakdown
from engineering.rules import FINISH_LEVEL_RATE_MULTIPLIERS
from models.schemas import (
    BOQLineItem,
    ConfidenceLevel,
    CostSummary,
    MaterialRate,
    ProjectInputs,
    QuantityLineItem,
    WastageFactors,
)
from utils import units

# Maps a quantity item's category to the relevant wastage-factor field name.
# "Concrete Materials" and "Procurement Summary" items are always
# `informational=True` and skipped entirely in generate_boq() before this
# lookup would even matter (see the loop below) - listed here anyway so the
# mapping stays a complete, self-documenting reference of every category.
CATEGORY_TO_WASTAGE_FIELD = {
    "Excavation": None,  # no wastage applied to excavated earth
    "Earthwork": None,  # backfill / plinth filling - measured compacted in place
    "PCC": "concrete_pct",
    "Footing": "concrete_pct",
    "Column": "concrete_pct",
    "Beam": "concrete_pct",
    "Slab": "concrete_pct",
    "Reinforcement": "steel_pct",
    "Formwork": "formwork_pct",
    "Masonry": "brick_block_pct",
    "Plaster": "plaster_pct",
    "Flooring": "flooring_pct",
    "Waterproofing": "misc_pct",
    "Painting": "paint_pct",
    "DPC": "concrete_pct",
    "Anti-termite": "misc_pct",
    "Ground Floor Base": "concrete_pct",
    "Foundation Masonry": "brick_block_pct",
    "Staircase": "concrete_pct",
    "Lintels": "concrete_pct",
    "Roof Treatment": "misc_pct",
    "Doors": None,  # supply+install rate is already an exact per-unit/per-area price
    "Windows": None,
    "Electrical": None,  # parametric supply+install allowances - no separate wastage
    "Plumbing & Sanitary": None,
    "Kitchen": None,
    "External Services": None,
    "Concrete Materials": None,  # informational-only; never priced (see generate_boq)
    "Procurement Summary": None,  # informational-only; never priced (see generate_boq)
}

PRELIMINARIES_PCT_OF_CIVIL_SUBTOTAL = 8.0


def _wastage_for(item: QuantityLineItem, wastage: WastageFactors) -> float:
    field = CATEGORY_TO_WASTAGE_FIELD.get(item.category, "misc_pct")
    if field is None:
        return 0.0
    return getattr(wastage, field, wastage.misc_pct)


def _round_qty(value: float, unit: str, unit_system: str) -> float:
    """SI: 3 decimals of the canonical unit (unchanged). FPS: m3/m2 keep full
    precision so they are rounded in cft/sqft when displayed/exported."""
    if units.is_fps(unit_system) and unit in ("m3", "m2"):
        return round(value, 9)
    return round(value, 3)


def _finish_level_multiplier(category: str, finish_level: str) -> float:
    """Only the finish-grade-sensitive categories listed in
    engineering.rules.FINISH_LEVEL_RATE_MULTIPLIERS (Flooring, Painting,
    Plaster, Doors, Windows, Electrical, Plumbing & Sanitary, Kitchen) are
    affected - every other category (structural concrete, steel,
    excavation, etc.) always returns 1.0 (no-op)."""
    return FINISH_LEVEL_RATE_MULTIPLIERS.get(finish_level, {}).get(category, 1.0)


# item_code -> (ProjectInputs grade field, grade the rate book is priced at)
GRADE_ADJUSTED_ITEMS = {
    "FTG-CONC-01": ("concrete_grade_footing", rules.RATE_BOOK_BASE_RCC_GRADE),
    "COL-CONC-01": ("concrete_grade_column", rules.RATE_BOOK_BASE_RCC_GRADE),
    "BEAM-CONC-01": ("concrete_grade_beam", rules.RATE_BOOK_BASE_RCC_GRADE),
    "SLAB-CONC-01": ("concrete_grade_slab", rules.RATE_BOOK_BASE_RCC_GRADE),
    "STAIR-CONC-01": ("concrete_grade_slab", rules.RATE_BOOK_BASE_RCC_GRADE),
    "LINTEL-CONC-01": ("concrete_grade_beam", rules.RATE_BOOK_BASE_RCC_GRADE),
    "PCC-01": ("pcc_grade", rules.RATE_BOOK_BASE_PCC_GRADE),
    "GF-PCC-01": ("pcc_grade", rules.RATE_BOOK_BASE_PCC_GRADE),
}


def _material_cost_per_m3(grade_label: str) -> float:
    bd = concrete_material_breakdown(1.0, grade_label)
    return (
        bd["cement_bags"] * rules.CEMENT_PRICE_PER_BAG
        + bd["sand_volume_m3"] * rules.SAND_PRICE_PER_M3
        + bd["aggregate_volume_m3"] * rules.AGGREGATE_PRICE_PER_M3
    )


def concrete_grade_rate_delta(grade_label: str, base_grade: str) -> float:
    """PKR/m3 to add to (or subtract from) a composite concrete rate priced
    at `base_grade` when `grade_label` is used instead. Works for SI ("M25")
    and FPS ("3600 psi") labels via rules.resolve_nominal_mix()."""
    return _material_cost_per_m3(grade_label) - _material_cost_per_m3(base_grade)


def generate_boq(
    mto_items: List[QuantityLineItem],
    rate_book: Dict[str, MaterialRate],
    wastage: WastageFactors,
    project_inputs: ProjectInputs,
    include_preliminaries: bool = True,
    preliminaries_pct: float = PRELIMINARIES_PCT_OF_CIVIL_SUBTOTAL,
) -> tuple[List[BOQLineItem], CostSummary]:
    boq_items: List[BOQLineItem] = []

    for item in mto_items:
        if item.informational:
            # Procurement-reference-only line (e.g. the cement/sand/
            # aggregate that make up a concrete pour) - its cost is
            # already inside its parent item's composite rate, so pricing
            # it again here would double-count. It still shows up in the
            # MTO (Step 4) - that's the whole point of these lines.
            continue

        wastage_pct = _wastage_for(item, wastage)
        qty_with_wastage = item.quantity * (1 + wastage_pct / 100.0)

        rate_row = rate_book.get(item.item_code)
        if rate_row is None:
            rate = 0.0
            remarks = "No rate found in rate book - please add a rate."
        else:
            rate = rate_row.rate
            remarks = ""

        grade_spec = GRADE_ADJUSTED_ITEMS.get(item.item_code)
        if grade_spec is not None and rate_row is not None:
            grade_label = getattr(project_inputs, grade_spec[0])
            delta = concrete_grade_rate_delta(grade_label, grade_spec[1])
            if abs(delta) >= 0.5:
                rate = max(rate + delta, 0.0)
                us = project_inputs.unit_system
                base_label = rules.grade_label_for_system(
                    grade_spec[1],
                    rules.PCC_GRADE_OPTIONS if grade_spec[0] == "pcc_grade" else rules.CONCRETE_GRADE_OPTIONS,
                    us,
                )
                shown_delta = units.display_rate(abs(delta), "m3", us)
                note = (
                    f"Rate {'+' if delta > 0 else '-'}{shown_delta:,.0f}/{units.display_unit('m3', us)} for grade {grade_label} "
                    f"(rate book priced at {base_label})."
                )
                remarks = f"{remarks} {note}".strip()

        finish_mult = _finish_level_multiplier(item.category, project_inputs.finish_level)
        if finish_mult != 1.0:
            rate = rate * finish_mult
            note = f"Rate x{finish_mult:.2f} for '{project_inputs.finish_level}' finish level."
            remarks = f"{remarks} {note}".strip()

        amount = qty_with_wastage * rate
        boq_items.append(
            BOQLineItem(
                item_code=item.item_code,
                description=item.description,
                category=item.category,
                unit=item.unit,
                quantity=_round_qty(item.quantity, item.unit, project_inputs.unit_system),
                wastage_pct=wastage_pct,
                quantity_with_wastage=_round_qty(qty_with_wastage, item.unit, project_inputs.unit_system),
                rate=rate,
                amount=round(amount, 2),
                confidence=item.confidence,
                remarks=remarks,
            )
        )

    civil_subtotal = sum(b.amount for b in boq_items)

    if include_preliminaries:
        prelim_amount = civil_subtotal * preliminaries_pct / 100.0
        boq_items.append(
            BOQLineItem(
                item_code="PRELIM-01",
                description=(
                    f"Preliminaries & site overheads "
                    f"({preliminaries_pct:.1f}% of subtotal of all items above)"
                ),
                category="Preliminaries",
                unit="LS",
                quantity=1.0,
                wastage_pct=0.0,
                quantity_with_wastage=1.0,
                rate=round(prelim_amount, 2),
                amount=round(prelim_amount, 2),
                confidence=ConfidenceLevel.LOW,
                remarks="Lump-sum allowance (site setup, supervision, water/power for works, transport). Percentage editable in Step 5.",
            )
        )

    subtotal = sum(b.amount for b in boq_items)
    contingency_amount = subtotal * project_inputs.contingency_pct / 100.0
    grand_total = subtotal + contingency_amount

    cost_summary = CostSummary(
        subtotal=round(subtotal, 2),
        contingency_pct=project_inputs.contingency_pct,
        contingency_amount=round(contingency_amount, 2),
        grand_total=round(grand_total, 2),
        currency=project_inputs.currency,
    )
    return boq_items, cost_summary


def boq_to_dataframe(items: List[BOQLineItem], unit_system: str = units.SI) -> pd.DataFrame:
    """`unit_system` only affects the displayed Unit/Quantity/Rate columns.
    Quantity and Rate are converted by inverse factors (see
    utils/units.py), so Quantity x Rate always reproduces the same
    Amount regardless of which unit system is selected - Amount itself
    (a currency figure) is never converted."""
    rows = []
    for i in items:
        qty, unit = units.quantity_and_unit_for_display(i.quantity, i.unit, unit_system)
        qty_wastage, _ = units.quantity_and_unit_for_display(i.quantity_with_wastage, i.unit, unit_system)
        rate = units.display_rate(i.rate, i.unit, unit_system)
        rows.append(
            {
                "Item Code": i.item_code,
                "Category": i.category,
                "Description": i.description,
                "Unit": unit,
                "Quantity": round(qty, 3),
                "Wastage %": i.wastage_pct,
                "Qty incl. Wastage": round(qty_wastage, 3),
                "Rate": round(rate, 2),
                "Amount": i.amount,
                "Confidence": i.confidence.value,
                "Remarks": i.remarks,
            }
        )
    return pd.DataFrame(rows)


def cost_by_category(items: List[BOQLineItem]) -> pd.DataFrame:
    # Category totals are pure currency amounts - unaffected by unit
    # system - so this always uses the canonical (SI) dataframe.
    df = boq_to_dataframe(items, unit_system=units.SI)
    if df.empty:
        return df
    grouped = df.groupby("Category", as_index=False)["Amount"].sum().sort_values("Amount", ascending=False)
    return grouped
