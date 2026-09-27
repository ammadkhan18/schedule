"""
Direct calculators for materials whose Formula_Key in the Master Database is
NOT 'RECIPE' (counts, lump sums, areas that are not tied to a work item).

Each function returns (net_qty, confidence, calculation text) or None when
the quantity genuinely needs a user input (the export then lists the
material as 'Needs input' with the required inputs from the database).
"""
from __future__ import annotations

import math
from typing import Callable, Dict, Optional, Tuple

from detailed_mto.model import ASSUMED, HIGH, MEDIUM, DetailedProject

Result = Optional[Tuple[float, str, str]]
DIRECT: Dict[str, Callable[[DetailedProject, object, dict], Result]] = {}


def d(*ids):
    def deco(fn):
        for i in ids:
            DIRECT[i] = fn
        return fn
    return deco


def _q(wq, wi):
    x = wq.get(wi)
    return x.qty if x is not None else 0.0


def _ls(note="1 lump sum"):
    return lambda p, kb, wq: (1.0, MEDIUM, note)


for _mid in ("PRE-002", "PRE-003", "PRE-004", "PRE-007", "PNT-011", "PWS-019", "ELE-039", "MSC-006", "MSC-007", "MSC-008",
             "MSC-003", "GAS-005", "EW-005", "ELE-040", "SOL-001", "SOL-003"):
    DIRECT[_mid] = _ls()


@d("PRE-001")
def _(p, kb, wq):
    if not p.v("PLOT_W"):
        return None
    return p.v("PLOT_W"), ASSUMED, "open frontage = plot width"


@d("PRE-005")
def _(p, kb, wq):
    return 1.0, MEDIUM, "1 borehole / SBC test per plot"


@d("PRE-006")
def _(p, kb, wq):
    pours = 2 + len(p.floors) + 2  # foundation, each slab, stairs, tanks
    return pours * 3.0, ASSUMED, f"~{pours} pours x 3 cubes"


@d("EW-006")
def _(p, kb, wq):
    return p.v("PAVING_AREA") * 3 / 12 * 1.15, ASSUMED, "paving area x 3in x 1.15"


@d("CON-002")
def _(p, kb, wq):
    return None


@d("CON-009")
def _(p, kb, wq):
    v = _q(wq, "WI-CN-07")
    return v, MEDIUM, f"slab RCC {v:,.0f} cft if RMC is used (replaces site-mix cement/sand/crush)"


@d("CON-010")
def _(p, kb, wq):
    rcc = sum(_q(wq, w) for w in ("WI-CN-03", "WI-CN-05", "WI-CN-06", "WI-CN-07", "WI-CN-08"))
    return rcc * 0.18 * 0.35, ASSUMED, "RCC cement bags x 0.35 L/bag"


@d("CON-011")
def _(p, kb, wq):
    return _q(wq, "WI-PL-03") * 0.01, ASSUMED, "ceiling plaster area x 0.01 L/sft"


@d("CON-012", "MSC-002")
def _(p, kb, wq):
    a = sum(f.covered_sft for f in p.floors)
    return a * 1.1 / 3, ASSUMED, "slab area x 1.1 / 3 reuses"


@d("CON-014")
def _(p, kb, wq):
    return None


@d("RBR-004")
def _(p, kb, wq):
    return None


@d("RBR-007")
def _(p, kb, wq):
    s = _q(wq, "WI-RF-01") + _q(wq, "WI-RF-04")
    return s * 0.015, MEDIUM, f"1.5% of footing + slab steel ({s:,.0f} kg)"


@d("RBR-008")
def _(p, kb, wq):
    return None


@d("RBR-009")
def _(p, kb, wq):
    n = p.v("N_MH")
    return n * 2 * 4 * 2.2 * 1.1, ASSUMED, "manhole cover frames 2x2x1/4in angle (2 frames x 8 ft x 2.2 kg/ft per MH)"


@d("RBR-010")
def _(p, kb, wq):
    junctions = 6 * len(p.storeys)
    h = p.storeys[0].storey_height_ft if p.storeys else 11
    return junctions * (h / 4) * 0.61 * 0.56 * len(p.storeys), ASSUMED, f"~{junctions} junctions/floor x dowel every 4 courses"


@d("FRM-007")
def _(p, kb, wq):
    return _q(wq, "WI-PL-02"), MEDIUM, "= external plaster area (sft of scaffold, per hire period)"


@d("MAS-005", "MAS-006", "MAS-010", "MAS-011", "MAS-012")
def _(p, kb, wq):
    return None


@d("MAS-009")
def _(p, kb, wq):
    L = sum(f.wall45_len_ft for f in p.floors)
    h = p.storeys[0].storey_height_ft if p.storeys else 11
    return L * (h / 1 * 0.25) * 0.03, MEDIUM, "4.5in wall length x courses/4 x 0.03 kg/rft"


@d("MAS-014")
def _(p, kb, wq):
    return p.v("ROOF_PERIM") + p.v("BOUNDARY_LEN"), MEDIUM, "parapet + boundary wall length"


@d("WPF-006")
def _(p, kb, wq):
    return p.v("ROOF_AREA") * 0.02 * 2, ASSUMED, "roof area x 0.02 L/sft x 2 coats"


@d("WPF-011")
def _(p, kb, wq):
    tw = p.v("TANK_WALL_IN") / 12
    per = 2 * (p.v("UGT_L") + p.v("UGT_W") + 4 * tw) + p.v("SEPTIC_N") * 2 * (p.v("SEP_L") + p.v("SEP_W") + 4 * tw)
    return per, MEDIUM, "tank base-wall construction joint perimeter"


@d("WPF-012")
def _(p, kb, wq):
    joints = sum(o.qty * 2 * (o.width_ft + o.height_ft) for o in p.windows()) + p.v("ROOF_PERIM")
    return joints / 27.0, MEDIUM, f"{joints:,.0f} rft of joints / 27 rft per tube"


@d("WPF-013")
def _(p, kb, wq):
    return p.v("ROOF_PERIM") + p.v("APRON_LEN"), ASSUMED, "roof screed perimeter + apron joint"


@d("WPF-014")
def _(p, kb, wq):
    per = sum(r.perimeter for r in p.rooms if r.is_wet or r.room_type == "Terrace / balcony")
    return per + p.v("N_FT") * 1, MEDIUM, "wet room perimeters + 1 rft per floor trap"


@d("PLS-002")
def _(p, kb, wq):
    conduit = _q(wq, "WI-EL-01") * 15 + _q(wq, "WI-EL-03") * 20 + _q(wq, "WI-EL-04") * 20
    return conduit * 0.5 + p.v("N_BATH") * 30, ASSUMED, "~50% of wall conduit + 30 rft per bath of concealed pipes"


@d("PLS-003", "PLS-006", "FLR-005", "FLR-013", "FLR-015", "FLR-016", "DWG-012", "DWG-018", "JNR-008", "JNR-010",
   "PNT-006", "PNT-012", "EXT-005", "EXT-010", "EXT-012", "HVC-011", "SOL-002", "PWS-015", "PDR-019", "ELE-003",
   "SAN-012", "RWH-006")
def _(p, kb, wq):
    return None


@d("PLS-004")
def _(p, kb, wq):
    per = sum(r.perimeter for r in p.rooms if r.room_type in ("Drawing room", "Lounge / TV lounge"))
    return per * 0.1, ASSUMED, "cornice length x 0.1 bag/rft"


@d("DWG-003")
def _(p, kb, wq):
    a = sum(o.area_total for o in p.doors() if "BATH" not in o.name.upper() and "MAIN" not in o.name.upper())
    return a, MEDIUM, "internal door shutters (flush alternative)"


@d("DWG-004")
def _(p, kb, wq):
    n = sum(o.qty for o in p.doors() if "BATH" in o.name.upper())
    return n, HIGH if n else ASSUMED, "bath doors from schedule"


@d("DWG-005")
def _(p, kb, wq):
    n = sum(o.qty for o in p.doors() if "MAIN" in o.name.upper())
    return n or 1, HIGH if n else ASSUMED, "main door"


@d("DWG-007")
def _(p, kb, wq):
    return sum(o.qty for o in p.doors() if o.external), MEDIUM, "external doors"


@d("DWG-010")
def _(p, kb, wq):
    return _q(wq, "WI-DW-04"), MEDIUM, "= window area (uPVC alternative)"


@d("DWG-015")
def _(p, kb, wq):
    return sum(o.area_total for o in p.openings if o.kind == "ventilator"), MEDIUM, "bath ventilators"


@d("DWG-017")
def _(p, kb, wq):
    n = sum(o.qty for o in p.doors() if "MUMTY" in o.name.upper())
    return n or (1 if p.mumty else 0), HIGH if n else ASSUMED, "mumty door(s) from schedule"


@d("CLG-003")
def _(p, kb, wq):
    return sum(r.area for r in p.rooms_of("Bathroom")), MEDIUM, "bathroom ceiling area"


@d("CLG-005")
def _(p, kb, wq):
    return sum(r.perimeter for r in p.rooms if r.room_type in ("Drawing room", "Lounge / TV lounge")), ASSUMED, "drawing/lounge perimeters"


@d("CLG-006")
def _(p, kb, wq):
    return p.v("N_BATH"), MEDIUM, "1 per bathroom"


@d("PNT-013")
def _(p, kb, wq):
    return _q(wq, "WI-PT-05") * 0.005, MEDIUM, "wood finish area x 0.005 kg/sft"


@d("JNR-004")
def _(p, kb, wq):
    return p.v("N_VANITY"), p.conf("N_VANITY"), "= VANITY count"


@d("JNR-009")
def _(p, kb, wq):
    return sum(o.qty * (o.width_ft + 1) for o in p.openings if o.kind == "window"), ASSUMED, "window widths + 1 ft"


@d("KIT-007", "KIT-008", "FLS-003")
def _(p, kb, wq):
    return float(len(p.rooms_of("Kitchen"))), MEDIUM, "1 per kitchen"


@d("KIT-010")
def _(p, kb, wq):
    return 1.0, MEDIUM, "'FRIDGE & OVEN' label on FF kitchen"


@d("PWS-003")
def _(p, kb, wq):
    return 40.0 * len(p.storeys), ASSUMED, "40 rft of 32 mm main per floor"


@d("PWS-004")
def _(p, kb, wq):
    return 2 * (p.v("H_TOTAL") + 10) + 30, ASSUMED, "risers CW/HW (building height + 10 ft) + 30 ft roof header"


@d("PWS-007")
def _(p, kb, wq):
    n = 1 * p.v("N_WC") + 2 * p.v("N_VANITY") + 3 * p.v("N_SHOWER") + p.v("N_HF") + 2 * p.v("N_KSINK") + p.v("N_WM")
    return n, MEDIUM, "outlets: WC 1, basin 2, shower 3, HF 1, sink 2, WM 1"


@d("PWS-009")
def _(p, kb, wq):
    return 2 * p.v("N_BATH"), MEDIUM, "2 per bathroom"


@d("PWS-010")
def _(p, kb, wq):
    return 2.0, MEDIUM, "1 per pump (lift + booster)"


@d("PWS-011")
def _(p, kb, wq):
    return 1 + p.v("N_OHT") + 1, MEDIUM, "UG + OH tanks + level controller"


@d("PWS-012", "PWS-013", "PWS-014", "ELE-038", "EXT-009", "ELE-019", "ELE-018")
def _(p, kb, wq):
    return 1.0, MEDIUM, "1 per house"


@d("PWS-016", "SAN-019")
def _(p, kb, wq):
    return p.v("N_OHT"), p.conf("N_OHT"), p.params["N_OHT"].source if "N_OHT" in p.params else ""


@d("PWS-017")
def _(p, kb, wq):
    return 2 * (p.v("H_TOTAL") + 10) + 30, ASSUMED, "exposed HW risers + roof CW header"


@d("PWS-020")
def _(p, kb, wq):
    return 1.0, MEDIUM, "1 at top of riser (legend)"


@d("PWS-021", "SAN-017")
def _(p, kb, wq):
    return 1.0, ASSUMED, "1 per house (option)"


@d("PWS-022", "SAN-016")
def _(p, kb, wq):
    return p.v("N_GEYSER"), p.conf("N_GEYSER"), "1 per floor"


@d("PWS-023")
def _(p, kb, wq):
    return 20.0, ASSUMED, "street to UG tank (default 20 ft)"


@d("PWS-024")
def _(p, kb, wq):
    return 4 * p.v("N_BATH") + 2 * len(p.rooms_of("Kitchen")) + p.v("N_AC"), MEDIUM, "4 per bath stack + 2 per kitchen + 1 per AC"


@d("PDR-008")
def _(p, kb, wq):
    return math.ceil(p.v("SEWER_LEN") / 13.0) + 4, ASSUMED, "sewer length / 13 ft pipe + fittings"


@d("PDR-010")
def _(p, kb, wq):
    return p.v("N_CO"), p.conf("N_CO"), "C.O count"


@d("PDR-013")
def _(p, kb, wq):
    return p.v("N_MH"), p.conf("N_MH"), "manhole count (materials via WI-PB-12)"


@d("PDR-017")
def _(p, kb, wq):
    return 0.0, ASSUMED, "only for manholes deeper than 3 ft (none assumed)"


@d("PDR-018")
def _(p, kb, wq):
    return p.v("N_MH") * 28 * 0.0136, MEDIUM, "28 sft plaster per MH x WP dose"


@d("PDR-023")
def _(p, kb, wq):
    return p.v("SEPTIC_N"), p.conf("SEPTIC_N"), "concrete/steel booked in WI-CN-11 / WI-RF-07"


@d("SAN-003")
def _(p, kb, wq):
    return p.v("N_WC"), MEDIUM, "= WC count (floor-mounted alternative)"


@d("SAN-009")
def _(p, kb, wq):
    return 2 * p.v("N_VANITY") + 2 * p.v("N_KSINK"), MEDIUM, "2 per basin + 2 per sink"


@d("SAN-011")
def _(p, kb, wq):
    return p.v("N_VANITY"), p.conf("N_VANITY"), "1 per basin"


@d("SAN-013")
def _(p, kb, wq):
    return p.v("N_SHOWER"), p.conf("N_SHOWER"), "1 per shower (premium)"


@d("SAN-015")
def _(p, kb, wq):
    n = 1 + len(p.rooms_of("Terrace / balcony")) + len(p.rooms_of("Porch / car porch")) + 1
    return float(n), ASSUMED, "roof + terraces + porch + lawn"


@d("SAN-018")
def _(p, kb, wq):
    return 3.0, MEDIUM, "booster + lift + recirculation (drawing legend)"


@d("GAS-004")
def _(p, kb, wq):
    return _q(wq, "WI-GS-01") * 25 * 0.3 * 1.2, ASSUMED, "30% of gas pipe buried x 1.2"


@d("GAS-006")
def _(p, kb, wq):
    return float(len(p.rooms_of("Kitchen"))) if p.options.gas_source == "LPG" else 0.0, MEDIUM, "1 set per kitchen if LPG"


@d("ELE-010")
def _(p, kb, wq):
    return 20 * 1.1, ASSUMED, "meter to main DB 20 m x 1.1"


@d("ELE-015")
def _(p, kb, wq):
    circuits = math.ceil(p.v("N_LIGHT") / 8) + math.ceil(p.v("N_SK13") / 6) + p.v("N_PW15") + p.v("N_AC") + p.v("N_GEYSER") + 2
    return math.ceil(circuits * 1.2), ASSUMED, f"{circuits:.0f} circuits (8 lights / 6 sockets per circuit, 1 per AC/15A) + 20% spare"


@d("ELE-016")
def _(p, kb, wq):
    return p.v("N_DB"), p.conf("N_DB"), "1 per DB"


@d("ELE-024")
def _(p, kb, wq):
    return 2.0 * len(p.rooms_of("Kitchen")), MEDIUM, "cooker + fridge per kitchen"


@d("ELE-025")
def _(p, kb, wq):
    return p.v("N_GEYSER"), p.conf("N_GEYSER"), "1 per water heater"


@d("ELE-027")
def _(p, kb, wq):
    return 1.0, MEDIUM, "1 at gate"


@d("ELE-029")
def _(p, kb, wq):
    n = sum(1 for _ in p.rooms if _.room_type in ("Bedroom", "Lounge / TV lounge", "Drawing room"))
    return n * 12.0, ASSUMED, "1 data point per bedroom/lounge x 12 m"


@d("ELE-030")
def _(p, kb, wq):
    return 1.0, MEDIUM, "1 set (outdoor + indoor units)"


@d("ELE-031")
def _(p, kb, wq):
    return 4.0, ASSUMED, "4 camera points"


@d("ELE-032")
def _(p, kb, wq):
    return p.v("N_LIGHT") * 0.6, ASSUMED, "60% of light points"


@d("ELE-033")
def _(p, kb, wq):
    return p.v("N_LIGHT") * 0.15, ASSUMED, "15% of light points (utility/stair)"


@d("ELE-034")
def _(p, kb, wq):
    return p.v("N_LIGHT") * 0.15, ASSUMED, "15% of light points (decorative, provisional)"


@d("ELE-035")
def _(p, kb, wq):
    return 4.0, ASSUMED, "external/gate lights"


@d("ELE-036", "ELE-037")
def _(p, kb, wq):
    return p.v("N_FAN"), p.conf("N_FAN"), "= ceiling fan points"


@d("HVC-001")
def _(p, kb, wq):
    return p.v("N_AC"), p.conf("N_AC"), "= AC points"


@d("HVC-009")
def _(p, kb, wq):
    return 8.0 * len(p.rooms_of("Kitchen")), ASSUMED, "8 rft per kitchen"


@d("HVC-010")
def _(p, kb, wq):
    return p.v("N_EXH"), p.conf("N_EXH"), "= exhaust fans"


@d("HVC-012")
def _(p, kb, wq):
    return p.v("ROOF_AREA"), p.conf("ROOF_AREA"), "roof area (insulated roof option)"


@d("HVC-013")
def _(p, kb, wq):
    return float(len(p.rooms_of("Bedroom", "Lounge / TV lounge"))), ASSUMED, "1 per bedroom/lounge (owner option)"


@d("EXT-002")
def _(p, kb, wq):
    return 2 * math.sqrt(max(p.v("PAVING_AREA"), 0)), ASSUMED, "open edges of paved area"


@d("EXT-004")
def _(p, kb, wq):
    return 20 * 1.5, ASSUMED, "planter ~20 sft x 1.5 ft soil (PLANTER AREA label)"


@d("EXT-006")
def _(p, kb, wq):
    return p.v("APRON_LEN") * 2.5, ASSUMED, "apron length x 2.5 ft"


@d("EXT-007")
def _(p, kb, wq):
    return p.v("BOUNDARY_LEN"), p.conf("BOUNDARY_LEN"), "boundary length (materials via WI-EX-01)"


@d("EXT-008")
def _(p, kb, wq):
    return _q(wq, "WI-DW-07"), p.conf("GATE_W"), "= gate area (steel via WI-DW-07)"


@d("EXT-011")
def _(p, kb, wq):
    return p.v("RAIL_LEN"), p.conf("RAIL_LEN"), "railing length"


@d("RWH-005")
def _(p, kb, wq):
    return p.v("RWH_N") * kb.k("K_RWH_DEPTH"), ASSUMED, "wells x recharge depth"


@d("FLS-001")
def _(p, kb, wq):
    return float(len(p.rooms_of("Kitchen")) + len(p.storeys)), MEDIUM, "1 per kitchen + 1 per floor"


@d("FLS-002")
def _(p, kb, wq):
    return float(len(p.storeys)), MEDIUM, "1 per floor"


@d("MSC-004")
def _(p, kb, wq):
    return _q(wq, "WI-EW-07"), MEDIUM, "= anti-termite treated area (chemical in EW-004)"


@d("MSC-005")
def _(p, kb, wq):
    return (_q(wq, "WI-FL-01") + _q(wq, "WI-FL-02") + _q(wq, "WI-FL-04")) * 0.5, ASSUMED, "finished floor x 0.5"


@d("MSC-009")
def _(p, kb, wq):
    return math.ceil(_q(wq, "WI-EW-09") / kb.k("K_TROLLEY_CFT")) + 5, MEDIUM, "surplus earth / trolley + 5 debris trips"
