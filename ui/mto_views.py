"""
Streamlit views for the drawing-based Material Take-Off:

* Step 2  - what the label scan found (plumbing, door schedule, tanks)
* Step 3  - review tables (rooms, doors & windows, counts & key dimensions, coefficients)
* Step 4  - material take-off (schedule, by stage, work items, traceability, assumptions, checks)
* Step 5  - export

All engineering lives in detailed_mto/ and knowledge/; this module only renders.
Costs are intentionally excluded in this version.
"""
from __future__ import annotations

from typing import List, Optional

import pandas as pd
import streamlit as st

from detailed_mto import build_project, compute
from detailed_mto.edits import (OPENING_COLS, ROOM_COLS, mark_user_edits, openings_to_rows, rooms_to_rows,
                                rows_to_openings, rows_to_rooms)
from detailed_mto.engine import (INCLUDED_STATUSES, ST_CALC, ST_CALC_ASSUMED, ST_NEEDS_INPUT, ST_OPTION, ST_PROVISIONAL,
                                 ST_REFERENCE, DetailedResult, purchase_qty)
from detailed_mto.export import STAGE_NAMES, benchmarks_for
from detailed_mto.validation import errors, validate_project
from knowledge import load_knowledge_base

FLOOR_KEYS = ["basement", "ground", "first", "second", "third", "roof"]
CONF_ICON = {"High": "🟢", "Medium": "🟡", "Low": "🟠", "Assumed": "⚪", "User": "🔵", "": ""}


def kb():
    return load_knowledge_base()


# ---------------------------------------------------------------- project building
def drawing_mode() -> str:
    if st.session_state.get("input_mode") == "sketch":
        return "sketch"
    facts = st.session_state.get("package_facts")
    if facts is not None and facts.has_facts():
        return "cad"
    return "scanned" if st.session_state.get("uploaded_files") else "none"


def _build(pi, params, room_rows=None, opening_rows=None, overrides=None):
    return build_project(pi, params, kb(), facts=st.session_state.get("package_facts"), scan=st.session_state.get("dmto_scan"),
                         options=st.session_state["dmto_options"],
                         rooms_override=rows_to_rooms(room_rows) if room_rows is not None else None,
                         openings_override=rows_to_openings(opening_rows) if opening_rows is not None else None,
                         overrides=overrides, drawing_mode=drawing_mode(),
                         floors_override=st.session_state.get("dmto_floors") if drawing_mode() == "sketch" else None)


def seed_review_rows(pi, params) -> None:
    """First time Step 3 opens after an analysis: fill rooms/openings from the drawings."""
    if st.session_state.get("dmto_rooms") is not None and st.session_state.get("dmto_openings") is not None:
        return
    p = _build(pi, params)
    if st.session_state.get("dmto_rooms") is None:
        st.session_state["dmto_rooms"] = rooms_to_rows(p)
    if st.session_state.get("dmto_openings") is None:
        st.session_state["dmto_openings"] = openings_to_rows(p)


def current_project(pi, params, room_rows=None, opening_rows=None, with_overrides=True):
    room_rows = st.session_state.get("dmto_rooms") if room_rows is None else room_rows
    opening_rows = st.session_state.get("dmto_openings") if opening_rows is None else opening_rows
    return _build(pi, params, room_rows, opening_rows,
                  overrides=(st.session_state.get("dmto_overrides") or {}) if with_overrides else None)


def input_signature(pi, params) -> str:
    """Fingerprint of everything the take-off depends on - used to detect out-of-date results."""
    import hashlib
    import json
    h = hashlib.sha256()
    for part in (pi.model_dump_json() if pi is not None else "", params.model_dump_json() if params is not None else "",
                 json.dumps(st.session_state.get("dmto_rooms"), sort_keys=True, default=str),
                 json.dumps(st.session_state.get("dmto_openings"), sort_keys=True, default=str),
                 json.dumps(st.session_state.get("dmto_overrides") or {}, sort_keys=True, default=str),
                 repr(st.session_state.get("dmto_options")), repr(st.session_state.get("uploaded_signature")),
                 repr(st.session_state.get("dmto_floors")),
                 drawing_mode()):
        h.update(part.encode())
    return h.hexdigest()


def run_takeoff(pi, params) -> DetailedResult:
    p = current_project(pi, params)
    res = compute(p, kb(), scope=st.session_state["dmto_options"].scope)
    st.session_state["dmto_result"] = res
    st.session_state["dmto_result_sig"] = input_signature(pi, params)
    st.session_state["dmto_xlsx"] = None
    return res


def ensure_current_result(pi, params) -> Optional[DetailedResult]:
    """Return an up-to-date take-off: recalculates automatically when any input changed since the
    last calculation (e.g. after jumping here from Step 3 via the sidebar), and says so."""
    res = st.session_state.get("dmto_result")
    if params is None:
        return res
    if res is None or st.session_state.get("dmto_result_sig") != input_signature(pi, params):
        stale = res is not None
        issues = validate_project(current_project(pi, params))
        if errors(issues):
            if stale:
                st.error("Your inputs changed but contain errors, so the take-off below is NOT up to date. "
                         "Fix these in Step 3: " + "; ".join(f"{i.label} {i.message}" for i in errors(issues)[:5]))
            return res
        with st.spinner("Inputs changed - recalculating the take-off..."):
            res = run_takeoff(pi, params)
        if stale:
            st.info("\U0001f504 Your inputs changed since the last calculation - the take-off has been recalculated.")
    return res


def _apply_editor_delta(rows: List[dict], delta, columns: List[str]) -> List[dict]:
    """Apply a st.data_editor widget-state delta (edited/added/deleted rows) to the rows it was built from."""
    if not isinstance(delta, dict):
        return rows
    out = [dict(r) for r in rows]
    for idx, changes in (delta.get("edited_rows") or {}).items():
        i = int(idx)
        if 0 <= i < len(out):
            out[i].update(changes)
    deleted = {int(i) for i in (delta.get("deleted_rows") or [])}
    out = [r for i, r in enumerate(out) if i not in deleted]
    for added in delta.get("added_rows") or []:
        row = {c: None for c in columns}
        row.update(added)
        out.append(row)
    return out


def commit_review_from_widgets() -> None:
    """Save Step 3 table edits that were not yet applied (used when leaving Step 3 via the sidebar)."""
    ver = st.session_state.get("dmto_ver", 0)
    rooms = st.session_state.get("dmto_rooms")
    opens = st.session_state.get("dmto_openings")
    rd = st.session_state.get(f"dmto_rooms_editor_{ver}")
    od = st.session_state.get(f"dmto_open_editor_{ver}")
    if rooms is None or opens is None or (not rd and not od):
        return
    save_review(_apply_editor_delta(rooms, rd, ROOM_COLS), _apply_editor_delta(opens, od, OPENING_COLS))


def render_drawing_mode_banner(mode: Optional[str] = None) -> None:
    mode = mode or drawing_mode()
    if mode == "scanned":
        st.error(
            "\u26a0\ufe0f **Your drawings could not be read** - they are scanned images or photos, so no dimensions, "
            "rooms or schedules were taken from them. The quantities are based on a **typical house for the chosen plot "
            "size**, not on your drawings, and every line is marked as assumed. To get real quantities: use "
            "**'Also ask the AI'** in Step 2 (needs a Groq key), switch to the **guided sketch route** in Step 1 "
            "(answer a few questions about your house), enter your rooms, doors/windows and key dimensions in Step 3, "
            "or upload the CAD-exported PDF from the architect."
        )
    elif mode == "sketch":
        st.info("\u270f\ufe0f **Concept take-off from your sketch & answers.** Walls, floor areas and openings come from a concept "
                "layout of your rooms, so expect roughly \u00b115-30% on the main materials. Check the rooms and sizes in "
                "Step 3; with the architect's drawings later, re-run for procurement-grade quantities.")
    elif mode == "none":
        st.warning("No drawings uploaded - quantities are based on a typical house for the chosen plot size. "
                   "Enter your rooms, doors/windows and key dimensions in Step 3 for real quantities.")


# ---------------------------------------------------------------- Step 2
def render_scan_summary(scan) -> None:
    if scan is None:
        return
    rows = []
    names = {"FT": "Floor traps (F.T)", "MH": "Manholes (M.H)", "GT": "Gully traps (G.T)", "CO": "Cleanouts (C.O)",
             "VANITY": "Vanities / basins", "WC": "WCs", "SHOWER": "Showers", "SUMP": "Roof outlets (sump/khura)",
             "WARDROBE": "Wardrobes", "BALCONY": "Balconies", "TERRACE": "Terraces", "DB": "Distribution boards",
             "GEYSER": "Water heaters", "OVEN": "Built-in oven"}
    for k, n in scan.label_counts.items():
        rows.append({"Item": names.get(k, k), "Count": n,
                     "Note": "detail/legend sheet only - not used as a count" if k in scan.detail_only else "from plan labels"})
    for d in scan.doors:
        rows.append({"Item": f"Door schedule: {d.name} {d.width_ft:g}'x{d.height_ft:g}' "
                             f"({'double' if d.leaves == 2 else 'single'}, {d.chogath_in:g}\" chogath)",
                     "Count": d.qty, "Note": d.source})
    if scan.oh_tank_gal:
        rows.append({"Item": f"Overhead tank {scan.oh_tank_gal:g} gal {scan.oh_tank_type}", "Count": 1, "Note": scan.sources.get("OH_TANK", "")})
    for label, v in (("Septic tank (detail)", scan.septic_detail), ("Septic tank (plan)", scan.septic_plan),
                     ("UG water tank (detail)", scan.ug_tank_detail), ("OH tank RCC (detail)", scan.oh_tank_detail)):
        if v:
            rows.append({"Item": f"{label} {v[0]:g}' x {v[1]:g}'", "Count": 1, "Note": ""})
    if rows:
        st.markdown("##### \U0001f50e Services & schedules read from the drawings")
        st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")
    for n in scan.notes:
        st.warning(n)


# ---------------------------------------------------------------- Step 3 review tables
def render_rooms_editor() -> List[dict]:
    types = [rd.room_type for rd in kb().room_defaults]
    df = pd.DataFrame(st.session_state["dmto_rooms"] or [], columns=ROOM_COLS)
    st.caption("Rooms drive floor/wall finishes, plaster, paint, ceilings, waterproofing and the default electrical "
               "points. Room type decides the finish defaults. Add rows for anything missing (e.g. terraces with sizes).")
    edited = st.data_editor(
        df, key=f"dmto_rooms_editor_{st.session_state['dmto_ver']}", hide_index=True, width="stretch", num_rows="dynamic",
        column_config={
            "Room type": st.column_config.SelectboxColumn(options=types, required=True),
            "Floor": st.column_config.SelectboxColumn(options=FLOOR_KEYS, required=True),
            "Length (ft)": st.column_config.NumberColumn(min_value=0.0, step=0.25, format="%.2f"),
            "Width (ft)": st.column_config.NumberColumn(min_value=0.0, step=0.25, format="%.2f"),
        },
        disabled=["Source", "Confidence"])
    rows = edited.to_dict("records")
    tot = sum(r.area for r in rows_to_rooms(rows))
    st.caption(f"{len(rows_to_rooms(rows))} rooms, total {tot:,.0f} sft.")
    return rows


def render_concept_plan(room_rows: List[dict], expanded: bool = False) -> None:
    """Schematic plan per floor (sketch route) so the user can see the rooms on the plot."""
    from detailed_mto.concept_plan import floor_svg
    pi = st.session_state.get("project_inputs")
    width = float(getattr(pi, "plot_width_ft", 0) or 0) or 30.0
    ov = st.session_state.get("dmto_overrides") or {}
    width = float(ov.get("PLOT_W", width))
    floors = [f for f in ("ground", "first", "second", "roof") if any(r.get("Floor") == f for r in room_rows)]
    if not floors:
        return
    with st.expander("\U0001f5fa\ufe0f Concept plan (schematic)", expanded=expanded):
        st.caption("A simple arrangement of your rooms on the plot width, to check nothing is missing or oversized. "
                   "It is not an architectural design - quantities use the room sizes, not this layout.")
        cols = st.columns(min(len(floors), 3))
        names = {"ground": "Ground floor", "first": "First floor", "second": "Second floor", "roof": "Mumty / roof"}
        for i, f in enumerate(floors):
            with cols[i % len(cols)]:
                svg = floor_svg([r for r in room_rows if r.get("Floor") == f], width, names[f])
                st.markdown(svg, unsafe_allow_html=True)


def render_openings_editor() -> List[dict]:
    df = pd.DataFrame(st.session_state["dmto_openings"] or [], columns=OPENING_COLS)
    st.caption("Doors come from the chogath/door schedule when the drawings have one. Window WIDTHS are rarely given "
               "on Pakistani D&W sheets - replace the assumed 4'x5' sizes with the real ones.")
    edited = st.data_editor(
        df, key=f"dmto_open_editor_{st.session_state['dmto_ver']}", hide_index=True, width="stretch", num_rows="dynamic",
        column_config={
            "Kind": st.column_config.SelectboxColumn(options=["door", "window", "ventilator"], required=True),
            "Width (ft)": st.column_config.NumberColumn(min_value=0.0, step=0.25, format="%.2f"),
            "Height (ft)": st.column_config.NumberColumn(min_value=0.0, step=0.25, format="%.2f"),
            "Qty": st.column_config.NumberColumn(min_value=0, step=1),
            "Leaves": st.column_config.NumberColumn(min_value=1, max_value=4, step=1),
            "External": st.column_config.CheckboxColumn(),
        },
        disabled=["Source", "Confidence"])
    return edited.to_dict("records")


def render_counts_editor(pi, params, room_rows, opening_rows) -> None:
    ov = dict(st.session_state.get("dmto_overrides") or {})
    base = current_project(pi, params, room_rows, opening_rows, with_overrides=False)  # drawing/default values
    cur = _build(pi, params, room_rows, opening_rows, overrides=ov)  # values actually used (edits propagated)
    groups = sorted({p.group for p in cur.params.values()})
    st.caption("Counts and dimensions used by the take-off. 🟢 read from drawings · 🟡 derived · ⚪ assumed default · "
               "🔵 your edit. Change any value - it overrides the drawing/default value, and values derived from it "
               "(e.g. wall heights from floor height) follow automatically.")
    sel = st.multiselect("Show groups", groups, default=groups, key="dmto_count_groups")
    rows = []
    for prm in sorted(cur.params.values(), key=lambda x: (x.group, x.key)):
        if prm.group not in sel:
            continue
        rows.append({"Key": prm.key, "Group": prm.group, "Parameter": prm.label, "Value": round(float(prm.value), 3),
                     "Unit": prm.unit, "": CONF_ICON.get(prm.confidence, ""), "Source": prm.source})
    df = pd.DataFrame(rows)
    edited = st.data_editor(df, key=f"dmto_counts_editor_{st.session_state.get('dmto_counts_ver', 0)}_{'-'.join(sel)}", hide_index=True,
                            width="stretch", disabled=["Key", "Group", "Parameter", "Unit", "", "Source"],
                            column_config={"Value": st.column_config.NumberColumn(format="%.2f")})
    changed = False
    for (_, a), (_, b) in zip(df.iterrows(), edited.iterrows()):
        key = a["Key"]
        try:
            newv = float(b["Value"])
        except (TypeError, ValueError):
            continue
        if abs(newv - float(a["Value"])) <= 1e-6:
            continue  # untouched in this run
        basev = base.params[key].value if key in base.params else None
        if basev is not None and abs(newv - basev) <= 1e-3:
            ov.pop(key, None)  # set back to the drawing/default value
        else:
            ov[key] = newv
        changed = True
    if changed:
        st.session_state["dmto_overrides"] = ov
        # rebuild only this table (own key counter) so derived values refresh; room/door edits are untouched
        st.session_state["dmto_counts_ver"] = st.session_state.get("dmto_counts_ver", 0) + 1
        st.rerun()
    if ov:
        st.caption(f"{len(ov)} value(s) edited by you: " + ", ".join(sorted(ov)))
        if st.button("Reset all edited counts to drawing/default values", key="dmto_reset_ov"):
            st.session_state["dmto_overrides"] = {}
            st.session_state["dmto_counts_ver"] = st.session_state.get("dmto_counts_ver", 0) + 1
            st.rerun()


def render_validation(pi, params, room_rows, opening_rows):
    """Plausibility checks on everything the take-off will use; returns the list of blocking errors."""
    issues = validate_project(_build(pi, params, room_rows, opening_rows, overrides=st.session_state.get("dmto_overrides") or {}))
    errs = errors(issues)
    warns = [i for i in issues if i.severity == "warning"]
    if errs:
        st.error("**Please fix before calculating:**\n\n" + "\n".join(f"- **{i.label}** {i.message}" for i in errs))
    if warns:
        with st.expander(f"\u26a0\ufe0f {len(warns)} unusual value(s) - please confirm", expanded=len(warns) <= 3):
            for i in warns:
                st.warning(f"**{i.label}** {i.message}")
    return errs


def save_review(room_rows, opening_rows) -> None:
    st.session_state["dmto_rooms"] = mark_user_edits(room_rows, st.session_state.get("dmto_rooms") or [],
                                                     ["Floor", "Room", "Room type", "Length (ft)", "Width (ft)"])
    st.session_state["dmto_openings"] = mark_user_edits(opening_rows, st.session_state.get("dmto_openings") or [],
                                                        ["Kind", "Name", "Width (ft)", "Height (ft)", "Qty", "Leaves", "External"])
    st.session_state["dmto_ver"] += 1


def render_coefficients() -> None:
    k = kb()
    st.caption("Engineering coefficients, mixes and wastage come from the Master Material Database "
               "(data/master_material_database.xlsx). Edit them in Excel and run scripts/validate_knowledge_base.py.")
    groups = sorted({m["group"] for m in k.coefficient_meta.values()})
    g = st.selectbox("Coefficient group", groups, index=groups.index("Steel") if "Steel" in groups else 0, key="dmto_coef_group")
    st.dataframe(pd.DataFrame([{"Coeff_ID": cid, "Description": m["description"], "Value": round(k.coefficients[cid], 4),
                                "Unit": m["unit"], "Source": m["source"]}
                               for cid, m in k.coefficient_meta.items() if m["group"] == g]), hide_index=True, width="stretch")
    c1, c2 = st.columns(2)
    with c1:
        st.markdown("**Nominal mixes**")
        st.dataframe(pd.DataFrame([{"Mix": x.description, "Cement bags/cft": round(x.values["J"], 4),
                                    "Sand cft/cft": round(x.values["K"], 3), "Crush cft/cft": round(x.values["L"], 3)}
                                   for x in k.mixes.values()]), hide_index=True, width="stretch")
    with c2:
        st.markdown("**Wastage**")
        st.dataframe(pd.DataFrame([{"Key": key, "Wastage": f"{v * 100:.1f}%"} for key, v in k.wastage.items()]),
                     hide_index=True, width="stretch", height=300)


# ---------------------------------------------------------------- Step 4
KEY_TOTALS = [
    ("Cement", {"CON-001"}, "bags", 1),
    ("Steel", {"RBR-001", "RBR-002", "RBR-003", "RBR-004"}, "ton", 1000),
    ("Bricks", {"MAS-001", "MAS-002"}, "Nos", 1),
    ("Sand", {"CON-004", "CON-005", "EW-003", "EXT-003", "PDR-020"}, "cft", 1),
    ("Crush", {"CON-006", "CON-007", "CON-008"}, "cft", 1),
    ("Tiles (floor + wall)", {"FLR-001", "FLR-002", "FLR-003"}, "sft", 1),
]


def _total(res: DetailedResult, ids) -> float:
    return sum(m.gross_qty for m in res.materials if m.material.mat_id in ids)


def render_takeoff(res: DetailedResult) -> None:
    counts = res.status_counts()
    quantified = sum(v for k, v in counts.items() if k in INCLUDED_STATUSES)
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Materials in scope", len(res.scoped_materials()))
    m2.metric("Quantified", quantified)
    m3.metric("Using assumed inputs", counts.get(ST_CALC_ASSUMED, 0))
    m4.metric("Need your input", counts.get(ST_NEEDS_INPUT, 0))
    shown = [(label, ids, unit, div) for label, ids, unit, div in KEY_TOTALS if _total(res, ids) > 0]
    for i in range(0, len(shown), 3):
        cols = st.columns(3)
        for c, (label, ids, unit, div) in zip(cols, shown[i:i + 3]):
            v = _total(res, ids) / div
            c.metric(label, f"{v:,.2f} {unit}" if div > 1 else f"{v:,.0f} {unit}")
    from ui import copilot_views
    copilot_views.render_checker(res)  # drawing conflicts, odd ratios, inconsistencies - with one-click fixes

    t0, tc, t1, t2, t3, t4, t5, t6, t_sched = st.tabs(["🛒 Shopping list", "🤖 Copilot", "📋 Material schedule",
                                               "🗓️ By construction stage", "📐 Work items", "🔍 Traceability",
                                               "📝 Assumptions & gaps", "✅ Checks"] + ["📅 Schedule & Gantt"],
                                              key="dmto_step4_tabs")  # keyed: stays on the same tab after a button click
    with t0:
        _shopping_tab(res)
    with tc:
        copilot_views.render_copilot(res)
    with t1:
        _schedule_tab(res)
    with t2:
        _stage_tab(res)
    with t3:
        st.dataframe(pd.DataFrame([{"WI_ID": w.wi_id, "Division": w.division, "Work item": w.description, "Unit": w.unit,
                                    "Quantity": round(w.qty, 2), "Status": w.status, "Confidence": w.confidence,
                                    "Calculation": w.calculation} for w in res.scoped_work_items()]),
                     hide_index=True, width="stretch", height=520)
    with t4:
        _trace_tab(res)
    with t5:
        _assumptions_tab(res)
    with t6:
        _checks_tab(res)
    with t_sched:  # construction schedule & Gantt chart (scheduling/, ui/schedule_views.py)
        from ui import schedule_views
        schedule_views.render_schedule_tab(res)


def _reliability(m) -> str:
    if m.material.unit.upper() == "LS":
        return "Lump-sum item"
    if m.status == ST_PROVISIONAL:
        return "Allowance - confirm with owner"
    return "✔ From drawings / inputs" if m.confidence in ("High", "Medium", "User") else "⚠ Check - uses defaults"


def _shopping_tab(res: DetailedResult) -> None:
    buy = res.purchase_list()
    cats = list(dict.fromkeys(m.material.category for m in buy))
    st.caption(f"{len(buy)} materials to purchase for scope **{res.scope}**, rounded up to how they are sold "
               "(bags, tons, coils, pipe lengths, hundreds of bricks). Wastage is included.")
    c1, c2 = st.columns([3, 1])
    sel = c1.multiselect("Trade", cats, default=[], placeholder="All trades", key="dmto_shop_cat")
    only_check = c2.checkbox("Only items to check", key="dmto_shop_check", help="Items whose quantity uses default values")
    rows = []
    for m in buy:
        if sel and m.material.category not in sel:
            continue
        rel = _reliability(m)
        if only_check and not rel.startswith("⚠"):
            continue
        q, unit = purchase_qty(m.material, m.gross_qty)
        code = _stage_code(m)
        rows.append({"Material": m.material.description, "Quantity to buy": q, "Buy unit": unit,
                     "When needed": f"{code} - {STAGE_NAMES.get(code, '')}", "Reliability": rel,
                     "Specification": m.material.specification, "Trade": m.material.category, "Code": m.material.mat_id})
    st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch", height=560,
                 column_config={"Material": st.column_config.TextColumn(width="large"),
                                "Quantity to buy": st.column_config.NumberColumn(format="localized", width="small"),
                                "Buy unit": st.column_config.TextColumn(width="small")})
    pending = [m for m in res.scoped_materials() if m.status == ST_NEEDS_INPUT]
    if pending:
        st.warning("Still to be quantified (an input is missing): " +
                   "; ".join(f"{m.material.description} ({m.material.required_inputs or 'quantity'})" for m in pending))


def _schedule_tab(res: DetailedResult) -> None:
    cats = list(dict.fromkeys(m.material.category for m in res.scoped_materials()))
    statuses = [ST_CALC, ST_CALC_ASSUMED, ST_PROVISIONAL, ST_NEEDS_INPUT, ST_OPTION, ST_REFERENCE, "Not required"]
    c1, c2, c3 = st.columns([2, 2, 1])
    sel_cats = c1.multiselect("Categories", cats, default=[], placeholder="All categories", key="dmto_f_cat")
    sel_st = c2.multiselect("Status", statuses, default=[ST_CALC, ST_CALC_ASSUMED, ST_PROVISIONAL, ST_NEEDS_INPUT], key="dmto_f_st")
    q = c3.text_input("Search", key="dmto_f_q")
    rows = []
    for m in res.scoped_materials():
        if sel_cats and m.material.category not in sel_cats:
            continue
        if sel_st and m.status not in sel_st:
            continue
        text = f"{m.material.mat_id} {m.material.description} {m.material.specification}".lower()
        if q and q.lower() not in text:
            continue
        rows.append({"Mat_ID": m.material.mat_id, "Category": m.material.category, "Material": m.material.description,
                     "Specification": m.material.specification, "Unit": m.material.unit,
                     "Net qty": round(m.net_qty, 2), "Wastage": f"{m.wastage_pct * 100:.1f}%",
                     "Qty incl. wastage": round(m.gross_qty, 2), "Purchase": m.purchase, "Status": m.status,
                     "Conf.": CONF_ICON.get(m.confidence, "") + " " + (m.confidence or ""), "Stage": m.material.stage,
                     "If selected": round(m.if_selected_qty, 2) if m.if_selected_qty else None})
    st.caption(f"{len(rows)} line(s) shown. Status 'Option - not included' lines show the quantity they would need in "
               "'If selected'; 'Counted elsewhere' lines are assemblies/duplicates, not bought twice.")
    st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch", height=560)


def _stage_code(m) -> str:
    return (m.material.stage or "S?").split(",")[0].split("-")[0].strip()


def _stage_tab(res: DetailedResult) -> None:
    k = kb()
    stages = sorted({_stage_code(m) for m in res.materials if m.included})
    labels = {s: f"{s} - {STAGE_NAMES.get(s, k.stages.get(s, ''))}" for s in stages}
    s = st.selectbox("Construction stage", stages, format_func=lambda x: labels[x], key="dmto_stage")
    rows = [{"Mat_ID": m.material.mat_id, "Material": m.material.description, "Unit": m.material.unit,
             "Qty incl. wastage": round(m.gross_qty, 2), "Purchase": m.purchase, "Status": m.status}
            for m in res.materials if m.included and _stage_code(m) == s]
    st.caption("Order these before/at this stage. Items cast into slabs or walls (sleeves, fan hooks, conduits, "
               "concealed cisterns, mixer bodies) are listed under the stage when they must be on site.")
    st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch", height=480)


def _trace_tab(res: DetailedResult) -> None:
    inc = [m for m in res.scoped_materials() if m.included or m.if_selected_qty]
    if not inc:
        return
    labels = {m.material.mat_id: f"{m.material.mat_id} - {m.material.description}" for m in inc}
    mid = st.selectbox("Material", list(labels), format_func=lambda x: labels[x], key="dmto_trace")
    m = res.by_id(mid)
    st.markdown(f"**{m.material.description}** - {m.material.specification}")
    st.markdown(f"Net **{m.net_qty:,.2f} {m.material.unit}** × (1 + {m.wastage_pct * 100:.1f}% wastage) = "
                f"**{m.gross_qty:,.2f} {m.material.unit}** · status: {m.status} · confidence: {m.confidence or '-'}")
    st.code(m.calculation or "-", language=None)
    contrib = [c for c in res.contributions if c.mat_id == mid]
    if contrib:
        wmap = {w.wi_id: w for w in res.work_items}
        st.dataframe(pd.DataFrame([{"Work item": f"{c.wi_id} {c.wi_desc}", "WI qty": round(c.wi_qty, 2), "WI unit": c.wi_unit,
                                    "Coefficient": round(c.coefficient, 5), "Coeff_ID": c.coeff_id, "Mix": c.mix_ref,
                                    "Material qty": round(c.net_qty, 2),
                                    "WI calculation": wmap[c.wi_id].calculation if c.wi_id in wmap else ""}
                                   for c in contrib]), hide_index=True, width="stretch")
    if m.material.notes:
        st.caption(m.material.notes)


def _assumptions_tab(res: DetailedResult) -> None:
    p = res.project
    for a in p.assumptions:
        st.info(a)
    needs = [m for m in res.scoped_materials() if m.status == ST_NEEDS_INPUT]
    if needs:
        st.markdown("**Materials that need an input**")
        for m in needs:
            st.write(f"- {m.material.mat_id} {m.material.description}: {m.material.required_inputs or 'quantity'}")
    low = [x for x in p.params.values() if x.confidence in ("Assumed", "Low")]
    if low:
        st.markdown("**Inputs still at default values** (edit them in Step 3 → Counts & dimensions)")
        st.dataframe(pd.DataFrame([{"Parameter": x.label, "Value": round(x.value, 2), "Unit": x.unit, "Basis": x.source}
                                   for x in low]), hide_index=True, width="stretch")


def benchmark_rows(res: DetailedResult) -> List[dict]:
    cov = sum(f.covered_sft for f in res.project.floors) or 1.0
    out = []
    for label, ids, unit, lo, hi in benchmarks_for(res.project):
        v = _total(res, set(ids)) / cov
        out.append({"Check": label, "Value": round(v, 2), "Unit": unit, "Typical range": f"{lo:g} - {hi:g}",
                    "Result": "OK" if lo <= v <= hi else "CHECK"})
    return out


def _checks_tab(res: DetailedResult) -> None:
    cov = sum(f.covered_sft for f in res.project.floors)
    st.caption(f"Ratios per sft of total covered area ({cov:,.0f} sft incl. mumty). Indicative ranges for 5-10 marla "
               "houses - a CHECK means look at the inputs, not that the number is wrong.")
    st.dataframe(pd.DataFrame(benchmark_rows(res)), hide_index=True, width="stretch")


# ---------------------------------------------------------------- Step 5
def export_bytes(res: DetailedResult) -> bytes:
    from detailed_mto.export import build_detailed_mto_workbook
    cache = st.session_state.get("dmto_xlsx")
    if cache and cache[0] == id(res):
        return cache[1]
    data = build_detailed_mto_workbook(res)
    st.session_state["dmto_xlsx"] = (id(res), data)
    return data


def schedule_csv(res: DetailedResult) -> bytes:
    df = pd.DataFrame([{"Mat_ID": m.material.mat_id, "Category": m.material.category, "Subcategory": m.material.subcategory,
                        "Material": m.material.description, "Specification": m.material.specification, "Unit": m.material.unit,
                        "Net qty": round(m.net_qty, 3), "Wastage %": round(m.wastage_pct * 100, 1),
                        "Qty incl. wastage": round(m.gross_qty, 3), "Purchase": m.purchase, "Status": m.status,
                        "Confidence": m.confidence, "Stage": m.material.stage} for m in res.scoped_materials()])
    return df.to_csv(index=False).encode("utf-8")


def options_summary(opts) -> str:
    mix = {"MX_RCC124": "RCC 1:2:4", "MX_RCC1153": "RCC 1:1.5:3"}.get(opts.rcc_mix, opts.rcc_mix)
    return (f"Scope: **{opts.scope}** · Finish: **{opts.finish_tier}** · {mix} · Roof: {opts.roof_system} · "
            f"Walls: {opts.masonry} · Gas: {opts.gas_source} · False ceilings: {'yes' if opts.include_false_ceiling else 'no'} · "
            f"Recharge well: {'yes' if opts.include_rwh else 'no'} · Seismic bands: {'yes' if opts.seismic_bands else 'no'}")


def result_is_stale(res: Optional[DetailedResult]) -> bool:
    return res is None
