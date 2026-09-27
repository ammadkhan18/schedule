"""
Detailed MTO engine:  work items  x  recipes  ->  every material in the database.

    material net qty = Σ (work item qty × recipe coefficient)      (recipe-driven)
                     = direct calculator                            (counts / LS / areas)
    gross qty        = net × (1 + wastage)

EVERY material of the Master Database appears in the result with a status,
so the exported schedule never silently drops an item:

    Calculated | Calculated (assumed inputs) | Provisional | Option - not included |
    Needs input | Not in scope | Not required
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from detailed_mto import direct as _direct
from detailed_mto import quantities as _q
from detailed_mto.model import ASSUMED, CONF_ORDER, HIGH, LOW, MEDIUM, USER, DetailedProject
from knowledge.loader import KnowledgeBase, Material

OPTIONAL_TIERS = {"Optional", "Alternative", "Premium", "Contingent"}
ACTIVATING_WIS = {"WI-CL-01", "WI-EX-02", "WI-WP-02", "WI-MS-06"}  # choosing these switches their optional materials on

SCOPE_TO_MAT_SCOPES = {
    "Architecture": {"Architecture", "Kitchen"},
    "Structure": {"Structure"},
    "Electrical": {"Electrical"},
    "Plumbing": {"Plumbing"},
    "HVAC": {"HVAC"},
    "External Works": {"External"},
    "Complete Project": None,  # everything
    "Grey Structure (PK)": {"Structure", "Plumbing", "External", "Complete"},
    "Finishing (PK)": {"Architecture", "Kitchen", "Electrical", "Plumbing", "HVAC", "External"},
}

ST_CALC = "Calculated"
ST_CALC_ASSUMED = "Calculated (assumed inputs)"
ST_PROVISIONAL = "Provisional"
ST_OPTION = "Option - not included"
ST_NEEDS_INPUT = "Needs input"
ST_SCOPE = "Not in scope"
ST_NOT_REQ = "Not required"
ST_REFERENCE = "Counted elsewhere (reference)"

# Assembly / duplicate lines: shown with their quantity for information, but NOT added to procurement,
# because the same material is already quantified by the line named here.
REFERENCE_LINES = {
    "SAN-016": "PWS-022 (water heaters)", "SAN-018": "PWS-013 / PWS-014 (pumps)", "SAN-019": "PWS-016 (OH tank)",
    "EXT-007": "WI-EX-01 materials (bricks, cement, sand, crush)", "EXT-008": "JNR-007 (gate steel)",
    "EXT-011": "JNR-005 (railings)", "PDR-013": "WI-PB-12 materials (bricks, cement, sand, crush, steel)",
    "PDR-023": "WI-CN-11 / WI-RF-07 (tank concrete & steel)", "MSC-003": "RBR-006 / FRM-005 / FRM-006",
    "MSC-004": "EW-004 (anti-termite chemical)", "DWG-004": "DWG-002 (door shutter area)",
    "DWG-005": "DWG-002 (door shutter area)", "DWG-017": "DWG-002 (door shutter area)",
}
FORCE_OPTION = {"HVC-012"}
STRUCTURAL_RCC_WIS = {"WI-CN-03", "WI-CN-04", "WI-CN-05", "WI-CN-06", "WI-CN-07", "WI-CN-08"}
_MIX_COL = {"cement": "J", "sand": "K", "agg": "L", "water": "P"}  # recommended upgrades that duplicate the selected roof system unless activated
INCLUDED_STATUSES = {ST_CALC, ST_CALC_ASSUMED, ST_PROVISIONAL}


@dataclass
class Contribution:
    wi_id: str
    wi_desc: str
    wi_qty: float
    wi_unit: str
    mat_id: str
    coefficient: float
    coeff_id: str
    mix_ref: str
    net_qty: float


@dataclass
class WorkItemLine:
    wi_id: str
    scope: str
    division: str
    description: str
    unit: str
    qty: float
    status: str
    confidence: str
    calculation: str
    measurement_rule: str
    legacy_code: str


@dataclass
class MaterialLine:
    material: Material
    net_qty: float
    wastage_pct: float
    gross_qty: float
    status: str
    confidence: str
    calculation: str
    driven_by: List[str] = field(default_factory=list)
    purchase: str = ""
    if_selected_qty: float = 0.0  # quantity if the option were selected / reference quantity

    @property
    def included(self) -> bool:
        return self.status in INCLUDED_STATUSES


@dataclass
class DetailedResult:
    project: DetailedProject
    materials: List[MaterialLine]
    work_items: List[WorkItemLine]
    contributions: List[Contribution]
    scope: str
    db_path: str

    def by_id(self, mat_id: str) -> Optional[MaterialLine]:
        return next((m for m in self.materials if m.material.mat_id == mat_id), None)

    def status_counts(self, in_scope_only: bool = True) -> Dict[str, int]:
        out: Dict[str, int] = {}
        for m in (self.scoped_materials() if in_scope_only else self.materials):
            out[m.status] = out.get(m.status, 0) + 1
        return out

    # --- scope-filtered views (what the user selected) ---------------------
    def scoped_materials(self) -> List[MaterialLine]:
        return [m for m in self.materials if m.status != ST_SCOPE]

    def purchase_list(self) -> List[MaterialLine]:
        return [m for m in self.materials if m.included and m.gross_qty > 0]

    def scoped_contributions(self) -> List[Contribution]:
        ok = {m.material.mat_id for m in self.scoped_materials()}
        return [c for c in self.contributions if c.mat_id in ok]

    def scoped_work_items(self) -> List[WorkItemLine]:
        if self.scope == "Complete Project":
            return list(self.work_items)
        used = {c.wi_id for c in self.scoped_contributions()}
        allowed = SCOPE_TO_MAT_SCOPES.get(self.scope) or set()
        return [w for w in self.work_items if w.wi_id in used or w.scope in allowed]


# ---------------------------------------------------------------------------
def material_in_scope(kb: KnowledgeBase, mat: Material, scope: str) -> bool:
    flag = kb.scope_map.get(mat.category, {}).get(scope, "Y")
    if scope == "Complete Project" or flag == "Y":
        return True
    if flag in ("-", ""):
        return False
    allowed = SCOPE_TO_MAT_SCOPES.get(scope)
    return allowed is None or mat.scope in allowed


def _blend_confidence(parts: List[tuple]) -> str:
    """parts: [(qty, confidence)] -> qty-weighted confidence label."""
    tot = sum(abs(q) for q, _ in parts)
    if tot <= 0:
        return min((c for _, c in parts), key=lambda c: CONF_ORDER.get(c, 0), default=ASSUMED)
    good = sum(abs(q) for q, c in parts if c in (HIGH, MEDIUM, USER))
    share = good / tot
    if share >= 0.8:
        best = [c for _, c in parts if c in (HIGH, MEDIUM, USER)]
        return MEDIUM if MEDIUM in best else (HIGH if HIGH in best else USER)
    return LOW if share >= 0.5 else ASSUMED


WIRE_IDS = {"ELE-006", "ELE-007", "ELE-008", "ELE-009", "ELE-011"}
STEEL_BAR_IDS = {"RBR-001", "RBR-002", "RBR-003", "RBR-004"}
BULK_CFT_IDS = {"CON-004", "CON-005", "CON-006", "CON-007", "CON-008", "EW-002", "EW-003", "EXT-003", "PDR-020", "WPF-009", "EXT-004", "RWH-002"}


def purchase_rule(mat: Material):
    """How a material is bought: returns (divisor, round-up digits, buy-unit label).
    Buy qty = ROUNDUP(qty incl. wastage / divisor, digits). Used for the export formulas too."""
    u = mat.unit.lower()
    mid = mat.mat_id
    if u == "ls":
        return None, 0, "lump sum"
    if u == "bag":
        return 1, 0, "bags (50 kg)"
    if mid in STEEL_BAR_IDS:
        return 1000, 2, "ton"
    if mid in WIRE_IDS:
        return 90, 0, "coils (90 m)"
    if u == "rft" and mat.wastage_key in ("W_PIPE_PPR", "W_PIPE_UPVC", "W_CONDUIT", "W_COPPER"):
        return 13, 0, "lengths (~13 ft)"
    if mid in ("MAS-001", "MAS-002"):
        return 1, -2, "bricks"
    if u == "cft" and mid in BULK_CFT_IDS:
        return 1, -1, "cft"
    return 1, 0, mat.unit


def purchase_qty(mat: Material, gross: float):
    """(buy quantity, buy unit) for a quantity incl. wastage."""
    div, digits, unit = purchase_rule(mat)
    if gross <= 0:
        return 0.0, unit
    if div is None:
        return 1.0, unit
    x = gross / div
    f = 10 ** digits
    return math.ceil(x * f - 1e-9) / f, unit


def purchase_text(mat: Material, gross: float) -> str:
    if gross <= 0:
        return ""
    q, unit = purchase_qty(mat, gross)
    if unit == "lump sum":
        return "lump sum"
    txt = f"{q:,.2f}" if q != int(q) else f"{int(q):,}"
    extra = f" (~{gross / 100:,.1f} trolleys)" if unit == "cft" and mat.mat_id in BULK_CFT_IDS and gross >= 100 else ""
    return f"{txt} {unit}{extra}"


def compute(project: DetailedProject, kb: KnowledgeBase, scope: Optional[str] = None) -> DetailedResult:
    scope = scope or project.options.scope or "Complete Project"
    p = project

    # 1) work item quantities (dependency order first, then everything else)
    wq: Dict[str, _q.WIQty] = {}
    order = list(_q.ORDER) + [w for w in kb.work_items if w not in _q.ORDER]
    for wid in order:
        fn = _q.REGISTRY.get(wid)
        if fn is None:
            wq[wid] = _q.WIQty(0.0, ASSUMED, "No calculator - needs input", selected=False)
            continue
        try:
            wq[wid] = fn(p, kb, wq)
            if wq[wid].qty < 0:  # impossible inputs must never create negative purchases
                wq[wid] = _q.WIQty(0.0, ASSUMED, f"{wq[wid].calc}  -> negative result set to 0 (check inputs)", selected=wq[wid].selected)
        except Exception as exc:  # a single bad input must not kill the whole schedule
            wq[wid] = _q.WIQty(0.0, ASSUMED, f"Calculation error: {exc}", selected=False)

    # When nothing was read from drawings (scans / no upload) no quantity may claim drawing-level confidence.
    unread = getattr(p, "drawing_mode", "cad") in ("scanned", "none")
    if unread:
        for w in wq.values():
            if w.confidence != USER:
                w.confidence = ASSUMED

    # 2) recipe contributions
    contributions: List[Contribution] = []
    per_mat: Dict[str, List[Contribution]] = {}
    for r in kb.recipes:
        w = wq.get(r.wi_id)
        if w is None or not w.selected or w.qty == 0:
            continue
        wi = kb.work_items[r.wi_id]
        coef, mix_ref = r.coefficient, r.mix_ref
        new_mix = p.options.rcc_mix
        if (r.wi_id in STRUCTURAL_RCC_WIS and r.mix_ref == "MX_RCC124" and new_mix and new_mix != "MX_RCC124"
                and new_mix in kb.mixes and r.mix_component in _MIX_COL):
            col = _MIX_COL[r.mix_component]
            old_v = kb.mixes["MX_RCC124"].values.get(col, 0.0)
            if old_v:
                coef = coef / old_v * kb.mixes[new_mix].values.get(col, 0.0)
                mix_ref = new_mix
        c = Contribution(r.wi_id, wi.description, w.qty, wi.unit, r.mat_id, coef, r.coeff_id, mix_ref, w.qty * coef)
        contributions.append(c)
        per_mat.setdefault(r.mat_id, []).append(c)

    # 3) construction water depends on total cement
    cem = sum(c.net_qty for c in per_mat.get("CON-001", []))
    cem_gross = cem * (1 + kb.materials["CON-001"].wastage_pct) if "CON-001" in kb.materials else cem
    water_gal = cem_gross * kb.k("K_WATER_L_BAG") / 4.546
    wq["WI-PRE-02"] = _q.WIQty(water_gal, ASSUMED if unread else MEDIUM, f"{cem_gross:,.0f} cement bags x {kb.k('K_WATER_L_BAG'):g} L/bag / 4.546 L/gal")
    for c in list(contributions):
        if c.wi_id == "WI-PRE-02":
            contributions.remove(c)
    per_mat["MSC-001"] = []
    for r in kb.recipes_for("WI-PRE-02"):
        c = Contribution("WI-PRE-02", kb.work_items["WI-PRE-02"].description, water_gal, "gal", r.mat_id, r.coefficient,
                         r.coeff_id, r.mix_ref, water_gal * r.coefficient)
        contributions.append(c)
        per_mat.setdefault(r.mat_id, []).append(c)

    # 4) work item lines
    wlines: List[WorkItemLine] = []
    for wid, wi in kb.work_items.items():
        w = wq[wid]
        if not w.selected:
            status = ST_OPTION if "error" not in w.calc.lower() and "needs input" not in w.calc.lower() else ST_NEEDS_INPUT
        elif w.qty == 0:
            status = ST_NOT_REQ
        else:
            status = ST_CALC if w.confidence in (HIGH, MEDIUM, USER) else ST_CALC_ASSUMED
        wlines.append(WorkItemLine(wid, wi.scope, wi.division, wi.description, wi.unit, w.qty, status, w.confidence,
                                   w.calc, wi.measurement_rule, wi.legacy_code))

    # 5) every material
    lines: List[MaterialLine] = []
    for mid, mat in kb.materials.items():
        contribs = per_mat.get(mid, [])
        res = None
        driven = sorted({c.wi_id for c in contribs})
        tier = mat.tier
        if contribs:
            net = sum(c.net_qty for c in contribs)
            conf = _blend_confidence([(c.net_qty, wq[c.wi_id].confidence) for c in contribs])
            calc = " + ".join(f"{c.wi_id} {c.wi_qty:,.2f} {c.wi_unit} x {c.coefficient:.4g}" for c in contribs[:6])
            if len(contribs) > 6:
                calc += f" + {len(contribs) - 6} more (see Material_Breakdown)"
            activated = any(c.wi_id in ACTIVATING_WIS for c in contribs)
        else:
            fn = _direct.DIRECT.get(mid)
            res = None
            if fn is not None:
                try:
                    res = fn(p, kb, wq)
                except Exception as exc:
                    res = None
                    calc = f"Calculation error: {exc}"
            if res is None:
                net, conf = 0.0, ASSUMED
                calc = (mat.calc_basis or "") if fn is None or res is None else ""
                activated = False
                status = ST_NEEDS_INPUT
            else:
                net, conf, calc = res
                if unread and conf != USER:
                    conf = ASSUMED
                activated = _activated_direct(mid, p)
        if not material_in_scope(kb, mat, scope):
            status = ST_SCOPE
        elif not contribs and (_direct.DIRECT.get(mid) is None or res is None):
            status = ST_NEEDS_INPUT if (tier not in OPTIONAL_TIERS or p.options.include_options) else ST_OPTION
        elif (tier in OPTIONAL_TIERS or mid in FORCE_OPTION) and not (activated or p.options.include_options):
            status = ST_OPTION
        elif mid in REFERENCE_LINES and net > 0:
            status = ST_REFERENCE
            calc = f"{calc}  ->  procured under {REFERENCE_LINES[mid]}"
        elif net <= 0:
            status = ST_NOT_REQ
        elif tier == "Provisional":
            status = ST_PROVISIONAL
        else:
            status = ST_CALC if conf in (HIGH, MEDIUM, USER) else ST_CALC_ASSUMED
        wp = 0.0 if mat.unit.upper() == "LS" else mat.wastage_pct
        included = status in INCLUDED_STATUSES
        net_out = net if included else 0.0
        gross = net_out * (1 + wp)
        info = (net * (1 + wp)) if status in (ST_OPTION, ST_REFERENCE) else 0.0
        ml = MaterialLine(mat, net_out, wp, gross, status, conf if (included or status == ST_REFERENCE) else "", calc, driven,
                          purchase_text(mat, gross), if_selected_qty=info)
        lines.append(ml)

    return DetailedResult(p, lines, wlines, contributions, scope, kb.path)


def _activated_direct(mid: str, p: DetailedProject) -> bool:
    o = p.options
    rules = {
        "CLG-003": o.include_false_ceiling, "CLG-005": o.include_false_ceiling and o.finish_tier != "Economy",
        "CLG-006": o.include_false_ceiling, "PLS-004": o.include_false_ceiling and o.finish_tier != "Economy",
        "HVC-012": o.roof_system == "Insulated", "WPF-006": False,
        "GAS-006": o.gas_source == "LPG", "GAS-005": o.gas_source == "SNGPL",
        "RWH-005": o.include_rwh, "RWH-006": o.include_rwh,
        "PDR-023": p.v("SEPTIC_N") > 0, "MAS-005": False,
        "SAN-013": o.finish_tier == "Premium", "PWS-021": o.finish_tier == "Premium",
        "KIT-007": o.finish_tier != "Economy", "KIT-008": o.finish_tier != "Economy", "KIT-010": o.finish_tier == "Premium",
        "JNR-009": o.finish_tier != "Economy", "EXT-005": False,
    }
    return bool(rules.get(mid, False))
