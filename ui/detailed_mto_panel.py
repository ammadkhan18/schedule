"""
Streamlit panel: "Detailed Material Schedule (all materials)".

Self-contained and additive - it only READS the existing session state
(project_inputs, extracted_params, package_facts, uploaded_files) and keeps
its own state under keys prefixed with ``dmto_``. Any error is caught and
shown inside the panel so the rest of Step 5 keeps working.
"""
from __future__ import annotations

import hashlib

import pandas as pd
import streamlit as st

_TIER_FROM_FINISH = {"Basic": "Economy", "Standard": "Standard", "Premium": "Premium"}



def _signature() -> str:
    h = hashlib.sha256()
    h.update(repr(st.session_state.get("uploaded_signature")).encode())
    params = st.session_state.get("extracted_params")
    if params is not None:
        h.update(params.model_dump_json().encode())
    pi = st.session_state.get("project_inputs")
    if pi is not None:
        h.update(pi.model_dump_json().encode())
    return h.hexdigest()


def _reset_if_stale(sig: str) -> None:
    if st.session_state.get("dmto_sig") != sig:
        for k in [k for k in list(st.session_state.keys()) if str(k).startswith("dmto_")]:
            del st.session_state[k]
        st.session_state["dmto_sig"] = sig


def render_detailed_mto_panel() -> None:
    st.markdown("##### Detailed Material Schedule (every material)")
    st.caption(
        "Quantifies every material in the Master Material Database (cement, steel by diameter, bricks, sand, crush, "
        "waterproofing, tiles, doors & windows, paints, plumbing, sanitary, gas, electrical, HVAC, kitchen, external works, "
        "rainwater harvesting ...) from the drawings and the parameters verified in Step 3. Each line shows its status, "
        "confidence and calculation. Rates are left blank for you to fill in."
    )
    with st.expander("Open detailed material schedule", expanded=False):
        try:
            _render_body()
        except Exception as exc:  # never break Step 5
            st.error(f"The detailed schedule could not be generated: {exc}")


def _render_body() -> None:
    from detailed_mto import Options, build_detailed_mto_workbook, build_project, compute, scan_pdf_bytes
    from detailed_mto.edits import apply_openings, apply_overrides, apply_rooms, openings_to_rows, rooms_to_rows
    from detailed_mto.engine import INCLUDED_STATUSES
    from knowledge import load_knowledge_base

    pi = st.session_state.get("project_inputs")
    params = st.session_state.get("extracted_params")
    if pi is None or params is None:
        st.info("Complete Steps 1-3 first.")
        return
    _reset_if_stale(_signature())
    kb = load_knowledge_base()

    # ---------------- options
    c1, c2, c3 = st.columns(3)
    with c1:
        scope = st.selectbox("Scope", kb.scope_names, index=kb.scope_names.index("Complete Project")
                             if "Complete Project" in kb.scope_names else 0, key="dmto_scope")
        tier_default = _TIER_FROM_FINISH.get(getattr(pi, "finish_level", "Standard"), "Standard")
        tier = st.selectbox("Finish tier", ["Economy", "Standard", "Premium"],
                            index=["Economy", "Standard", "Premium"].index(tier_default), key="dmto_tier")
    with c2:
        roof = st.selectbox("Roof treatment", ["Traditional", "Insulated"], key="dmto_roof",
                            help="Traditional = bitumen + polythene + earth + mud + brick tiles; Insulated = EPS/XPS + membrane + screed")
        gas = st.selectbox("Gas source", ["SNGPL", "LPG", "None"], key="dmto_gas")
    with c3:
        masonry = st.selectbox("Walling", ["Brick", "Block"], key="dmto_masonry")
        fc = st.checkbox("False ceilings", value=tier != "Economy", key="dmto_fc")
        rwh = st.checkbox("Rainwater recharge well (CDA)", value=True, key="dmto_rwh")
        opt = st.checkbox("Also quantify optional / premium items", value=False, key="dmto_opt")
    options = Options(scope=scope, finish_tier=tier, roof_system=roof, masonry=masonry, gas_source=gas,
                      include_false_ceiling=fc, include_rwh=rwh, include_options=opt)

    # ---------------- label scan (cached per upload)
    if "dmto_scan" not in st.session_state:
        with st.spinner("Reading plumbing labels, door schedule and tank details from the drawings..."):
            try:
                st.session_state["dmto_scan"] = scan_pdf_bytes(st.session_state.get("uploaded_files") or [])
            except Exception:
                st.session_state["dmto_scan"] = None
    scan = st.session_state.get("dmto_scan")
    facts = st.session_state.get("package_facts")

    project = build_project(pi, params, kb, facts=facts, files=None, scan=scan, options=options)
    base_rooms, base_open = rooms_to_rows(project), openings_to_rows(project)

    # ---------------- review tables
    st.markdown("**1. Check the inputs** - values marked *Assumed* are defaults; edit anything you know better.")
    tab_p, tab_r, tab_o = st.tabs(["Key inputs", "Rooms", "Doors & windows"])
    with tab_p:
        rows = [{"Key": pr.key, "Group": pr.group, "Parameter": pr.label, "Value": round(pr.value, 2), "Unit": pr.unit,
                 "Source": pr.source, "Confidence": pr.confidence}
                for pr in sorted(project.params.values(), key=lambda x: (x.group, x.key))]
        df = pd.DataFrame(rows)
        edited = st.data_editor(df, key="dmto_params_editor", hide_index=True, width="stretch",
                                disabled=["Key", "Group", "Parameter", "Unit", "Source", "Confidence"])
        overrides = {}
        for (_, a), (_, b) in zip(df.iterrows(), edited.iterrows()):
            try:
                if abs(float(b["Value"]) - float(a["Value"])) > 1e-9:
                    overrides[a["Key"]] = float(b["Value"])
            except (TypeError, ValueError):
                pass
    with tab_r:
        room_types = [rd.room_type for rd in kb.room_defaults]
        rdf = st.data_editor(
            pd.DataFrame(base_rooms), key="dmto_rooms_editor", hide_index=True, width="stretch", num_rows="dynamic",
            column_config={"Room type": st.column_config.SelectboxColumn(options=room_types)},
            disabled=["Source", "Confidence"])
    with tab_o:
        st.caption("Window widths are rarely given on Pakistani D&W sheets - correct the assumed sizes here.")
        odf = st.data_editor(
            pd.DataFrame(base_open), key="dmto_open_editor", hide_index=True, width="stretch", num_rows="dynamic",
            column_config={"Kind": st.column_config.SelectboxColumn(options=["door", "window", "ventilator"])},
            disabled=["Source", "Confidence"])

    apply_overrides(project, overrides)
    apply_rooms(project, rdf.to_dict("records"), base_rooms)
    apply_openings(project, odf.to_dict("records"), base_open)
    result = compute(project, kb, scope=scope)

    # ---------------- results
    st.markdown("**2. Result**")
    counts = result.status_counts()
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Materials in database", len(result.materials))
    m2.metric("Quantified", sum(v for k, v in counts.items() if k in INCLUDED_STATUSES))
    m3.metric("Assumed inputs", counts.get("Calculated (assumed inputs)", 0))
    m4.metric("Needs input", counts.get("Needs input", 0))

    def tot(ids):
        return sum(m.gross_qty for m in result.materials if m.material.mat_id in ids)

    k1, k2, k3, k4 = st.columns(4)
    k1.metric("Cement", f"{tot({'CON-001'}):,.0f} bags")
    k2.metric("Steel", f"{tot({'RBR-001', 'RBR-002', 'RBR-003', 'RBR-004'}) / 1000:,.2f} ton")
    k3.metric("Bricks", f"{tot({'MAS-001', 'MAS-002'}):,.0f}")
    k4.metric("Sand / Crush", f"{tot({'CON-004', 'CON-005'}):,.0f} / {tot({'CON-006', 'CON-007', 'CON-008'}):,.0f} cft")

    show_all = st.checkbox("Show all database lines (incl. options, references, not in scope)", value=False, key="dmto_showall")
    view = [m for m in result.materials if show_all or m.included]
    st.dataframe(pd.DataFrame([{
        "Mat_ID": m.material.mat_id, "Category": m.material.category, "Material": m.material.description,
        "Unit": m.material.unit, "Qty incl. wastage": round(m.gross_qty, 2), "Purchase": m.purchase,
        "Status": m.status, "Confidence": m.confidence, "Stage": m.material.stage,
    } for m in view]), hide_index=True, width="stretch", height=420)

    if project.conflicts:
        with st.expander(f"Drawing conflicts found ({len(project.conflicts)})"):
            for c in project.conflicts:
                st.warning(c)

    xlsx = build_detailed_mto_workbook(result)
    name = (getattr(pi, "project_name", "") or "Project").replace(" ", "_")
    st.download_button("\U0001f4e6 Download Detailed Material Schedule (Excel)", data=xlsx,
                       file_name=f"{name}_Detailed_MTO.xlsx",
                       mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                       width="stretch", key="dmto_download")
    st.caption("Separate from the MTO + BOQ export above (which is unchanged). Sheets: Summary, Material_Schedule, "
               "Procurement_by_Stage, BOQ_Work_Items, Material_Breakdown, Project_Inputs, Rooms, Openings, "
               "Assumptions_Gaps, Benchmarks.")
