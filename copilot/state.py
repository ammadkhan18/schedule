"""
Project state for the copilot, independent of Streamlit so it can be tested.

A ProjectState holds exactly the inputs the take-off uses (the same objects the
app keeps in session state). Changes proposed by the copilot / checker /
interviewer are plain dicts that are validated and applied to a COPY of the
state - nothing is changed until the user presses Apply.

Change types
------------
  {"type": "set_param",      "key": "N_AC", "value": 6}
  {"type": "reset_param",    "key": "N_AC"}
  {"type": "set_option",     "name": "masonry", "value": "Block"}
  {"type": "update_room",    "room": "BED ROOM", "floor": "first"?, "length_ft"?, "width_ft"?, "room_type"?, "new_name"?}
  {"type": "add_room",       "room": "STORE", "floor": "ground", "room_type": "Store / utility", "length_ft": 6, "width_ft": 5}
  {"type": "remove_room",    "room": "LAUNDRY", "floor": "ground"?}
  {"type": "update_opening", "name": "Windows", "width_ft"?, "height_ft"?, "qty"?}   (name matches by substring)
  {"type": "add_opening",    "name": "...", "kind": "window", "width_ft": 4, "height_ft": 5, "qty": 2, "external": true}
"""
from __future__ import annotations

import copy
from dataclasses import dataclass, field, fields, replace
from typing import Any, Dict, List, Optional, Tuple

from detailed_mto import build_project, compute
from detailed_mto.edits import rows_to_openings, rows_to_rooms
from detailed_mto.engine import DetailedResult
from detailed_mto.model import DetailedProject, Options
from detailed_mto.validation import errors as val_errors
from detailed_mto.validation import validate_project
from knowledge import load_knowledge_base

ROOM_TYPES = ["Bedroom", "Bathroom", "Drawing room", "Lounge / TV lounge", "Kitchen", "Laundry", "Staircase",
              "Porch / car porch", "Terrace / balcony", "Store / utility", "Servant quarter", "Mumty", "Planter / lawn"]
OPTION_CHOICES = {
    "scope": None,  # validated against the knowledge base
    "finish_tier": ["Economy", "Standard", "Premium"],
    "roof_system": ["Traditional", "Insulated"],
    "masonry": ["Brick", "Block"],
    "gas_source": ["SNGPL", "LPG", "None"],
    "rcc_mix": ["MX_RCC124", "MX_RCC1153"],
    "include_false_ceiling": [True, False],
    "include_rwh": [True, False],
    "include_options": [True, False],
    "seismic_bands": [True, False],
}


@dataclass
class ProjectState:
    pi: Any  # models.schemas.ProjectInputs
    params: Any  # models.schemas.ExtractedBuildingParams
    rooms: Optional[List[dict]] = None
    openings: Optional[List[dict]] = None
    overrides: Dict[str, float] = field(default_factory=dict)
    options: Options = field(default_factory=Options)
    floors: Optional[list] = None  # concept floors (sketch route)
    facts: Any = None
    scan: Any = None
    mode: str = "cad"

    def copy(self) -> "ProjectState":
        return ProjectState(self.pi, self.params, copy.deepcopy(self.rooms), copy.deepcopy(self.openings),
                            dict(self.overrides), replace(self.options), self.floors, self.facts, self.scan, self.mode)

    def snapshot(self) -> dict:
        """What the user can change (for undo / scenarios)."""
        return {"rooms": copy.deepcopy(self.rooms), "openings": copy.deepcopy(self.openings),
                "overrides": dict(self.overrides), "options": replace(self.options)}

    def restore(self, snap: dict) -> "ProjectState":
        s = self.copy()
        s.rooms, s.openings = copy.deepcopy(snap["rooms"]), copy.deepcopy(snap["openings"])
        s.overrides, s.options = dict(snap["overrides"]), replace(snap["options"])
        return s


def build(state: ProjectState) -> DetailedProject:
    return build_project(state.pi, state.params, load_knowledge_base(), facts=state.facts, scan=state.scan,
                         options=state.options,
                         rooms_override=rows_to_rooms(state.rooms) if state.rooms is not None else None,
                         openings_override=rows_to_openings(state.openings) if state.openings is not None else None,
                         overrides=state.overrides, drawing_mode=state.mode,
                         floors_override=state.floors if state.mode == "sketch" else None)


def run(state: ProjectState) -> DetailedResult:
    return compute(build(state), load_knowledge_base(), scope=state.options.scope)


# ---------------------------------------------------------------------------
# applying changes
# ---------------------------------------------------------------------------
def _num(v, name) -> float:
    try:
        f = float(v)
    except (TypeError, ValueError):
        raise ValueError(f"{name} must be a number (got {v!r})")
    if f != f:
        raise ValueError(f"{name} must be a number")
    return f


def _find_rooms(rows: List[dict], name: str, floor: Optional[str]) -> List[int]:
    n = (name or "").strip().upper()
    idx = [i for i, r in enumerate(rows) if str(r.get("Room", "")).strip().upper() == n
           and (not floor or r.get("Floor") == floor)]
    if not idx:  # forgiving substring match ("master bed" -> "MASTER BED ROOM (1)")
        idx = [i for i, r in enumerate(rows) if n and n in str(r.get("Room", "")).upper()
               and (not floor or r.get("Floor") == floor)]
    return idx


def apply_change(state: ProjectState, ch: dict) -> Tuple[ProjectState, str]:
    """Apply ONE change to a copy of the state. Returns (new_state, human description). Raises ValueError."""
    s = state.copy()
    t = ch.get("type")
    if t == "set_param":
        key = str(ch.get("key", ""))
        p = build(s)
        if key not in p.params:
            raise ValueError(f"Unknown input '{key}'")
        v = _num(ch.get("value"), key)
        old = p.params[key].value
        s.overrides[key] = v
        return s, f"{p.params[key].label}: {old:,.2f} → {v:,.2f} {p.params[key].unit}"
    if t == "reset_param":
        key = str(ch.get("key", ""))
        s.overrides.pop(key, None)
        return s, f"{key} reset to the drawing/default value"
    if t == "set_option":
        name, val = ch.get("name"), ch.get("value")
        if name not in OPTION_CHOICES:
            raise ValueError(f"Unknown option '{name}'")
        choices = OPTION_CHOICES[name]
        if name == "scope":
            if val not in load_knowledge_base().scope_names:
                raise ValueError(f"Unknown scope '{val}'")
        elif isinstance(choices[0], bool):
            val = val if isinstance(val, bool) else str(val).lower() in ("true", "yes", "1", "on")
        elif val not in choices:
            raise ValueError(f"{name} must be one of {choices}")
        old = getattr(s.options, name)
        s.options = replace(s.options, **{name: val})
        return s, f"{name.replace('_', ' ')}: {old} → {val}"
    if t in ("update_room", "remove_room", "add_room"):
        if s.rooms is None:
            s.rooms = []
        name = str(ch.get("room") or "").strip()
        if not name:
            raise ValueError("room name is required")
        if t == "add_room":
            rt = ch.get("room_type") or "Bedroom"
            if rt not in ROOM_TYPES:
                raise ValueError(f"room_type must be one of {ROOM_TYPES}")
            L, W = _num(ch.get("length_ft"), "length_ft"), _num(ch.get("width_ft"), "width_ft")
            fl = ch.get("floor") or "ground"
            s.rooms.append({"Floor": fl, "Room": name.upper(), "Room type": rt, "Length (ft)": L, "Width (ft)": W,
                            "Source": "User input (copilot)", "Confidence": "User"})
            return s, f"Add {name.upper()} ({rt}) {L:g}'x{W:g}' on {fl} floor"
        idx = _find_rooms(s.rooms, name, ch.get("floor"))
        if not idx:
            raise ValueError(f"No room called '{name}'" + (f" on {ch.get('floor')}" if ch.get("floor") else ""))
        if len(idx) > 1 and ch.get("nth"):
            k = int(_num(ch["nth"], "nth")) - 1
            if not 0 <= k < len(idx):
                raise ValueError(f"nth must be between 1 and {len(idx)}")
            idx = [idx[k]]
        if len(idx) > 1 and not ch.get("all"):
            options = "; ".join(f"#{n + 1} {s.rooms[i]['Room']} ({s.rooms[i]['Floor']}, {s.rooms[i]['Length (ft)']:g}'x"
                                f"{s.rooms[i]['Width (ft)']:g}')" for n, i in enumerate(idx[:6]))
            raise ValueError(f"'{name}' matches several rooms: {options}. Say which one (nth=1, 2 ...) or all=true")
        if t == "remove_room":
            removed = [s.rooms[i]["Room"] for i in idx]
            s.rooms = [r for i, r in enumerate(s.rooms) if i not in idx]
            return s, "Remove " + ", ".join(removed)
        descs = []
        for i in idx:
            r = dict(s.rooms[i])
            before = f"{r['Length (ft)']:g}'x{r['Width (ft)']:g}'"
            if ch.get("length_ft") is not None:
                r["Length (ft)"] = _num(ch["length_ft"], "length_ft")
            if ch.get("width_ft") is not None:
                r["Width (ft)"] = _num(ch["width_ft"], "width_ft")
            if ch.get("room_type"):
                if ch["room_type"] not in ROOM_TYPES:
                    raise ValueError(f"room_type must be one of {ROOM_TYPES}")
                r["Room type"] = ch["room_type"]
            if ch.get("new_name"):
                r["Room"] = str(ch["new_name"]).upper()
            r["Source"], r["Confidence"] = "User input (copilot)", "User"
            s.rooms[i] = r
            descs.append(f"{r['Room']}: {before} → {r['Length (ft)']:g}'x{r['Width (ft)']:g}' ({r['Room type']})")
        return s, "; ".join(descs)
    if t in ("update_opening", "add_opening"):
        if s.openings is None:
            s.openings = []
        name = str(ch.get("name") or "").strip()
        if t == "add_opening":
            kind = ch.get("kind") or "window"
            if kind not in ("door", "window", "ventilator"):
                raise ValueError("kind must be door, window or ventilator")
            row = {"Kind": kind, "Name": name or kind.title(), "Width (ft)": _num(ch.get("width_ft"), "width_ft"),
                   "Height (ft)": _num(ch.get("height_ft"), "height_ft"), "Qty": int(_num(ch.get("qty", 1), "qty")),
                   "Leaves": int(ch.get("leaves") or (2 if kind == "window" else 1)), "Chogath (in)": 5.0,
                   "External": bool(ch.get("external", kind != "door")), "Source": "User input (copilot)", "Confidence": "User"}
            s.openings.append(row)
            return s, f"Add {row['Qty']} x {row['Name']} {row['Width (ft)']:g}'x{row['Height (ft)']:g}'"
        n = name.lower()
        idx = [i for i, o in enumerate(s.openings) if n and n in str(o.get("Name", "")).lower()]
        if not idx and ch.get("kind"):
            idx = [i for i, o in enumerate(s.openings) if o.get("Kind") == ch.get("kind")]
        if not idx:
            raise ValueError(f"No door/window group matching '{name}'")
        descs = []
        for i in idx:
            o = dict(s.openings[i])
            before = f"{o['Qty']} x {o['Width (ft)']:g}'x{o['Height (ft)']:g}'"
            for fld, col in (("width_ft", "Width (ft)"), ("height_ft", "Height (ft)")):
                if ch.get(fld) is not None:
                    o[col] = _num(ch[fld], fld)
            if ch.get("qty") is not None:
                o["Qty"] = int(_num(ch["qty"], "qty"))
            o["Source"], o["Confidence"] = "User input (copilot)", "User"
            s.openings[i] = o
            descs.append(f"{o['Name']}: {before} → {o['Qty']} x {o['Width (ft)']:g}'x{o['Height (ft)']:g}'")
        return s, "; ".join(descs)
    raise ValueError(f"Unknown change type '{t}'")


def apply_changes(state: ProjectState, changes: List[dict]) -> Tuple[ProjectState, List[str], List[str]]:
    """Apply several changes; returns (new_state, descriptions, problems). Invalid changes are skipped and reported;
    the result is also run through the input checks."""
    s, descs, problems = state, [], []
    for ch in changes or []:
        try:
            s, d = apply_change(s, ch)
            descs.append(d)
        except ValueError as exc:
            problems.append(str(exc))
    if descs:
        for issue in val_errors(validate_project(build(s))):
            problems.append(f"{issue.label} {issue.message}")
    return s, descs, problems


def option_fields() -> List[str]:
    return [f.name for f in fields(Options)]
