"""
Input sanity validation - run on the Step 3 parameters BEFORE any
quantity is calculated.

Returns two lists:
  - errors:   physically impossible / self-contradictory inputs. The UI
              blocks "Confirm & Calculate MTO" until they are fixed, because
              the calculations would otherwise silently produce zero or
              meaningless quantities (e.g. openings larger than the walls
              they sit in -> masonry clamps to 0 with no explanation).
  - warnings: plausible-but-unusual inputs worth a second look (shown, not
              blocking).

Pure Python, no Streamlit - unit-testable like engineering/calculations.py.
"""
from __future__ import annotations

import math
from typing import List, Optional, Tuple

from engineering import rules
from engineering.calculations import external_perimeter_estimate, founding_depth_estimate
from models.schemas import EngineeringAssumptions, ExtractedBuildingParams, ProjectInputs
from utils import units


def validate_params(
    params: ExtractedBuildingParams,
    project_inputs: Optional[ProjectInputs] = None,
    assumptions: Optional[EngineeringAssumptions] = None,
) -> Tuple[List[str], List[str]]:
    errors: List[str] = []
    warnings: List[str] = []
    A = assumptions or EngineeringAssumptions()
    us = project_inputs.unit_system if project_inputs is not None else units.SI
    fps = units.is_fps(us)

    def L(value_m: float, si_text: str) -> str:
        """Length in the project's unit system (SI text passed verbatim)."""
        return units.fmt_ftin(value_m) if fps else si_text

    def Ar(value_sqm: float, si_text: str, places: int = 0) -> str:
        return f"{value_sqm * units.SQFT_PER_SQM:,.{places}f} sqft" if fps else si_text

    floors = params.num_floors.value
    f, c, b, s, w, o = params.footings, params.columns, params.beams, params.slabs, params.walls, params.openings

    # ---- Hard errors ---------------------------------------------------------
    if floors < 1:
        errors.append("Number of RCC slab levels must be at least 1.")
    elif abs(floors - round(floors)) > 1e-9:
        warnings.append(f"Number of slab levels is {floors:g} - expected a whole number.")
    if floors > 4:
        warnings.append(f"{floors:g} slab levels is beyond this tool's scope (simple 1-2 storey houses); results will be less reliable.")

    positive_fields = {
        "Plinth/built-up area per floor": params.plinth_area_per_floor_sqm.value,
        **({} if f.footing_type == "strip" else {"Footing length": f.length_m.value}),
        ("Strip foundation (PCC) width" if f.footing_type == "strip" else "Footing width"): f.width_m.value,
        ("PCC bed thickness" if f.footing_type == "strip" else "Footing thickness"): f.depth_m.value,
        "Column width": c.width_m.value,
        "Column depth": c.depth_m.value,
        "Column height per floor": c.height_per_floor_m.value,
        "Beam width": b.width_m.value,
        "Beam depth": b.depth_m.value,
        "Beam average length": b.avg_length_m.value,
        "Slab area per floor": s.area_per_floor_sqm.value,
        "Slab thickness": s.thickness_m.value,
        "Total wall length per floor": w.total_length_per_floor_m.value,
        "Wall height": w.height_m.value,
        "Wall thickness": w.thickness_m.value,
    }
    for label, value in positive_fields.items():
        if value <= 0:
            errors.append(f"{label} must be greater than zero.")

    fd = founding_depth_estimate(f, us).value
    if fd <= f.depth_m.value:
        errors.append(
            f"Founding depth ({L(fd, f'{fd:.2f} m')}) must be greater than the footing thickness "
            f"({units.fmt_in(f.depth_m.value) if fps else f'{f.depth_m.value:.2f} m'}) - "
            "the footing sits below ground level."
        )

    gross_wall = w.total_length_per_floor_m.value * w.height_m.value
    opening_area = (
        o.door_count_per_floor.value * o.avg_door_area_sqm.value
        + o.window_count_per_floor.value * o.avg_window_area_sqm.value
    )
    if gross_wall > 0 and opening_area >= gross_wall:
        errors.append(
            f"Door + window area per floor ({Ar(opening_area, f'{opening_area:.1f} m²')}) is larger than the total wall area per floor "
            f"({Ar(gross_wall, f'{gross_wall:.1f} m²')}). Check opening counts/sizes and wall length."
        )
    elif gross_wall > 0 and opening_area > 0.4 * gross_wall:
        warnings.append(
            f"Openings are {opening_area / gross_wall:.0%} of the wall area - unusually high for a house (typically 10-25%)."
        )

    perim = external_perimeter_estimate(params).value
    if perim > w.total_length_per_floor_m.value + 1e-9:
        errors.append(
            f"External perimeter ({L(perim, f'{perim:.1f} m')}) cannot exceed the total wall length per floor "
            f"({L(w.total_length_per_floor_m.value, f'{w.total_length_per_floor_m.value:.1f} m')}) - total wall length must include the external walls."
        )

    # ---- Warnings ------------------------------------------------------------
    area = params.plinth_area_per_floor_sqm.value
    if area > 0:
        min_perim = 4 * math.sqrt(area)  # square = minimum perimeter for a rectangular plan
        if perim < 0.95 * min_perim:
            warnings.append(
                f"External perimeter {L(perim, f'{perim:.1f} m')} is smaller than possible for a {Ar(area, f'{area:.0f} m²')} rectangular plan "
                f"(minimum ≈ {L(min_perim, f'{min_perim:.1f} m')})."
            )
        ratio = w.total_length_per_floor_m.value / area
        if ratio < 0.4 or ratio > 1.2:
            if fps:
                ratio_ft = ratio * units.FT_PER_M / units.SQFT_PER_SQM
                warnings.append(
                    f"Total wall length is {ratio_ft:.3f} ft per sqft of plan area - typical houses are about "
                    f"{0.6 * units.FT_PER_M / units.SQFT_PER_SQM:.2f}-{1.0 * units.FT_PER_M / units.SQFT_PER_SQM:.2f} "
                    f"(e.g. {0.6 * area * units.FT_PER_M:.0f}-{1.0 * area * units.FT_PER_M:.0f} ft for this plan)."
                )
            else:
                warnings.append(
                    f"Total wall length is {ratio:.2f} m per m² of plan area - typical houses are about 0.6-1.0 "
                    f"(e.g. {0.6*area:.0f}-{1.0*area:.0f} m for this plan)."
                )
    if area > 0 and s.area_per_floor_sqm.value > 0:
        diff = abs(s.area_per_floor_sqm.value - area) / area
        if diff > 0.15:
            warnings.append(
                f"Slab area ({Ar(s.area_per_floor_sqm.value, f'{s.area_per_floor_sqm.value:.0f} m²')}) differs from plinth area "
                f"({Ar(area, f'{area:.0f} m²')}) by {diff:.0%}."
            )

    if f.footing_type == "strip":
        if f.width_m.value < 2 * w.thickness_m.value:
            warnings.append("Strip foundation width is less than twice the wall thickness - unusually narrow.")
    elif f.footing_type != "isolated":
        warnings.append(
            f"Footing type is '{f.footing_type}', but quantities are calculated as isolated pad footings (MVP limitation)."
        )
    elif f.count.value != c.count.value:
        warnings.append(
            f"Footing count ({f.count.value:g}) differs from column count ({c.count.value:g}) - "
            "isolated footings are usually one per column."
        )

    if b.depth_m.value <= s.thickness_m.value:
        warnings.append("Beam depth is not greater than slab thickness - beams at slab levels contribute no concrete.")
    height_tol = 20 * 0.0254 if fps else 0.5  # 1'-8" in FPS
    if abs(w.height_m.value - c.height_per_floor_m.value) > height_tol:
        warnings.append(
            f"Wall height ({L(w.height_m.value, f'{w.height_m.value:.2f} m')}) differs from column height per floor "
            f"({L(c.height_per_floor_m.value, f'{c.height_per_floor_m.value:.2f} m')}) by more than {L(height_tol, '0.5 m')}."
        )
    deep_limit = 10 * 0.3048 if fps else 3.0  # 10'-0" in FPS
    if fd > deep_limit:
        typical = "4'-0\" to 6'-6\"" if fps else "1.2-2.0 m"
        warnings.append(f"Founding depth {L(fd, f'{fd:.2f} m')} is unusually deep for a small house (typically {typical}).")
    if w.wall_material not in rules.MASONRY_UNIT_SIZES_M:
        warnings.append(f"Wall material '{w.wall_material}' is not recognised - default modular brick sizes will be used.")

    if project_inputs is not None and project_inputs.include_mep:
        if params.services.bathroom_count_total.value < 1:
            warnings.append("No bathrooms entered while MEP & sanitary is included.")
        if params.services.kitchen_count_total.value < 1:
            warnings.append("No kitchen entered while MEP & sanitary is included.")

    if project_inputs is not None and project_inputs.plot_marla:
        from engineering.plot_templates import plausibility_warnings

        warnings.extend(plausibility_warnings(params, project_inputs))

    det = rules.detailing(us)
    if A.plinth_height_m < det["gf_soling_thickness_m"] + det["gf_pcc_floor_thickness_m"]:
        warnings.append("Plinth height is less than the ground-floor soling + PCC base thickness - plinth filling will be zero.")

    return errors, warnings
