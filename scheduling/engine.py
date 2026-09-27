"""
Schedule engine: BOQ work items -> activities -> CPM on a working-day calendar.

Durations
    duration (working days) = ceil( sum(qty_i / output_i) / gangs / productivity )
    where output_i is the output of one gang per working day for work item i.
    Lump-sum items (mobilisation, water-supply system, handover) have fixed durations.

Logic
    Finish-to-start links with lags in CALENDAR days (concrete cures on Sundays too):
    successor start = first working day on/after (predecessor finish + 1 + lag).
    Structure goes floor by floor: columns -> beams/slab/stair -> (curing) -> next-floor columns;
    masonry on a floor starts after the slab above is de-shuttered, then rough-ins, plaster,
    waterproofing/tiling, paint and fittings. With "one gang per trade" the same trade finishes
    a floor before moving to the next.

Critical path
    Forward pass (ES/EF) and backward pass (LS/LF) in working-day indices; total float = LS - ES;
    activities with zero float are critical.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Dict, List, Optional, Tuple

from scheduling.productivity import default_rate

WEEKDAY_NAMES = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]

PHASES = {
    "PRE": "1 · Preliminaries",
    "SUB": "2 · Substructure",
    "STR": "3 · Grey structure",
    "MEP": "4 · MEP rough-in",
    "PLS": "5 · Plaster & roof",
    "FIN": "6 · Finishes",
    "FIT": "7 · Fittings & MEP final",
    "EXT": "8 · External works",
    "HND": "9 · Handover",
}

# default number of gangs working in parallel on one activity (editable)
DEFAULT_GANGS = {"PLI": 2, "FLR": 2, "PTI": 2, "EXP": 2, "ELF": 2}


@dataclass
class ScheduleSettings:
    start_date: str = ""  # ISO yyyy-mm-dd; "" = today
    work_weekdays: List[int] = field(default_factory=lambda: [0, 1, 2, 3, 4, 5])  # Mon..Sat (Sunday off)
    holidays: List[str] = field(default_factory=list)  # ISO dates with no work (Eid, public holidays)
    productivity_pct: float = 100.0  # 100 = norms as listed; 80 = slower site, 120 = faster
    one_gang_per_trade: bool = True  # same trade finishes one floor before starting the next
    footing_curing_days: int = 3  # calendar days after footing/plinth-beam pour before next work
    column_curing_days: int = 7  # calendar days after a slab pour before next-floor columns start
    deshuttering_days: int = 14  # calendar days after a slab pour before props come out / masonry below
    plaster_drying_days: int = 21  # calendar days plaster cures/dries before putty & paint
    rate_overrides: Dict[str, float] = field(default_factory=dict)  # "TEMPLATE|WI-ID" -> output per gang-day
    gang_overrides: Dict[str, float] = field(default_factory=dict)  # template key -> gangs
    duration_overrides: Dict[str, int] = field(default_factory=dict)  # activity id -> working days

    def start(self) -> date:
        try:
            return date.fromisoformat(self.start_date) if self.start_date else date.today()
        except ValueError:
            return date.today()


@dataclass
class Component:
    wi_id: str
    description: str
    qty: float
    unit: str
    rate: Optional[float] = None  # output per gang-day (None for lump-sum)
    gang_basis: str = ""

    @property
    def gang_days(self) -> float:
        return self.qty / self.rate if self.rate else 0.0


@dataclass
class Activity:
    id: str
    template: str
    name: str
    phase: str
    floor: str = ""
    components: List[Component] = field(default_factory=list)
    fixed_days: int = 0
    gangs: float = 1.0
    preds: List[Tuple[str, int]] = field(default_factory=list)  # (activity id, lag in calendar days)
    duration: int = 0
    overridden: bool = False
    es: int = 0
    ef: int = 0
    ls: int = 0
    lf: int = 0
    start: Optional[date] = None
    finish: Optional[date] = None
    total_float: int = 0
    critical: bool = False

    @property
    def gang_days(self) -> float:
        return sum(c.gang_days for c in self.components)

    @property
    def phase_name(self) -> str:
        return PHASES.get(self.phase, self.phase)

    def work_text(self) -> str:
        parts = [f"{c.qty:,.0f} {c.unit} {c.description.split('(')[0].strip().lower()}"
                 for c in self.components if c.qty > 0 and c.unit.upper() != "LS"]
        return "; ".join(parts)

    def basis_text(self) -> str:
        if self.overridden:
            return "Duration set by user"
        if self.fixed_days and not any(c.rate for c in self.components):
            return f"Fixed allowance {self.fixed_days} working days"
        bits = [f"{c.qty:,.0f} {c.unit} ÷ {c.rate:g}/gang-day" for c in self.components if c.rate and c.qty > 0]
        g = f" ÷ {self.gangs:g} gangs" if self.gangs != 1 else ""
        return f"({' + '.join(bits)}){g} = {self.gang_days / max(self.gangs, 0.01):.1f} days"


@dataclass
class Schedule:
    activities: List[Activity]
    settings: ScheduleSettings
    calendar: List[date]  # working dates, index = working-day number

    @property
    def start(self) -> Optional[date]:
        return min((a.start for a in self.activities if a.start), default=None)

    @property
    def finish(self) -> Optional[date]:
        return max((a.finish for a in self.activities if a.finish), default=None)

    @property
    def working_days(self) -> int:
        return (max(a.ef for a in self.activities) + 1) if self.activities else 0

    @property
    def calendar_days(self) -> int:
        return ((self.finish - self.start).days + 1) if self.activities else 0

    def critical_path(self) -> List[Activity]:
        return [a for a in self.activities if a.critical]

    def by_id(self, aid: str) -> Optional[Activity]:
        return next((a for a in self.activities if a.id == aid), None)

    def phase_spans(self) -> List[Tuple[str, date, date]]:
        out: Dict[str, List[date]] = {}
        for a in self.activities:
            out.setdefault(a.phase_name, []).extend([a.start, a.finish])
        return sorted(((k, min(v), max(v)) for k, v in out.items()), key=lambda x: (x[1], x[0]))


# ---------------------------------------------------------------------------
# calendar
# ---------------------------------------------------------------------------
class WorkCalendar:
    def __init__(self, start: date, weekdays: List[int], holidays: List[str], horizon: int = 4000):
        self.weekdays = set(weekdays) or {0, 1, 2, 3, 4, 5}
        self.holidays = set()
        for h in holidays or []:
            try:
                self.holidays.add(date.fromisoformat(str(h).strip()))
            except ValueError:
                continue
        self.dates: List[date] = []
        d = start
        while len(self.dates) < horizon:
            if self.is_work(d):
                self.dates.append(d)
            d += timedelta(days=1)
        self._index = {d: i for i, d in enumerate(self.dates)}

    def is_work(self, d: date) -> bool:
        return d.weekday() in self.weekdays and d not in self.holidays

    def first_on_or_after(self, d: date) -> int:
        if d <= self.dates[0]:
            return 0
        while d not in self._index:
            d += timedelta(days=1)
            if d > self.dates[-1]:
                return len(self.dates) - 1
        return self._index[d]

    def last_on_or_before(self, d: date) -> int:
        if d < self.dates[0]:
            return -1
        while d not in self._index:
            d -= timedelta(days=1)
        return self._index[d]


# ---------------------------------------------------------------------------
# activity network from the take-off
# ---------------------------------------------------------------------------
class _Builder:
    def __init__(self, res, settings: ScheduleSettings):
        self.s = settings
        self.acts: List[Activity] = []
        from detailed_mto.engine import INCLUDED_STATUSES
        # only work items that are quantified and included in the selected scope drive the schedule
        self.wi = {w.wi_id: w for w in res.scoped_work_items() if (w.qty or 0) > 0 and w.status in INCLUDED_STATUSES}
        self.project = res.project

    def q(self, wi_id: str) -> float:
        w = self.wi.get(wi_id)
        return float(w.qty) if w else 0.0

    def add(self, aid: str, template: str, name: str, phase: str, floor: str = "",
            parts: Optional[List[Tuple[str, float]]] = None, fixed_days: int = 0,
            preds: Optional[List[Tuple[str, int]]] = None) -> Activity:
        comps = []
        for wi_id, frac in parts or []:
            w = self.wi.get(wi_id)
            if w is None or frac <= 0:
                continue
            qty = float(w.qty) * frac
            if qty <= 1e-9:
                continue
            if w.unit.upper() == "LS":
                comps.append(Component(wi_id, w.description, qty, w.unit, None, "lump sum"))
                continue
            rate, basis = default_rate(template, wi_id)
            rate = float(self.s.rate_overrides.get(f"{template}|{wi_id}", rate) or rate)
            comps.append(Component(wi_id, w.description, qty, w.unit, rate, basis))
        # gangs: per-activity edit, else per-activity-type edit, else default
        gangs = float(self.s.gang_overrides.get(aid, self.s.gang_overrides.get(template, DEFAULT_GANGS.get(template, 1.0)))
                      or 1.0)
        a = Activity(aid, template, name, phase, floor, comps, fixed_days, max(gangs, 0.1),
                     [(p, lag) for p, lag in (preds or []) if p])
        self.acts.append(a)
        return a


def _weights(items: List[Tuple[str, float]]) -> Dict[str, float]:
    tot = sum(max(v, 0.0) for _, v in items)
    if tot <= 0:
        return {k: 1.0 / len(items) for k, _ in items} if items else {}
    return {k: max(v, 0.0) / tot for k, v in items}


def _network(res, s: ScheduleSettings) -> List[Activity]:
    b = _Builder(res, s)
    p = res.project
    storeys = list(p.storeys) or [f for f in p.floors] or []
    mumty = p.mumty
    if not storeys:
        from detailed_mto.model import Floor
        storeys = [Floor("ground", "Ground floor", 1.0, 0, 0, 0, 10)]
    fc, dsh, cure = s.footing_curing_days, s.deshuttering_days, s.column_curing_days
    chain = s.one_gang_per_trade

    # --- weights for splitting whole-building quantities floor by floor
    lv = [(f.key, f.covered_sft) for f in storeys] + ([("mumty", mumty.covered_sft)] if mumty else [])
    w_str = _weights(lv)  # structure & masonry (incl. mumty)
    fin = [(f.key, f.covered_sft) for f in storeys]
    if mumty and fin:
        fin[-1] = (fin[-1][0], fin[-1][1] + mumty.covered_sft)
    w_fin = _weights(fin)  # internal finishes (mumty folded into the top storey)
    n = len(storeys)

    # FW-02 (ply/timber formwork) is shared by footings, plinth beams, columns, stairs, lintels/chajjas and tanks
    fw2 = _weights([("ftg", b.q("WI-CN-03")), ("plb", b.q("WI-CN-04")), ("col", b.q("WI-CN-05")),
                    ("stair", b.q("WI-CN-08")), ("lin", b.q("WI-CN-09") + b.q("WI-CN-10")), ("tank", b.q("WI-CN-11"))])
    # RF-03 covers plinth beams + floor beams
    rf3 = _weights([("plb", b.q("WI-CN-04")), ("beam", b.q("WI-CN-06"))])

    # ---- 1 Preliminaries
    b.add("PRE", "PRE", "Mobilisation, site clearance & setting out", "PRE", parts=[("WI-PRE-01", 1)], fixed_days=5)

    # ---- 2 Substructure
    b.add("EXC", "EXC", "Excavation for foundations", "SUB", parts=[("WI-EW-01", 1), ("WI-EW-02", 1)], preds=[("PRE", 0)])
    b.add("PCC", "PCC", "Anti-termite & PCC 1:4:8 under footings", "SUB", parts=[("WI-CN-01", 1)], preds=[("EXC", 0)])
    b.add("FTG", "FTG", "RCC footings (steel, formwork, concrete)", "SUB",
          parts=[("WI-CN-03", 1), ("WI-RF-01", 1), ("WI-FW-02", fw2.get("ftg", 0))], preds=[("PCC", 1)])
    b.add("FMS", "FMS", "Foundation brick masonry (below DPC)", "SUB", parts=[("WI-MS-01", 1)], preds=[("FTG", fc)])
    b.add("PLB", "PLB", "Column stubs & plinth beams", "SUB",
          parts=[("WI-CN-04", 1), ("WI-RF-03", rf3.get("plb", 0)), ("WI-FW-02", fw2.get("plb", 0))],
          preds=[("FTG", fc), ("FMS", 0)])
    b.add("BKF", "BKF", "Backfill, plinth filling & disposal of surplus earth", "SUB",
          parts=[("WI-EW-04", 1), ("WI-EW-05", 1), ("WI-EW-09", 1)], preds=[("PLB", fc)])
    b.add("DPC", "DPC", "Damp-proof course (DPC)", "SUB", parts=[("WI-CN-12", 1)], preds=[("BKF", 0)])
    b.add("TNK", "TNK", "UG water tank & septic tank", "SUB",
          parts=[("WI-EW-03", 1), ("WI-CN-11", 1), ("WI-RF-07", 1), ("WI-FW-02", fw2.get("tank", 0)), ("WI-PL-04", 1)],
          preds=[("EXC", 0)])

    # ---- 3 Grey structure, floor by floor
    prev_slab = None
    for i, f in enumerate(storeys):
        w = w_str.get(f.key, 0)
        k = f.key.upper()[:3]
        col_preds = [("DPC", 0)] if i == 0 else [(prev_slab, cure)]
        b.add(f"COL-{k}", "COL", f"RCC columns - {f.name}", "STR", f.name,
              parts=[("WI-CN-05", w), ("WI-RF-02", w), ("WI-FW-02", fw2.get("col", 0) * w)], preds=col_preds)
        b.add(f"SLB-{k}", "SLB", f"Beams, slab & stair over {f.name}", "STR", f.name,
              parts=[("WI-CN-06", w), ("WI-CN-07", w), ("WI-RF-03", rf3.get("beam", 0) * w), ("WI-RF-04", w),
                     ("WI-FW-01", w), ("WI-CN-08", 1 / n), ("WI-RF-05", 1 / n), ("WI-FW-02", fw2.get("stair", 0) / n)],
              preds=[(f"COL-{k}", 0)])
        prev_slab = f"SLB-{k}"
    if mumty:
        w = w_str.get("mumty", 0)
        b.add("MUM", "MUM", "Mumty columns & slab", "STR", mumty.name,
              parts=[("WI-CN-05", w), ("WI-RF-02", w), ("WI-FW-02", fw2.get("col", 0) * w), ("WI-CN-06", w),
                     ("WI-CN-07", w), ("WI-RF-03", rf3.get("beam", 0) * w), ("WI-RF-04", w), ("WI-FW-01", w)],
              preds=[(prev_slab, cure)])

    # masonry, rough-ins, plaster, floors - per storey
    ids: Dict[str, List[str]] = {t: [] for t in ("MAS", "ELR", "PLR", "PLI", "FLR")}
    for i, f in enumerate(storeys):
        k = f.key.upper()[:3]
        w, wf = w_str.get(f.key, 0), w_fin.get(f.key, 0)
        mp = [(f"SLB-{k}", dsh)] + ([(ids["MAS"][-1], 0)] if chain and ids["MAS"] else [])
        b.add(f"MAS-{k}", "MAS", f"Brick masonry, door frames, lintels & bands - {f.name}", "STR", f.name,
              parts=[("WI-MS-02", w), ("WI-MS-03", w), ("WI-MS-06", w), ("WI-CN-09", w), ("WI-CN-10", w),
                     ("WI-RF-06", w), ("WI-FW-02", fw2.get("lin", 0) * w), ("WI-DW-01", wf)], preds=mp)
        ids["MAS"].append(f"MAS-{k}")
        if i == 0:
            b.add("GFB", "GFB", "Ground-floor base: sand fill, anti-termite, polythene, soling, PCC", "STR", f.name,
                  parts=[("WI-EW-06", 1), ("WI-EW-07", 1), ("WI-EW-08", 1), ("WI-MS-05", 1), ("WI-CN-02", 1)],
                  preds=[(f"MAS-{k}", 0)])
        b.add(f"ELR-{k}", "ELR", f"Electrical rough-in (chasing, conduits, boxes) - {f.name}", "MEP", f.name,
              parts=[(x, wf) for x in ("WI-EL-01", "WI-EL-02", "WI-EL-03", "WI-EL-04", "WI-EL-05", "WI-EL-06")],
              preds=[(f"MAS-{k}", 0)] + ([(ids["ELR"][-1], 0)] if chain and ids["ELR"] else []))
        ids["ELR"].append(f"ELR-{k}")
        b.add(f"PLR-{k}", "PLR", f"Plumbing, gas & AC pipe rough-in - {f.name}", "MEP", f.name,
              parts=[("WI-PB-06", wf), ("WI-PB-05", wf), ("WI-GS-01", wf), ("WI-HV-01", wf)],
              preds=[(f"MAS-{k}", 0)] + ([(ids["PLR"][-1], 0)] if chain and ids["PLR"] else []))
        ids["PLR"].append(f"PLR-{k}")
        b.add(f"PLI-{k}", "PLI", f"Internal plaster (walls & ceilings) - {f.name}", "PLS", f.name,
              parts=[("WI-PL-01", wf), ("WI-PL-03", wf)],
              preds=[(f"ELR-{k}", 0), (f"PLR-{k}", 0)] + ([(ids["PLI"][-1], 0)] if chain and ids["PLI"] else []))
        ids["PLI"].append(f"PLI-{k}")

    top_k = storeys[-1].key.upper()[:3]
    after_struct = "MUM" if mumty else f"SLB-{top_k}"
    wm = w_str.get("mumty", 0) if mumty else 0
    b.add("PAR", "PAR", "Parapet & mumty walls", "STR", "Roof",
          parts=[("WI-MS-04", 1), ("WI-MS-02", wm), ("WI-MS-03", wm), ("WI-MS-06", wm), ("WI-CN-09", wm),
                 ("WI-CN-10", wm), ("WI-RF-06", wm), ("WI-FW-02", fw2.get("lin", 0) * wm)],
          preds=[(after_struct, dsh)] + ([(ids["MAS"][-1], 0)] if chain else []))
    b.add("ROF", "ROF", "Roof waterproofing, slope screed & brick tiles", "PLS", "Roof",
          parts=[("WI-WP-01", 1), ("WI-WP-02", 1), ("WI-WP-04", 1)], preds=[("PAR", 0)])
    b.add("EXP", "EXP", "External plaster (scaffolding)", "PLS", "",
          parts=[("WI-PL-02", 1)], preds=[("PAR", 0)] + [(x, 0) for x in ids["MAS"]])

    # ---- 6 Finishes
    for i, f in enumerate(storeys):
        k = f.key.upper()[:3]
        wf = w_fin.get(f.key, 0)
        fp = [(f"PLI-{k}", 0)] + ([("GFB", 0)] if i == 0 else []) + ([(ids["FLR"][-1], 0)] if chain and ids["FLR"] else [])
        b.add(f"FLR-{k}", "FLR", f"Wet-area waterproofing, flooring & wall tiles - {f.name}", "FIN", f.name,
              parts=[("WI-WP-03", wf), ("WI-FL-01", wf), ("WI-FL-02", wf), ("WI-FL-03", wf), ("WI-FL-04", wf),
                     ("WI-FL-05", wf), ("WI-FL-08", wf)], preds=fp)
        ids["FLR"].append(f"FLR-{k}")
    all_pli = [(x, 0) for x in ids["PLI"]]
    all_flr = [(x, 0) for x in ids["FLR"]]
    b.add("STM", "STM", "Stair treads & risers (marble)", "FIN", "", parts=[("WI-FL-06", 1)], preds=all_pli + [("EXP", 0)])
    b.add("FCL", "FCL", "Gypsum false ceilings", "FIN", "", parts=[("WI-CL-01", 1)], preds=all_pli)
    b.add("WIN", "WIN", "Aluminium/uPVC windows & MS grills", "FIN", "",
          parts=[("WI-DW-04", 1), ("WI-DW-05", 1)], preds=all_pli + [("EXP", 0)])
    b.add("PTI", "PTI", "Interior painting (putty, primer, emulsion)", "FIN", "",
          parts=[("WI-PT-01", 1), ("WI-PT-02", 1)],
          preds=all_flr + [("FCL", 0)] + [(x, s.plaster_drying_days) for x in ids["PLI"]])
    b.add("PTE", "PTE", "Exterior painting (weather coat)", "FIN", "", parts=[("WI-PT-03", 1)],
          preds=[("EXP", s.plaster_drying_days), ("WIN", 0)])

    # ---- 7 Fittings & MEP final
    b.add("DOR", "DOR", "Door shutters & hardware", "FIT", "", parts=[("WI-DW-02", 1), ("WI-DW-03", 1)],
          preds=all_flr + [("PTI", 0)])
    b.add("JNR", "JNR", "Kitchen cabinets, countertop & wardrobes", "FIT", "",
          parts=[("WI-KT-01", 1), ("WI-KT-02", 1), ("WI-KT-03", 1), ("WI-JN-01", 1)], preds=all_flr)
    b.add("RAI", "RAI", "Stair/balcony railings & main gate", "FIT", "",
          parts=[("WI-DW-06", 1), ("WI-DW-07", 1)], preds=[("STM", 0)])
    b.add("PTM", "PTM", "Enamel on MS works & polish on doors", "FIT", "",
          parts=[("WI-PT-04", 1), ("WI-PT-05", 1)], preds=[("DOR", 0), ("RAI", 0), ("WIN", 0)])
    b.add("ELF", "ELF", "Wiring, DBs, earthing, light fixtures & fans", "FIT", "",
          parts=[(x, 1) for x in ("WI-EL-01", "WI-EL-02", "WI-EL-03", "WI-EL-04", "WI-EL-05", "WI-EL-06",
                                  "WI-EL-07", "WI-EL-08", "WI-EL-09", "WI-EL-10", "WI-HV-02")],
          preds=[("PTI", 0)])
    b.add("SAN", "SAN", "Sanitary fixtures, kitchen sink & washing-machine points", "FIT", "",
          parts=[(x, 1) for x in ("WI-PB-01", "WI-PB-02", "WI-PB-03", "WI-PB-04", "WI-PB-07", "WI-PB-08")],
          preds=all_flr + [("JNR", 0)])
    b.add("WSS", "WSS", "Pumps, overhead tank & water-supply system", "FIT", "Roof",
          parts=[("WI-PB-14", 1)], fixed_days=4, preds=[("ROF", 0), ("TNK", 0)])

    # ---- 8 External works
    b.add("EXD", "EXD", "Sewer line, manholes, gully trap, rainwater pipes & apron", "EXT", "",
          parts=[(x, 1) for x in ("WI-PB-09", "WI-PB-10", "WI-PB-11", "WI-PB-12", "WI-PB-13", "WI-CN-13")],
          preds=[("EXP", 0), ("TNK", 0), ("ROF", 0)])
    b.add("RWH", "RWH", "Rainwater-harvesting recharge well", "EXT", "", parts=[("WI-EX-02", 1)], preds=[("EXD", 0)])
    b.add("BND", "BND", "Boundary wall incl. foundation", "EXT", "", parts=[("WI-EX-01", 1)], preds=[("BKF", 0)])
    b.add("OUT", "OUT", "Outdoor pavers (porch, driveway)", "EXT", "", parts=[("WI-FL-07", 1)], preds=[("EXD", 0)])

    # ---- 9 Handover
    b.add("HND", "HND", "Testing, commissioning, snagging & handover", "HND", "", fixed_days=5)
    return b.acts


def _compute_durations(acts: List[Activity], s: ScheduleSettings) -> None:
    prod = max(float(s.productivity_pct or 100.0), 10.0) / 100.0
    for a in acts:
        ov = s.duration_overrides.get(a.id)
        if ov is not None and int(ov) > 0:
            a.duration, a.overridden = int(ov), True
            continue
        gd = a.gang_days / max(a.gangs, 0.1) / prod
        a.duration = max(int(math.ceil(gd - 1e-6)), a.fixed_days) if (gd > 0 or a.fixed_days) else 0
        if gd > 0:
            a.duration = max(a.duration, 1)


def _prune(acts: List[Activity]) -> List[Activity]:
    """Remove activities with no work (outside scope / zero quantity), bridging their links."""
    by = {a.id: a for a in acts}
    dead = {a.id for a in acts if a.duration <= 0 and a.id != "HND"}
    for a in acts:
        if a.id in dead:
            continue
        new: Dict[str, int] = {}
        stack = list(a.preds)
        seen = set()
        while stack:
            pid, lag = stack.pop()
            if (pid, lag) in seen or pid not in by:
                continue
            seen.add((pid, lag))
            if pid in dead:
                stack.extend((pp, pl + lag) for pp, pl in by[pid].preds)
            else:
                new[pid] = max(new.get(pid, -10 ** 6), lag)
        a.preds = list(new.items())
    live = [a for a in acts if a.id not in dead]
    # handover follows every activity that nothing else follows
    has_succ = {pid for a in live for pid, _ in a.preds}
    hnd = next((a for a in live if a.id == "HND"), None)
    if hnd is not None:
        hnd.preds = [(a.id, 0) for a in live if a.id != "HND" and a.id not in has_succ]
        if not hnd.preds and len(live) == 1:
            return []
    return live


def _topo(acts: List[Activity]) -> List[Activity]:
    by = {a.id: a for a in acts}
    indeg = {a.id: 0 for a in acts}
    succ: Dict[str, List[str]] = {a.id: [] for a in acts}
    for a in acts:
        for pid, _ in a.preds:
            if pid in by:
                indeg[a.id] += 1
                succ[pid].append(a.id)
    order = [a.id for a in acts if indeg[a.id] == 0]
    out = []
    while order:
        x = order.pop(0)
        out.append(by[x])
        for y in succ[x]:
            indeg[y] -= 1
            if indeg[y] == 0:
                order.append(y)
    if len(out) != len(acts):
        raise ValueError("The activity logic contains a loop")
    return out


def _cpm(acts: List[Activity], cal: WorkCalendar) -> None:
    order = _topo(acts)
    by = {a.id: a for a in acts}
    D = cal.dates
    for a in order:
        es = 0
        for pid, lag in a.preds:
            p = by[pid]
            es = max(es, cal.first_on_or_after(D[p.ef] + timedelta(days=1 + lag)) if p.duration else p.es)
        a.es, a.ef = es, es + a.duration - 1
    end = max(a.ef for a in acts)
    succs: Dict[str, List[Tuple[str, int]]] = {a.id: [] for a in acts}
    for a in acts:
        for pid, lag in a.preds:
            succs[pid].append((a.id, lag))
    for a in reversed(order):
        lf = end
        for sid, lag in succs[a.id]:
            s = by[sid]
            lf = min(lf, cal.last_on_or_before(D[s.ls] - timedelta(days=1 + lag)))
        a.lf, a.ls = lf, lf - a.duration + 1
    for a in acts:
        a.start, a.finish = D[a.es], D[max(a.ef, a.es)]
        a.total_float = max(a.ls - a.es, 0)
        a.critical = False
    # critical path = the longest (driving) path back from the project finish. A lag that spans a
    # non-working day can leave a spurious 1-day float on a driving activity, so walk the driving links.
    stack = [a for a in acts if a.ef == end]
    while stack:
        a = stack.pop()
        if a.critical:
            continue
        a.critical, a.total_float = True, 0
        for pid, lag in a.preds:
            p = by[pid]
            if cal.first_on_or_after(D[p.ef] + timedelta(days=1 + lag)) == a.es:
                stack.append(p)


def build_schedule(res, settings: Optional[ScheduleSettings] = None) -> Schedule:
    s = settings or ScheduleSettings()
    acts = _network(res, s)
    _compute_durations(acts, s)
    acts = _prune(acts)
    cal = WorkCalendar(s.start(), s.work_weekdays, s.holidays)
    if acts:
        _cpm(acts, cal)
        acts.sort(key=lambda a: (a.es, a.ef, a.phase))
    return Schedule(acts, s, cal.dates)


# ---------------------------------------------------------------------------
# material delivery plan: when each purchased material must be on site
# ---------------------------------------------------------------------------
STAGE_TO_TEMPLATES = {  # fallback for materials not linked to a work item
    "S0": ["PRE"], "S1": ["EXC", "PCC", "FTG"], "S2": ["SLB"], "S3": ["MAS"], "S4": ["COL"], "S5": ["ELR", "PLR"],
    "S6": ["PLI", "FLR"], "S7": ["ROF", "EXP"], "S8": ["EXD", "BND"], "S9": ["SAN", "ELF"],
}


def material_delivery_plan(res, sched: Schedule, lead_days: int = 3) -> List[dict]:
    from detailed_mto.engine import purchase_qty

    first: Dict[str, Activity] = {}
    for a in sorted(sched.activities, key=lambda x: x.es):
        for c in a.components:
            first.setdefault(c.wi_id, a)
    tmpl_first: Dict[str, Activity] = {}
    for a in sorted(sched.activities, key=lambda x: x.es):
        tmpl_first.setdefault(a.template, a)
    wis_of: Dict[str, set] = {}
    for c in res.contributions:
        if c.net_qty > 0:
            wis_of.setdefault(c.mat_id, set()).add(c.wi_id)
    rows = []
    for m in res.purchase_list():
        cand = [first[w] for w in (wis_of.get(m.material.mat_id, set()) | set(m.driven_by or [])) if w in first]
        if not cand:
            code = (m.material.stage or "S?").split(",")[0].split("-")[0].strip()
            cand = [tmpl_first[t] for t in STAGE_TO_TEMPLATES.get(code, []) if t in tmpl_first]
        if not cand:
            continue
        a = min(cand, key=lambda x: x.es)
        q, unit = purchase_qty(m.material, m.gross_qty)
        rows.append({"Material": m.material.description, "Quantity to buy": q, "Buy unit": unit,
                     "First used in": a.name, "Needed on site": a.start,
                     "Order by": a.start - timedelta(days=lead_days), "Trade": m.material.category,
                     "Code": m.material.mat_id})
    rows.sort(key=lambda r: (r["Needed on site"], r["Trade"], r["Material"]))
    return rows
