"""
Work-item quantity calculators - one small function per WI_ID of the Master
Database (Work_Items sheet). Each returns a WIQty with the quantity in the
work item's FPS unit, a confidence and a human-readable calculation string
(written to the export for traceability).

Materials are NOT computed here: the engine multiplies these quantities by
the Recipe coefficients from the database.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Callable, Dict, List

from detailed_mto.model import ASSUMED, HIGH, MEDIUM, DetailedProject, worst
from knowledge.loader import KnowledgeBase


@dataclass
class WIQty:
    qty: float
    confidence: str
    calc: str
    selected: bool = True  # False = alternative/optional work item not chosen
    inputs: Dict[str, float] = field(default_factory=dict)


REGISTRY: Dict[str, Callable[[DetailedProject, KnowledgeBase, Dict[str, "WIQty"]], WIQty]] = {}


def wi(wi_id: str):
    def deco(fn):
        REGISTRY[wi_id] = fn
        return fn
    return deco


def _f(x: float) -> str:
    return f"{x:,.2f}".rstrip("0").rstrip(".")


# ------------------------------------------------------------------ helpers
def slab_t(p):
    return p.v("T_SLAB_IN") / 12.0


def wall_h(p, fl):
    return max(fl.storey_height_ft - slab_t(p), 0)


def openings_area(p, external_only=False):
    tot = 0.0
    for o in p.openings:
        if external_only and not o.external:
            continue
        tot += o.area_total
    return tot


def openings_in_9(p):
    # external windows & doors sit in 9in walls; internal doors split 50/50 between 9in and 4.5in walls
    ext = sum(o.area_total for o in p.openings if o.external)
    internal = sum(o.area_total for o in p.openings if not o.external)
    return ext + 0.5 * internal, 0.5 * internal


def gf_floor_area(p):
    gf = p.storeys[0] if p.storeys else None
    rooms = [r for r in p.rooms if gf and r.floor == gf.key and r.room_type not in ("Planter / lawn",)]
    area = sum(r.area for r in rooms)
    if not area and gf:
        area = gf.covered_sft * 0.85
    return area, (HIGH if rooms else ASSUMED)


def stair_geometry(p):
    lifts = p.v("STAIR_LIFTS")
    h = p.storeys[0].storey_height_ft if p.storeys else 11.5
    risers = math.ceil(h * 12 / 6.75)
    tread = 10 / 12.0
    width = p.v("STAIR_W")
    going = (risers / 2) * tread
    slope = math.sqrt(going ** 2 + (h / 2) ** 2)
    return lifts, risers, tread, width, slope


def tank_concrete(L, W, D, t_wall_in, t_slab_in):
    tw = t_wall_in / 12
    ts = t_slab_in / 12
    walls = 2 * (L + W + 2 * tw) * D * tw
    base = (L + 2 * tw) * (W + 2 * tw) * tw
    cover = (L + 2 * tw) * (W + 2 * tw) * ts
    return walls + base + cover


# ============================== PRELIMINARIES ===============================
@wi("WI-PRE-01")
def _pre01(p, kb, q):
    return WIQty(1, HIGH, "1 lump sum per project")


@wi("WI-PRE-02")
def _pre02(p, kb, q):  # computed after cement is known - engine overrides with cement bags
    return WIQty(0, MEDIUM, "total cement bags x K_WATER_L_BAG / 4.546 (computed after all cement is summed)")


# ================================ EARTHWORK =================================
@wi("WI-EW-01")
def _ew01(p, kb, q):
    ws = p.v("P_WORKSPACE")
    d = p.v("FDN_DEPTH")
    a = p.v("STRIP_LEN_9") * (p.v("PCC_W_9") + 2 * ws) * d
    b = p.v("STRIP_LEN_45") * (p.v("PCC_W_45") + 2 * ws) * d
    return WIQty(a + b, worst(p.conf("STRIP_LEN_9"), p.conf("PCC_W_9"), p.conf("FDN_DEPTH")),
                 f"9in: {_f(p.v('STRIP_LEN_9'))} rft x ({_f(p.v('PCC_W_9'))}+2x{_f(ws)}) x {_f(d)} ft + "
                 f"4.5in: {_f(p.v('STRIP_LEN_45'))} rft x ({_f(p.v('PCC_W_45'))}+2x{_f(ws)}) x {_f(d)} ft")


@wi("WI-EW-02")
def _ew02(p, kb, q):
    ws = p.v("P_WORKSPACE")
    n, L, B = p.v("FTG_N"), p.v("FTG_L"), p.v("FTG_B")
    d = p.v("FDN_DEPTH")
    return WIQty(n * (L + 2 * ws) * (B + 2 * ws) * d, worst(p.conf("FTG_N"), p.conf("FTG_L")),
                 f"{_f(n)} x ({_f(L)}+{_f(2 * ws)}) x ({_f(B)}+{_f(2 * ws)}) x {_f(d)} ft")


@wi("WI-EW-03")
def _ew03(p, kb, q):
    tw = p.v("TANK_WALL_IN") / 12
    ug = (p.v("UGT_L") + 2 * tw + 1) * (p.v("UGT_W") + 2 * tw + 1) * (p.v("UGT_D") + 1.5)
    sep = p.v("SEPTIC_N") * (p.v("SEP_L") + 2 * tw + 1) * (p.v("SEP_W") + 2 * tw + 1) * (p.v("SEP_D") + 1.5)
    mh = p.v("N_MH") * 3.5 * 3.5 * 3.5
    sew = p.v("SEWER_LEN") * 2 * 2.5
    return WIQty(ug + sep + mh + sew, ASSUMED,
                 f"UG tank {_f(ug)} + septic {_f(sep)} + manholes {_f(mh)} + sewer trench {_f(p.v('SEWER_LEN'))} rft x 2 ft x 2.5 ft")


@wi("WI-EW-04")
def _ew04(p, kb, q):
    exc = q["WI-EW-01"].qty + q["WI-EW-02"].qty
    below = q["WI-CN-01"].qty + q["WI-CN-03"].qty + q["WI-MS-01"].qty * 0.6  # ~60% of footing brickwork below NGL
    return WIQty(max(exc - below, 0), MEDIUM, f"excavation {_f(exc)} - PCC/RCC/brick below NGL {_f(below)}")


@wi("WI-EW-05")
def _ew05(p, kb, q):
    area, c = gf_floor_area(p)
    h = p.v("H_PLINTH") - 9 / 12.0
    return WIQty(max(area * h, 0), worst(c, p.conf("H_PLINTH")),
                 f"GF floor area {_f(area)} sft x (plinth {_f(p.v('H_PLINTH'))} - 0.75 ft floor build-up)")


@wi("WI-EW-06")
def _ew06(p, kb, q):
    area, c = gf_floor_area(p)
    return WIQty(area * 3 / 12, c, f"GF floor area {_f(area)} sft x 3 in")


@wi("WI-EW-07")
def _ew07(p, kb, q):
    area, c = gf_floor_area(p)
    trench = (p.v("STRIP_LEN_9") + p.v("STRIP_LEN_45")) * (1.5 + 2 * p.v("FDN_DEPTH"))
    return WIQty(area + trench, worst(c, p.conf("STRIP_LEN_9")), f"GF floor {_f(area)} + trench bottom & sides {_f(trench)} sft")


@wi("WI-EW-08")
def _ew08(p, kb, q):
    area, c = gf_floor_area(p)
    return WIQty(area, c, f"GF floor area {_f(area)} sft")


@wi("WI-EW-09")
def _ew09(p, kb, q):
    surplus = q["WI-EW-01"].qty + q["WI-EW-02"].qty + q["WI-EW-03"].qty - q["WI-EW-04"].qty
    return WIQty(max(surplus, 0) * 1.25, MEDIUM, f"(excavation - backfill) {_f(surplus)} cft x 1.25 bulking")


# ================================= CONCRETE =================================
@wi("WI-CN-01")
def _cn01(p, kb, q):
    t = p.v("PCC_T")
    strip = (p.v("STRIP_LEN_9") * p.v("PCC_W_9") + p.v("STRIP_LEN_45") * p.v("PCC_W_45")) * t
    ftg = p.v("FTG_N") * (p.v("FTG_L") + 1) * (p.v("FTG_B") + 1) * 0.25
    return WIQty(strip + ftg, worst(p.conf("STRIP_LEN_9"), p.conf("PCC_T")),
                 f"strip PCC {_f(strip)} cft (t={_f(t)} ft) + footing blinding {_f(ftg)} cft")


@wi("WI-CN-02")
def _cn02(p, kb, q):
    area, c = gf_floor_area(p)
    return WIQty(area * 3 / 12, c, f"GF floor area {_f(area)} sft x 3 in PCC")


@wi("WI-CN-03")
def _cn03(p, kb, q):
    n, L, B, D = p.v("FTG_N"), p.v("FTG_L"), p.v("FTG_B"), p.v("FTG_D")
    return WIQty(n * L * B * D, worst(p.conf("FTG_N"), p.conf("FTG_L")), f"{_f(n)} x {_f(L)} x {_f(B)} x {_f(D)} ft")


@wi("WI-CN-04")
def _cn04(p, kb, q):
    L = p.v("PB_LEN")
    return WIQty(L * 0.75 * 1.0, p.conf("PB_LEN"), f"{_f(L)} rft x 9in x 12in")


@wi("WI-CN-05")
def _cn05(p, kb, q):
    n, b, d = p.v("COL_N"), p.v("COL_B_IN") / 12, p.v("COL_D_IN") / 12
    h = sum(f.storey_height_ft for f in p.storeys) + p.v("FDN_DEPTH")
    return WIQty(n * b * d * h, worst(p.conf("COL_N"), p.conf("COL_B_IN")),
                 f"{_f(n)} cols x {_f(b * 12)}x{_f(d * 12)} in x {_f(h)} ft (footing top to roof)")


@wi("WI-CN-06")
def _cn06(p, kb, q):
    n, L, b, D = p.v("BEAM_N"), p.v("BEAM_LEN"), p.v("BEAM_B_IN") / 12, p.v("BEAM_D_IN") / 12
    levels = len(p.storeys)
    vol = n * L * b * max(D - slab_t(p), 0) * levels
    return WIQty(vol, worst(p.conf("BEAM_N"), p.conf("BEAM_LEN")),
                 f"{_f(n)} beams x {_f(L)} ft x {_f(b * 12)}in x ({_f(D * 12)}-{_f(p.v('T_SLAB_IN'))})in x {levels} levels")


def slab_areas(p):
    out = []
    st = p.storeys
    for i, f in enumerate(st):
        void = p.v("STAIR_VOID") if i < len(st) - 1 else 0.0
        above = st[i + 1].covered_sft if i + 1 < len(st) else 0.0
        out.append((f"Slab over {f.name}", max(max(f.covered_sft, above) - void, 0)))
    if p.mumty:
        out.append(("Mumty roof slab", p.mumty.covered_sft))
    return out


@wi("WI-CN-07")
def _cn07(p, kb, q):
    parts = slab_areas(p)
    area = sum(a for _, a in parts)
    return WIQty(area * slab_t(p), worst(*(f.confidence for f in p.floors), p.conf("T_SLAB_IN")),
                 " + ".join(f"{n} {_f(a)} sft" for n, a in parts) + f" = {_f(area)} sft x {_f(p.v('T_SLAB_IN'))} in")


@wi("WI-CN-08")
def _cn08(p, kb, q):
    lifts, risers, tread, width, slope = stair_geometry(p)
    waist = 2 * slope * width * 5 / 12
    steps = risers * 0.5 * (6.75 / 12) * tread * width
    landing = 2 * (width * 2 * width) * 5 / 12
    per = waist + steps + landing
    return WIQty(lifts * per, worst(p.conf("STAIR_W"), MEDIUM),
                 f"{_f(lifts)} lifts x (waist {_f(waist)} + steps {_f(steps)} + landings {_f(landing)}) cft; {risers} risers/lift")


@wi("WI-CN-09")
def _cn09(p, kb, q):
    d = p.v("BAND_D_IN") / 12
    band = p.v("BAND_LEN") * 0.75 * d
    # lintels over openings in 4.5in walls + all openings if no bands
    lint = 0.0
    for o in p.openings:
        t = 0.375 if not o.external else 0.75
        if p.v("BAND_LEN") and o.external:
            continue  # external openings are spanned by the band
        lint += o.qty * (o.width_ft + 1.0) * t * 0.5
    return WIQty(band + lint, worst(p.conf("BAND_LEN"), ASSUMED),
                 f"band {_f(p.v('BAND_LEN'))} rft x 9in x {_f(p.v('BAND_D_IN'))}in = {_f(band)} + lintels {_f(lint)} cft")


@wi("WI-CN-10")
def _cn10(p, kb, q):
    L = sum(o.qty * (o.width_ft + 1) for o in p.openings if o.kind == "window")
    return WIQty(L * 1.5 * 0.25, ASSUMED, f"window chajjas {_f(L)} rft x 1'-6\" projection x 3in avg")


@wi("WI-CN-11")
def _cn11(p, kb, q):
    tw, ts = p.v("TANK_WALL_IN"), p.v("TANK_SLAB_IN")
    ug = tank_concrete(p.v("UGT_L"), p.v("UGT_W"), p.v("UGT_D"), tw, ts)
    sep = p.v("SEPTIC_N") * tank_concrete(p.v("SEP_L"), p.v("SEP_W"), p.v("SEP_D"), tw, ts)
    return WIQty(ug + sep, worst(p.conf("UGT_L"), p.conf("UGT_D")),
                 f"UG tank {_f(p.v('UGT_L'))}x{_f(p.v('UGT_W'))}x{_f(p.v('UGT_D'))} ft = {_f(ug)} cft + septic {_f(sep)} cft "
                 f"(walls/base {_f(tw)}in, slab {_f(ts)}in)")


@wi("WI-CN-12")
def _cn12(p, kb, q):
    gf = p.storeys[0] if p.storeys else None
    a = (gf.wall9_len_ft * 0.75 + gf.wall45_len_ft * 0.375) if gf else 0
    return WIQty(a, gf.confidence if gf else ASSUMED, f"GF walls: 9in {_f(gf.wall9_len_ft if gf else 0)} rft x 0.75 + 4.5in "
                                                      f"{_f(gf.wall45_len_ft if gf else 0)} rft x 0.375 ft")


@wi("WI-CN-13")
def _cn13(p, kb, q):
    return WIQty(p.v("APRON_LEN") * 2.5, ASSUMED, f"{_f(p.v('APRON_LEN'))} rft x 2.5 ft apron")


# ============================== REINFORCEMENT ===============================
def _ratio(wi_id, src, kid, label):
    @wi(wi_id)
    def _fn(p, kb, q):
        vol = sum(q[s].qty for s in src)
        k = kb.k(kid)
        return WIQty(vol * k, worst(*(q[s].confidence for s in src), MEDIUM),
                     f"{label} {_f(vol)} cft x {_f(k)} kg/cft ({kid}; replace with BBS when available)")
    return _fn


_ratio("WI-RF-01", ["WI-CN-03"], "K_ST_FTG", "footing RCC")
_ratio("WI-RF-02", ["WI-CN-05"], "K_ST_COL", "column RCC")
_ratio("WI-RF-04", ["WI-CN-07"], "K_ST_SLAB", "slab RCC")
_ratio("WI-RF-05", ["WI-CN-08"], "K_ST_STAIR", "stair RCC")
_ratio("WI-RF-06", ["WI-CN-09", "WI-CN-10"], "K_ST_LINTEL", "lintel/band/chajja RCC")
_ratio("WI-RF-07", ["WI-CN-11"], "K_ST_TANK", "tank RCC")


@wi("WI-RF-03")
def _rf03(p, kb, q):
    b = q["WI-CN-06"].qty * kb.k("K_ST_BEAM")
    pb = q["WI-CN-04"].qty * kb.k("K_ST_PB")
    return WIQty(b + pb, worst(q["WI-CN-06"].confidence, MEDIUM), f"beams {_f(b)} kg + plinth beams {_f(pb)} kg (ratio method)")


# ================================= FORMWORK =================================
@wi("WI-FW-01")
def _fw01(p, kb, q):
    area = sum(a for _, a in slab_areas(p))
    beams = p.v("BEAM_N") * p.v("BEAM_LEN") * 2 * max(p.v("BEAM_D_IN") - p.v("T_SLAB_IN"), 0) / 12 * len(p.storeys)
    edges = sum(f.ext_perimeter_ft for f in p.floors) * slab_t(p)
    return WIQty(area + beams + edges, MEDIUM, f"slab soffit {_f(area)} + beam sides {_f(beams)} + slab edges {_f(edges)} sft")


@wi("WI-FW-02")
def _fw02(p, kb, q):
    col = p.v("COL_N") * 2 * (p.v("COL_B_IN") + p.v("COL_D_IN")) / 12 * (sum(f.storey_height_ft for f in p.storeys))
    ftg = p.v("FTG_N") * 2 * (p.v("FTG_L") + p.v("FTG_B")) * p.v("FTG_D")
    band = p.v("BAND_LEN") * 2 * p.v("BAND_D_IN") / 12 + sum(o.qty * (o.width_ft + 1) * 1.5 for o in p.openings if o.kind == "window")
    lifts, risers, tread, width, slope = stair_geometry(p)
    stair = lifts * (2 * slope * width + risers * width * 6.75 / 12 + 2 * width * 2 * width)
    tw = p.v("TANK_WALL_IN") / 12
    tank = 2 * 2 * (p.v("UGT_L") + p.v("UGT_W") + 2 * tw) * p.v("UGT_D") + p.v("SEPTIC_N") * 2 * 2 * (p.v("SEP_L") + p.v("SEP_W") + 2 * tw) * p.v("SEP_D")
    return WIQty(col + ftg + band + stair + tank, MEDIUM,
                 f"columns {_f(col)} + footings {_f(ftg)} + bands/lintels/chajjas {_f(band)} + stairs {_f(stair)} + tanks {_f(tank)} sft")


# ================================= MASONRY ==================================
@wi("WI-MS-01")
def _ms01(p, kb, q):
    h = max(p.v("FDN_DEPTH") - p.v("PCC_T") + p.v("H_PLINTH"), 0)
    w9 = (p.v("PCC_W_9") - 0.5 + 0.75) / 2
    w45 = (p.v("PCC_W_45") - 0.5 + 0.375) / 2
    vol = p.v("STRIP_LEN_9") * w9 * h + p.v("STRIP_LEN_45") * w45 * h
    return WIQty(vol, worst(p.conf("STRIP_LEN_9"), p.conf("FDN_DEPTH"), MEDIUM),
                 f"stepped footing to DPC, height {_f(h)} ft; avg width 9in walls {_f(w9)} ft x {_f(p.v('STRIP_LEN_9'))} rft + "
                 f"4.5in walls {_f(w45)} ft x {_f(p.v('STRIP_LEN_45'))} rft")


def _is_block(p):
    return p.options.masonry == "Block"


@wi("WI-MS-02")
def _ms02(p, kb, q):
    gross = sum(f.wall9_len_ft * wall_h(p, f) for f in p.floors)
    op9, _ = openings_in_9(p)
    col_area = p.v("COL_N") * (p.v("COL_B_IN") / 12) * sum(wall_h(p, f) for f in p.storeys)
    band = p.v("BAND_LEN") * p.v("BAND_D_IN") / 12
    net = max(gross - op9 - col_area - band, 0)
    return WIQty(net * 0.75, worst(*(f.confidence for f in p.floors)),
                 f"(9in wall CL x height {_f(gross)} sft - openings {_f(op9)} - columns {_f(col_area)} - bands {_f(band)}) x 0.75 ft",
                 selected=not _is_block(p))


@wi("WI-MS-03")
def _ms03(p, kb, q):
    gross = sum(f.wall45_len_ft * wall_h(p, f) for f in p.floors)
    _, op45 = openings_in_9(p)
    return WIQty(max(gross - op45, 0), worst(*(f.confidence for f in p.floors)),
                 f"4.5in wall length x height {_f(gross)} sft - openings {_f(op45)} sft", selected=not _is_block(p))


@wi("WI-MS-04")
def _ms04(p, kb, q):
    L = p.v("ROOF_PERIM")
    return WIQty(L * p.v("H_PARAPET") * 0.75, worst(p.conf("ROOF_PERIM"), p.conf("H_PARAPET")),
                 f"{_f(L)} rft x {_f(p.v('H_PARAPET'))} ft x 0.75 ft (9in)")


@wi("WI-MS-05")
def _ms05(p, kb, q):
    area, c = gf_floor_area(p)
    return WIQty(area, c, f"GF floor area {_f(area)} sft")


@wi("WI-MS-06")
def _ms06(p, kb, q):
    gross = sum((f.wall9_len_ft + f.wall45_len_ft) * wall_h(p, f) for f in p.floors)
    return WIQty(max(gross - openings_area(p), 0), MEDIUM, f"all walls {_f(gross)} sft - openings (block option)",
                 selected=_is_block(p))


# ================================= PLASTER ==================================
def room_wall_area(p, rooms):
    tot = 0.0
    for r in rooms:
        fl = next((f for f in p.floors if f.key == r.floor), None)
        h = wall_h(p, fl) if fl else 10.5
        tot += r.perimeter * h
    return tot


@wi("WI-PL-01")
def _pl01(p, kb, q):
    rooms = [r for r in p.rooms if not r.is_open]
    gross = room_wall_area(p, rooms)
    ded = 2 * sum(o.area_total for o in p.openings if not o.external) + sum(o.area_total for o in p.openings if o.external)
    reveals = sum(o.qty * 2 * (o.width_ft + o.height_ft) * 0.5 for o in p.openings)
    return WIQty(max(gross - ded + reveals, 0), worst(*(r.confidence for r in rooms)) if rooms else ASSUMED,
                 f"room perimeters x wall height {_f(gross)} - openings {_f(ded)} + reveals {_f(reveals)} sft")


def facade(p):
    frac = p.v("EXT_EXPOSED_FRAC", 1.0)
    gross = 0.0
    for i, f in enumerate(p.floors):
        h = f.storey_height_ft + (p.v("H_PLINTH") if i == 0 else 0)
        gross += f.ext_perimeter_ft * h * (1.0 if f.is_mumty else frac)
    parapet = p.v("ROOF_PERIM") * p.v("H_PARAPET") * (1 + frac)  # outer (exposed) + inner face
    ext_open = openings_area(p, external_only=True)
    return gross, parapet, ext_open, frac


@wi("WI-PL-02")
def _pl02(p, kb, q):
    gross, parapet, ext_open, frac = facade(p)
    return WIQty(max(gross + parapet - ext_open, 0), worst(p.conf("EXT_EXPOSED_FRAC"), MEDIUM),
                 f"external walls {_f(gross)} (exposed fraction {_f(frac)}) + parapet both faces {_f(parapet)} - openings {_f(ext_open)} sft")


@wi("WI-PL-03")
def _pl03(p, kb, q):
    rooms = [r for r in p.rooms if r.room_type not in ("Terrace / balcony", "Planter / lawn")]
    a = sum(r.area for r in rooms)
    return WIQty(a, worst(*(r.confidence for r in rooms)) if rooms else ASSUMED, f"sum of room ceiling areas {_f(a)} sft")


@wi("WI-PL-04")
def _pl04(p, kb, q):
    def internal(L, W, D):
        return 2 * (L + W) * D + L * W
    a = internal(p.v("UGT_L"), p.v("UGT_W"), p.v("UGT_D")) + p.v("SEPTIC_N") * internal(p.v("SEP_L"), p.v("SEP_W"), p.v("SEP_D"))
    return WIQty(a, worst(p.conf("UGT_L"), ASSUMED), f"tank internal walls + floor {_f(a)} sft (manhole plaster is in WI-PB-12)")


# ========================= WATERPROOFING & SCREEDS ==========================
@wi("WI-WP-01")
def _wp01(p, kb, q):
    a = p.v("ROOF_AREA") + p.v("ROOF_PERIM") * 1.0
    return WIQty(a, p.conf("ROOF_AREA"), f"roof {_f(p.v('ROOF_AREA'))} sft + 1 ft upturn x {_f(p.v('ROOF_PERIM'))} rft",
                 selected=p.options.roof_system != "Insulated")


@wi("WI-WP-02")
def _wp02(p, kb, q):
    a = p.v("ROOF_AREA") + p.v("ROOF_PERIM") * 1.0
    return WIQty(a, p.conf("ROOF_AREA"), f"roof {_f(p.v('ROOF_AREA'))} sft + upturns (insulated system)",
                 selected=p.options.roof_system == "Insulated")


@wi("WI-WP-03")
def _wp03(p, kb, q):
    wet = [r for r in p.rooms if r.is_wet]
    ter = [r for r in p.rooms if r.room_type == "Terrace / balcony"]
    a = sum(r.area + r.perimeter * 1.0 for r in wet) + sum(r.area + r.perimeter * 0.5 for r in ter)
    shower = p.v("N_SHOWER") * 6 * 6.5
    return WIQty(a + shower, worst(*(r.confidence for r in wet)) if wet else ASSUMED,
                 f"wet rooms floor + 12in upturn, terraces, shower walls {_f(shower)} sft")


@wi("WI-WP-04")
def _wp04(p, kb, q):
    ter = sum(r.area for r in p.rooms if r.room_type == "Terrace / balcony")
    baths = sum(r.area for r in p.rooms_of("Bathroom"))
    a = p.v("ROOF_AREA") + ter + baths
    return WIQty(a, MEDIUM, f"roof {_f(p.v('ROOF_AREA'))} + terraces {_f(ter)} + baths {_f(baths)} sft")


# ================================= FLOORING =================================
def _finish_rooms(p, predicate):
    return [r for r in p.rooms if predicate(r)]


@wi("WI-FL-01")
def _fl01(p, kb, q):
    rooms = _finish_rooms(p, lambda r: r.room_type in ("Bedroom", "Lounge / TV lounge", "Mumty", "Store / utility",
                                                       "Servant quarter", "Drawing room"))
    a = sum(r.area for r in rooms)
    return WIQty(a, worst(*(r.confidence for r in rooms)) if rooms else ASSUMED,
                 f"{len(rooms)} dry rooms: " + ", ".join(f"{r.name} {_f(r.area)}" for r in rooms[:12]) + f" = {_f(a)} sft")


@wi("WI-FL-02")
def _fl02(p, kb, q):
    rooms = _finish_rooms(p, lambda r: r.is_wet)
    a = sum(r.area for r in rooms)
    return WIQty(a, worst(*(r.confidence for r in rooms)) if rooms else ASSUMED, f"{len(rooms)} wet rooms = {_f(a)} sft")


@wi("WI-FL-03")
def _fl03(p, kb, q):
    tot = 0.0
    for r in p.rooms:
        rd = next((d for d in kb.room_defaults if d.room_type == r.room_type), None)
        th = rd.wall_tile_height_ft if rd else 0
        if th:
            per = r.perimeter if r.room_type != "Kitchen" else (r.length_ft + r.width_ft)  # backsplash on counter walls
            tot += per * th - (2.5 * th if r.room_type == "Bathroom" else 0)
    return WIQty(max(tot, 0), MEDIUM, f"room perimeter x tile height (bath full height, kitchen backsplash) = {_f(tot)} sft")


@wi("WI-FL-04")
def _fl04(p, kb, q):
    stairs = p.rooms_of("Staircase")
    a = sum(r.area for r in stairs) * 0.5  # landings & lobby; treads in WI-FL-06
    return WIQty(a, MEDIUM if stairs else ASSUMED, f"stair hall landings/lobby 50% of {_f(sum(r.area for r in stairs))} sft")


@wi("WI-FL-05")
def _fl05(p, kb, q):
    rooms = [r for r in p.rooms if r.room_type not in ("Bathroom", "Porch / car porch", "Planter / lawn")]
    per = sum(r.perimeter for r in rooms)
    doors = sum(o.qty * o.width_ft for o in p.doors()) * 1.5
    return WIQty(max(per - doors, 0), MEDIUM, f"room perimeters {_f(per)} - door widths {_f(doors)} rft")


@wi("WI-FL-06")
def _fl06(p, kb, q):
    lifts, risers, tread, width, slope = stair_geometry(p)
    return WIQty(lifts * risers * width, MEDIUM, f"{_f(lifts)} lifts x {risers} steps x {_f(width)} ft")


@wi("WI-FL-07")
def _fl07(p, kb, q):
    a = p.v("PAVING_AREA")
    return WIQty(a, p.conf("PAVING_AREA"), f"porch/driveway {_f(a)} sft (terraces are tiled under WI-FL-02/03 WP)")


@wi("WI-FL-08")
def _fl08(p, kb, q):
    w = sum(o.qty * o.width_ft for o in p.windows())
    d = sum(o.qty * o.width_ft for o in p.doors())
    return WIQty(w + d, MEDIUM, f"window sills {_f(w)} + door thresholds {_f(d)} rft")


# ============================= DOORS & WINDOWS ==============================
@wi("WI-DW-01")
def _dw01(p, kb, q):
    L = sum(o.qty * (2 * o.height_ft + o.width_ft) for o in p.doors())
    return WIQty(L, worst(*(o.confidence for o in p.doors())), f"sum qty x (2H + W) = {_f(L)} rft")


@wi("WI-DW-02")
def _dw02(p, kb, q):
    a = sum(o.area_total for o in p.doors())
    return WIQty(a, worst(*(o.confidence for o in p.doors())), " + ".join(f"{o.qty}x{_f(o.width_ft)}x{_f(o.height_ft)}" for o in p.doors()) + f" = {_f(a)} sft")


@wi("WI-DW-03")
def _dw03(p, kb, q):
    n = sum(o.qty * o.leaves for o in p.doors())
    return WIQty(n, worst(*(o.confidence for o in p.doors())), f"door leaves = {n}")


@wi("WI-DW-04")
def _dw04(p, kb, q):
    a = sum(o.area_total for o in p.windows())
    return WIQty(a, worst(*(o.confidence for o in p.windows())), " + ".join(f"{o.qty}x{_f(o.width_ft)}x{_f(o.height_ft)}" for o in p.windows()) + f" = {_f(a)} sft")


@wi("WI-DW-05")
def _dw05(p, kb, q):
    a = sum(o.area_total for o in p.windows() if o.kind == "window")
    return WIQty(a, ASSUMED, f"grills on all windows {_f(a)} sft")


@wi("WI-DW-06")
def _dw06(p, kb, q):
    return WIQty(p.v("RAIL_LEN"), p.conf("RAIL_LEN"), f"{_f(p.v('RAIL_LEN'))} rft")


@wi("WI-DW-07")
def _dw07(p, kb, q):
    return WIQty(p.v("GATE_W") * p.v("GATE_H"), p.conf("GATE_W"), f"{_f(p.v('GATE_W'))} x {_f(p.v('GATE_H'))} ft")


# ================================= CEILING ==================================
@wi("WI-CL-01")
def _cl01(p, kb, q):
    tot = 0.0
    for r in p.rooms:
        rd = next((d for d in kb.room_defaults if d.room_type == r.room_type), None)
        if rd and r.room_type != "Bathroom":  # baths use PVC panels (CLG-003)
            tot += r.area * rd.false_ceiling_pct
    return WIQty(tot, ASSUMED, f"sum(room area x false-ceiling %) = {_f(tot)} sft", selected=p.options.include_false_ceiling)


# ================================= PAINTING =================================
@wi("WI-PT-01")
def _pt01(p, kb, q):
    a = max(q["WI-PL-01"].qty - q["WI-FL-03"].qty, 0)
    return WIQty(a, q["WI-PL-01"].confidence, f"internal plaster {_f(q['WI-PL-01'].qty)} - wall tiles {_f(q['WI-FL-03'].qty)} sft")


@wi("WI-PT-02")
def _pt02(p, kb, q):
    return WIQty(q["WI-PL-03"].qty, q["WI-PL-03"].confidence, "= ceiling plaster area")


@wi("WI-PT-03")
def _pt03(p, kb, q):
    return WIQty(q["WI-PL-02"].qty, q["WI-PL-02"].confidence, "= external plaster area (deduct cladding when known)")


@wi("WI-PT-04")
def _pt04(p, kb, q):
    a = 2 * (q["WI-DW-05"].qty + q["WI-DW-07"].qty) + q["WI-DW-06"].qty * 2
    return WIQty(a, ASSUMED, "2 x (grills + gate) + 2 sft/rft railings")


@wi("WI-PT-05")
def _pt05(p, kb, q):
    a = 2 * q["WI-DW-02"].qty + q["WI-DW-01"].qty * (kb.k("K_CHOG_W") + 2 * kb.k("K_CHOG_T")) / 12
    return WIQty(a, q["WI-DW-02"].confidence, "2 x shutter area + chogath exposed faces")


# ================================ ELECTRICAL ================================
def _count(wi_id, key, label):
    @wi(wi_id)
    def _fn(p, kb, q):
        return WIQty(p.v(key), p.conf(key), f"{label} = {_f(p.v(key))} ({p.params[key].source if key in p.params else ''})")
    return _fn


_count("WI-EL-01", "N_LIGHT", "light points")
_count("WI-EL-02", "N_FAN", "fan points")
_count("WI-EL-03", "N_SK13", "13A socket points")
_count("WI-EL-04", "N_PW15", "15A/power points")
_count("WI-EL-05", "N_AC", "AC points")
_count("WI-EL-06", "N_LV", "low-voltage points")
_count("WI-EL-07", "SUBMAIN_LEN", "sub-main route")
_count("WI-EL-08", "N_DB", "distribution boards")
_count("WI-EL-09", "N_EARTH", "earth pits")


@wi("WI-EL-10")
def _el10(p, kb, q):
    n = p.v("N_LIGHT") + p.v("N_FAN")
    return WIQty(n, p.conf("N_LIGHT"), "light + fan fixtures")


_count("WI-HV-01", "N_AC", "split AC pre-piping sets")
_count("WI-HV-02", "N_EXH", "exhaust fans")

# ================================= PLUMBING =================================
_count("WI-PB-01", "N_WC", "WCs")
_count("WI-PB-02", "N_VANITY", "basins")
_count("WI-PB-03", "N_SHOWER", "showers")
_count("WI-PB-04", "N_HF", "health faucets")
_count("WI-PB-05", "N_FT", "floor traps")
_count("WI-PB-06", "N_BATH", "bathroom groups")
_count("WI-PB-07", "N_KSINK", "kitchen sinks")
_count("WI-PB-08", "N_WM", "washing machine points")
_count("WI-PB-10", "N_RWP", "roof outlets")
_count("WI-PB-11", "SEWER_LEN", "external sewer")
_count("WI-PB-12", "N_MH", "manholes")
_count("WI-PB-13", "N_GT", "gully traps")
_count("WI-GS-01", "N_GAS", "gas points")


@wi("WI-PB-09")
def _pb09(p, kb, q):
    L = p.v("N_RWP") * (p.v("H_TOTAL") + 2)
    return WIQty(L, p.conf("N_RWP"), f"{_f(p.v('N_RWP'))} RWP x ({_f(p.v('H_TOTAL'))} + 2) ft")


@wi("WI-PB-14")
def _pb14(p, kb, q):
    return WIQty(1, MEDIUM, "water system lump sum (pumps, OH tank, valves) - components itemised as materials")


# ============================ KITCHEN / JOINERY =============================
_count("WI-KT-01", "COUNTER_LEN", "base cabinets")
_count("WI-KT-02", "WALLCAB_LEN", "wall cabinets")
_count("WI-KT-03", "COUNTER_LEN", "countertop")
_count("WI-JN-01", "WARDROBE_AREA", "wardrobe face area")

# ================================= EXTERNAL =================================
_count("WI-EX-01", "BOUNDARY_LEN", "boundary wall")


@wi("WI-EX-02")
def _ex02(p, kb, q):
    return WIQty(p.v("RWH_N"), ASSUMED, "CDA recharge well requirement", selected=p.options.include_rwh)


# evaluation order (dependencies first)
ORDER: List[str] = [
    "WI-PRE-01", "WI-EW-01", "WI-EW-02", "WI-EW-03", "WI-CN-01", "WI-CN-02", "WI-CN-03", "WI-CN-04", "WI-CN-05",
    "WI-CN-06", "WI-CN-07", "WI-CN-08", "WI-CN-09", "WI-CN-10", "WI-CN-11", "WI-CN-12", "WI-CN-13", "WI-MS-01",
    "WI-EW-04", "WI-EW-05", "WI-EW-06", "WI-EW-07", "WI-EW-08", "WI-EW-09",
]
