"""
Single source of truth for every data structure that flows through the
pipeline: AI extraction -> user edits -> engineering calculations ->
MTO -> BOQ -> export.

Keeping everything in Pydantic models (rather than loose dicts) means:
- The Groq JSON output is validated immediately (fail fast on bad AI output)
- Every numeric field can carry a confidence level + provenance note
- Streamlit forms, calculation functions, and exporters all share one
  contract, so a change here can't silently break one module without
  breaking the others too (Pydantic raises).
"""
from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional, Union

from pydantic import BaseModel, Field, model_validator


class ConfidenceLevel(str, Enum):
    HIGH = "High"
    MEDIUM = "Medium"
    LOW = "Low"


class Source(str, Enum):
    AI_EXTRACTED = "AI-extracted"
    DEFAULT_ASSUMPTION = "Default assumption"
    USER_EDITED = "User-edited"
    USER_INPUT = "User-input"
    DRAWING_READ = "Read from drawing"  # text layer / vector geometry of CAD PDFs, no AI


class UnitSystem(str, Enum):
    """The project's unit system. FPS (feet-inch, Pakistani practice) is the
    default. Values are STORED in one canonical base (m, m2, m3, kg) and
    converted exactly; in FPS mode every input, default, calculation trace,
    text and export is produced in FPS (see utils/units.py and
    engineering/rules.py DETAILING)."""

    SI = "SI"
    FPS = "FPS"


class Estimate(BaseModel):
    """A single numeric field with confidence + provenance, editable by the user."""

    value: float
    confidence: ConfidenceLevel = ConfidenceLevel.MEDIUM
    source: Source = Source.DEFAULT_ASSUMPTION
    note: str = ""

    def with_value(self, new_value: float) -> "Estimate":
        return Estimate(
            value=new_value,
            confidence=self.confidence,
            source=Source.USER_EDITED,
            note=self.note,
        )


# --------------------------------------------------------------------------
# Building parameters (populated by AI, then edited by the user)
# --------------------------------------------------------------------------


class FootingSpec(BaseModel):
    footing_type: str = "isolated"  # isolated | strip | raft | combined
    count: Estimate
    length_m: Estimate
    width_m: Estimate
    # Footing THICKNESS (depth of the concrete pad itself) - NOT the
    # excavation depth. Field name kept as `depth_m` for backward
    # compatibility with saved/AI JSON.
    depth_m: Estimate
    # Founding depth: natural ground level -> underside of the footing.
    # Drives excavation depth (plus PCC thickness below it). Optional for
    # backward compatibility; calculations fall back to
    # engineering.rules.DEFAULT_FOUNDING_DEPTH_M when it is None.
    founding_depth_m: Optional[Estimate] = None


class ColumnSpec(BaseModel):
    count: Estimate
    width_m: Estimate  # 'b'
    depth_m: Estimate  # 'd'
    height_per_floor_m: Estimate


class BeamSpec(BaseModel):
    count: Estimate
    avg_length_m: Estimate
    width_m: Estimate
    depth_m: Estimate


class SlabSpec(BaseModel):
    area_per_floor_sqm: Estimate
    thickness_m: Estimate


class WallSpec(BaseModel):
    total_length_per_floor_m: Estimate  # ALL walls (external + internal) on one floor
    height_m: Estimate
    thickness_m: Estimate
    # Must be a key of engineering.rules.MASONRY_UNIT_SIZES_M (the previous
    # default "Burnt clay brick (modular)" was not a valid key).
    wall_material: str = "Burnt clay brick (modular 190x90x90mm)"
    # External (building-perimeter) wall length per floor - used to split
    # internal vs external plaster/paint and for the roof parapet. Optional
    # for backward compatibility; calculations fall back to 4 x sqrt(plinth
    # area) (a square footprint, Low confidence) when it is None.
    external_perimeter_m: Optional[Estimate] = None


class OpeningsSpec(BaseModel):
    door_count_per_floor: Estimate
    avg_door_area_sqm: Estimate
    window_count_per_floor: Estimate
    avg_window_area_sqm: Estimate


class ServicesSpec(BaseModel):
    """Counts that drive the parametric MEP (plumbing/sanitary, kitchen)
    BOQ lines. Whole-building totals, not per floor."""

    bathroom_count_total: Estimate
    kitchen_count_total: Estimate


def _default_services() -> "ServicesSpec":
    return ServicesSpec(
        bathroom_count_total=Estimate(value=2, confidence=ConfidenceLevel.LOW, source=Source.DEFAULT_ASSUMPTION, note="Default: 2 bathrooms - please verify"),
        kitchen_count_total=Estimate(value=1, confidence=ConfidenceLevel.LOW, source=Source.DEFAULT_ASSUMPTION, note="Default: 1 kitchen - please verify"),
    )


class ExtractedBuildingParams(BaseModel):
    num_floors: Estimate
    plinth_area_per_floor_sqm: Estimate
    footings: FootingSpec
    columns: ColumnSpec
    beams: BeamSpec
    slabs: SlabSpec
    walls: WallSpec
    openings: OpeningsSpec
    services: ServicesSpec = Field(default_factory=_default_services)
    overall_notes: str = ""
    extraction_warnings: List[str] = Field(default_factory=list)


# --------------------------------------------------------------------------
# User-supplied project inputs (technical details, not read from drawing)
# --------------------------------------------------------------------------


# Per-unit-system defaults for the ProjectInputs fields whose natural value
# differs between SI and FPS (grade labels, wall/plaster thickness). FPS is
# the default system (Pakistani practice). Kept here (not imported from
# engineering/ or utils/) so this module stays dependency-free.
_UNIT_SYSTEM_DEFAULTS = {
    "SI": {
        "concrete_grade_footing": "M20",
        "concrete_grade_column": "M20",
        "concrete_grade_beam": "M20",
        "concrete_grade_slab": "M20",
        "pcc_grade": "M10",
        "steel_grade": "Fe415",
        "wall_thickness_mm": 230,
        "plaster_thickness_internal_mm": 12,
        "plaster_thickness_external_mm": 18,
    },
    "FPS": {
        "concrete_grade_footing": "3000 psi",
        "concrete_grade_column": "3000 psi",
        "concrete_grade_beam": "3000 psi",
        "concrete_grade_slab": "3000 psi",
        "pcc_grade": "1500 psi",
        "steel_grade": "Grade 60 (60,000 psi)",
        "wall_thickness_mm": 228.6,  # 9"
        "plaster_thickness_internal_mm": 12.7,  # 1/2"
        "plaster_thickness_external_mm": 19.05,  # 3/4"
    },
}
# Index-aligned grade lists (mirror engineering/rules.py *_GRADE_OPTIONS).
_GRADE_LISTS = {
    "concrete": {"SI": ["M10", "M15", "M20", "M25"], "FPS": ["1500 psi", "2200 psi", "3000 psi", "3600 psi"]},
    "pcc": {"SI": ["M7.5", "M10", "M15"], "FPS": ["1100 psi", "1500 psi", "2200 psi"]},
    "steel": {"SI": ["Fe250", "Fe415", "Fe500"], "FPS": ["Grade 40 (40,000 psi)", "Grade 60 (60,000 psi)", "Grade 75 (75,000 psi)"]},
}
_GRADE_FIELDS = {
    "concrete_grade_footing": "concrete",
    "concrete_grade_column": "concrete",
    "concrete_grade_beam": "concrete",
    "concrete_grade_slab": "concrete",
    "pcc_grade": "pcc",
    "steel_grade": "steel",
}


class ProjectInputs(BaseModel):
    project_name: str = "Untitled Project"
    client_name: str = ""
    location: str = ""
    soil_type: str = "Ordinary soil"  # Soft/Ordinary/Hard/Murrum/Rock
    # Grade / thickness defaults depend on unit_system - see
    # _UNIT_SYSTEM_DEFAULTS and _apply_unit_system_defaults below.
    concrete_grade_footing: str = "3000 psi"
    concrete_grade_column: str = "3000 psi"
    concrete_grade_beam: str = "3000 psi"
    concrete_grade_slab: str = "3000 psi"
    pcc_grade: str = "1500 psi"
    steel_grade: str = "Grade 60 (60,000 psi)"  # ~ Fe415 (see engineering/rules.py STEEL_GRADE_OPTIONS)
    wall_material: str = "Burnt clay brick (modular 190x90x90mm)"
    wall_thickness_mm: Union[int, float] = 228.6
    plaster_thickness_internal_mm: Union[int, float] = 12.7
    plaster_thickness_external_mm: Union[int, float] = 19.05
    finish_level: str = "Standard"  # Basic | Standard | Premium
    include_flooring: bool = True
    include_waterproofing: bool = True
    include_painting: bool = True
    include_dpc: bool = True
    include_anti_termite: bool = True
    include_mep: bool = True  # electrical, plumbing & sanitary, kitchen, external water/drainage
    include_staircase: bool = True
    include_parapet: bool = True
    include_roof_treatment: bool = True  # roof insulation (mud fill) + brick/tuff tiles
    contingency_pct: float = 5.0
    currency: str = "PKR"
    unit_system: str = UnitSystem.FPS.value  # "FPS" (default, Pakistani practice) or "SI" - see UnitSystem
    # Plot (5-10 marla scope). plot_marla None = not specified (generic defaults).
    plot_marla: Optional[float] = None
    marla_sqft: float = 225.0  # 225 society/LDA standard, 272.25 traditional
    plot_width_ft: Optional[float] = None  # frontage; None = typical for the plot size
    plot_storeys: int = 2  # used by the plot template (1 = single storey, 2 = G+1, 3 = G+2)

    @model_validator(mode="before")
    @classmethod
    def _apply_unit_system_defaults(cls, data: Any) -> Any:
        """Fields not supplied get the defaults of the chosen unit system
        (so ProjectInputs(unit_system="SI") is exactly the metric setup and
        ProjectInputs() the FPS one), and grade labels given in the other
        system are mapped to their equivalent (e.g. "M20" -> "3000 psi")."""
        if not isinstance(data, dict):
            return data
        data = dict(data)
        us = data.get("unit_system", UnitSystem.FPS.value)
        if us not in _UNIT_SYSTEM_DEFAULTS:
            return data
        for field, default in _UNIT_SYSTEM_DEFAULTS[us].items():
            if field not in data:
                data[field] = default
        for field, kind in _GRADE_FIELDS.items():
            label = data.get(field)
            lists = _GRADE_LISTS[kind]
            for other_us, lst in lists.items():
                if other_us != us and label in lst:
                    data[field] = lists[us][lst.index(label)]
        return data


class EngineeringAssumptions(BaseModel):
    """User-editable thumb rules and key default dimensions (Step 3,
    "Engineering assumptions" panel). Defaults mirror engineering/rules.py.
    Steel values are the kg of reinforcement per m3 of concrete applied to
    each member type (before the steel-grade quantity factor)."""

    steel_kg_per_m3_footing: float = 80.0
    steel_kg_per_m3_column: float = 170.0
    steel_kg_per_m3_beam: float = 135.0
    steel_kg_per_m3_slab: float = 85.0
    steel_kg_per_m3_stair: float = 100.0
    steel_kg_per_m3_lintel: float = 80.0
    pcc_thickness_m: float = 0.075
    plinth_height_m: float = 0.6
    excavation_working_space_m: float = 0.15
    parapet_height_m: float = 0.9

    @classmethod
    def for_unit_system(cls, unit_system: str) -> "EngineeringAssumptions":
        """Defaults for a unit system: SI keeps the metric values above;
        FPS uses round feet-inch values (3" PCC, 2'-0" plinth, 6" working
        space, 3'-0" parapet) - mirrors engineering/rules.py
        ASSUMPTION_DIMENSION_DEFAULTS."""
        if unit_system == UnitSystem.FPS.value:
            inch = 0.0254
            return cls(
                pcc_thickness_m=3 * inch,
                plinth_height_m=24 * inch,
                excavation_working_space_m=6 * inch,
                parapet_height_m=36 * inch,
            )
        return cls()


class WastageFactors(BaseModel):
    concrete_pct: float = 5.0
    steel_pct: float = 3.0
    brick_block_pct: float = 5.0
    plaster_pct: float = 10.0
    formwork_pct: float = 5.0
    flooring_pct: float = 5.0
    paint_pct: float = 5.0
    misc_pct: float = 5.0


# --------------------------------------------------------------------------
# Rates
# --------------------------------------------------------------------------


class MaterialRate(BaseModel):
    item_code: str
    description: str
    unit: str
    rate: float
    category: str


# --------------------------------------------------------------------------
# MTO / BOQ line items
# --------------------------------------------------------------------------


class QuantityLineItem(BaseModel):
    item_code: str
    description: str
    category: str
    unit: str
    quantity: float
    confidence: ConfidenceLevel
    formula: str
    inputs_used: Dict[str, float] = Field(default_factory=dict)
    assumptions: List[str] = Field(default_factory=list)
    # True for a derived procurement-reference line (e.g. the cement/sand/
    # aggregate that make up a concrete pour already priced as one composite
    # m3 rate) - its cost is already counted in `parent_item_code`'s BOQ
    # line, so generate_boq() must NOT create a separate priced line for it
    # (that would double-count the cost). Still shown in the MTO (Step 4)
    # because that's exactly the quantity someone needs to go buy cement/
    # sand/aggregate. See mto_boq/boq_generator.py and engineering/
    # calculations.py:concrete_material_breakdown()/mortar_material_breakdown().
    informational: bool = False
    parent_item_code: str = ""


class BOQLineItem(BaseModel):
    item_code: str
    description: str
    category: str
    unit: str
    quantity: float
    wastage_pct: float
    quantity_with_wastage: float
    rate: float
    amount: float
    confidence: ConfidenceLevel
    remarks: str = ""


class CostSummary(BaseModel):
    subtotal: float
    contingency_pct: float
    contingency_amount: float
    grand_total: float
    currency: str = "PKR"


class ProjectResult(BaseModel):
    project_inputs: ProjectInputs
    extracted_params: ExtractedBuildingParams
    mto_items: List[QuantityLineItem]
    boq_items: List[BOQLineItem]
    cost_summary: CostSummary
    generated_at: str = Field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))
