"""
Deterministic analysis used by the copilot (and usable without any AI):

* key_totals / diff_results  - what changes between two take-offs
* explain_material           - where a material quantity comes from
* sensitivity                - which ASSUMED inputs move the quantities most
* questions_for              - plain-language questions for the most important assumptions
* check_takeoff              - review of a finished take-off with fixes the user can apply
* material_saving_options    - alternatives run through the engine and ranked
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional

from copilot.state import ProjectState, apply_changes, build, run
from detailed_mto.engine import ST_CALC_ASSUMED, ST_NEEDS_INPUT, DetailedResult
from detailed_mto.export import benchmarks_for

KEY_GROUPS = [  # label, ids, unit, divisor
    ("Cement", ("CON-001",), "bags", 1),
    ("Steel", ("RBR-001", "RBR-002", "RBR-003", "RBR-004"), "ton", 1000),
    ("Bricks", ("MAS-001", "MAS-002"), "Nos", 1),
    ("Sand", ("CON-004", "CON-005"), "cft", 1),
    ("Crush", ("CON-006", "CON-007", "CON-008"), "cft", 1),
    ("Tiles", ("FLR-001", "FLR-002", "FLR-003"), "sft", 1),
    ("Paint", ("PNT-003", "PNT-004", "PNT-005"), "gal", 1),
    ("Wiring", ("ELE-006", "ELE-007", "ELE-008", "ELE-009", "ELE-011"), "m", 1),
]
# weight of each key group in the "impact" score (rough share of material cost in a 5-10 marla house)
KEY_WEIGHTS = {"Cement": 0.18, "Steel": 0.22, "Bricks": 0.14, "Sand": 0.05, "Crush": 0.05, "Tiles": 0.12, "Paint": 0.06,
               "Wiring": 0.08}


def key_totals(res: DetailedResult) -> Dict[str, float]:
    out = {}
    for label, ids, _unit, div in KEY_GROUPS:
        out[label] = sum(m.gross_qty for m in res.materials if m.material.mat_id in ids) / div
    return out


def fmt_total(label: str, v: float) -> str:
    unit = next(u for lb, _i, u, _d in KEY_GROUPS if lb == label)
    return f"{v:,.2f} {unit}" if unit == "ton" else f"{v:,.0f} {unit}"


def diff_results(a: DetailedResult, b: DetailedResult, top: int = 8) -> dict:
    ka, kb_ = key_totals(a), key_totals(b)
    keys = []
    for label in ka:
        d = kb_[label] - ka[label]
        pct = (d / ka[label] * 100) if ka[label] else (100.0 if kb_[label] else 0.0)
        keys.append({"material": label, "before": fmt_total(label, ka[label]), "after": fmt_total(label, kb_[label]),
                     "change_pct": round(pct, 1)})
    mb = {m.material.mat_id: m for m in b.materials}
    changes = []
    for m in a.materials:
        n = mb.get(m.material.mat_id)
        if n is None:
            continue
        d = n.gross_qty - m.gross_qty
        if abs(d) > 1e-6 and (m.gross_qty or n.gross_qty):
            base = max(m.gross_qty, n.gross_qty, 1e-9)
            changes.append((abs(d) / base, m, n, d))
    changes.sort(key=lambda x: -x[0])
    lines = [{"mat_id": m.material.mat_id, "material": m.material.description, "unit": m.material.unit,
              "before": round(m.gross_qty, 2), "after": round(n.gross_qty, 2), "change": round(d, 2),
              "status_after": n.status} for _r, m, n, d in changes[:top]]
    return {"key_totals": keys, "lines_changed": len(changes), "top_changes": lines}


def find_materials(res: DetailedResult, query: str, limit: int = 8) -> List[dict]:
    q = (query or "").lower().strip()
    words = [w for w in q.replace(",", " ").split() if len(w) > 1]
    scored = []
    for m in res.materials:
        text = f"{m.material.mat_id} {m.material.description} {m.material.specification} {m.material.category}".lower()
        if q == m.material.mat_id.lower():
            return [_mat_brief(m)]
        score = sum(1 for w in words if w in text)
        if score:
            scored.append((score, m.gross_qty, m))
    scored.sort(key=lambda x: (-x[0], -x[1]))
    return [_mat_brief(m) for _s, _q, m in scored[:limit]]


def _mat_brief(m) -> dict:
    return {"mat_id": m.material.mat_id, "material": m.material.description, "qty": round(m.gross_qty, 2),
            "unit": m.material.unit, "buy": m.purchase, "status": m.status}


GROUP_WORDS = {"steel": "Steel", "rebar": "Steel", "sarya": "Steel", "saria": "Steel", "cement": "Cement", "brick": "Bricks",
               "bricks": "Bricks", "sand": "Sand", "crush": "Crush", "bajri": "Crush", "aggregate": "Crush", "tile": "Tiles",
               "tiles": "Tiles", "paint": "Paint", "wire": "Wiring", "wiring": "Wiring", "wires": "Wiring"}


def explain(res: DetailedResult, query: str) -> dict:
    """Explain a material (Mat_ID or words) or a main group ('steel', 'cement', 'bricks' ...)."""
    q = (query or "").strip().lower()
    group = GROUP_WORDS.get(q) or next((lb for lb, *_ in KEY_GROUPS if lb.lower() == q), None)
    if group:
        return explain_group(res, group)
    return explain_material(res, query)


def explain_group(res: DetailedResult, label: str) -> dict:
    _lb, ids, unit, div = next(g for g in KEY_GROUPS if g[0] == label)
    agg: Dict[str, list] = {}
    for c in res.contributions:
        if c.mat_id in ids:
            a = agg.setdefault(c.wi_id, [c.wi_desc, 0.0, c.wi_qty, c.wi_unit])
            a[1] += c.net_qty
    tot = sum(a[1] for a in agg.values()) or 1.0
    wmap = {w.wi_id: w for w in res.work_items}
    parts = [{"work_item": f"{w} {a[0]}", "work_qty": f"{a[2]:,.1f} {a[3]}", "material_qty": round(a[1] / div, 3),
              "share_pct": round(a[1] / tot * 100, 1), "how_measured": wmap[w].calculation[:220] if w in wmap else ""}
             for w, a in sorted(agg.items(), key=lambda x: -x[1][1])[:8]]
    items = [{"mat_id": m.material.mat_id, "material": m.material.description, "qty_incl_wastage": round(m.gross_qty, 1),
              "unit": m.material.unit, "buy": m.purchase} for m in res.materials if m.material.mat_id in ids and m.gross_qty > 0]
    return {"group": label, "total": fmt_total(label, sum(m.gross_qty for m in res.materials if m.material.mat_id in ids) / div),
            "items": items, "parts": parts, "unit_of_parts": unit}


def explain_material(res: DetailedResult, mat_id: str) -> dict:
    m = res.by_id(mat_id)
    if m is None:
        cands = find_materials(res, mat_id, 1)
        m = res.by_id(cands[0]["mat_id"]) if cands else None
    if m is None:
        return {"error": f"No material '{mat_id}'"}
    wmap = {w.wi_id: w for w in res.work_items}
    contribs = sorted([c for c in res.contributions if c.mat_id == m.material.mat_id], key=lambda c: -c.net_qty)
    tot = sum(c.net_qty for c in contribs) or 1.0
    parts = [{"work_item": f"{c.wi_id} {c.wi_desc}", "work_qty": f"{c.wi_qty:,.1f} {c.wi_unit}",
              "coefficient": round(c.coefficient, 4), "material_qty": round(c.net_qty, 2),
              "share_pct": round(c.net_qty / tot * 100, 1),
              "how_measured": (wmap[c.wi_id].calculation[:220] if c.wi_id in wmap else "")} for c in contribs[:6]]
    return {"mat_id": m.material.mat_id, "material": m.material.description, "spec": m.material.specification,
            "unit": m.material.unit, "net_qty": round(m.net_qty, 2), "wastage_pct": round(m.wastage_pct * 100, 1),
            "qty_incl_wastage": round(m.gross_qty, 2), "buy": m.purchase, "status": m.status, "confidence": m.confidence,
            "calculation": m.calculation[:400] if not contribs else "", "parts": parts,
            "more_parts": max(len(contribs) - 6, 0)}


# ---------------------------------------------------------------------------
# sensitivity & smart questions
# ---------------------------------------------------------------------------
# inputs a user can actually answer, with how to perturb them for the test
SENSITIVE = {  # key: (relative step, absolute step)
    "H_FLOOR": (0.0, 1.0), "T_SLAB_IN": (0.0, 1.0), "H_PLINTH": (0.0, 1.0), "H_PARAPET": (0.0, 1.0),
    "FDN_DEPTH": (0.0, 1.0), "PCC_W_9": (0.0, 0.5), "PCC_T": (0.0, 0.25), "EXT_EXPOSED_FRAC": (0.0, 0.2),
    "COL_N": (0.25, 0.0), "BEAM_N": (0.25, 0.0), "FTG_L": (0.0, 1.0), "FTG_D": (0.0, 0.25),
    "N_LIGHT": (0.25, 0.0), "N_SK13": (0.25, 0.0), "N_AC": (0.0, 2.0), "N_FAN": (0.25, 0.0),
    "N_BATH": (0.0, 1.0), "WARDROBE_AREA": (0.3, 0.0), "COUNTER_LEN": (0.3, 0.0), "BOUNDARY_LEN": (0.0, 30.0),
    "PAVING_AREA": (0.3, 0.0), "UGT_L": (0.0, 1.0), "SEWER_LEN": (0.5, 0.0), "N_RWP": (0.0, 2.0),
    "ROOF_AREA": (0.1, 0.0), "BAND_LEN": (0.25, 0.0), "RAIL_LEN": (0.3, 0.0),
}
WINDOW_TEST = "__WINDOWS__"


@dataclass
class Sensitivity:
    key: str
    label: str
    value: float
    unit: str
    source: str
    confidence: str
    impact: float  # weighted % change of key materials for the test step
    drivers: List[str] = field(default_factory=list)


def _impact(base: Dict[str, float], new: Dict[str, float]) -> tuple:
    score, drivers = 0.0, []
    for k, w in KEY_WEIGHTS.items():
        b = base.get(k, 0.0)
        if b > 0:
            pct = abs(new.get(k, 0.0) - b) / b * 100
            score += w * pct
            if pct >= 2:
                drivers.append(f"{k} {pct:+.0f}%" if new[k] >= b else f"{k} -{pct:.0f}%")
    return score, drivers


def sensitivity(state: ProjectState, top_n: int = 6, only_uncertain: bool = True) -> List[Sensitivity]:
    """Rank the inputs the user should confirm: those still ASSUMED/LOW whose change moves the main materials most."""
    base_p = build(state)
    base = key_totals(run(state))
    out: List[Sensitivity] = []
    for key, (rel, ab) in SENSITIVE.items():
        prm = base_p.params.get(key)
        if prm is None or (only_uncertain and prm.confidence not in ("Assumed", "Low")):
            continue
        step = prm.value * rel + ab
        if step <= 0:
            continue
        s2, _, probs = apply_changes(state, [{"type": "set_param", "key": key, "value": prm.value + step}])
        if probs:
            continue
        score, drivers = _impact(base, key_totals(run(s2)))
        out.append(Sensitivity(key, prm.label, prm.value, prm.unit, prm.source, prm.confidence, score, drivers))
    # window sizes are assumed on most Pakistani D&W sheets
    wins = [o for o in (state.openings or []) if o.get("Kind") == "window" and o.get("Confidence") in ("Assumed", "Low")]
    if wins:
        ch = [{"type": "update_opening", "name": o["Name"], "width_ft": float(o["Width (ft)"]) + 1} for o in wins]
        s2, _, probs = apply_changes(state, ch)
        if not probs:
            score, drivers = _impact(base, key_totals(run(s2)))
            w = wins[0]
            out.append(Sensitivity(WINDOW_TEST, "Window sizes", float(w["Width (ft)"]), "ft", "Assumed 4'x5' (not on drawings)",
                                   "Assumed", score, drivers))
    out.sort(key=lambda x: -x.impact)
    return [x for x in out if x.impact > 0.05][:top_n]


QUESTION_BANK = {
    "N_AC": ("How many split ACs will the house have?", "number", [0, 2, 4, 6, 8, 10]),
    "N_LIGHT": ("Roughly how many light points in the whole house?", "number", [40, 60, 80, 100, 130]),
    "N_SK13": ("Roughly how many normal (13A) sockets in the whole house?", "number", [30, 45, 60, 80]),
    "N_FAN": ("How many ceiling fans?", "number", [4, 6, 8, 10, 12]),
    "H_FLOOR": ("What is the floor-to-floor height? (ft)", "number", [10.5, 11.0, 11.5, 12.0, 12.5]),
    "T_SLAB_IN": ("How thick is the roof slab? (inches)", "number", [5, 6, 7]),
    "H_PLINTH": ("How high is the plinth (house floor) above the road? (ft)", "number", [1.0, 1.5, 2.0, 2.5, 3.0]),
    "H_PARAPET": ("How high is the roof parapet wall? (ft)", "number", [2.0, 3.0, 3.5, 4.0]),
    "FDN_DEPTH": ("How deep is the foundation below ground? (ft)", "number", [3.0, 3.5, 4.0, 5.0]),
    "PCC_W_9": ("Width of the foundation concrete (PCC) under main walls? (ft)", "number", [2.0, 2.5, 3.0, 3.5]),
    "PCC_T": ("Thickness of the foundation PCC? (ft)", "number", [0.25, 0.33, 0.5]),
    "EXT_EXPOSED_FRAC": ("Are the side walls shared with the neighbours?", "choice",
                         [("Both sides shared", 0.6), ("One side shared (corner plot)", 0.8), ("No, all sides open", 1.0)]),
    "COL_N": ("How many RCC columns are there?", "number", [0, 4, 8, 12, 16, 20]),
    "BEAM_N": ("How many RCC beams per floor?", "number", [0, 4, 8, 12, 20]),
    "N_BATH": ("How many bathrooms in total?", "number", [2, 3, 4, 5, 6]),
    "WARDROBE_AREA": ("Total front area of built-in wardrobes? (sft; one 6'x8' wardrobe = 48)", "number", [0, 48, 96, 144, 192]),
    "COUNTER_LEN": ("Total kitchen counter length? (ft)", "number", [8, 12, 16, 20, 24]),
    "BOUNDARY_LEN": ("How long is the boundary wall you will build? (ft)", "number", [0, 25, 35, 80, 120]),
    "PAVING_AREA": ("Area of paving (porch / driveway)? (sft)", "number", [0, 150, 200, 300, 400]),
    "UGT_L": ("Underground water tank size?", "choice", [("About 500 gallons", 4.0), ("About 1,000 gallons", 5.7),
                                                         ("About 1,500 gallons", 7.0), ("About 2,000 gallons", 8.0)]),
    "SEWER_LEN": ("Distance from the house to the street sewer? (ft)", "number", [10, 20, 40, 60]),
    "N_RWP": ("How many rain-water pipes from the roof?", "number", [2, 3, 4, 6]),
    "ROOF_AREA": ("Area of the roof to be waterproofed? (sft)", "number", []),
    "BAND_LEN": ("Length of seismic lintel band? (rft)", "number", []),
    "RAIL_LEN": ("Total railing length (stairs + balconies)? (rft)", "number", [30, 50, 70, 100]),
    "FTG_L": ("Typical column footing size? (ft)", "number", [3.0, 3.5, 4.0, 5.0]),
    "FTG_D": ("Column footing thickness? (ft)", "number", [1.0, 1.25, 1.5]),
    WINDOW_TEST: ("What is the typical window size in the rooms?", "choice",
                  [("3' x 4' (small)", (3.0, 4.0)), ("4' x 5' (typical)", (4.0, 5.0)), ("5' x 5'", (5.0, 5.0)),
                   ("6' x 5' (large)", (6.0, 5.0))]),
}


def changes_for_answer(state: ProjectState, key: str, answer) -> List[dict]:
    """Turn an answer to a smart question into proposed changes."""
    if key == WINDOW_TEST:
        w, h = answer
        return [{"type": "update_opening", "name": o["Name"], "width_ft": w, "height_ft": h}
                for o in (state.openings or []) if o.get("Kind") == "window" and "mumty" not in str(o.get("Name", "")).lower()]
    if key == "UGT_L":
        return [{"type": "set_param", "key": "UGT_L", "value": answer}, {"type": "set_param", "key": "UGT_W", "value": answer}]
    return [{"type": "set_param", "key": key, "value": answer}]


# ---------------------------------------------------------------------------
# take-off checker
# ---------------------------------------------------------------------------
@dataclass
class Finding:
    severity: str  # "problem" | "check" | "info"
    title: str
    detail: str
    changes: List[dict] = field(default_factory=list)  # optional one-click fix
    fix_label: str = ""


def _top_contributors(res: DetailedResult, ids, n=3) -> List[tuple]:
    agg: Dict[str, float] = {}
    names: Dict[str, str] = {}
    for c in res.contributions:
        if c.mat_id in ids:
            agg[c.wi_id] = agg.get(c.wi_id, 0.0) + c.net_qty
            names[c.wi_id] = c.wi_desc
    tot = sum(agg.values()) or 1.0
    return [(w, names[w], q / tot * 100) for w, q in sorted(agg.items(), key=lambda x: -x[1])[:n]]


def check_takeoff(state: ProjectState, res: DetailedResult) -> List[Finding]:
    p = res.project
    out: List[Finding] = []
    cov = sum(f.covered_sft for f in p.floors) or 1.0
    strip = p.v("FDN_STRIP") >= 0.5

    # 1. benchmark ratios, with the reason
    for label, ids, unit, lo, hi in benchmarks_for(p):
        v = sum(m.gross_qty for m in res.materials if m.material.mat_id in ids) / cov
        if v == 0 or lo <= v <= hi:
            continue
        drivers = ", ".join(f"{name} {share:.0f}%" for _w, name, share in _top_contributors(res, set(ids)))
        f = Finding("check", f"{label.split(' per ')[0]} is {'high' if v > hi else 'low'}: {v:.2f} {unit}",
                    f"Typical {lo:g}-{hi:g} {unit} for 5-10 marla houses. Biggest contributors: {drivers or 'n/a'}.")
        if "Steel" in label and v > hi and strip and p.v("COL_N") > 8:
            f.changes = [{"type": "set_param", "key": "COL_N", "value": 4}, {"type": "set_param", "key": "BEAM_N", "value": 4}]
            f.fix_label = "Use 4 columns / 4 beams (typical for a load-bearing house)"
        out.append(f)

    # 2. structural consistency
    if strip and p.v("COL_N") > 8:
        out.append(Finding("problem", f"Load-bearing house with {p.v('COL_N'):.0f} RCC columns",
                           "Strip (load-bearing) foundations were read, but the column count looks like an RCC frame "
                           "(usually from the plot template). This inflates steel, concrete and formwork.",
                           [{"type": "set_param", "key": "COL_N", "value": 4}, {"type": "set_param", "key": "BEAM_N", "value": 4}],
                           "Set 4 columns and 4 beams per level"))
    if not strip and p.v("COL_N") < 4 and p.storeys:
        out.append(Finding("problem", "RCC frame with almost no columns",
                           f"Isolated footings are selected but only {p.v('COL_N'):.0f} columns are counted."))

    # 3. plumbing / services consistency
    baths, wcs, fts = p.v("N_BATH"), p.v("N_WC"), p.v("N_FT")
    if baths and fts < baths:
        out.append(Finding("problem", f"{fts:.0f} floor traps for {baths:.0f} bathrooms",
                           "Every bathroom needs at least one floor trap (usually two with a shower).",
                           [{"type": "set_param", "key": "N_FT", "value": 2 * baths + len(p.rooms_of('Kitchen'))}],
                           f"Set floor traps to {2 * baths + len(p.rooms_of('Kitchen')):.0f}"))
    if baths and wcs < baths:
        out.append(Finding("check", f"{wcs:.0f} WCs for {baths:.0f} bathrooms", "Is one bathroom a shower-only room?",
                           [{"type": "set_param", "key": "N_WC", "value": baths}], f"Set WCs to {baths:.0f}"))
    bedrooms = len(p.rooms_of("Bedroom"))
    lounges = len(p.rooms_of("Lounge / TV lounge", "Drawing room"))
    if p.v("N_AC") > bedrooms + lounges + 1:
        out.append(Finding("check", f"{p.v('N_AC'):.0f} AC points for {bedrooms} bedrooms and {lounges} lounges",
                           "More AC points than main rooms - intentional?",
                           [{"type": "set_param", "key": "N_AC", "value": bedrooms + lounges}], f"Set to {bedrooms + lounges}"))
    if len(p.storeys) > 1 and not p.rooms_of("Staircase"):
        out.append(Finding("problem", "No staircase in a multi-storey house",
                           "Stair concrete, steel, marble treads and railing are missing.",
                           [{"type": "add_room", "room": "STAIRCASE", "floor": "ground", "room_type": "Staircase",
                             "length_ft": 12.5, "width_ft": 9}], "Add a 12'-6\" x 9' staircase"))
    if not p.rooms_of("Kitchen"):
        out.append(Finding("problem", "No kitchen", "Kitchen cabinets, sink, tiles and gas/electric points are missing.",
                           [{"type": "add_room", "room": "KITCHEN", "floor": "ground", "room_type": "Kitchen",
                             "length_ft": 10, "width_ft": 9}], "Add a 10' x 9' kitchen"))
    if p.v("N_EARTH") < 1:
        out.append(Finding("problem", "No earthing pit", "At least 2 earth pits are recommended for safety.",
                           [{"type": "set_param", "key": "N_EARTH", "value": 2}], "Set 2 earth pits"))

    # 4. geometry sanity
    top = p.storeys[-1] if p.storeys else None
    if top and p.mumty and p.mumty.covered_sft > 0.5 * top.covered_sft:
        out.append(Finding("problem", f"Mumty area {p.mumty.covered_sft:,.0f} sft looks too big",
                           f"A mumty is usually 150-250 sft; this is over half the top floor ({top.covered_sft:,.0f} sft) - "
                           "probably the roof outline was read as the mumty."))
    wins = [o for o in p.openings if o.kind == "window"]
    if wins and all(o.confidence in ("Assumed", "Low") for o in wins):
        out.append(Finding("check", "Window sizes are assumed (4' x 5')",
                           "Window widths are not given on the drawings. Glass, aluminium, grills, lintels and plaster "
                           "depend on them - answer the window question in Step 3 or edit Doors & windows."))
    el = [p.params[k] for k in ("N_LIGHT", "N_SK13", "N_FAN") if k in p.params]
    if el and all(x.confidence == "Assumed" for x in el):
        out.append(Finding("check", "Electrical points are estimated from room types",
                           f"{p.v('N_LIGHT'):.0f} lights, {p.v('N_SK13'):.0f} sockets, {p.v('N_FAN'):.0f} fans. Count them "
                           "on the electrical drawings (or give rough totals) - wires, conduits and switches follow these."))

    # 5. completeness
    needs = [m for m in res.scoped_materials() if m.status == ST_NEEDS_INPUT]
    if needs:
        out.append(Finding("info", f"{len(needs)} material(s) still need an input",
                           "; ".join(f"{m.material.description} ({m.material.required_inputs or 'quantity'})" for m in needs[:4])))
    assumed = sum(1 for m in res.scoped_materials() if m.status == ST_CALC_ASSUMED)
    if assumed:
        out.append(Finding("info", f"{assumed} lines use at least one default value",
                           "Answer the 'Most important questions' in Step 3 to firm up the biggest ones first."))
    for c in p.conflicts[:4]:
        out.append(Finding("check", "Drawing conflict", c))
    order = {"problem": 0, "check": 1, "info": 2}
    out.sort(key=lambda f: order.get(f.severity, 3))
    return out


# ---------------------------------------------------------------------------
# material-saving alternatives (quantities only - costs come later)
# ---------------------------------------------------------------------------
def material_saving_options(state: ProjectState) -> List[dict]:
    base = run(state)
    cand = []
    o = state.options
    if o.masonry != "Block":
        cand.append(("Concrete block walls instead of brick", [{"type": "set_option", "name": "masonry", "value": "Block"}]))
    if o.finish_tier != "Economy":
        cand.append(("Economy finish level", [{"type": "set_option", "name": "finish_tier", "value": "Economy"}]))
    if o.include_false_ceiling:
        cand.append(("No false ceilings", [{"type": "set_option", "name": "include_false_ceiling", "value": False}]))
    if o.roof_system != "Insulated":
        cand.append(("Insulated roof (foam board) instead of mud + brick tiles",
                     [{"type": "set_option", "name": "roof_system", "value": "Insulated"}]))
    p = build(state)
    if p.v("N_AC") > 2:
        cand.append((f"AC points only in bedrooms ({len(p.rooms_of('Bedroom'))})",
                     [{"type": "set_param", "key": "N_AC", "value": len(p.rooms_of("Bedroom"))}]))
    if p.v("FDN_STRIP") >= 0.5 and p.v("COL_N") > 8:
        cand.append(("Load-bearing: 4 columns / 4 beams instead of the template frame",
                     [{"type": "set_param", "key": "COL_N", "value": 4}, {"type": "set_param", "key": "BEAM_N", "value": 4}]))
    out = []
    for label, changes in cand:
        s2, descs, probs = apply_changes(state, changes)
        if probs:
            continue
        d = diff_results(base, run(s2), top=5)
        saved = [k for k in d["key_totals"] if k["change_pct"] < -0.5]
        added = [k for k in d["key_totals"] if k["change_pct"] > 0.5]
        score = -sum(KEY_WEIGHTS.get(k["material"], 0) * k["change_pct"] for k in d["key_totals"])
        reduced = [c for c in d["top_changes"] if c["change"] < 0]
        if score <= 0.1 and not reduced:
            continue
        out.append({"option": label, "changes": changes, "saves": saved, "adds": added, "score": round(score, 2),
                    "reduced_items": [f"{c['material']} ({c['before']:,.0f} → {c['after']:,.0f} {c['unit']})" for c in reduced[:3]],
                    "note": _option_note(label)})
    out.sort(key=lambda x: -x["score"])
    return out


def _option_note(label: str) -> str:
    if "block" in label.lower():
        return "Fewer bricks and mortar; check block availability and that the structure is designed for blocks."
    if "insulated" in label.lower():
        return "Much cooler top floor in summer; different materials (boards, membrane) replace earth and brick tiles."
    if "economy" in label.lower():
        return "Fewer premium items (cornices, built-in appliances)."
    if "columns" in label.lower():
        return "Only valid if the house really is load-bearing - confirm with the engineer."
    return ""


def scenario_table(named: Dict[str, ProjectState]) -> List[dict]:
    rows = []
    for name, s in named.items():
        t = key_totals(run(s))
        rows.append({"Scenario": name, **{k: fmt_total(k, v) for k, v in t.items()}})
    return rows


def summarize(res: DetailedResult) -> dict:
    p = res.project
    return {"project": p.project_name, "scope": res.scope, "drawing_source": p.drawing_mode,
            "floors": [{"floor": f.name, "covered_sft": round(f.covered_sft), "height_ft": f.storey_height_ft} for f in p.floors],
            "rooms": {t: len(p.rooms_of(t)) for t in ("Bedroom", "Bathroom", "Kitchen", "Lounge / TV lounge", "Drawing room")
                      if p.rooms_of(t)},
            "structure": "load-bearing (strip foundations)" if p.v("FDN_STRIP") >= 0.5 else "RCC frame",
            "options": {k: getattr(p.options, k) for k in ("finish_tier", "roof_system", "masonry", "gas_source", "rcc_mix")},
            "key_totals": {k: fmt_total(k, v) for k, v in key_totals(res).items()},
            "lines": res.status_counts()}


def first_or_none(xs: list) -> Optional[dict]:
    return xs[0] if xs else None
