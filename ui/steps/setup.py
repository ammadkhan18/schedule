"""Step 1 - project setup, take-off specification and drawing upload."""
from __future__ import annotations

import hashlib

import streamlit as st

import config
from detailed_mto import Options
from engineering import plot_templates
from knowledge import load_knowledge_base
from models.schemas import EngineeringAssumptions, ProjectInputs
from ui.state import clear_dmto_review, clear_drawing_derived_state, go_to_step
from ui.steps.analysis import base_params
from ui.steps.constants import RCC_MIXES, TIER_TO_FINISH, TIERS
from utils import units


def _uploads_signature(files: list) -> tuple:
    """Content fingerprint of the uploaded drawings + their view tags."""
    return tuple((f["name"], hashlib.md5(f["bytes"]).hexdigest(), f["view_tag"]) for f in files)



# ---------------------------------------------------------------------------
# STEP 1 — Project setup + upload
# ---------------------------------------------------------------------------
def step_1():
    st.header("Step 1 \u00b7 Project Setup")
    pi: ProjectInputs = st.session_state["project_inputs"]

    st.markdown("##### What do you have?")
    modes = {"drawings": "\U0001f4d0 Architect's drawings (CAD PDF / drawing set)",
             "sketch": "\u270f\ufe0f A sketch, a photo, or just an idea - guide me with questions"}
    cur_mode = st.session_state.get("input_mode", "drawings")
    input_mode = st.radio("How will you give us the house design?", list(modes), format_func=lambda k: modes[k],
                          index=list(modes).index(cur_mode) if cur_mode in modes else 0, key="input_mode_radio",
                          label_visibility="collapsed")
    if input_mode == "sketch":
        st.info("No drawings needed: in the next step you can upload a hand sketch or photo and/or describe the house; "
                "the AI reads it (optional) and the app asks a few simple questions, then calculates every material. "
                "Result: a concept-level take-off (about \u00b115-30%).")
    else:
        st.write("Enter the project details, choose the take-off specification and upload the complete drawing set "
                 "(architectural, structural, plumbing and electrical sheets).")

    st.markdown("##### Units")
    unit_system_options = [units.SI, units.FPS]
    unit_system = st.selectbox(
        "Unit System",
        options=unit_system_options,
        format_func=lambda v: units.UNIT_SYSTEM_LABELS[v],
        index=unit_system_options.index(pi.unit_system) if pi.unit_system in unit_system_options else 1,
        help=(
            "FPS (default, standard Pakistani practice): every input, default dimension, calculation "
            "trace, MTO/BOQ quantity, rate and export is in feet/inches, sqft and cft, with psi concrete "
            "grades and Grade 40/60/75 steel. SI: everything in metres, m² and m³ with M-grades and "
            "Fe-grade steel. Conversions are exact; each system uses its own round standard details "
            "(e.g. 6\" lintels in FPS vs 150 mm in SI)."
        ),
        key="unit_system_selector",
    )

    # ---- Plot (the app's 5-10 marla scope) --------------------------------
    st.markdown("##### Plot (5-10 marla)")
    plot_options = [None] + plot_templates.PLOT_SIZES_MARLA
    pc1, pc2, pc3, pc4 = st.columns(4)
    plot_marla = pc1.selectbox(
        "Plot size",
        plot_options,
        index=plot_options.index(pi.plot_marla) if pi.plot_marla in plot_options else 0,
        format_func=lambda m: "Not specified" if m is None else f"{m} marla",
        help="Choosing a plot size gives a complete typical house as the starting point (even with no drawing) "
        "and lets the app flag values that are implausible for that plot.",
    )
    marla_options = list(plot_templates.MARLA_STANDARDS)
    marla_sqft = pc2.selectbox(
        "Marla standard",
        marla_options,
        index=marla_options.index(pi.marla_sqft) if pi.marla_sqft in marla_options else 0,
        format_func=lambda v: plot_templates.MARLA_STANDARDS[v],
        disabled=plot_marla is None,
        help="Housing societies (LDA/DHA/Bahria style) use 225 sqft per marla; traditional measurement uses 272.25 sqft - a ~21% difference.",
    )
    storey_labels = {1: "Single storey", 2: "G+1 (double storey)", 3: "G+2"}
    plot_storeys = pc3.selectbox(
        "Storeys",
        plot_templates.STOREY_OPTIONS,
        index=plot_templates.STOREY_OPTIONS.index(pi.plot_storeys) if pi.plot_storeys in plot_templates.STOREY_OPTIONS else 1,
        format_func=lambda n: storey_labels[n],
        disabled=plot_marla is None,
    )
    typical_w, _ = plot_templates.plot_dimensions_ft(plot_marla or 5, marla_sqft)
    current_w = pi.plot_width_ft or typical_w
    fps_units = units.is_fps(unit_system)
    shown_w = current_w if fps_units else current_w * 0.3048
    entered_w = pc4.number_input(
        f"Plot width / frontage ({'ft' if fps_units else 'm'})",
        min_value=1.0,
        value=float(round(shown_w, 2)),
        step=1.0 if fps_units else 0.5,
        disabled=plot_marla is None,
        key=f"plot_width_{plot_marla}_{marla_sqft}_{unit_system}",
        help="Typical frontage for this plot size is pre-filled - change it to match your plot.",
    )
    entered_w_ft = entered_w if fps_units else entered_w / 0.3048
    plot_width_ft = None if abs(entered_w_ft - typical_w) < 0.01 else entered_w_ft
    if plot_marla:
        W, D = plot_templates.plot_dimensions_ft(plot_marla, marla_sqft, plot_width_ft)
        cw, cd = plot_templates.covered_footprint_ft(plot_marla, marla_sqft, plot_width_ft)
        L = lambda ft: units.length_text(ft * 0.3048, unit_system, f"{ft * 0.3048:.2f} m")  # noqa: E731
        Ar = lambda sq: units.area_text(sq * 0.09290304, unit_system, f"{sq * 0.09290304:,.1f} m²", 0)  # noqa: E731
        st.caption(
            f"Plot {L(W)} x {L(D)} = {Ar(W * D)}. Typical covered footprint {L(cw)} x {L(cd)} = {Ar(cw * cd)} per floor "
            "(front/rear open space as typical - edit in Step 3 if different)."
        )

    # No st.form() here on purpose: a form batches every widget inside it
    # and only reruns on submit, which is exactly what breaks per-file
    # drawing tags (see below) - and the user also wants the Continue
    # button to visually sit AFTER the upload section, so the whole step
    # is simplest as one flat sequence of plain widgets with one button
    # at the very end. Streamlit reruns the whole script on every widget
    # interaction regardless of whether a form is used, so nothing here
    # loses functionality by not being wrapped in a form.
    c1, c2 = st.columns(2)
    with c1:
        project_name = st.text_input("Project Name", pi.project_name)
        client_name = st.text_input("Client Name (optional)", pi.client_name)
    with c2:
        location = st.text_input("Location / City", pi.location)
        grade_unit_key = units.FPS if unit_system == units.FPS else units.SI
        wall_thickness_options = units.WALL_THICKNESS_OPTIONS_MM[grade_unit_key]
        # A value saved under the other unit system (e.g. 230 mm) maps to
        # the nearest option of this one (228.6 mm = 9").
        current_wall_mm = units.nearest_option(pi.wall_thickness_mm, wall_thickness_options)
        wall_thickness_mm = st.selectbox(
            "Main (external/load-bearing) wall thickness",
            wall_thickness_options,
            index=wall_thickness_options.index(current_wall_mm) if current_wall_mm in wall_thickness_options else 3,
            format_func=lambda mm: units.wall_thickness_label(mm, unit_system),
            help="Used only when the drawings are scanned images (CAD PDFs give measured wall thicknesses).",
        )

    # ---- Take-off specification (drives which materials are quantified) ----
    opts: Options = st.session_state["dmto_options"]
    kb = load_knowledge_base()
    st.markdown("##### Take-off scope & specification")
    st.caption(
        "These choices decide WHICH materials are quantified (e.g. traditional roof = bitumen + earth + brick tiles; "
        "insulated roof = EPS/XPS + membrane). Quantities always come from the drawings. Costs are not part of this version."
    )
    s1, s2, s3, s4 = st.columns(4)
    scope = s1.selectbox("Scope", kb.scope_names, index=kb.scope_names.index(opts.scope) if opts.scope in kb.scope_names else 0,
                         help="Complete Project, a single discipline, or the Pakistani 'Grey Structure' / 'Finishing' contract packages.")
    tier = s2.selectbox("Finish tier", TIERS, index=TIERS.index(opts.finish_tier) if opts.finish_tier in TIERS else 1,
                        help="Economy skips false ceilings/cornices and built-in kitchen appliances; Premium adds items such as "
                        "linear shower drains, recirculation pump and built-in oven.")
    mix_keys = list(RCC_MIXES)
    rcc_mix = s3.selectbox("Structural RCC mix", mix_keys, index=mix_keys.index(opts.rcc_mix) if opts.rcc_mix in mix_keys else 0,
                           format_func=lambda k: RCC_MIXES[k],
                           help="Applies to footings, columns, beams, slabs and stairs. Tanks, lintels and DPC keep their drawing mixes.")
    roof = s4.selectbox("Roof treatment", ["Traditional", "Insulated"], index=["Traditional", "Insulated"].index(opts.roof_system),
                        help="Traditional = 2 coats bitumen + polythene + earth + mud plaster + brick tiles. "
                        "Insulated = EPS/XPS board + membrane + screed.")
    s5, s6, s7, s8 = st.columns(4)
    masonry = s5.selectbox("Walling", ["Brick", "Block"], index=["Brick", "Block"].index(opts.masonry))
    gas = s6.selectbox("Gas supply", ["SNGPL", "LPG", "None"], index=["SNGPL", "LPG", "None"].index(opts.gas_source),
                       help="New SNGPL domestic connections are restricted - choose LPG if the house will use cylinders.")
    fc = s7.checkbox("False ceilings", value=opts.include_false_ceiling)
    rwh = s7.checkbox("Rainwater recharge well", value=opts.include_rwh, help="CDA requires rainwater harvesting/recharge (not shown in most drawing sets).")
    bands = s8.checkbox("Seismic lintel bands", value=opts.seismic_bands, help="Recommended for load-bearing masonry in Islamabad/Rawalpindi (BCP zone 2B).")
    include_opt = s8.checkbox("Quantify optional items too", value=opts.include_options,
                              help="Also include Optional/Alternative/Premium materials in the take-off.")

    uploaded_files_with_tags = list(st.session_state.get("uploaded_files") or [])
    if input_mode == "drawings":
        st.markdown("##### Drawing Upload")
        st.caption(
            f"Upload the whole drawing set - up to {config.MAX_DRAWING_FILES} files (floor plans, sections, elevations, "
            "foundation/structural sheets), as multi-page PDFs or images. CAD-exported PDFs are read directly, free and "
            "without AI: sheets are sorted automatically, and room sizes, levels, schedules and wall lengths are taken "
            f"from the drawing itself. Only the {config.MAX_TOTAL_IMAGES} most useful pages are ever sent to the AI, for "
            "whatever is still missing."
        )
        raw_uploads = st.file_uploader(
            "Upload drawing(s) (PDF, PNG, or JPG)",
            type=config.SUPPORTED_FILE_TYPES,
            accept_multiple_files=True,
            key="drawing_uploader",
        )

        uploaded_files_with_tags = []
        if raw_uploads:
            if len(raw_uploads) > config.MAX_DRAWING_FILES:
                st.warning(f"Only the first {config.MAX_DRAWING_FILES} files will be used - remove some to change which ones.")
            for i, f in enumerate(raw_uploads[: config.MAX_DRAWING_FILES]):
                fc1, fc2 = st.columns([3, 2])
                fc1.caption(f"\U0001f4c4 {f.name}")
                view_tag = fc2.selectbox(
                    "View type",
                    config.DRAWING_VIEW_TYPES,
                    index=min(i, len(config.DRAWING_VIEW_TYPES) - 1),
                    key=f"view_tag_{i}",
                    label_visibility="collapsed",
                )
                uploaded_files_with_tags.append({"name": f.name, "bytes": f.getvalue(), "view_tag": view_tag})
        elif st.session_state.get("uploaded_files"):
            # Streamlit empties a file_uploader when you navigate away from the
            # step that shows it, so coming back to Step 1 would otherwise
            # silently drop the drawings. Keep the previously attached files
            # (with editable view tags) unless new files are uploaded or the
            # user explicitly removes them.
            st.caption("Previously attached drawing(s) are kept. Upload new files above to replace them.")
            for i, f in enumerate(st.session_state["uploaded_files"]):
                fc1, fc2 = st.columns([3, 2])
                fc1.caption(f"\U0001f4ce {f['name']}")
                tag = f.get("view_tag", config.DRAWING_VIEW_TYPES[-1])
                view_tag = fc2.selectbox(
                    "View type",
                    config.DRAWING_VIEW_TYPES,
                    index=config.DRAWING_VIEW_TYPES.index(tag) if tag in config.DRAWING_VIEW_TYPES else len(config.DRAWING_VIEW_TYPES) - 1,
                    key=f"kept_view_tag_{i}",
                    label_visibility="collapsed",
                )
                uploaded_files_with_tags.append({"name": f["name"], "bytes": f["bytes"], "view_tag": view_tag})
            if st.button("Remove attached drawing(s)"):
                st.session_state["uploaded_files"] = []
                clear_drawing_derived_state()
                st.session_state["uploaded_signature"] = None
                st.rerun()

    submitted = st.button("Continue to Drawing Analysis →" if input_mode == "drawings" else "Continue to your requirements →",
                          width="stretch", type="primary")

    if submitted:
        st.session_state["project_inputs"] = pi.model_copy(update=dict(
            project_name=project_name or "Untitled Project",
            client_name=client_name,
            location=location,
            wall_thickness_mm=wall_thickness_mm,
            finish_level=TIER_TO_FINISH.get(tier, "Standard"),
            unit_system=unit_system,
            plot_marla=plot_marla,
            marla_sqft=marla_sqft,
            plot_width_ft=plot_width_ft,
            plot_storeys=plot_storeys,
        ))
        new_opts = Options(scope=scope, finish_tier=tier, roof_system=roof, masonry=masonry, gas_source=gas,
                           include_false_ceiling=fc, include_rwh=rwh, include_options=include_opt, seismic_bands=bands,
                           rcc_mix=rcc_mix)
        if new_opts != opts:
            st.session_state["dmto_options"] = new_opts
            st.session_state["dmto_result"] = None
        # If the drawings (or their view tags) changed since the last
        # analysis, drop every cached image/OCR/AI result derived from the
        # old ones - otherwise Step 2 would analyse the stale drawings.
        if unit_system != pi.unit_system:
            # Switch the Step 3 engineering-assumption defaults to the new
            # unit system's round values - unless the user edited them.
            if st.session_state["assumptions"] == EngineeringAssumptions.for_unit_system(pi.unit_system):
                st.session_state["assumptions"] = EngineeringAssumptions.for_unit_system(unit_system)
            st.session_state["dmto_result"] = None
        new_pi = st.session_state["project_inputs"]
        plot_key = lambda x: (x.plot_marla, x.marla_sqft, x.plot_width_ft, x.plot_storeys)  # noqa: E731
        if unit_system != pi.unit_system or plot_key(pi) != plot_key(new_pi):
            # Parameters still at the old starting point (plot template or
            # unit-system defaults) are dropped so the new one is used, e.g.
            # 1.2 m -> 4'-0" footings, or a 5 -> 10 marla template.
            if st.session_state.get("extracted_params") is not None and st.session_state["extracted_params"] == base_params(pi):
                st.session_state["extracted_params"] = None
            # The drawing analysis text is unit-formatted and uses the plot
            # width as a hint - redo it for the new settings.
            st.session_state["package_facts"] = None
            st.session_state["uploaded_images"] = []
            clear_dmto_review()
        if input_mode != st.session_state.get("input_mode", "drawings"):
            clear_drawing_derived_state()
            st.session_state["uploaded_signature"] = None
            st.session_state["brief"] = None
        st.session_state["input_mode"] = input_mode
        if input_mode == "sketch":
            st.session_state["uploaded_files"] = []
            go_to_step(2)
            st.rerun()
        signature = _uploads_signature(uploaded_files_with_tags)
        if signature != st.session_state.get("uploaded_signature"):
            clear_drawing_derived_state()
            st.session_state["uploaded_signature"] = signature
        st.session_state["uploaded_files"] = uploaded_files_with_tags
        go_to_step(2)
        st.rerun()
