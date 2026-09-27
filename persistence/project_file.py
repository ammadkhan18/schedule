"""
Save / open a whole project as one JSON file (".costlens.json").

Everything the take-off needs is stored - project settings, the reviewed
parameters, rooms, doors & windows, counts, options, the drawing facts read
from the CAD set, the label scan, the sketch brief, copilot scenarios and the
chat - so a project reopens exactly as it was, without re-reading the
drawings. The drawings themselves are optional (they make the file larger).

The file is plain JSON (no pickle), so opening a file from someone else can
never run code.
"""
from __future__ import annotations

import base64
import json
import re
from dataclasses import asdict, fields, is_dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import config
from detailed_mto.brief import BriefRoom, ProjectBrief
from detailed_mto.model import Floor, Options
from detailed_mto.text_scanner import DoorRow, ScanResult
from drawing_processing.package_analyzer import FloorFacts, PackageFacts, SheetSummary
from models.schemas import ConfidenceLevel, ExtractedBuildingParams, ProjectInputs

FORMAT = "costlens-project"
FORMAT_VERSION = 1
LIBRARY_DIR = Path(getattr(config, "PROJECTS_DIR", Path(__file__).resolve().parent.parent / "projects"))


# ---------------------------------------------------------------------------
# drawing facts <-> dict (tuple / enum / float keys made JSON-safe)
# ---------------------------------------------------------------------------
def _pairs(d: Dict[str, tuple]) -> Dict[str, list]:
    return {k: [v[0], v[1]] for k, v in (d or {}).items()}


def _unpairs(d: Dict[str, list]) -> Dict[str, tuple]:
    return {k: (float(v[0]), str(v[1])) for k, v in (d or {}).items()}


def facts_to_dict(f: Optional[PackageFacts]) -> Optional[dict]:
    if f is None:
        return None
    floors = {}
    for k, ff in f.floors.items():
        d = asdict(ff)
        d["thickness_breakdown"] = {str(t): v for t, v in ff.thickness_breakdown.items()}
        d["geometry_confidence"] = getattr(ff.geometry_confidence, "value", str(ff.geometry_confidence))
        d["rooms"] = [list(r) for r in ff.rooms]
        floors[k] = d
    return {
        "sheets": [asdict(s) for s in f.sheets], "floors": floors,
        "section": _pairs(f.section), "structural": _pairs(f.structural), "plot": _pairs(f.plot),
        "conflicts": list(f.conflicts), "foundation": _pairs(f.foundation), "openings": _pairs(f.openings),
        "elevation": _pairs(f.elevation),
        "page_kinds": [[k[0], k[1], v] for k, v in f.page_kinds.items()],
        "best_plan_page": {k: list(v) for k, v in f.best_plan_page.items()},
    }


def facts_from_dict(d: Optional[dict]) -> Optional[PackageFacts]:
    if not d:
        return None
    f = PackageFacts()
    f.sheets = [SheetSummary(**s) for s in d.get("sheets", [])]
    for k, fd in (d.get("floors") or {}).items():
        fd = dict(fd)
        fd["thickness_breakdown"] = {float(t): float(v) for t, v in (fd.get("thickness_breakdown") or {}).items()}
        try:
            fd["geometry_confidence"] = ConfidenceLevel(fd.get("geometry_confidence", "Medium"))
        except ValueError:
            fd["geometry_confidence"] = ConfidenceLevel.MEDIUM
        fd["rooms"] = [tuple(r) for r in fd.get("rooms", [])]
        allowed = {x.name for x in fields(FloorFacts)}
        f.floors[k] = FloorFacts(**{x: v for x, v in fd.items() if x in allowed})
    for name in ("section", "structural", "plot", "foundation", "openings", "elevation"):
        setattr(f, name, _unpairs(d.get(name, {})))
    f.conflicts = list(d.get("conflicts", []))
    f.page_kinds = {(int(a), int(b)): v for a, b, v in d.get("page_kinds", [])}
    f.best_plan_page = {k: (int(v[0]), int(v[1])) for k, v in (d.get("best_plan_page") or {}).items()}
    return f


def scan_to_dict(s: Optional[ScanResult]) -> Optional[dict]:
    if s is None:
        return None
    d = asdict(s)
    d["detail_only"] = sorted(s.detail_only)
    d["per_page"] = {str(k): v for k, v in s.per_page.items()}
    return d


def scan_from_dict(d: Optional[dict]) -> Optional[ScanResult]:
    if not d:
        return None
    d = dict(d)
    d["doors"] = [DoorRow(**x) for x in d.get("doors", [])]
    d["detail_only"] = set(d.get("detail_only", []))
    d["per_page"] = {int(k): v for k, v in (d.get("per_page") or {}).items()}
    for k in ("septic_plan", "septic_detail", "ug_tank_detail", "oh_tank_detail"):
        if d.get(k) is not None:
            d[k] = tuple(d[k])
    allowed = {x.name for x in fields(ScanResult)}
    return ScanResult(**{k: v for k, v in d.items() if k in allowed})


def _dc(obj) -> Any:
    return asdict(obj) if is_dataclass(obj) else obj


def _options_from(d: Optional[dict]) -> Options:
    allowed = {x.name for x in fields(Options)}
    return Options(**{k: v for k, v in (d or {}).items() if k in allowed})


def _brief_from(d: Optional[dict]) -> Optional[ProjectBrief]:
    if not d:
        return None
    d = dict(d)
    d["rooms"] = [BriefRoom(**r) for r in d.get("rooms", [])]
    allowed = {x.name for x in fields(ProjectBrief)}
    return ProjectBrief(**{k: v for k, v in d.items() if k in allowed})


# ---------------------------------------------------------------------------
# whole project
# ---------------------------------------------------------------------------
def project_to_dict(ss: Dict[str, Any], include_drawings: bool = False) -> dict:
    """ss: a mapping with the app's session-state keys."""
    pi: ProjectInputs = ss.get("project_inputs")
    params: Optional[ExtractedBuildingParams] = ss.get("extracted_params")
    scen = {name: {"rooms": snap["rooms"], "openings": snap["openings"], "overrides": snap["overrides"],
                   "options": _dc(snap["options"])} for name, snap in (ss.get("copilot_scenarios") or {}).items()}
    data = {
        "format": FORMAT, "format_version": FORMAT_VERSION, "app_version": config.APP_VERSION,
        "saved_at": datetime.now().isoformat(timespec="seconds"),
        "name": (pi.project_name if pi else "") or "Untitled Project",
        "step": int(ss.get("step", 1) or 1), "max_step": int(ss.get("max_step", 1) or 1),
        "input_mode": ss.get("input_mode", "drawings"),
        "project_inputs": pi.model_dump(mode="json") if pi else None,
        "extracted_params": params.model_dump(mode="json") if params is not None else None,
        "dmto_options": _dc(ss.get("dmto_options") or Options()),
        "dmto_rooms": ss.get("dmto_rooms"), "dmto_openings": ss.get("dmto_openings"),
        "dmto_overrides": ss.get("dmto_overrides") or {},
        "dmto_floors": [_dc(f) for f in ss.get("dmto_floors") or []] or None,
        "package_facts": facts_to_dict(ss.get("package_facts")),
        "dmto_scan": scan_to_dict(ss.get("dmto_scan")),
        "uploaded_signature": [list(x) for x in ss.get("uploaded_signature") or []] or None,
        "drawing_filled": list(ss.get("drawing_filled") or []),
        "brief": _dc(ss.get("brief")) if ss.get("brief") is not None else None,
        "copilot_scenarios": scen,
        "copilot_msgs": [{"role": m["role"], "content": m["content"]} for m in ss.get("copilot_msgs") or []][-30:],
        "drawings": None,
    }
    if include_drawings and ss.get("uploaded_files"):
        data["drawings"] = [{"name": f["name"], "view_tag": f.get("view_tag", "Auto"),
                             "b64": base64.b64encode(f["bytes"]).decode("ascii")} for f in ss["uploaded_files"]]
    return data


def project_to_json(ss: Dict[str, Any], include_drawings: bool = False) -> bytes:
    return json.dumps(project_to_dict(ss, include_drawings), ensure_ascii=False, indent=1).encode("utf-8")


def project_from_json(raw: bytes) -> Dict[str, Any]:
    """Parse a project file into {session_key: value}. Raises ValueError with a friendly message."""
    try:
        d = json.loads(raw.decode("utf-8") if isinstance(raw, (bytes, bytearray)) else raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"This is not a CostLens project file ({exc.__class__.__name__}).")
    if not isinstance(d, dict) or d.get("format") != FORMAT:
        raise ValueError("This is not a CostLens project file.")
    if int(d.get("format_version", 0)) > FORMAT_VERSION:
        raise ValueError("This project was saved by a newer version of the app - please update the app.")
    try:
        pi = ProjectInputs.model_validate(d["project_inputs"]) if d.get("project_inputs") else ProjectInputs()
        params = ExtractedBuildingParams.model_validate(d["extracted_params"]) if d.get("extracted_params") else None
    except Exception as exc:  # noqa: BLE001
        raise ValueError(f"The project file is damaged: {exc}")
    floors = [Floor(**f) for f in d.get("dmto_floors") or []] or None
    scen = {}
    for name, snap in (d.get("copilot_scenarios") or {}).items():
        scen[name] = {"rooms": snap.get("rooms"), "openings": snap.get("openings"), "overrides": snap.get("overrides") or {},
                      "options": _options_from(snap.get("options"))}
    files = [{"name": x["name"], "view_tag": x.get("view_tag", "Auto"), "bytes": base64.b64decode(x["b64"])}
             for x in d.get("drawings") or []]
    sig = d.get("uploaded_signature")
    has_params = params is not None
    return {
        "project_inputs": pi, "extracted_params": params, "input_mode": d.get("input_mode", "drawings"),
        "dmto_options": _options_from(d.get("dmto_options")),
        "dmto_rooms": d.get("dmto_rooms"), "dmto_openings": d.get("dmto_openings"),
        "dmto_overrides": {k: float(v) for k, v in (d.get("dmto_overrides") or {}).items()},
        "dmto_floors": floors, "package_facts": facts_from_dict(d.get("package_facts")),
        "dmto_scan": scan_from_dict(d.get("dmto_scan")),
        "uploaded_signature": tuple(tuple(x) for x in sig) if sig else None,
        "uploaded_files": files, "drawing_filled": d.get("drawing_filled") or [],
        "brief": _brief_from(d.get("brief")),
        "copilot_scenarios": scen, "copilot_msgs": d.get("copilot_msgs") or [],
        "step": min(int(d.get("step", 1)), 4 if has_params else 1), "max_step": int(d.get("max_step", 1)) if has_params else 1,
        "dmto_result": None,
    }


# ---------------------------------------------------------------------------
# local project library (a folder of project files)
# ---------------------------------------------------------------------------
def _slug(name: str) -> str:
    s = re.sub(r"[^A-Za-z0-9_-]+", "_", name or "project").strip("_")[:60]
    return s or "project"


def library_save(ss: Dict[str, Any], folder: Optional[Path] = None) -> Path:
    folder = Path(folder or LIBRARY_DIR)
    folder.mkdir(parents=True, exist_ok=True)
    pi = ss.get("project_inputs")
    path = folder / f"{_slug(pi.project_name if pi else 'project')}.costlens.json"
    path.write_bytes(project_to_json(ss, include_drawings=False))
    return path


def library_list(folder: Optional[Path] = None) -> List[dict]:
    folder = Path(folder or LIBRARY_DIR)
    if not folder.exists():
        return []
    out = []
    for p in sorted(folder.glob("*.costlens.json"), key=lambda x: x.stat().st_mtime, reverse=True):
        try:
            head = json.loads(p.read_text(encoding="utf-8"))
            out.append({"path": p, "name": head.get("name", p.stem), "saved_at": head.get("saved_at", ""),
                        "mode": head.get("input_mode", "drawings")})
        except Exception:  # noqa: BLE001 - skip unreadable files
            continue
    return out


def library_delete(path: Path) -> None:
    p = Path(path)
    if p.suffixes[-2:] == [".costlens", ".json"] and p.exists():
        p.unlink()
