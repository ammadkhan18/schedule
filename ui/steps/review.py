"""Step 3 - review rooms, doors & windows, counts & dimensions, structure."""
from __future__ import annotations


import streamlit as st

from engineering import rules
from engineering.calculations import external_perimeter_estimate, founding_depth_estimate
from engineering.validation import validate_params
from models.schemas import ConfidenceLevel, Estimate, ProjectInputs, Source
from ui import mto_views
from ui.components import confidence_badge, render_estimate_input
from ui.state import go_to_step
from utils import units


# ---------------------------------------------------------------------------
# STEP 3 — Review drawing data
# ---------------------------------------------------------------------------
def _render_structure_inputs(params, unit_system):
    """Structural parameters (columns, beams, slab, footings) and fallbacks for
    scanned drawings. Rooms, walls, doors and services read from CAD PDFs take
    precedence in the material take-off."""
    with st.expander("General", expanded=True):
        c1, c2 = st.columns(2)
        with c1:
            params.num_floors = render_estimate_input("Number of RCC slab levels (incl. roof)", params.num_floors, "num_floors_inp", "floors", step=1.0)
        with c2:
            params.plinth_area_per_floor_sqm = render_estimate_input("Plinth/built-up area per floor", params.plinth_area_per_floor_sqm, "plinth_area_inp", step=1.0, unit_system=unit_system, quantity_kind="area")

    with st.expander("Footings", expanded=True):
        footing_types = ["isolated", "strip", "raft", "combined"]
        current_type = params.footings.footing_type if params.footings.footing_type in footing_types else "isolated"
        params.footings.footing_type = st.selectbox(
            "Footing type", footing_types, index=footing_types.index(current_type),
            help="isolated = RCC frame on pad footings; strip = load-bearing brick walls on stepped strip foundations "
            "(typical for many 5-10 marla houses). raft/combined are calculated as isolated pads.",
        )
        if params.footings.founding_depth_m is None:
            params.footings.founding_depth_m = founding_depth_estimate(params.footings, unit_system)
        if params.footings.footing_type != st.session_state.get("_last_footing_type", params.footings.footing_type) and "strip" in (
            params.footings.footing_type, st.session_state.get("_last_footing_type")
        ):
            # switching to/from strip: swap in that system's standard sizes
            det = rules.detailing(unit_system)
            low = lambda v, n: Estimate(value=v, confidence=ConfidenceLevel.LOW, source=Source.DEFAULT_ASSUMPTION, note=n)  # noqa: E731
            if params.footings.footing_type == "strip":
                params.footings.width_m = low(det["strip_width_m"], "Typical strip foundation width")
                params.footings.depth_m = low(det["strip_pcc_thickness_m"], "Typical PCC bed thickness")
                params.footings.founding_depth_m = low(det["strip_founding_depth_m"], "Typical strip foundation depth")
            else:
                params.footings.width_m = low(det["footing_thickness_m"] * 0 + params.footings.length_m.value, "Square footing")
                params.footings.depth_m = low(det["footing_thickness_m"], "Typical footing thickness")
                params.footings.founding_depth_m = low(det["founding_depth_m"], "Typical founding depth")
        st.session_state["_last_footing_type"] = params.footings.footing_type
        strip = params.footings.footing_type == "strip"
        if strip:
            st.caption(
                "Strip foundations (load-bearing walls): a PCC bed and stepped brick footing run under every "
                "ground-floor wall (length = total wall length). Width = PCC/trench width; thickness = PCC bed; "
                "Count and Length are not used."
            )
        c1, c2, c3, c4, c5 = st.columns(5)
        with c1:
            params.footings.count = render_estimate_input("Count", params.footings.count, "ftg_count", "nos", step=1.0)
        with c2:
            params.footings.length_m = render_estimate_input("Length", params.footings.length_m, "ftg_len", step=0.05, unit_system=unit_system, quantity_kind="length")
        with c3:
            params.footings.width_m = render_estimate_input("Strip (PCC) width" if strip else "Width", params.footings.width_m, "ftg_wid", step=0.05, unit_system=unit_system, quantity_kind="length")
        with c4:
            params.footings.depth_m = render_estimate_input(
                "PCC bed thickness" if strip else "Footing thickness", params.footings.depth_m, "ftg_dep", step=0.05, unit_system=unit_system, quantity_kind="thickness",
                help_text=(
                    'Depth of the concrete pad itself (typically 12"-24"). Drives footing concrete volume.'
                    if units.is_fps(unit_system)
                    else "Depth of the concrete pad itself (typically 0.3-0.6 m). Drives footing concrete volume."
                ),
            )
        with c5:
            params.footings.founding_depth_m = render_estimate_input(
                "Founding depth", params.footings.founding_depth_m, "ftg_found", step=0.05, unit_system=unit_system, quantity_kind="length",
                help_text=(
                    "Natural ground level down to the UNDERSIDE of the footing (typically 4'-0\" to 6'-6\"). Drives excavation depth."
                    if units.is_fps(unit_system)
                    else "Natural ground level down to the UNDERSIDE of the footing (typically 1.2-2.0 m). Drives excavation depth."
                ),
            )

    with st.expander("Columns", expanded=True):
        c1, c2, c3, c4 = st.columns(4)
        with c1:
            params.columns.count = render_estimate_input("Count", params.columns.count, "col_count", "nos", step=1.0)
        with c2:
            params.columns.width_m = render_estimate_input("Width (b)", params.columns.width_m, "col_b", step=0.01, unit_system=unit_system, quantity_kind="thickness")
        with c3:
            params.columns.depth_m = render_estimate_input("Depth (d)", params.columns.depth_m, "col_d", step=0.01, unit_system=unit_system, quantity_kind="thickness")
        with c4:
            params.columns.height_per_floor_m = render_estimate_input("Height/floor", params.columns.height_per_floor_m, "col_h", step=0.05, unit_system=unit_system, quantity_kind="length")

    with st.expander("Beams", expanded=True):
        c1, c2, c3, c4 = st.columns(4)
        with c1:
            params.beams.count = render_estimate_input("Count per level", params.beams.count, "beam_count", "nos", step=1.0)
        with c2:
            params.beams.avg_length_m = render_estimate_input("Avg length", params.beams.avg_length_m, "beam_len", step=0.1, unit_system=unit_system, quantity_kind="length")
        with c3:
            params.beams.width_m = render_estimate_input("Width", params.beams.width_m, "beam_w", step=0.01, unit_system=unit_system, quantity_kind="thickness")
        with c4:
            params.beams.depth_m = render_estimate_input("Depth", params.beams.depth_m, "beam_d", step=0.01, unit_system=unit_system, quantity_kind="thickness")

    with st.expander("Slabs", expanded=True):
        c1, c2 = st.columns(2)
        with c1:
            params.slabs.area_per_floor_sqm = render_estimate_input("Area per floor", params.slabs.area_per_floor_sqm, "slab_area", step=1.0, unit_system=unit_system, quantity_kind="area")
        with c2:
            params.slabs.thickness_m = render_estimate_input("Thickness", params.slabs.thickness_m, "slab_t", step=0.005, unit_system=unit_system, quantity_kind="thickness")

    with st.expander("Walls / Masonry (fallback when walls are not measured from CAD)", expanded=False):
        c1, c2, c3 = st.columns(3)
        with c1:
            params.walls.total_length_per_floor_m = render_estimate_input("Total wall length/floor", params.walls.total_length_per_floor_m, "wall_len", step=0.5, unit_system=unit_system, quantity_kind="length")
        with c2:
            params.walls.height_m = render_estimate_input("Wall height", params.walls.height_m, "wall_h", step=0.05, unit_system=unit_system, quantity_kind="length")
        with c3:
            params.walls.thickness_m = render_estimate_input("Wall thickness", params.walls.thickness_m, "wall_t", step=0.01, unit_system=unit_system, quantity_kind="thickness")
        if params.walls.external_perimeter_m is None:
            params.walls.external_perimeter_m = external_perimeter_estimate(params)
        params.walls.external_perimeter_m = render_estimate_input(
            "External wall perimeter/floor", params.walls.external_perimeter_m, "wall_ext_perim", step=0.5,
            unit_system=unit_system, quantity_kind="length",
            help_text="Outer building perimeter only (part of the total wall length). Splits internal vs external "
            "plaster/paint and sizes the roof parapet.",
        )
        params.walls.wall_material = st.selectbox(
            "Wall material", list(rules.MASONRY_UNIT_SIZES_M.keys()),
            format_func=lambda v: units.relabel_wall_material(v, unit_system),
            index=list(rules.MASONRY_UNIT_SIZES_M.keys()).index(params.walls.wall_material) if params.walls.wall_material in rules.MASONRY_UNIT_SIZES_M else 0,
        )

    with st.expander("Fallback: doors & windows (used only when the drawings give no schedule)", expanded=False):
        c1, c2, c3, c4 = st.columns(4)
        with c1:
            params.openings.door_count_per_floor = render_estimate_input("Doors/floor", params.openings.door_count_per_floor, "door_count", "nos", step=1.0)
        with c2:
            params.openings.avg_door_area_sqm = render_estimate_input("Avg door area", params.openings.avg_door_area_sqm, "door_area", step=0.05, unit_system=unit_system, quantity_kind="area")
        with c3:
            params.openings.window_count_per_floor = render_estimate_input("Windows/floor", params.openings.window_count_per_floor, "win_count", "nos", step=1.0)
        with c4:
            params.openings.avg_window_area_sqm = render_estimate_input("Avg window area", params.openings.avg_window_area_sqm, "win_area", step=0.05, unit_system=unit_system, quantity_kind="area")

    with st.expander("Fallback: bathrooms & kitchens (used only when no room list is read)", expanded=False):
        c1, c2 = st.columns(2)
        with c1:
            params.services.bathroom_count_total = render_estimate_input("Bathrooms (whole building)", params.services.bathroom_count_total, "svc_baths", "nos", step=1.0)
        with c2:
            params.services.kitchen_count_total = render_estimate_input("Kitchens (whole building)", params.services.kitchen_count_total, "svc_kitchens", "nos", step=1.0)

    return params


def step_3():
    st.header("Step 3 \u00b7 Review Data")
    pi: ProjectInputs = st.session_state["project_inputs"]
    params = st.session_state["extracted_params"]
    unit_system = pi.unit_system

    if st.session_state.get("ai_errors"):
        for e in st.session_state["ai_errors"]:
            st.error(e)
    facts = st.session_state.get("package_facts")
    n_rooms = sum(len(f.rooms) for f in facts.floors.values()) if (facts is not None and facts.has_facts()) else 0
    if st.session_state.get("input_mode") == "sketch":
        st.success(f"\u2705 Project created from your requirements: {len(st.session_state.get('dmto_rooms') or [])} rooms. "
                   "Check the tabs below, then calculate.")
    elif n_rooms:
        st.success(
            f"\u2705 Read from the drawings: {n_rooms} rooms with sizes, wall lengths by thickness, levels and "
            f"{len(st.session_state.get('drawing_filled') or [])} structural value(s). Check the tabs below - values marked "
            "\u26aa are defaults, \U0001f7e2 come from the drawings."
        )
    if (st.session_state.get("used_ai") or st.session_state.get("drawing_filled") or pi.plot_marla) and params.extraction_warnings:
        with st.expander("\u26a0\ufe0f Extraction warnings", expanded=False):
            for w in params.extraction_warnings:
                st.warning(w)
    if params.overall_notes and st.session_state.get("used_ai") and st.session_state.get("input_mode") != "sketch":
        st.info(f"**AI notes:** {params.overall_notes}")
    mto_views.render_drawing_mode_banner()
    st.caption(mto_views.options_summary(st.session_state["dmto_options"]) + " (change in Step 1)")

    mto_views.seed_review_rows(pi, params)
    from ui import copilot_views
    copilot_views.render_smart_questions()
    t_rooms, t_open, t_counts, t_struct, t_coef = st.tabs(
        ["\U0001f3e0 Rooms", "\U0001f6aa Doors & windows", "\u2699\ufe0f Advanced: all counts & dimensions",
         "\U0001f3d7\ufe0f Advanced: structure", "\U0001f4da Coefficients"], key="dmto_step3_tabs")
    with t_struct:
        if units.is_fps(unit_system):
            st.caption("FPS units: lengths in feet, member sizes in inches, areas in sqft.")
        legend = " ".join(confidence_badge(c) for c in [ConfidenceLevel.HIGH, ConfidenceLevel.MEDIUM, ConfidenceLevel.LOW])
        st.markdown(f"Columns, beams, slab and footings used for concrete, steel and formwork. Confidence: {legend}",
                    unsafe_allow_html=True)
        params = _render_structure_inputs(params, unit_system)
        st.session_state["extracted_params"] = params
    with t_rooms:
        room_rows = mto_views.render_rooms_editor()
        if st.session_state.get("input_mode") == "sketch":
            mto_views.render_concept_plan(room_rows)
    with t_open:
        opening_rows = mto_views.render_openings_editor()
    with t_counts:
        mto_views.render_counts_editor(pi, params, room_rows, opening_rows)
    with t_coef:
        mto_views.render_coefficients()

    dmto_errors = mto_views.render_validation(pi, params, room_rows, opening_rows)
    errors, warnings = validate_params(params, pi, st.session_state["assumptions"])
    errors = list(errors) + [f"{i.label} {i.message}" for i in dmto_errors]
    if errors or warnings:
        with st.expander(f"Input checks ({len(errors)} error(s), {len(warnings)} warning(s))", expanded=bool(errors)):
            for e in errors:
                st.error(e)
            for w in warnings:
                st.warning(w)
    st.caption("Table edits are kept when you press Apply, Calculate, Back or use the sidebar steps.")

    c1, c2, c3 = st.columns([1, 1, 2])
    with c1:
        if st.button("\u2190 Back to Step 2"):
            mto_views.save_review(room_rows, opening_rows)
            go_to_step(2)
            st.rerun()
    with c2:
        if st.button("\U0001f504 Apply table edits", help="Saves the Rooms / Doors & windows tables and refreshes the counts derived from them."):
            mto_views.save_review(room_rows, opening_rows)
            st.rerun()
    with c3:
        if st.button("Calculate Material Take-Off \u2192", type="primary", width="stretch", disabled=bool(errors)):
            mto_views.save_review(room_rows, opening_rows)
            with st.spinner("Calculating every material from the drawing data..."):
                mto_views.run_takeoff(pi, params)
            go_to_step(4)
            st.rerun()
