"""
Builds a DetailedProject from what the app already knows:

  1. PackageFacts from drawing_processing.package_analyzer (rooms per floor,
     measured walls by thickness, levels, strip foundation, door/window
     totals, area statement) - READ from the drawings (High/Medium);
  2. the supplementary text scan (plumbing labels, door schedule, tanks) -
     READ from the drawings;
  3. the user-verified ExtractedBuildingParams + ProjectInputs from Step 3
     (columns, beams, slab thickness, wall data when no vector facts exist);
  4. Room_Finish_Defaults from the Master Database for everything drawings
     rarely show (electrical points, finishes) - ASSUMED, clearly marked.

Nothing here modifies the objects passed in.
"""
from __future__ import annotations

import math
from typing import Optional

from detailed_mto.model import ASSUMED, HIGH, LOW, MEDIUM, USER, DetailedProject, Floor, OpeningGroup, Options, Room
from detailed_mto.text_scanner import ScanResult, scan_pdf_bytes
from knowledge.loader import KnowledgeBase

M_TO_FT = 3.28084
SQM_TO_SFT = 10.7639
FLOOR_NAMES = {"basement": "Basement", "ground": "Ground floor", "first": "First floor", "second": "Second floor",
               "third": "Third floor", "roof": "Mumty / roof"}
STOREY_ORDER = ["basement", "ground", "first", "second", "third"]
_KIND_TO_TYPE = {"bathroom": "Bathroom", "kitchen": "Kitchen", "bedroom": "Bedroom", "living": "Lounge / TV lounge",
                 "stair": "Staircase", "open": "Porch / car porch", "room": "Bedroom"}


def _est(e, default=0.0):
    try:
        return float(e.value) if e is not None else default
    except Exception:
        return default


def _conf_of(e) -> str:
    try:
        src = getattr(getattr(e, "source", None), "value", str(getattr(e, "source", "")))
        if src in ("User-edited", "User-input"):
            return USER
        c = getattr(e.confidence, "value", str(e.confidence))
        return {"High": HIGH, "Medium": MEDIUM, "Low": LOW}.get(c, MEDIUM)
    except Exception:
        return ASSUMED


def classify_room(name: str, kind: str, kb: KnowledgeBase) -> str:
    u = name.upper().strip()
    best, blen = None, 0
    for rd in kb.room_defaults:
        for kw in rd.keywords:
            if kw and kw in u and len(kw) > blen:
                best, blen = rd.room_type, len(kw)
    if best:
        return best
    if u.startswith("LAUND"):
        return "Laundry"
    if "DRAW" in u:
        return "Drawing room"
    return _KIND_TO_TYPE.get(kind, "Bedroom")


def build_project(project_inputs, params, kb: KnowledgeBase, facts=None, files: Optional[list] = None,
                  scan: Optional[ScanResult] = None, options: Optional[Options] = None,
                  rooms_override: Optional[list] = None, openings_override: Optional[list] = None,
                  overrides: Optional[dict] = None, drawing_mode: Optional[str] = None,
                  floors_override: Optional[list] = None) -> DetailedProject:
    """rooms_override / openings_override: user-reviewed lists of Room / OpeningGroup that replace the
    drawing-derived ones BEFORE counts that depend on them (baths, kitchens, points ...) are derived."""
    pi = project_inputs
    p = DetailedProject(project_name=getattr(pi, "project_name", "") or "Untitled Project",
                        client=getattr(pi, "client_name", ""), location=getattr(pi, "location", ""))
    p.options = options or Options()
    p.overrides = dict(overrides or {})
    if scan is None:
        try:
            scan = scan_pdf_bytes(files or [])
        except Exception as exc:  # never break the build on a scan problem
            scan = ScanResult(notes=[f"Label scan skipped: {exc}"])
    has_facts = facts is not None and getattr(facts, "has_facts", lambda: False)()
    fx = facts if has_facts else None
    if drawing_mode is None:
        drawing_mode = "cad" if has_facts else ("scanned" if files else "none")
    p.drawing_mode = drawing_mode

    def fact(dct_name, key):
        if fx is None:
            return None
        d = getattr(fx, dct_name, {}) or {}
        return d.get(key)

    # ------------------------------------------------------------------ plot
    plot_w = fact("plot", "plot_width_ft")
    plot_d = fact("plot", "plot_depth_ft")
    pw = plot_w[0] if plot_w else (getattr(pi, "plot_width_ft", None) or 0.0)
    pdp = plot_d[0] if plot_d else 0.0
    p.set("PLOT_W", "Plot width", pw, "ft", "Site", plot_w[1] if plot_w else "Step 1 input / unknown",
          HIGH if plot_w else (MEDIUM if pw else ASSUMED))
    p.set("PLOT_D", "Plot depth", pdp, "ft", "Site", plot_d[1] if plot_d else "Unknown", HIGH if plot_d else ASSUMED)
    pw, pdp = p.v("PLOT_W"), p.v("PLOT_D")  # user edits flow into everything derived below
    if fx is not None:
        p.conflicts.extend(getattr(fx, "conflicts", []) or [])
    p.conflicts.extend(scan.notes)

    # ---------------------------------------------------------------- levels
    ev = fact("elevation", "plinth_height_ft") or fact("section", "plinth_height_ft")
    p.set("H_PLINTH", "Plinth height above NSL", ev[0] if ev else 1.5, "ft", "Levels", ev[1] if ev else "Default",
          HIGH if ev else ASSUMED)
    fh = fact("elevation", "floor_height_ft")
    fh_default = _est(getattr(params.columns, "height_per_floor_m", None), 3.5) * M_TO_FT if params else 11.5
    p.set("H_FLOOR", "Floor-to-floor height", fh[0] if fh else fh_default, "ft", "Levels",
          fh[1] if fh else "Step 3 column height/floor", HIGH if fh else MEDIUM)
    st = fact("elevation", "slab_thickness_ft")
    slab_in = st[0] * 12 if st else _est(params.slabs.thickness_m, 0.15) * M_TO_FT * 12
    p.set("T_SLAB_IN", "Slab thickness", slab_in, "in", "Structure", st[1] if st else "Step 3 slab thickness",
          HIGH if st else _conf_of(params.slabs.thickness_m))
    p.set("H_PARAPET", "Parapet height", 3.0, "ft", "Levels", "Default (elevations show 2-3 ft)", ASSUMED)
    p.set("H_MUMTY", "Mumty storey height", 9.0, "ft", "Levels", "Default (8'-6\" clear + slab)", ASSUMED)
    p.set("P_WORKSPACE", "Excavation working space each side", 0.5, "ft", "Assumptions", "Default", ASSUMED)

    # ---------------------------------------------------------------- floors
    storey_keys = []
    if fx is not None:
        storey_keys = [k for k in STOREY_ORDER if k in fx.floors]
    n_storeys_default = int(getattr(pi, "plot_storeys", 2) or 2)
    wall_t_in = (getattr(pi, "wall_thickness_mm", 228.6) or 228.6) / 25.4
    h_floor = p.v("H_FLOOR")
    if floors_override:
        # concept floors from a sketch / guided brief: heights always follow the current H_FLOOR / H_MUMTY
        for f in floors_override:
            p.floors.append(Floor(f.key, f.name, f.covered_sft, f.ext_perimeter_ft, f.wall9_len_ft, f.wall45_len_ft,
                                  p.v("H_MUMTY") if f.is_mumty else h_floor, f.is_mumty, f.source, f.confidence))
    elif storey_keys:
        for k in storey_keys:
            ff = fx.floors[k]
            w9 = sum(L for t, L in (ff.thickness_breakdown or {}).items() if t >= 7.0)
            w45 = sum(L for t, L in (ff.thickness_breakdown or {}).items() if t < 7.0)
            if not ff.thickness_breakdown and ff.wall_length_ft:
                w9, w45 = ff.wall_length_ft * 0.55, ff.wall_length_ft * 0.45
            cov = ff.covered_sqft or 0.0
            p.floors.append(Floor(k, FLOOR_NAMES.get(k, k.title()), cov, ff.perimeter_ft or 4 * math.sqrt(max(cov, 1)),
                                  w9, w45, h_floor, source=ff.source or ff.covered_source,
                                  confidence=getattr(ff.geometry_confidence, "value", MEDIUM)))
    else:
        area = _est(params.plinth_area_per_floor_sqm, 90) * SQM_TO_SFT
        wall_len = _est(params.walls.total_length_per_floor_m, 60) * M_TO_FT
        ext = _est(params.walls.external_perimeter_m, 0) * M_TO_FT if params.walls.external_perimeter_m else 4 * math.sqrt(area)
        frac9 = 0.55 if wall_t_in >= 7 else 0.0
        for i in range(max(1, n_storeys_default)):
            k = STOREY_ORDER[1 + i] if 1 + i < len(STOREY_ORDER) else f"floor{i}"
            p.floors.append(Floor(k, FLOOR_NAMES.get(k, k.title()), area, ext, wall_len * frac9, wall_len * (1 - frac9),
                                  h_floor, source="Step 3 parameters", confidence=MEDIUM))
        p.assumptions.append("No vector drawing facts: floor areas and wall lengths come from the Step 3 parameters.")

    # mumty
    second = fact("plot", "covered_second_sqft")
    roof_f = fx.floors.get("roof") if fx is not None else None
    mumty_area = second[0] if second else (180.0 if len(p.floors) >= 1 else 0.0)
    if mumty_area and mumty_area < 400 and not floors_override:
        per = 4 * math.sqrt(mumty_area) * 1.05
        p.floors.append(Floor("roof", "Mumty", mumty_area, per, per, 0.0, p.v("H_MUMTY"), is_mumty=True,
                              source=second[1] if second else "Default", confidence=HIGH if second else ASSUMED))
    top = p.storeys[-1] if p.storeys else None
    roof_perim = (roof_f.perimeter_ft if (roof_f and roof_f.perimeter_ft) else (top.ext_perimeter_ft if top else 0.0))

    # exposed facade fraction (party walls on side boundaries)
    gf = p.storeys[0] if p.storeys else None
    built_to_sides = False
    if fx is not None and gf is not None and pw:
        g = fx.floors.get(gf.key)
        widths = [x for x in (getattr(g, "width_ft", None), getattr(g, "depth_ft", None)) if x]
        built_to_sides = any(abs(x - pw) <= 2.5 for x in widths)
    frac = 0.6 if built_to_sides else 1.0
    p.set("EXT_EXPOSED_FRAC", "Fraction of external walls exposed (plastered/painted outside)", frac, "-", "External",
          "Derived: building width ~ plot width -> side walls on boundary" if built_to_sides else "Default",
          ASSUMED, "Reduce if side/rear walls are party walls; 1.0 = all four sides exposed.")

    # ----------------------------------------------------------------- rooms
    n_ter, n_bal = scan.get("TERRACE"), scan.get("BALCONY")
    if rooms_override is not None:
        p.rooms = list(rooms_override)
    if rooms_override is None and fx is not None:
        for k, ff in fx.floors.items():
            if k == "roof":
                continue
            for r in ff.rooms:
                name, L, W, kind = r[0], r[1], r[2], r[3]
                p.rooms.append(Room(k, name, classify_room(name, kind, kb), L, W, ff.source, HIGH))
    if not p.rooms and rooms_override is None:
        baths = int(_est(params.services.bathroom_count_total, 2))
        kits = int(_est(params.services.kitchen_count_total, 1))
        for i, fl in enumerate(p.storeys):
            nb = baths // len(p.storeys) + (1 if i < baths % len(p.storeys) else 0)
            nk = kits // len(p.storeys) + (1 if i < kits % len(p.storeys) else 0)
            for j in range(nb):
                p.rooms.append(Room(fl.key, f"BATH {j + 1}", "Bathroom", 7, 5, "Assumed", ASSUMED))
            for j in range(nk):
                p.rooms.append(Room(fl.key, f"KITCHEN {j + 1}", "Kitchen", 10, 8, "Assumed", ASSUMED))
            rest = max(fl.covered_sft * 0.8 - 35 * nb - 80 * nk, 100)
            nrooms = max(2, int(rest // 150))
            side = math.sqrt(rest / nrooms)
            for j in range(nrooms):
                p.rooms.append(Room(fl.key, f"ROOM {j + 1}", "Bedroom" if j else "Lounge / TV lounge", side, side, "Assumed", ASSUMED))
        p.assumptions.append("Room list not readable from drawings - generic rooms were generated from covered area and bath/kitchen counts.")
    mt = p.mumty
    if mt is not None and rooms_override is None:
        side = math.sqrt(mt.covered_sft * 0.8)
        p.rooms.append(Room("roof", "MUMTY", "Mumty", side, side, mt.source, mt.confidence))
    # terraces / balconies (labels without dimensions)
    if rooms_override is None:
        for i in range(n_ter):
            p.rooms.append(Room("first", f"TERRACE {i + 1}", "Terrace / balcony", 10, 6, "Label only - size assumed", ASSUMED))
        for i in range(n_bal):
            p.rooms.append(Room("first", f"BALCONY {i + 1}", "Terrace / balcony", 8, 3.5, "Label only - size assumed", ASSUMED))
    if (n_ter or n_bal) and rooms_override is None:
        p.assumptions.append(f"{n_ter} terrace(s) and {n_bal} balcony(ies) are labelled but not dimensioned - sizes assumed (10'x6', 8'x3'-6\").")

    # -------------------------------------------------------------- openings
    if openings_override is not None:
        p.openings = list(openings_override)
    elif scan.doors:
        for d in scan.doors:
            u = d.name.upper()
            ext = "MAIN" in u or "MUMTY" in u
            p.openings.append(OpeningGroup("door", d.name, d.width_ft, d.height_ft, d.qty, d.leaves, d.chogath_in, ext, d.source, HIGH))
    else:
        dt = fact("openings", "doors_total")
        n_d = int(dt[0]) if dt else int(_est(params.openings.door_count_per_floor, 5) * len(p.storeys))
        baths = len(p.rooms_of("Bathroom"))
        p.openings.append(OpeningGroup("door", "Main door", 4.5, 8, 1, 2, 10, True, "Assumed", ASSUMED))
        if p.mumty:
            p.openings.append(OpeningGroup("door", "Mumty door", 3, 7, 1, 1, 5, True, "Assumed", ASSUMED))
        p.openings.append(OpeningGroup("door", "Bath doors", 2.5, 7, baths, 1, 5, False, "Assumed", ASSUMED))
        rest = max(n_d - 1 - baths - (1 if p.mumty else 0), 0)
        p.openings.append(OpeningGroup("door", "Room doors", 3.5, 7, rest, 1, 5, False,
                                       dt[1] if dt else "Step 3 door count", MEDIUM if dt else LOW))
        p.assumptions.append("Door sizes by type assumed (main 4'-6\"x8', room 3'-6\"x7', bath 2'-6\"x7'); totals from drawings/Step 3.")
    for fl in (p.storeys + ([p.mumty] if p.mumty else [])) if openings_override is None else []:
        wk = fact("openings", f"windows_{fl.key}")
        n_w = int(wk[0]) if wk else int(_est(params.openings.window_count_per_floor, 4)) if not fl.is_mumty else 1
        n_b = len([r for r in p.rooms_on(fl.key) if r.room_type == "Bathroom"])
        n_win = max(n_w - n_b, 0)
        if n_win:
            p.openings.append(OpeningGroup("window", f"Windows - {fl.name}", 4.0, 5.0 if not fl.is_mumty else 3.0, n_win, 2, 0,
                                           True, wk[1] if wk else "Step 3 window count", MEDIUM if wk else LOW))
        if n_b:
            p.openings.append(OpeningGroup("ventilator", f"Bath ventilators - {fl.name}", 2.0, 1.5, n_b, 1, 0, True,
                                           "One per bathroom (sill 6 ft)", ASSUMED))
    if openings_override is None or any(o.confidence == ASSUMED and o.kind != "door" for o in p.openings):
        p.assumptions.append("Window WIDTHS are not given on the sample-style D&W sheets - 4'x5' windows and 2'x1'-6\" bath "
                             "ventilators are assumed. Enter the real sizes in the Doors & windows table when known.")

    # ------------------------------------------------------------ foundation
    strip = fact("foundation", "strip") or (getattr(params.footings, "footing_type", "") == "strip")
    gfl = p.storeys[0] if p.storeys else None
    sw = fact("foundation", "strip_width_ft")
    fd = fact("foundation", "founding_depth_ft")
    fdn_depth = fd[0] if fd else (_est(params.footings.founding_depth_m, 1.2) * M_TO_FT if params.footings.founding_depth_m else 3.5)
    p.set("FDN_STRIP", "Strip (load-bearing) foundations", 1 if strip else 0, "flag", "Foundation",
          "Drawings" if fact("foundation", "strip") else "Step 3", HIGH if fact("foundation", "strip") else MEDIUM)
    p.set("FDN_DEPTH", "Founding depth below NSL", fdn_depth, "ft", "Foundation", fd[1] if fd else "Step 3 / default",
          HIGH if fd else MEDIUM)
    p.set("STRIP_LEN_9", "Strip foundation length under 9in walls", gfl.wall9_len_ft if (gfl and strip) else 0, "rft",
          "Foundation", "= GF 9in wall centre-line length", gfl.confidence if gfl else ASSUMED)
    p.set("STRIP_LEN_45", "Strip foundation length under 4.5in walls", gfl.wall45_len_ft if (gfl and strip) else 0, "rft",
          "Foundation", "= GF 4.5in wall length", gfl.confidence if gfl else ASSUMED)
    p.set("PCC_W_9", "PCC width under 9in walls", sw[0] if sw else 2.5, "ft", "Foundation", sw[1] if sw else "Default",
          HIGH if sw else ASSUMED)
    p.set("PCC_W_45", "PCC width under 4.5in walls", 1.5, "ft", "Foundation", "Drawing sections 1'-6\" (typ.)", ASSUMED)
    pcc_t = _est(params.footings.depth_m, 0.15) * M_TO_FT if strip else 0.5
    p.set("PCC_T", "Foundation PCC thickness", max(min(pcc_t, 1.0), 0.25), "ft", "Foundation", "Step 3 (PCC bed thickness)",
          _conf_of(params.footings.depth_m))

    # ------------------------------------------------------- columns / beams
    col_n = _est(params.columns.count, 0)
    p.set("COL_N", "Columns (count)", col_n, "Nos", "Structure", "Step 3", _conf_of(params.columns.count))
    p.set("COL_B_IN", "Column width b", _est(params.columns.width_m, 0.23) * M_TO_FT * 12, "in", "Structure", "Step 3",
          _conf_of(params.columns.width_m))
    p.set("COL_D_IN", "Column depth d", _est(params.columns.depth_m, 0.3) * M_TO_FT * 12, "in", "Structure", "Step 3",
          _conf_of(params.columns.depth_m))
    if strip:
        p.set("FTG_N", "Isolated column footings", col_n, "Nos", "Foundation", "= column count", _conf_of(params.columns.count))
        p.set("FTG_L", "Footing length", 3.5, "ft", "Foundation", "Drawing C1 4'x4', C2/C3 3'x3' (avg)", ASSUMED)
        p.set("FTG_B", "Footing width", 3.5, "ft", "Foundation", "Drawing (avg)", ASSUMED)
        p.set("FTG_D", "Footing depth", 1.25, "ft", "Foundation", "Default", ASSUMED)
    else:
        p.set("FTG_N", "Isolated column footings", _est(params.footings.count, col_n), "Nos", "Foundation", "Step 3",
              _conf_of(params.footings.count))
        p.set("FTG_L", "Footing length", _est(params.footings.length_m, 1.2) * M_TO_FT, "ft", "Foundation", "Step 3",
              _conf_of(params.footings.length_m))
        p.set("FTG_B", "Footing width", _est(params.footings.width_m, 1.2) * M_TO_FT, "ft", "Foundation", "Step 3",
              _conf_of(params.footings.width_m))
        p.set("FTG_D", "Footing depth", _est(params.footings.depth_m, 0.45) * M_TO_FT, "ft", "Foundation", "Step 3",
              _conf_of(params.footings.depth_m))
    p.set("BEAM_N", "Beams per slab level", _est(params.beams.count, 0), "Nos", "Structure", "Step 3", _conf_of(params.beams.count))
    p.set("BEAM_LEN", "Average beam length", _est(params.beams.avg_length_m, 0) * M_TO_FT, "ft", "Structure", "Step 3",
          _conf_of(params.beams.avg_length_m))
    p.set("BEAM_B_IN", "Beam width", _est(params.beams.width_m, 0.23) * M_TO_FT * 12, "in", "Structure", "Step 3",
          _conf_of(params.beams.width_m))
    p.set("BEAM_D_IN", "Beam overall depth", _est(params.beams.depth_m, 0.45) * M_TO_FT * 12, "in", "Structure", "Step 3",
          _conf_of(params.beams.depth_m))
    p.set("PB_LEN", "Plinth / grade beam length", 0 if strip else (gfl.wall9_len_ft if gfl else 0), "rft", "Structure",
          "RCC frame: under all 9in walls" if not strip else "Load-bearing: none (DPC band instead)", ASSUMED)
    band_len = sum(f.wall9_len_ft for f in p.storeys) if p.options.seismic_bands else 0
    p.set("BAND_LEN", "Seismic lintel band length (9in walls)", band_len, "rft", "Structure",
          "= 9in wall CL length per storey (BCP zone 2B)", ASSUMED,
          "Not drawn in the sample set - recommended for load-bearing masonry in Islamabad/Rawalpindi.")
    p.set("BAND_D_IN", "Band depth", 6, "in", "Structure", "Default", ASSUMED)

    # ------------------------------------------------------------------ stair
    stairs = p.rooms_of("Staircase")
    st_w = min(3.5, (stairs[0].width_ft / 2) if stairs else 3.5)
    p.set("STAIR_W", "Stair flight width", st_w, "ft", "Structure", "Half of stair hall width (max 3'-6\")" if stairs else "Default",
          MEDIUM if stairs else ASSUMED)
    p.set("STAIR_LIFTS", "Storey lifts served by the stair", len(p.storeys) if p.mumty else max(len(p.storeys) - 1, 0), "Nos",
          "Structure", "GF->FF, FF->mumty ...", MEDIUM)
    p.set("STAIR_VOID", "Stair void in intermediate slabs", (stairs[0].area * 0.6) if stairs else 0, "sft", "Structure",
          "60% of stair hall area", ASSUMED)

    # ------------------------------------------------------------------- roof
    top_cov = top.covered_sft if top else 0
    p.set("ROOF_AREA", "Roof area to treat (top roof incl. mumty roof)", top_cov, "sft", "Roof",
          f"= {top.name} covered area" if top else "", top.confidence if top else ASSUMED)
    p.set("ROOF_PERIM", "Roof perimeter (parapet length)", roof_perim, "rft", "Roof", "Roof/mumty plan outline", MEDIUM)

    # ------------------------------------------------------------- plumbing
    baths = len(p.rooms_of("Bathroom"))
    kits = len(p.rooms_of("Kitchen"))
    laund = len(p.rooms_of("Laundry"))

    def label(key, default, desc, unit="Nos", group="Plumbing", derived=""):
        n = scan.get(key)
        src = scan.sources.get(next((s for s in scan.sources if s.startswith(key + ":")), ""), "")
        if n and key not in scan.detail_only:
            p.set("N_" + key, desc, n, unit, group, f"Label count ({src} ...)", HIGH)
        else:
            p.set("N_" + key, desc, default, unit, group, derived or "Default rule", ASSUMED)

    label("WC", baths, "WCs", derived="1 per bathroom")
    label("VANITY", baths, "Wash basins / vanities", derived="1 per bathroom")
    label("SHOWER", baths, "Showers", derived="1 per bathroom")
    p.set("N_HF", "Health faucets", p.v("N_WC"), "Nos", "Plumbing", "= WC count", p.conf("N_WC"))
    label("FT", 2 * baths + kits + laund + 1, "Floor traps", derived="2 per bath + 1 per kitchen/laundry + 1")
    p.set("N_KSINK", "Kitchen sinks", kits, "Nos", "Plumbing", "= kitchens", HIGH if fx is not None else MEDIUM)
    p.set("N_WM", "Washing machine points", max(laund, 1), "Nos", "Plumbing", "= laundries (min 1)", MEDIUM)
    p.set("N_BATH", "Bathrooms", baths, "Nos", "Plumbing", "Room list", HIGH if fx is not None else MEDIUM)
    label("MH", 2, "Manholes", derived="Default 2")
    label("GT", max(kits, 1), "Gully traps", derived="1 per kitchen stack")
    label("CO", baths, "Cleanouts", derived="1 per stack")
    rwp_rule = math.ceil(p.v("ROOF_AREA") / 450.0) if p.v("ROOF_AREA") else 2
    n_sump = scan.get("SUMP")
    p.set("N_RWP", "Rainwater pipes / roof khuras", max(n_sump, rwp_rule), "Nos", "Plumbing",
          f"max(SUMP labels={n_sump}, roof area/450)", MEDIUM if n_sump else ASSUMED)
    h_total = p.v("H_PLINTH") + sum(f.storey_height_ft for f in p.storeys)
    p.set("H_TOTAL", "Building height to main roof", h_total, "ft", "Levels", "= plinth + storeys", MEDIUM)
    p.set("SEWER_LEN", "External sewer length (house to main)", (pw or 30) + 10, "rft", "Plumbing",
          "Default = plot width + 10 ft", ASSUMED)
    p.set("N_GEYSER", "Water heaters (geysers)", max(len(p.storeys), 1), "Nos", "Plumbing", "1 per floor", ASSUMED)
    gas = kits + (p.v("N_GEYSER") if p.options.gas_source == "SNGPL" else 0) if p.options.gas_source != "None" else 0
    p.set("N_GAS", "Gas points", gas, "Nos", "Gas", f"kitchens + geysers ({p.options.gas_source})", ASSUMED)
    p.set("N_OHT", "Overhead water tanks", 1, "Nos", "Plumbing",
          scan.sources.get("OH_TANK", "Default") + (f" ({scan.oh_tank_gal:g} gal {scan.oh_tank_type})" if scan.oh_tank_gal else ""),
          HIGH if scan.oh_tank_gal else ASSUMED)
    if scan.oh_tank_gal and scan.oh_tank_detail:
        p.conflicts.append(f"Overhead tank: plan shows {scan.oh_tank_gal:g} gal {scan.oh_tank_type} tank, the tank sheet also has an "
                           f"RCC O.H.W.T detail ({scan.oh_tank_detail[0]:g}'x{scan.oh_tank_detail[1]:g}'). The prefabricated tank is used; confirm.")
    ug = scan.ug_tank_detail
    p.set("UGT_L", "UG water tank internal length", ug[0] if ug else 6.0, "ft", "Tanks", scan.sources.get("ug_tank_detail", "Default"), HIGH if ug else ASSUMED)
    p.set("UGT_W", "UG water tank internal width", ug[1] if ug else 5.0, "ft", "Tanks", scan.sources.get("ug_tank_detail", "Default"), HIGH if ug else ASSUMED)
    p.set("UGT_D", "UG water tank water depth", 5.0, "ft", "Tanks", "Default", ASSUMED)
    sp = scan.septic_detail or scan.septic_plan
    p.set("SEPTIC_N", "Septic tanks", 1 if sp else 0, "Nos", "Tanks", scan.sources.get("septic_detail", scan.sources.get("septic_plan", "Not shown")),
          HIGH if sp else ASSUMED)
    p.set("SEP_L", "Septic tank internal length", sp[0] if sp else 6.5, "ft", "Tanks", "Tank detail" if scan.septic_detail else "Plan/default",
          HIGH if scan.septic_detail else ASSUMED)
    p.set("SEP_W", "Septic tank internal width", sp[1] if sp else 5.0, "ft", "Tanks", "Tank detail" if scan.septic_detail else "Plan/default",
          HIGH if scan.septic_detail else ASSUMED)
    p.set("SEP_D", "Septic tank liquid depth", 5.0, "ft", "Tanks", "Default", ASSUMED)
    p.set("TANK_WALL_IN", "Tank wall/base thickness", 6, "in", "Tanks", "Drawing sh.16 (6\" RCC)", MEDIUM)
    p.set("TANK_SLAB_IN", "Tank cover slab thickness", 4, "in", "Tanks", "Drawing sh.16 (4\" RCC slab)", MEDIUM)

    # ------------------------------------------------------------ electrical
    def room_points(col):
        tot = 0.0
        for r in p.rooms:
            rd = next((d for d in kb.room_defaults if d.room_type == r.room_type), None)
            if rd:
                tot += rd.points.get(col, 0.0)
        return tot

    el_src = "Room_Finish_Defaults x room list (symbols on electrical sheets not yet auto-counted)"
    p.set("N_LIGHT", "Light points", room_points("Light Pts") + 4, "Nos", "Electrical", el_src + " + 4 external", ASSUMED)
    p.set("N_FAN", "Ceiling fan points", room_points("Fan Pts"), "Nos", "Electrical", el_src, ASSUMED)
    p.set("N_SK13", "13A socket points", room_points("13A Sockets"), "Nos", "Electrical", el_src, ASSUMED)
    p.set("N_PW15", "15A / geyser / cooker points", room_points("15A/Power Pts"), "Nos", "Electrical", el_src, ASSUMED)
    p.set("N_AC", "Split AC points", room_points("AC Pts"), "Nos", "Electrical", el_src, ASSUMED)
    p.set("N_LV", "TV / data / intercom points", room_points("TV Pts") + room_points("Data Pts") + 2, "Nos", "Electrical",
          el_src + " + intercom", ASSUMED)
    p.set("N_EXH", "Exhaust fans", room_points("Exhaust Fans"), "Nos", "HVAC", el_src, ASSUMED)
    n_db = scan.get("DB")
    p.set("N_DB", "Distribution boards", n_db if n_db else len(p.storeys), "Nos", "Electrical",
          "DB labels on electrical plans" if n_db else "1 per floor", HIGH if n_db else ASSUMED)
    p.set("N_EARTH", "Earthing pits", 2, "Nos", "Electrical", "Drawing detail sh.30 (min 2)", MEDIUM)
    p.set("SUBMAIN_LEN", "Sub-main feeder route", max(len(p.storeys) - 1, 0) * (h_floor + 30) + 20, "rft", "Electrical",
          "(floors-1) x (floor height + 30 ft) + 20 ft", ASSUMED)
    p.assumptions.append("Electrical point counts use room-type defaults. Symbols on the electrical sheets are not yet "
                         "auto-counted - verify against sheets 27-28 and edit the counts.")

    # ---------------------------------------------------- kitchen / joinery
    counter = sum(max(r.length_ft + r.width_ft - 2, 8) for r in p.rooms_of("Kitchen"))
    p.set("COUNTER_LEN", "Kitchen counter / base cabinet length", counter, "rft", "Kitchen", "L-shape: L + W - 2 ft per kitchen", ASSUMED)
    p.set("WALLCAB_LEN", "Kitchen wall cabinet length", counter * 0.6, "rft", "Kitchen", "60% of counter length", ASSUMED)
    n_ward = scan.get("WARDROBE") or len(p.rooms_of("Bedroom"))
    p.set("WARDROBE_AREA", "Wardrobe face area", n_ward * 6 * 8, "sft", "Joinery",
          f"{n_ward} wardrobes x 6 ft x 8 ft" + (" (WARDROBE labels)" if scan.get("WARDROBE") else " (1 per bedroom)"),
          MEDIUM if scan.get("WARDROBE") else ASSUMED)
    p.set("RAIL_LEN", "Stair & balcony railing length", p.v("STAIR_LIFTS") * 2 * (h_floor * 12 / 6.75 / 2 * 10 / 12 + 2) + n_bal * 8,
          "rft", "Architecture", "stair flights + 8 ft per balcony", ASSUMED)

    # --------------------------------------------------------------- external
    p.set("GATE_W", "Main gate width", 10.0, "ft", "External", "Default (GATE label on GF plan)", ASSUMED)
    gate_w = p.v("GATE_W")
    p.set("GATE_H", "Main gate height", 7.0, "ft", "External", "Default", ASSUMED)
    bl = max((pw or 30) - gate_w, 0) if built_to_sides else max(2 * (pdp or 50) + (pw or 30) - gate_w, 0)
    p.set("BOUNDARY_LEN", "Boundary wall length", bl, "rft", "External",
          "Front wall only (building on side boundaries)" if built_to_sides else "Plot perimeter excl. rear/gate", ASSUMED)
    porch = sum(r.area for r in p.rooms_of("Porch / car porch"))
    p.set("PAVING_AREA", "Porch / driveway paving", porch, "sft", "External", "Porch room dimensions", HIGH if porch else ASSUMED)
    p.set("APRON_LEN", "Plinth protection length", (pw or 0) if built_to_sides else (gfl.ext_perimeter_ft if gfl else 0), "rft",
          "External", "Exposed frontage", ASSUMED)
    p.set("RWH_N", "Rainwater recharge wells", 1 if p.options.include_rwh else 0, "Nos", "External",
          "CDA requirement (not in drawings)", ASSUMED)

    if p.drawing_mode in ("scanned", "none"):
        # Nothing was READ from drawings: everything except the user's own edits is a template /
        # default value and must be shown (and exported) as such.
        for prm in p.params.values():
            if prm.confidence != USER:
                prm.confidence = ASSUMED
        for fl in p.floors:
            fl.confidence = ASSUMED
        for rm in p.rooms:
            if rm.confidence != USER:
                rm.confidence = ASSUMED
        for o in p.openings:
            if o.confidence != USER:
                o.confidence = ASSUMED
        if p.drawing_mode == "scanned":
            p.conflicts.insert(0, "The uploaded drawings are scanned images / photos - no dimensions could be read from them. "
                                  "Quantities are based on a TYPICAL house for the chosen plot size, not on these drawings. "
                                  "Use the AI analysis in Step 2 or enter the rooms, doors and key dimensions in Step 3.")
        else:
            p.assumptions.insert(0, "No drawings uploaded - quantities are based on a typical house for the chosen plot size.")
    return p
