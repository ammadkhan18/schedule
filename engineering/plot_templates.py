"""
5-10 marla plot templates & plausibility ranges (Pakistani residential scope).

The app is purpose-built for 5-10 marla houses, and that scope is used as
knowledge:
  - a plot size alone produces a complete, realistic starting building
    (build_template_params) - so an estimate exists even with no drawing;
  - every extracted/typed value can be checked against what is physically
    plausible on that plot (plausibility_warnings).

Two marla standards are in use and differ by ~21%, so the user must pick:
  - 225 sqft/marla   - housing societies / LDA-style (5 marla = 1,125 sqft)
  - 272.25 sqft/marla - traditional (5 marla = 1,361 sqft)

Frontages, setbacks and room counts below are TYPICAL values for row
houses on 5-10 marla plots, not bylaw requirements - every one is editable
and each society's bylaws govern. All lengths here are feet; the builder
converts to metres (exact) for the calculation engine.
"""
from __future__ import annotations

import math
from typing import List, Optional

from models.schemas import (
    BeamSpec,
    ColumnSpec,
    ConfidenceLevel,
    Estimate,
    ExtractedBuildingParams,
    FootingSpec,
    OpeningsSpec,
    ProjectInputs,
    ServicesSpec,
    SlabSpec,
    Source,
    WallSpec,
)
from utils import units

FT = 0.3048
IN = 0.0254
SQFT = FT * FT

MARLA_STANDARDS = {
    225.0: "Society / LDA standard (225 sqft per marla)",
    272.25: "Traditional (272.25 sqft per marla)",
}
DEFAULT_MARLA_SQFT = 225.0
PLOT_SIZES_MARLA = [5, 7, 8, 10]
STOREY_OPTIONS = [1, 2, 3]  # single storey, G+1, G+2

# Typical frontage (plot width, ft) per marla standard.
TYPICAL_FRONTAGE_FT = {
    225.0: {5: 25, 7: 30, 8: 30, 10: 35},
    272.25: {5: 30, 7: 35, 8: 35, 10: 40},
}
# Typical (front, rear) open space in ft; side setbacks are 0 for row houses.
TYPICAL_SETBACKS_FT = {5: (5, 3), 7: (5, 5), 8: (5, 5), 10: (7, 5)}
# Typical fit-out per floor.
TYPICAL_PER_FLOOR = {
    5: {"baths": 2, "doors": 7, "windows": 7},
    7: {"baths": 2, "doors": 8, "windows": 8},
    8: {"baths": 2, "doors": 9, "windows": 9},
    10: {"baths": 3, "doors": 10, "windows": 10},
}
MAX_COLUMN_SPAN_FT = 14.0  # typical residential grid spacing limit


def plot_area_sqft(marla: float, marla_sqft: float = DEFAULT_MARLA_SQFT) -> float:
    return marla * marla_sqft


def _nearest_size(marla: float) -> int:
    return min(PLOT_SIZES_MARLA, key=lambda m: abs(m - marla))


def plot_dimensions_ft(marla: float, marla_sqft: float = DEFAULT_MARLA_SQFT, width_ft: Optional[float] = None) -> tuple:
    """(width, depth) of the plot in ft. Depth = area / frontage."""
    area = plot_area_sqft(marla, marla_sqft)
    table = TYPICAL_FRONTAGE_FT.get(marla_sqft, TYPICAL_FRONTAGE_FT[DEFAULT_MARLA_SQFT])
    width = width_ft or table.get(_nearest_size(marla), math.sqrt(area / 1.8))
    return width, area / width


def covered_footprint_ft(marla: float, marla_sqft: float = DEFAULT_MARLA_SQFT, width_ft: Optional[float] = None) -> tuple:
    """(width, depth) of the typical covered footprint: full width (row
    house, no side setbacks) x (plot depth - front - rear open space)."""
    width, depth = plot_dimensions_ft(marla, marla_sqft, width_ft)
    front, rear = TYPICAL_SETBACKS_FT[_nearest_size(marla)]
    return width, max(depth - front - rear, depth * 0.6)


def column_grid(width_ft: float, depth_ft: float) -> tuple:
    """Columns along width x along depth, spans <= MAX_COLUMN_SPAN_FT."""
    nx = math.ceil(width_ft / MAX_COLUMN_SPAN_FT) + 1
    ny = math.ceil(depth_ft / MAX_COLUMN_SPAN_FT) + 1
    return nx, ny


def build_template_params(project_inputs: ProjectInputs) -> Optional[ExtractedBuildingParams]:
    """A complete typical building for the plot in Step 1 (None if no plot
    size was chosen). Every value is Low confidence and labelled, so Step 3
    visibly asks the user to confirm it."""
    marla = project_inputs.plot_marla
    if not marla:
        return None
    us = project_inputs.unit_system
    msq = project_inputs.marla_sqft
    storeys = int(project_inputs.plot_storeys)
    size = _nearest_size(marla)
    W, D = covered_footprint_ft(marla, msq, project_inputs.plot_width_ft)
    covered = W * D
    perimeter = 2 * (W + D)
    # external walls + typical internal partitions (~0.12 ft per sqft)
    wall_len = perimeter + 0.12 * covered
    nx, ny = column_grid(W, D)
    n_cols = nx * ny
    n_beams = nx * (ny - 1) + ny * (nx - 1)
    avg_beam = (nx * D + ny * W) / n_beams
    footing_ft = {1: 4.0, 2: 5.0, 3: 6.0}.get(storeys, 5.0)
    footing_t_in = 24.0 if storeys >= 3 else 18.0
    col_w_in = 12.0 if storeys >= 3 else 9.0
    per_floor = TYPICAL_PER_FLOOR[size]
    label = f"{marla:g} marla template"
    L = lambda ft: units.length_text(ft * FT, us, f"{ft * FT:.2f} m")  # noqa: E731
    Ar = lambda sq: units.area_text(sq * SQFT, us, f"{sq * SQFT:.1f} m²", 0)  # noqa: E731

    def est(v: float, note: str) -> Estimate:
        return Estimate(value=v, confidence=ConfidenceLevel.LOW, source=Source.DEFAULT_ASSUMPTION, note=f"{label}: {note}")

    return ExtractedBuildingParams(
        num_floors=est(storeys, {1: "single storey", 2: "G+1", 3: "G+2"}.get(storeys, f"{storeys} storeys")),
        plinth_area_per_floor_sqm=est(covered * SQFT, f"covered {L(W)} x {L(D)} = {Ar(covered)} per floor"),
        footings=FootingSpec(
            footing_type="isolated",
            count=est(n_cols, f"one per column ({nx} x {ny} grid)"),
            length_m=est(footing_ft * FT, f"{L(footing_ft)} square footing for {storeys} storey(s)"),
            width_m=est(footing_ft * FT, f"{L(footing_ft)} square footing for {storeys} storey(s)"),
            depth_m=est(footing_t_in * IN, "typical footing thickness"),
            founding_depth_m=est(5 * FT, "typical founding depth"),
        ),
        columns=ColumnSpec(
            count=est(n_cols, f"{nx} x {ny} grid, spans <= {L(MAX_COLUMN_SPAN_FT)}"),
            width_m=est(col_w_in * IN, "typical column width"),
            depth_m=est(18 * IN, "typical column depth"),
            height_per_floor_m=est(10 * FT, "typical floor-to-floor height"),
        ),
        beams=BeamSpec(
            count=est(n_beams, "grid beams per level"),
            avg_length_m=est(avg_beam * FT, "average grid span"),
            width_m=est(9 * IN, "typical beam width"),
            depth_m=est(18 * IN, "typical beam depth"),
        ),
        slabs=SlabSpec(
            area_per_floor_sqm=est(covered * SQFT, "slab = covered area"),
            thickness_m=est(5 * IN, "typical slab thickness"),
        ),
        walls=WallSpec(
            total_length_per_floor_m=est(wall_len * FT, "external perimeter + typical internal partitions"),
            height_m=est(10 * FT, "typical wall height"),
            thickness_m=est(project_inputs.wall_thickness_mm / 1000.0, "from Step 1 'Wall Thickness'"),
            wall_material=project_inputs.wall_material,
            external_perimeter_m=est(perimeter * FT, f"2 x ({L(W)} + {L(D)})"),
        ),
        openings=OpeningsSpec(
            door_count_per_floor=est(per_floor["doors"], "typical door count per floor"),
            avg_door_area_sqm=est(21 * SQFT, "3'-0\" x 7'-0\" door" if units.is_fps(us) else "0.91 m x 2.13 m door"),
            window_count_per_floor=est(per_floor["windows"], "typical window count per floor"),
            avg_window_area_sqm=est(16 * SQFT, "4'-0\" x 4'-0\" window" if units.is_fps(us) else "1.22 m x 1.22 m window"),
        ),
        services=ServicesSpec(
            bathroom_count_total=est(per_floor["baths"] * storeys, f"{per_floor['baths']} per floor"),
            kitchen_count_total=est(1, "one kitchen (add one per separate portion)"),
        ),
        overall_notes=(
            f"Typical {marla:g} marla house ({MARLA_STANDARDS.get(msq, f'{msq:g} sqft/marla')}), "
            f"plot {L(plot_dimensions_ft(marla, msq, project_inputs.plot_width_ft)[0])} x "
            f"{L(plot_dimensions_ft(marla, msq, project_inputs.plot_width_ft)[1])}. Review every value."
        ),
        extraction_warnings=[f"Values come from the {marla:g} marla template - please review and edit before proceeding."],
    )


def plausibility_warnings(params: ExtractedBuildingParams, project_inputs: ProjectInputs) -> List[str]:
    """Checks the parameters against what is physically plausible on the
    chosen plot. Empty when no plot size is set."""
    marla = project_inputs.plot_marla
    if not marla:
        return []
    us = project_inputs.unit_system
    msq = project_inputs.marla_sqft
    plot = plot_area_sqft(marla, msq)
    size = _nearest_size(marla)
    Ar = lambda sq: units.area_text(sq * SQFT, us, f"{sq * SQFT:.0f} m²", 0)  # noqa: E731
    out: List[str] = []
    covered = params.plinth_area_per_floor_sqm.value / SQFT
    if covered > plot * 1.02:
        out.append(
            f"Covered area per floor ({Ar(covered)}) is larger than the whole {marla:g} marla plot ({Ar(plot)}). "
            "Check the plan area or the marla standard in Step 1."
        )
    elif covered < plot * 0.45:
        out.append(
            f"Covered area per floor ({Ar(covered)}) is under 45% of the {marla:g} marla plot ({Ar(plot)}) - "
            "unusually small for a row house."
        )
    storeys = params.num_floors.value
    if storeys > 3:
        out.append(f"{storeys:g} storeys is unusual for a {marla:g} marla house (typically G+1, at most G+2).")
    W, D = covered_footprint_ft(marla, msq, project_inputs.plot_width_ft)
    nx, ny = column_grid(W, D)
    typical_cols = nx * ny
    cols = params.columns.count.value
    load_bearing = params.footings.footing_type == "strip"  # few RCC columns is normal for load-bearing walls
    if not load_bearing and (cols < 0.6 * typical_cols or cols > 1.8 * typical_cols):
        out.append(
            f"{cols:g} columns is outside the typical range for this plot "
            f"({math.floor(0.6 * typical_cols)}-{math.ceil(1.8 * typical_cols)}; template grid {nx} x {ny})."
        )
    baths = params.services.bathroom_count_total.value
    max_baths = (TYPICAL_PER_FLOOR[size]["baths"] + 2) * max(storeys, 1)
    if baths > max_baths:
        out.append(f"{baths:g} bathrooms is more than usual for a {marla:g} marla house with {storeys:g} storey(s).")
    return out
