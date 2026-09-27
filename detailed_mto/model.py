"""
Project model for the Detailed MTO.

Everything is stored in FPS units (ft, sft, cft, in where stated) because
the Master Material Database coefficients are per FPS work-item unit.
Every value carries provenance (source + confidence) so the export can show
what was READ from the drawings, what was DERIVED and what was ASSUMED.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional

# Confidence labels used throughout the detailed MTO
HIGH, MEDIUM, LOW, ASSUMED, USER = "High", "Medium", "Low", "Assumed", "User"
CONF_ORDER = {USER: 5, HIGH: 4, MEDIUM: 3, LOW: 2, ASSUMED: 1}


def worst(*levels: str) -> str:
    levels = [lv for lv in levels if lv]
    if not levels:
        return ASSUMED
    return min(levels, key=lambda lv: CONF_ORDER.get(lv, 0))


@dataclass
class Param:
    key: str
    label: str
    value: float
    unit: str
    group: str
    source: str = "Default"
    confidence: str = ASSUMED
    note: str = ""


@dataclass
class Room:
    floor: str
    name: str
    room_type: str  # a Room_Finish_Defaults room type
    length_ft: float
    width_ft: float
    source: str = ""
    confidence: str = HIGH

    @property
    def area(self) -> float:
        return self.length_ft * self.width_ft

    @property
    def perimeter(self) -> float:
        return 2 * (self.length_ft + self.width_ft)

    @property
    def is_wet(self) -> bool:
        return self.room_type in ("Bathroom", "Kitchen", "Laundry")

    @property
    def is_open(self) -> bool:
        return self.room_type in ("Porch / car porch", "Terrace / balcony", "Planter / lawn")


@dataclass
class OpeningGroup:
    kind: str  # door | window | ventilator
    name: str
    width_ft: float
    height_ft: float
    qty: int
    leaves: int = 1
    chogath_in: float = 5.0
    external: bool = False
    source: str = ""
    confidence: str = ASSUMED

    @property
    def area_each(self) -> float:
        return self.width_ft * self.height_ft

    @property
    def area_total(self) -> float:
        return self.area_each * self.qty


@dataclass
class Floor:
    key: str  # ground | first | second | roof
    name: str
    covered_sft: float
    ext_perimeter_ft: float
    wall9_len_ft: float
    wall45_len_ft: float
    storey_height_ft: float
    is_mumty: bool = False
    source: str = ""
    confidence: str = MEDIUM


@dataclass
class Options:
    scope: str = "Complete Project"
    finish_tier: str = "Standard"  # Economy | Standard | Premium
    roof_system: str = "Traditional"  # Traditional (bitumen + earth + brick tiles) | Insulated (EPS/XPS + membrane)
    masonry: str = "Brick"  # Brick | Block
    gas_source: str = "SNGPL"  # SNGPL | LPG | None
    include_false_ceiling: bool = True
    include_rwh: bool = True
    include_options: bool = False  # also quantify Optional/Premium/Alternative materials
    seismic_bands: bool = True
    rcc_mix: str = "MX_RCC124"  # structural RCC nominal mix: MX_RCC124 (1:2:4, drawing spec) | MX_RCC1153 (1:1.5:3)


@dataclass
class DetailedProject:
    project_name: str = "Untitled Project"
    client: str = ""
    location: str = ""
    params: Dict[str, Param] = field(default_factory=dict)
    floors: List[Floor] = field(default_factory=list)
    rooms: List[Room] = field(default_factory=list)
    openings: List[OpeningGroup] = field(default_factory=list)
    options: Options = field(default_factory=Options)
    assumptions: List[str] = field(default_factory=list)
    conflicts: List[str] = field(default_factory=list)
    # user edits {param_key: value}; applied when the parameter is SET, so every value
    # derived from it afterwards (wall heights, building height, sewer length ...) follows
    overrides: Dict[str, float] = field(default_factory=dict)
    # "cad" = vector drawings were read; "scanned" = drawings uploaded but unreadable
    # (images / scans); "none" = no drawings uploaded (plot template / manual values)
    drawing_mode: str = "cad"

    # --- param helpers ---------------------------------------------------
    def set(self, key, label, value, unit, group, source="Default", confidence=ASSUMED, note="") -> None:
        if key in self.overrides:
            try:
                v = float(self.overrides[key])
                self.params[key] = Param(key, label, v, unit, group, "User input", USER,
                                         f"Edited by user (drawing/default value was {float(value or 0.0):,.2f})")
                return
            except (TypeError, ValueError):
                pass
        self.params[key] = Param(key, label, float(value or 0.0), unit, group, source, confidence, note)

    def v(self, key: str, default: float = 0.0) -> float:
        p = self.params.get(key)
        return p.value if p is not None else default

    def conf(self, key: str) -> str:
        p = self.params.get(key)
        return p.confidence if p is not None else ASSUMED

    def override(self, key: str, value: float, note: str = "Edited by user") -> None:
        p = self.params.get(key)
        if p is None:
            return
        p.value = float(value)
        p.source = "User input"
        p.confidence = USER
        p.note = note

    # --- geometry helpers --------------------------------------------------
    @property
    def storeys(self) -> List[Floor]:
        return [f for f in self.floors if not f.is_mumty]

    @property
    def mumty(self) -> Optional[Floor]:
        for f in self.floors:
            if f.is_mumty:
                return f
        return None

    def rooms_on(self, floor_key: str) -> List[Room]:
        return [r for r in self.rooms if r.floor == floor_key]

    def rooms_of(self, *types: str) -> List[Room]:
        return [r for r in self.rooms if r.room_type in types]

    def doors(self) -> List[OpeningGroup]:
        return [o for o in self.openings if o.kind == "door"]

    def windows(self) -> List[OpeningGroup]:
        return [o for o in self.openings if o.kind in ("window", "ventilator")]
