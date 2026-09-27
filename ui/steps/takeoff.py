"""Step 4 - the material take-off (with the copilot and the take-off check)."""
from __future__ import annotations


import streamlit as st

from models.schemas import ProjectInputs
from ui import mto_views
from ui.state import go_to_step


# ---------------------------------------------------------------------------
# STEP 4 — Material take-off
# ---------------------------------------------------------------------------
def step_4():
    st.header("Step 4 \u00b7 Material Take-Off")
    pi: ProjectInputs = st.session_state["project_inputs"]
    if st.session_state.get("extracted_params") is None:
        st.info("Complete Steps 1-3 first.")
        if st.button("\u2190 Back to Step 1"):
            go_to_step(1)
            st.rerun()
        return
    res = mto_views.ensure_current_result(pi, st.session_state["extracted_params"])
    if res is None:
        if st.button("\u2190 Back to Step 3 to fix the inputs"):
            go_to_step(3)
            st.rerun()
        return
    mto_views.render_drawing_mode_banner(res.project.drawing_mode)
    st.caption(mto_views.options_summary(st.session_state["dmto_options"]) +
               " \u00b7 All quantities in Pakistani FPS units (cft, sft, rft, bags, kg, Nos). Costs are not included in this version.")
    mto_views.render_takeoff(res)

    c1, c2 = st.columns(2)
    with c1:
        if st.button("\u2190 Back to Step 3 (review data)"):
            go_to_step(3)
            st.rerun()
    with c2:
        if st.button("Continue to Export \u2192", type="primary", width="stretch"):
            go_to_step(5)
            st.rerun()
