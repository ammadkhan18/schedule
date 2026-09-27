"""
CostLens - drawing-based Material Take-Off for 5-10 marla houses (Pakistan).
Streamlit entrypoint: page setup, sidebar and the step router only.

The wizard:
  1. Project setup - what the user has (CAD drawings, or a sketch / idea), take-off specification
  2. Drawing analysis (CAD text/vector reading, label scan, optional AI)  or
     Your requirements (sketch / description + AI follow-up + guided questions)
  3. Review data - most important questions, rooms, doors & windows, counts, structure
  4. Material take-off - take-off check, copilot, shopping list, schedule, traceability
  5. Export - Excel workbook, CSV, WhatsApp text and PDF shopping list

Code map:
  ui/steps/*          one module per step          ui/sidebar.py      navigation + project save/open
  ui/mto_views.py     review tables & take-off UI  ui/brief_views.py  requirements (sketch) route
  ui/copilot_views.py copilot, checker, questions  persistence/       project files
  detailed_mto/       take-off engine & exports    copilot/           agent tools & analysis
  knowledge/          Master Material Database     drawing_processing/ PDF reading

Costs are intentionally excluded in this version (quantities only); the legacy
cost modules (mto_boq/, export/) are kept for the future pricing step.
"""
from __future__ import annotations

import streamlit as st

import config
from ui import theme
from ui.sidebar import render_sidebar
from ui.state import go_to_step, init_session_state

st.set_page_config(page_title=f"{config.APP_NAME} \u00b7 {config.APP_TAGLINE}",
                   page_icon=str(config.LOGO_ICON_PATH) if config.LOGO_ICON_PATH.exists() else "\U0001f3d7\ufe0f",
                   layout="wide")
init_session_state()
theme.inject_theme()

if hasattr(st, "logo"):
    # The sidebar header is dark navy, so it needs the white-text lockup. `size` needs Streamlit >= 1.41.
    try:
        st.logo(str(config.LOGO_HORIZONTAL_ON_DARK_PATH), icon_image=str(config.LOGO_ICON_PATH), size="large")
    except TypeError:
        try:
            st.logo(str(config.LOGO_HORIZONTAL_ON_DARK_PATH), icon_image=str(config.LOGO_ICON_PATH))
        except Exception:  # noqa: BLE001
            pass
    except Exception:  # noqa: BLE001
        pass

render_sidebar()
theme.render_hero()
flash = st.session_state.pop("project_loaded_flash", None)
if flash:
    st.success(flash)

step = st.session_state["step"]
if step == 1:
    from ui.steps.setup import step_1
    step_1()
elif step == 2:
    if st.session_state.get("input_mode") == "sketch":
        from ui.brief_views import render_brief_step
        render_brief_step(st.session_state["project_inputs"], go_to_step, st.session_state.get("groq_api_key", ""))
    else:
        from ui.steps.analysis import step_2
        step_2()
elif step == 3:
    from ui.steps.review import step_3
    step_3()
elif step == 4:
    from ui.steps.takeoff import step_4
    step_4()
elif step == 5:
    from ui.steps.export import step_5
    step_5()
else:
    st.session_state["step"] = 1
    st.rerun()

# A new step always opens at the top of the page (Streamlit keeps the scroll position between reruns).
if st.session_state.get("_scrolled_step") != step:
    st.session_state["_scrolled_step"] = step
    theme.scroll_to_top()
