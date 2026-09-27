"""
Plausibility checks for the take-off inputs (5-10 marla houses, FPS units).

Each check returns an Issue with severity:
  "error"   - the value is impossible (negative count, zero floor height ...);
              the take-off is blocked until it is fixed
  "warning" - unusual for a 5-10 marla house; allowed, but shown to the user
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from detailed_mto.model import DetailedProject

# key -> (hard_min, hard_max, typical_min, typical_max); None = no limit
RANGES: Dict[str, Tuple[Optional[float], Optional[float], Optional[float], Optional[float]]] = {
    "H_FLOOR": (7.0, 20.0, 9.5, 13.5),
    "H_PLINTH": (0.0, 6.0, 0.5, 3.0),
    "H_MUMTY": (6.0, 14.0, 7.5, 10.0),
    "H_PARAPET": (0.0, 6.0, 2.0, 4.0),
    "H_TOTAL": (7.0, 60.0, 10.0, 40.0),
    "T_SLAB_IN": (3.0, 12.0, 4.5, 7.0),
    "FDN_DEPTH": (1.0, 12.0, 2.5, 6.0),
    "PCC_T": (0.1, 2.0, 0.25, 0.75),
    "PCC_W_9": (0.75, 8.0, 1.5, 4.0),
    "PCC_W_45": (0.5, 6.0, 1.0, 2.5),
    "PLOT_W": (0.0, 200.0, 20.0, 70.0),
    "PLOT_D": (0.0, 300.0, 30.0, 100.0),
    "ROOF_AREA": (0.0, 20000.0, 400.0, 3500.0),
    "ROOF_PERIM": (0.0, 2000.0, 60.0, 350.0),
    "EXT_EXPOSED_FRAC": (0.0, 1.0, 0.3, 1.0),
    "P_WORKSPACE": (0.0, 3.0, 0.25, 1.0),
    "COL_B_IN": (0.0, 36.0, 9.0, 24.0),
    "COL_D_IN": (0.0, 48.0, 9.0, 30.0),
    "BEAM_B_IN": (0.0, 36.0, 9.0, 18.0),
    "BEAM_D_IN": (0.0, 48.0, 12.0, 30.0),
    "BAND_D_IN": (0.0, 18.0, 4.0, 9.0),
    "STAIR_W": (0.0, 10.0, 3.0, 5.0),
    "UGT_D": (0.0, 15.0, 3.0, 8.0),
    "SEP_D": (0.0, 15.0, 3.0, 8.0),
    "TANK_WALL_IN": (3.0, 18.0, 4.5, 9.0),
    "TANK_SLAB_IN": (3.0, 12.0, 4.0, 6.0),
    "N_WC": (0, 30, 1, 8), "N_VANITY": (0, 30, 1, 8), "N_SHOWER": (0, 30, 1, 8), "N_BATH": (0, 30, 1, 8),
    "N_FT": (0, 80, 2, 25), "N_KSINK": (0, 10, 1, 3), "N_MH": (0, 20, 1, 6), "N_GT": (0, 10, 0, 4),
    "N_RWP": (0, 30, 1, 10), "N_GAS": (0, 30, 0, 10), "N_GEYSER": (0, 10, 0, 4),
    "N_LIGHT": (0, 600, 20, 200), "N_FAN": (0, 100, 2, 25), "N_SK13": (0, 400, 10, 120), "N_PW15": (0, 100, 0, 30),
    "N_AC": (0, 40, 0, 15), "N_LV": (0, 150, 0, 40), "N_EXH": (0, 30, 0, 10), "N_DB": (0, 10, 1, 4), "N_EARTH": (0, 10, 1, 4),
    "COUNTER_LEN": (0, 200, 6, 60), "WARDROBE_AREA": (0, 2000, 0, 600), "BOUNDARY_LEN": (0, 1500, 0, 400),
    "SEWER_LEN": (0, 1000, 10, 150), "PAVING_AREA": (0, 5000, 0, 1500),
}
COUNT_PREFIX = "N_"
MAX_ROOM_SIDE_FT = 60.0
MAX_OPENING_W_FT, MAX_OPENING_H_FT = 20.0, 14.0


@dataclass
class Issue:
    severity: str  # error | warning
    key: str
    label: str
    message: str


def validate_project(p: DetailedProject) -> List[Issue]:
    issues: List[Issue] = []
    for key, prm in p.params.items():
        v = prm.value
        if v != v:  # NaN
            issues.append(Issue("error", key, prm.label, "is empty - enter a number"))
            continue
        if v < 0:
            issues.append(Issue("error", key, prm.label, f"= {v:,.2f} {prm.unit} - cannot be negative"))
            continue
        rng = RANGES.get(key)
        if rng is None:
            continue
        lo, hi, tlo, thi = rng
        if (lo is not None and v < lo) or (hi is not None and v > hi):
            issues.append(Issue("error", key, prm.label,
                                f"= {v:,.2f} {prm.unit} is not possible for a house (allowed {lo:g} - {hi:g})"))
        elif (tlo is not None and v < tlo) or (thi is not None and v > thi):
            issues.append(Issue("warning", key, prm.label,
                                f"= {v:,.2f} {prm.unit} is unusual for a 5-10 marla house (typical {tlo:g} - {thi:g}) - please confirm"))
        if key.startswith(COUNT_PREFIX) and abs(v - round(v)) > 1e-6:
            issues.append(Issue("warning", key, prm.label, f"= {v:,.2f} should be a whole number"))
    for r in p.rooms:
        if r.length_ft <= 0 or r.width_ft <= 0:
            issues.append(Issue("error", "ROOM", f"Room {r.name}", "has a zero or negative size"))
        elif r.length_ft > MAX_ROOM_SIDE_FT or r.width_ft > MAX_ROOM_SIDE_FT:
            issues.append(Issue("warning", "ROOM", f"Room {r.name}",
                                f"is {r.length_ft:g}' x {r.width_ft:g}' - larger than expected, check the units (feet)"))
    for o in p.openings:
        if o.qty < 0 or o.width_ft < 0 or o.height_ft < 0:
            issues.append(Issue("error", "OPENING", f"{o.kind.title()} {o.name}", "has a negative size or quantity"))
        elif o.qty > 0 and (o.width_ft <= 0 or o.height_ft <= 0):
            issues.append(Issue("error", "OPENING", f"{o.kind.title()} {o.name}", "has a zero width or height"))
        elif o.width_ft > MAX_OPENING_W_FT or o.height_ft > MAX_OPENING_H_FT:
            issues.append(Issue("warning", "OPENING", f"{o.kind.title()} {o.name}",
                                f"is {o.width_ft:g}' x {o.height_ft:g}' - larger than expected, check the units (feet)"))
    covered = sum(f.covered_sft for f in p.storeys)
    if p.storeys and covered <= 0:
        issues.append(Issue("error", "FLOORS", "Covered area", "is zero - the drawings/plot size gave no floor area"))
    top = p.storeys[-1] if p.storeys else None
    if top is not None and p.v("ROOF_AREA") > 2.5 * max(top.covered_sft, 1):
        issues.append(Issue("warning", "ROOF_AREA", p.params["ROOF_AREA"].label,
                            f"= {p.v('ROOF_AREA'):,.0f} sft is much larger than the top floor ({top.covered_sft:,.0f} sft)"))
    return issues


def errors(issues: List[Issue]) -> List[Issue]:
    return [i for i in issues if i.severity == "error"]
