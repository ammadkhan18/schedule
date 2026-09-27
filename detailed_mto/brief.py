"""
Guided project brief - the route for users WITHOUT architectural drawings.

A normal user uploads a hand sketch / photo and/or describes the house in
words; the AI (optional) reads it, then the app asks the remaining questions.
The answers (a ProjectBrief) are converted here into exactly the inputs the
take-off engine already uses (rooms, doors/windows, floors, key dimensions,
options), so Steps 3-5 work unchanged.

Concept-stage accuracy: about +/-10-15 % with dimensions written on the
sketch, +/-20-30 % from a description only. Everything is editable in Step 3.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from detailed_mto.model import ASSUMED, MEDIUM, USER, Floor

FLOORS = ["ground", "first", "second"]
FLOOR_LABEL = {"ground": "Ground floor", "first": "First floor", "second": "Second floor", "roof": "Mumty / roof"}
ROOM_TYPES = ["Bedroom", "Bathroom", "Drawing room", "Lounge / TV lounge", "Kitchen", "Laundry", "Staircase",
              "Porch / car porch", "Terrace / balcony", "Store / utility", "Servant quarter", "Mumty", "Planter / lawn"]
TYPE_ALIASES = {
    "bedroom": "Bedroom", "bed": "Bedroom", "master": "Bedroom", "kids": "Bedroom", "guest": "Bedroom",
    "bath": "Bathroom", "bathroom": "Bathroom", "toilet": "Bathroom", "washroom": "Bathroom", "wc": "Bathroom",
    "drawing": "Drawing room", "drawing room": "Drawing room", "sitting": "Drawing room",
    "lounge": "Lounge / TV lounge", "tv lounge": "Lounge / TV lounge", "living": "Lounge / TV lounge", "family": "Lounge / TV lounge",
    "kitchen": "Kitchen", "laundry": "Laundry", "wash area": "Laundry", "stair": "Staircase", "staircase": "Staircase",
    "porch": "Porch / car porch", "car porch": "Porch / car porch", "garage": "Porch / car porch", "parking": "Porch / car porch",
    "terrace": "Terrace / balcony", "balcony": "Terrace / balcony", "store": "Store / utility", "utility": "Store / utility",
    "servant": "Servant quarter", "servant quarter": "Servant quarter", "mumty": "Mumty", "lawn": "Planter / lawn",
    "planter": "Planter / lawn", "garden": "Planter / lawn",
}
TYPICAL_SIZE = {  # (L, W) ft for a 5 marla (272.25) house - scaled with plot size
    "Bedroom": (12.0, 13.0), "Bathroom": (5.0, 8.5), "Drawing room": (13.5, 11.0), "Lounge / TV lounge": (18.0, 11.0),
    "Kitchen": (10.0, 9.0), "Laundry": (8.5, 4.0), "Staircase": (12.5, 9.0), "Porch / car porch": (12.0, 17.0),
    "Terrace / balcony": (10.0, 6.0), "Store / utility": (6.0, 5.0), "Servant quarter": (10.0, 10.0), "Mumty": (13.0, 13.0),
    "Planter / lawn": (6.0, 4.0),
}
BASE_PLOT_SFT = 1361.0  # 5 marla x 272.25


@dataclass
class BriefRoom:
    floor: str
    name: str
    room_type: str
    length_ft: float
    width_ft: float
    source: str = "Typical size"  # "Your sketch (AI)", "You", "Typical size"

    @property
    def area(self) -> float:
        return self.length_ft * self.width_ft

    @property
    def perimeter(self) -> float:
        return 2 * (self.length_ft + self.width_ft)


@dataclass
class ProjectBrief:
    # plot
    marla: float = 5.0
    marla_sqft: float = 272.25
    plot_width_ft: Optional[float] = None
    plot_depth_ft: Optional[float] = None
    storeys: int = 2
    mumty: bool = True
    side_walls: str = "Both sides shared"  # Both sides shared | One side shared (corner) | Independent
    # structure & levels
    structure: str = "Load-bearing brick"  # Load-bearing brick | RCC frame (columns & beams)
    floor_height_ft: float = 11.5
    plinth_ft: float = 1.5
    # rooms
    rooms: List[BriefRoom] = field(default_factory=list)
    # services
    ac_rooms: List[str] = field(default_factory=lambda: ["Bedrooms", "Lounges"])
    water_heating: str = "Gas geyser"  # Gas geyser | Electric geyser | Solar water heater
    ug_tank_gal: float = 1000.0
    oh_tank_gal: float = 600.0
    sewer: str = "Municipal sewer"  # Municipal sewer | Septic tank
    # outside
    boundary: str = "Front wall + gate only"  # Front wall + gate only | Front, back & sides | None
    gate_width_ft: float = 10.0
    # provenance / AI
    description: str = ""
    ai_notes: str = ""
    ai_questions: List[str] = field(default_factory=list)
    from_ai: List[str] = field(default_factory=list)  # which fields the AI filled

    @property
    def plot_area(self) -> float:
        return self.marla * self.marla_sqft

    def plot_dims(self) -> Tuple[float, float]:
        from engineering import plot_templates
        w = self.plot_width_ft
        if not w:
            w, _ = plot_templates.plot_dimensions_ft(self.marla, self.marla_sqft)
        d = self.plot_depth_ft or self.plot_area / w
        return w, d

    def floor_keys(self) -> List[str]:
        return FLOORS[: max(1, min(self.storeys, 3))]


# ---------------------------------------------------------------------------
# typical programme (used when the user/AI does not give rooms)
# ---------------------------------------------------------------------------
def _scale(marla: float, marla_sqft: float) -> float:
    return max(0.8, min(1.35, math.sqrt(marla * marla_sqft / BASE_PLOT_SFT)))


def typical_rooms(brief: ProjectBrief) -> List[BriefRoom]:
    """Typical Pakistani 5-10 marla programme for the plot size and storeys."""
    f = _scale(brief.marla, brief.marla_sqft)
    big = brief.marla >= 8
    out: List[BriefRoom] = []

    def add(floor, name, t, scale=True):
        L, W = TYPICAL_SIZE[t]
        k = f if scale else 1.0
        out.append(BriefRoom(floor, name, t, round(L * k * 2) / 2, round(W * k * 2) / 2, "Typical size"))

    keys = brief.floor_keys()
    # ground floor
    add("ground", "CAR PORCH", "Porch / car porch", scale=False)
    add("ground", "DRAWING ROOM", "Drawing room")
    add("ground", "LOUNGE", "Lounge / TV lounge")
    add("ground", "KITCHEN", "Kitchen")
    add("ground", "BED ROOM 1", "Bedroom")
    add("ground", "BATH 1", "Bathroom", scale=False)
    if len(keys) == 1 or big:
        add("ground", "BED ROOM 2", "Bedroom")
        add("ground", "BATH 2", "Bathroom", scale=False)
    if len(keys) > 1:
        add("ground", "STAIRCASE", "Staircase", scale=False)
    add("ground", "LAUNDRY", "Laundry", scale=False)
    add("ground", "POWDER ROOM", "Bathroom", scale=False)
    # upper floors
    n_bed_up = 3 if big else 2
    for fk in keys[1:]:
        tag = "FF" if fk == "first" else "SF"
        add(fk, f"{tag} TV LOUNGE", "Lounge / TV lounge")
        for i in range(n_bed_up):
            add(fk, f"{tag} BED ROOM {i + 1}", "Bedroom")
            add(fk, f"{tag} BATH {i + 1}", "Bathroom", scale=False)
        add(fk, f"{tag} KITCHEN", "Kitchen")
        add(fk, f"{tag} STAIRCASE", "Staircase", scale=False)
        add(fk, f"{tag} FRONT TERRACE", "Terrace / balcony", scale=False)
    return out


def add_attached_baths(rooms: List[BriefRoom]) -> List[BriefRoom]:
    """Make sure every bedroom has a bathroom on the same floor (typical in Pakistan)."""
    out = list(rooms)
    for fk in {r.floor for r in rooms}:
        beds = [r for r in rooms if r.floor == fk and r.room_type == "Bedroom"]
        baths = [r for r in rooms if r.floor == fk and r.room_type == "Bathroom"]
        for i in range(len(baths), len(beds)):
            L, W = TYPICAL_SIZE["Bathroom"]
            out.append(BriefRoom(fk, f"BATH (attached to {beds[i].name})", "Bathroom", L, W, "Typical size"))
    return out


# ---------------------------------------------------------------------------
# free-text parsing without AI (fallback)
# ---------------------------------------------------------------------------
_NUMWORDS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "single": 1, "double": 2, "triple": 3}


def _num(tok: str) -> Optional[float]:
    tok = tok.lower()
    if tok in _NUMWORDS:
        return float(_NUMWORDS[tok])
    try:
        return float(tok)
    except ValueError:
        return None


def parse_description(text: str, brief: ProjectBrief) -> List[str]:
    """Rule-based reading of a description like '5 marla double storey, 4 bedrooms with attached baths,
    corner plot, RCC frame'. Updates the brief; returns what was understood (for display)."""
    t = (text or "").lower()
    got: List[str] = []
    m = re.search(r"(\d+(?:\.\d+)?)\s*marla", t)
    if m:
        brief.marla = float(m.group(1))
        got.append(f"{brief.marla:g} marla plot")
    m = re.search(r"(\d+|one|two|three|single|double|triple)[\s-]*(?:storey|story|stories|storeys|floor house)", t)
    if m and _num(m.group(1)):
        brief.storeys = int(_num(m.group(1)))
        got.append(f"{brief.storeys} storey(s)")
    elif re.search(r"g\s*\+\s*2", t):
        brief.storeys = 3
        got.append("3 storeys (G+2)")
    elif re.search(r"g\s*\+\s*1", t):
        brief.storeys = 2
        got.append("2 storeys (G+1)")
    m = re.search(r"(\d+)\s*(?:ft|feet|')?\s*(?:x|by|\*)\s*(\d+)\s*(?:ft|feet|')?\s*plot|plot\s*(?:of|size)?\s*(\d+)\s*(?:x|by)\s*(\d+)", t)
    if m:
        a, b = [float(x) for x in m.groups() if x][:2]
        brief.plot_width_ft, brief.plot_depth_ft = min(a, b), max(a, b)
        got.append(f"plot {brief.plot_width_ft:g}' x {brief.plot_depth_ft:g}'")
    if "corner" in t:
        brief.side_walls = "One side shared (corner)"
        got.append("corner plot")
    if re.search(r"rcc frame|column|framed|frame structure", t):
        brief.structure = "RCC frame (columns & beams)"
        got.append("RCC frame")
    elif re.search(r"load[- ]?bearing", t):
        brief.structure = "Load-bearing brick"
        got.append("load-bearing walls")
    if "septic" in t:
        brief.sewer = "Septic tank"
        got.append("septic tank")
    if "solar" in t:
        brief.water_heating = "Solar water heater"
        got.append("solar water heating")
    if "no mumty" in t or "without mumty" in t:
        brief.mumty = False
    # room counts
    rooms = typical_rooms(brief)
    m = re.search(r"(\d+|one|two|three|four|five|six)\s*(?:bed ?rooms?|beds?\b|bhk)", t)
    if m and _num(m.group(1)):
        n = int(_num(m.group(1)))
        rooms = _set_bedroom_count(rooms, n, brief)
        got.append(f"{n} bedrooms")
    if re.search(r"attached bath|with bath|ensuite|en-suite", t):
        rooms = add_attached_baths(rooms)
        got.append("attached bathrooms")
    for word, rtype, name in ((r"\bservant", "Servant quarter", "SERVANT QUARTER"), (r"\bstore\b|\bstore room", "Store / utility", "STORE"),
                              (r"\blawn\b|\bgarden\b", "Planter / lawn", "LAWN")):
        if re.search("(?:" + word + r")(?!\s*(?:no|not)\b)", t) and not re.search(r"(?:no|without)\s+(?:a\s+)?(?:" + word + ")", t) \
                and not any(r.room_type == rtype for r in rooms):
            L, W = TYPICAL_SIZE[rtype]
            rooms.append(BriefRoom("ground", name, rtype, L, W, "Your description"))
            got.append(name.lower())
    if re.search(r"no (?:drawing|drawing room)", t):
        rooms = [r for r in rooms if r.room_type != "Drawing room"]
    brief.rooms = rooms
    return got


def _set_bedroom_count(rooms: List[BriefRoom], n: int, brief: ProjectBrief) -> List[BriefRoom]:
    beds = [r for r in rooms if r.room_type == "Bedroom"]
    if n == len(beds):
        return rooms
    rest = [r for r in rooms if r.room_type not in ("Bedroom", "Bathroom")]
    keys = brief.floor_keys()
    f = _scale(brief.marla, brief.marla_sqft)
    out = list(rest)
    per_floor = [n // len(keys) + (1 if i < n % len(keys) else 0) for i in range(len(keys))]
    for fk, cnt in zip(keys, per_floor):
        for i in range(cnt):
            L, W = TYPICAL_SIZE["Bedroom"]
            out.append(BriefRoom(fk, f"{fk[:1].upper()}F BED ROOM {i + 1}", "Bedroom", round(L * f * 2) / 2, round(W * f * 2) / 2,
                                 "Your description"))
            bl, bw = TYPICAL_SIZE["Bathroom"]
            out.append(BriefRoom(fk, f"{fk[:1].upper()}F BATH {i + 1}", "Bathroom", bl, bw, "Typical size"))
    return out


# ---------------------------------------------------------------------------
# AI JSON -> brief
# ---------------------------------------------------------------------------
_FTIN = re.compile(r"(\d+(?:\.\d+)?)\s*(?:'|ft|feet)?\s*-?\s*(\d+(?:\.\d+)?)?\s*(?:\"|in)?")


def to_feet(v) -> Optional[float]:
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v) if v > 0 else None
    s = str(v).strip().replace("’", "'").replace("”", '"')
    m = _FTIN.match(s)
    if not m:
        return None
    ft = float(m.group(1))
    inch = float(m.group(2)) if m.group(2) and ("'" in s or "ft" in s) else 0.0
    val = ft + inch / 12.0
    return val if val > 0 else None


def room_type_of(name: str, given: Optional[str] = None) -> str:
    for cand in (given or "", name or ""):
        c = cand.lower().strip()
        if not c:
            continue
        for rt in ROOM_TYPES:
            if c == rt.lower():
                return rt
        best, blen = None, 0
        for alias, rt in TYPE_ALIASES.items():
            if alias in c and len(alias) > blen:
                best, blen = rt, len(alias)
        if best:
            return best
    return "Bedroom"


def apply_ai_result(data: dict, brief: ProjectBrief) -> List[str]:
    """Merge the AI's JSON reading of the sketch/description into the brief. Returns what was understood."""
    got: List[str] = []
    if not isinstance(data, dict):
        return got
    plot = data.get("plot") or {}
    if to_feet(plot.get("marla")):
        brief.marla = float(to_feet(plot.get("marla")))
        got.append(f"{brief.marla:g} marla")
        brief.from_ai.append("marla")
    w, d = to_feet(plot.get("width_ft")), to_feet(plot.get("depth_ft"))
    if w and d:
        brief.plot_width_ft, brief.plot_depth_ft = min(w, d), max(w, d)
        got.append(f"plot {brief.plot_width_ft:g}' x {brief.plot_depth_ft:g}'")
        brief.from_ai.append("plot size")
    st = data.get("storeys")
    if isinstance(st, (int, float)) and 1 <= st <= 3:
        brief.storeys = int(st)
        got.append(f"{brief.storeys} storey(s)")
        brief.from_ai.append("storeys")
    s = str(data.get("structure") or "").lower()
    if "frame" in s or "rcc" in s or "column" in s:
        brief.structure = "RCC frame (columns & beams)"
        brief.from_ai.append("structure")
    elif "load" in s or "brick" in s:
        brief.structure = "Load-bearing brick"
        brief.from_ai.append("structure")
    rooms: List[BriefRoom] = []
    for fl in data.get("floors") or []:
        fk = str((fl or {}).get("floor") or "ground").lower()
        fk = "ground" if fk.startswith(("g", "0")) else "first" if fk.startswith(("f", "1")) else \
            "second" if fk.startswith(("s", "2")) else "roof" if fk.startswith(("r", "m")) else "ground"
        for r in (fl or {}).get("rooms") or []:
            name = str(r.get("name") or r.get("type") or "ROOM").upper()[:40]
            rt = room_type_of(name, r.get("type"))
            L, W = to_feet(r.get("length_ft")), to_feet(r.get("width_ft"))
            src = "Your sketch (AI)"
            if not (L and W):
                L, W = TYPICAL_SIZE.get(rt, (10.0, 10.0))
                src = "Typical size (not readable on sketch)"
            rooms.append(BriefRoom(fk, name, rt, round(L, 2), round(W, 2), src))
    if rooms:
        if any(r.floor == "roof" for r in rooms):
            brief.mumty = True
            rooms = [r for r in rooms if r.floor != "roof"]
        max_floor = max(FLOORS.index(r.floor) for r in rooms if r.floor in FLOORS) + 1
        brief.storeys = max(brief.storeys, max_floor)
        brief.rooms = rooms
        got.append(f"{len(rooms)} rooms ({sum(1 for r in rooms if r.source.startswith('Your'))} with sizes)")
        brief.from_ai.append("rooms")
    feats = data.get("features") or {}
    if feats.get("corner_plot"):
        brief.side_walls = "One side shared (corner)"
        got.append("corner plot")
    if feats.get("septic_tank"):
        brief.sewer = "Septic tank"
    if feats.get("mumty") is False:
        brief.mumty = False
    brief.ai_notes = str(data.get("notes") or "")[:600]
    qs = data.get("questions") or []
    brief.ai_questions = [str(q)[:200] for q in qs if str(q).strip()][:8]
    return got


def complete_programme(brief: ProjectBrief) -> List[str]:
    """Add rooms every house needs but a sketch often leaves out (stairs, a kitchen, a bath per floor).
    Returns what was added so the user can see it."""
    added: List[str] = []
    keys = brief.floor_keys()
    has = lambda fk, t: any(r.floor == fk and r.room_type == t for r in brief.rooms)  # noqa: E731

    def add(fk, name, t):
        L, W = TYPICAL_SIZE[t]
        brief.rooms.append(BriefRoom(fk, name, t, L, W, "Added (typical size)"))
        added.append(f"{name.lower()} ({FLOOR_LABEL[fk].lower()})")

    if not any(r.room_type == "Kitchen" for r in brief.rooms):
        add("ground", "KITCHEN", "Kitchen")
    for fk in keys:
        occupied = any(r.floor == fk for r in brief.rooms)
        if len(keys) > 1 and occupied and not has(fk, "Staircase") and (fk != keys[-1] or brief.mumty):
            add(fk, "STAIRCASE", "Staircase")
        if occupied and not has(fk, "Bathroom"):
            add(fk, "BATH", "Bathroom")
    return added


# ---------------------------------------------------------------------------
# brief -> engine inputs
# ---------------------------------------------------------------------------
def _floor_geometry(rooms: List[BriefRoom], width_ft: float, plot_depth_ft: float, load_bearing: bool,
                    fk: str, rear_open_ft: float) -> Floor:
    enclosed = [r for r in rooms if r.room_type not in ("Terrace / balcony", "Planter / lawn")]
    room_area = sum(r.area for r in enclosed)
    covered = room_area * 1.28  # walls, passages & shafts: ~28 % on 5-10 marla plans (calibrated on a real 5 marla set)
    max_cov = width_ft * max(plot_depth_ft - (0 if fk != "ground" else rear_open_ft), 10)
    covered = min(covered, max_cov) if max_cov > 0 else covered
    depth = covered / max(width_ft, 1)
    ext = 2 * (width_ft + depth)
    total_cl = (sum(r.perimeter for r in enclosed) + ext) / 2.0  # shared walls counted once
    internal = max(total_cl - ext, 0)
    # share of internal walls built 9" (rest 4.5" partitions) - calibrated on a real 5 marla load-bearing set
    w9 = ext + internal * (0.25 if load_bearing else 0.15)
    w45 = internal - (w9 - ext)
    return Floor(fk, FLOOR_LABEL[fk], round(covered, 1), round(ext, 1), round(w9, 1), round(w45, 1), 11.5,
                 source="Concept layout from your rooms (walls ~ shared room edges)", confidence=MEDIUM)


def _openings(rooms: List[BriefRoom], storeys: int, mumty: bool) -> List[dict]:
    rows: List[dict] = []

    def add(kind, name, w, h, qty, leaves=1, chog=5.0, ext=False, src="Typical for this room type"):
        if qty > 0:
            rows.append({"Kind": kind, "Name": name, "Width (ft)": w, "Height (ft)": h, "Qty": qty, "Leaves": leaves,
                         "Chogath (in)": chog, "External": ext, "Source": src, "Confidence": ASSUMED})

    n = lambda *types: sum(1 for r in rooms if r.room_type in types)  # noqa: E731
    add("door", "Main entrance door", 4.5, 8.0, 1, 2, 10.0, True)
    add("door", "Room doors", 3.5, 7.0, n("Bedroom", "Drawing room", "Kitchen", "Store / utility", "Servant quarter")
        + max(storeys - 1, 0))
    add("door", "Bath doors", 2.5, 7.0, n("Bathroom", "Laundry"))
    if mumty:
        add("door", "Mumty / roof door", 3.0, 7.0, 1, 1, 5.0, True)
    add("window", "Windows - rooms", 4.0, 5.0, n("Bedroom", "Drawing room", "Lounge / TV lounge", "Servant quarter"), 2, 0, True)
    add("window", "Windows - kitchens", 3.0, 4.0, n("Kitchen"), 2, 0, True)
    add("window", "Stair windows", 3.0, 4.0, n("Staircase"), 2, 0, True)
    add("ventilator", "Bath ventilators", 2.0, 1.5, n("Bathroom", "Laundry"), 1, 0, True)
    if mumty:
        add("window", "Mumty window", 3.0, 3.0, 1, 2, 0, True)
    return rows


def brief_to_inputs(brief: ProjectBrief) -> dict:
    """-> {'rooms': rows, 'openings': rows, 'floors': [Floor], 'overrides': {...}, 'options': {...}, 'structure': str}"""
    w, d = brief.plot_dims()
    rooms = [r for r in brief.rooms if r.length_ft > 0 and r.width_ft > 0 and r.floor in FLOORS[: brief.storeys]]
    load_bearing = brief.structure.startswith("Load")
    front_rear = 0.2 * d  # porch/lawn/rear open space on the ground floor (typical)
    floors: List[Floor] = []
    for fk in brief.floor_keys():
        fr = [r for r in rooms if r.floor == fk]
        if not fr:
            continue
        fl = _floor_geometry(fr, w, d, load_bearing, fk, front_rear)
        fl.storey_height_ft = brief.floor_height_ft
        floors.append(fl)
    if brief.mumty and floors:
        area = 13.0 * 13.0 * _scale(brief.marla, brief.marla_sqft) ** 2
        per = 4 * math.sqrt(area)
        floors.append(Floor("roof", "Mumty", round(area, 1), round(per, 1), round(per, 1), 0.0, 9.0, True,
                            "Typical mumty over the stair", ASSUMED))
    room_rows = []
    for r in rooms:
        conf = USER if r.source in ("You", "Your description") else (MEDIUM if r.source.startswith("Your sketch") else ASSUMED)
        room_rows.append({"Floor": r.floor, "Room": r.name, "Room type": r.room_type, "Length (ft)": round(r.length_ft, 2),
                          "Width (ft)": round(r.width_ft, 2), "Source": r.source, "Confidence": conf})
    if brief.mumty:
        mt = floors[-1] if floors and floors[-1].is_mumty else None
        if mt:
            side = round(math.sqrt(mt.covered_sft * 0.8), 2)
            room_rows.append({"Floor": "roof", "Room": "MUMTY", "Room type": "Mumty", "Length (ft)": side, "Width (ft)": side,
                              "Source": "Typical mumty", "Confidence": ASSUMED})

    # key answers -> engine parameters (shown as the user's own inputs in Step 3)
    n = lambda *types: sum(1 for r in rooms if r.room_type in types)  # noqa: E731
    ov: Dict[str, float] = {
        "H_FLOOR": brief.floor_height_ft,
        "H_PLINTH": brief.plinth_ft,
        "PLOT_W": w,
        "PLOT_D": d,
        "EXT_EXPOSED_FRAC": {"Both sides shared": 0.6, "One side shared (corner)": 0.8}.get(brief.side_walls, 1.0),
        "GATE_W": brief.gate_width_ft,
    }
    ac = 0
    if "Bedrooms" in brief.ac_rooms:
        ac += n("Bedroom")
    if "Lounges" in brief.ac_rooms:
        ac += n("Lounge / TV lounge")
    if "Drawing room" in brief.ac_rooms:
        ac += n("Drawing room")
    ov["N_AC"] = ac
    geysers = max(1, len(brief.floor_keys()))
    ov["N_GEYSER"] = geysers
    ug_side = math.sqrt(max(brief.ug_tank_gal, 100) / 6.229 / 5.0)
    ov.update({"UGT_L": round(ug_side, 2), "UGT_W": round(ug_side, 2), "UGT_D": 5.0})
    ov["N_OHT"] = 1 if brief.oh_tank_gal > 0 else 0
    ov["SEPTIC_N"] = 1 if brief.sewer == "Septic tank" else 0
    if brief.boundary == "None":
        ov["BOUNDARY_LEN"] = 0
    elif brief.boundary == "Front wall + gate only":
        ov["BOUNDARY_LEN"] = max(w - brief.gate_width_ft, 0)
    else:
        ov["BOUNDARY_LEN"] = max(2 * d + w - brief.gate_width_ft, 0)
    if brief.water_heating != "Gas geyser":
        ov["N_GAS"] = n("Kitchen")
    return {"rooms": room_rows, "openings": _openings(rooms, len(brief.floor_keys()), brief.mumty), "floors": floors,
            "overrides": ov, "structure": "strip" if load_bearing else "isolated", "load_bearing": load_bearing}


def brief_summary(brief: ProjectBrief) -> List[Tuple[str, str]]:
    w, d = brief.plot_dims()
    beds = sum(1 for r in brief.rooms if r.room_type == "Bedroom")
    baths = sum(1 for r in brief.rooms if r.room_type == "Bathroom")
    return [
        ("Plot", f"{brief.marla:g} marla ({brief.plot_area:,.0f} sft), {w:.0f}' x {d:.0f}', {brief.side_walls.lower()}"),
        ("House", f"{brief.storeys} storey(s){' + mumty' if brief.mumty else ''}, {brief.structure.lower()}, "
                  f"floor height {brief.floor_height_ft:g}'"),
        ("Rooms", f"{len(brief.rooms)} rooms: {beds} bedrooms, {baths} bathrooms, "
                  f"{sum(1 for r in brief.rooms if r.room_type == 'Kitchen')} kitchen(s)"),
        ("Services", f"AC in {', '.join(brief.ac_rooms) or 'no rooms'}; {brief.water_heating.lower()}; "
                     f"UG tank {brief.ug_tank_gal:,.0f} gal, OH tank {brief.oh_tank_gal:,.0f} gal; {brief.sewer.lower()}"),
        ("Outside", f"{brief.boundary.lower()}, gate {brief.gate_width_ft:g}'"),
    ]
