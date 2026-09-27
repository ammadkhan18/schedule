"""Step 5 - export (Excel workbook, CSV, shopping list)."""
from __future__ import annotations


import streamlit as st

import config
from models.schemas import ProjectInputs
from ui import mto_views
from ui.state import go_to_step, reset_project


# ---------------------------------------------------------------------------
# STEP 5 — Export
# ---------------------------------------------------------------------------
def step_5():
    st.header("Step 5 \u00b7 Export")
    pi: ProjectInputs = st.session_state["project_inputs"]
    res = mto_views.ensure_current_result(pi, st.session_state.get("extracted_params"))
    if res is not None:
        mto_views.render_drawing_mode_banner(res.project.drawing_mode)
    if res is None:
        st.info("Calculate the material take-off first (Step 3).")
        if st.button("\u2190 Back to Step 3"):
            go_to_step(3)
            st.rerun()
        return
    counts = res.status_counts()
    st.markdown(
        f"**{pi.project_name}** \u2014 scope **{res.scope}**: {len(res.scoped_materials())} materials in scope, "
        f"{len(res.purchase_list())} to purchase, {counts.get('Needs input', 0)} need input. "
        "Materials outside the selected scope are not included in the files."
    )
    name = (pi.project_name or "Project").replace(" ", "_")
    from detailed_mto.shopping import shopping_pdf, shopping_text

    st.markdown("##### \U0001f4e6 Full take-off")
    e1, e2 = st.columns(2)
    with e1:
        st.download_button(
            "\U0001f4e6 Material Take-Off workbook (Excel)", data=mto_views.export_bytes(res),
            file_name=f"{name}_Material_TakeOff.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", width="stretch", type="primary",
        )
    with e2:
        st.download_button("\U0001f4c4 Material schedule (CSV)", data=mto_views.schedule_csv(res),
                           file_name=f"{name}_Material_Schedule.csv", mime="text/csv", width="stretch")
    st.caption(
        "The workbook opens on a **Summary** for non-technical readers (main materials + a shopping list of every material, "
        "grouped by trade, in purchase units, with when it is needed and how reliable it is). Detail sheets: Material_Schedule "
        "(editable wastage), Procurement_by_Stage, BOQ_Work_Items, Material_Breakdown, Project_Inputs, Rooms, Openings, "
        "Assumptions_Gaps and Benchmarks - all with links and filter buttons."
    )

    st.markdown("##### \U0001f4f2 Share the shopping list")
    trades = list(dict.fromkeys(m.material.category for m in res.purchase_list()))
    pick = st.multiselect("Trades to include (e.g. only what one supplier sells)", trades, default=[],
                          placeholder="All trades", key="share_trades")
    text = shopping_text(res, pick or None)
    s1, s2, s3 = st.columns(3)
    with s1:
        st.download_button("\U0001f4dd Text for WhatsApp / SMS", data=text.encode("utf-8"),
                           file_name=f"{name}_shopping_list.txt", mime="text/plain", width="stretch")
    with s2:
        st.download_button("\U0001f5a8\ufe0f Shopping list (PDF)", data=lambda: shopping_pdf(res), file_name=f"{name}_shopping_list.pdf",
                           mime="application/pdf", width="stretch")
    with s3:
        from urllib.parse import quote
        short = text if len(text) < 1800 else text[:1750] + "\n... (full list in the attached file)"
        st.link_button("\U0001f4ac Open in WhatsApp", f"https://wa.me/?text={quote(short)}", width="stretch",
                       help="Opens WhatsApp with the list ready to send (long lists are shortened - send the text/PDF file instead).")
    with st.expander("Preview / copy the text"):
        st.code(text, language=None)

    st.caption("Costs are intentionally excluded - pricing will be added as a separate step. "
               f"\u26a0\ufe0f {config.DISCLAIMER_TEXT_SHORT}")
    c1, c2 = st.columns(2)
    with c1:
        if st.button("\u2190 Back to Step 4 (Material Take-Off)"):
            go_to_step(4)
            st.rerun()
    with c2:
        if st.button("\U0001f504 Start a new project", width="stretch"):
            reset_project()
            st.rerun()
