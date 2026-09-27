"""
Centralizes Streamlit `st.session_state` initialization so app.py and the
UI components don't scatter `if "x" not in st.session_state` checks
everywhere.
"""
from __future__ import annotations

import streamlit as st

from ai.extraction import default_building_params  # noqa: F401  (kept for backwards compatibility)
from detailed_mto.model import Options
from models.schemas import EngineeringAssumptions, ProjectInputs, WastageFactors
from mto_boq.boq_generator import PRELIMINARIES_PCT_OF_CIVIL_SUBTOTAL
from mto_boq.rates import load_default_rates


def init_session_state():
    defaults = {
        "step": 1,
        "max_step": 1,  # furthest step reached - sidebar navigation allows jumping to any step up to this
        "_scrolled_step": None,  # last step the page was scrolled to the top for
        "groq_api_key": "",
        "uploaded_files": [],  # list of {"name": str, "bytes": bytes, "view_tag": str}
        "uploaded_images": [],
        "uploaded_image_labels": [],
        "ocr_hint_text": "",
        "pdf_text_hint": "",
        "package_facts": None,  # drawing_processing.package_analyzer.PackageFacts for the uploaded set
        "drawing_filled": [],  # parameter names filled from the drawings (no AI)
        "uploaded_signature": None,  # fingerprint of the uploaded files+tags the cached images belong to
        "user_groq_api_key": "",  # optional key typed into the sidebar (session memory only)
        "assumptions": EngineeringAssumptions.for_unit_system(ProjectInputs().unit_system),
        "preliminaries_pct": PRELIMINARIES_PCT_OF_CIVIL_SUBTOTAL,
        "export_cache": {},  # signature -> (excel_bytes, pdf_bytes)
        "project_inputs": ProjectInputs(),
        "extracted_params": None,
        "raw_ai_response": "",
        "ai_errors": [],
        "wastage_factors": WastageFactors(),
        "rate_book": {r.item_code: r for r in load_default_rates()},
        "mto_items": None,
        "boq_items": None,
        "cost_summary": None,
        "used_ai": False,
        # --- Detailed material take-off (Master Material Database) ---
        "dmto_options": Options(),
        "dmto_scan": None,  # detailed_mto.text_scanner.ScanResult for the uploaded set
        "dmto_rooms": None,  # reviewed room rows (list of dicts) - seeded from the drawings in Step 3
        "dmto_openings": None,  # reviewed door/window rows
        "dmto_overrides": {},  # user-edited key counts/dimensions {param_key: value}
        "dmto_ver": 0,  # bumps the review-table widget keys after edits are saved
        "dmto_result": None,  # detailed_mto.engine.DetailedResult
        "dmto_xlsx": None,  # (result id, bytes) cache of the export
        "dmto_floors": None,  # concept floors (list of Floor) from the guided brief route
        # --- guided route for users without drawings ---
        "input_mode": "drawings",  # "drawings" (architect's CAD/scans) | "sketch" (sketch / description + questions)
        "brief": None,  # detailed_mto.brief.ProjectBrief
        "sketch_files": [],
    }
    for key, value in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = value


def reset_project():
    """Wipes everything tied to the CURRENT project - drawing/AI results
    AND the project inputs/rate-book/wastage edits a user made in Steps
    1/5 - so "Start New Project" actually starts from a clean slate rather
    than silently carrying the previous project's name/client/grades/
    custom rates into the next one."""
    keys_to_clear = [
        "uploaded_files",
        "uploaded_images",
        "uploaded_image_labels",
        "ocr_hint_text",
        "pdf_text_hint",
        "package_facts",
        "drawing_filled",
        "uploaded_signature",
        "export_cache",
        "extracted_params",
        "raw_ai_response",
        "ai_errors",
        "mto_items",
        "boq_items",
        "cost_summary",
        "used_ai",
        "drawing_uploader",  # clears the file_uploader widget itself
        "max_step",
        "dmto_options",
        *DMTO_DERIVED_KEYS,
        *BRIEF_KEYS,
        *COPILOT_KEYS,
    ]
    for k in keys_to_clear:
        if k in st.session_state:
            del st.session_state[k]
    st.session_state["project_inputs"] = ProjectInputs()
    st.session_state["wastage_factors"] = WastageFactors()
    st.session_state["rate_book"] = {r.item_code: r for r in load_default_rates()}
    st.session_state["assumptions"] = EngineeringAssumptions.for_unit_system(ProjectInputs().unit_system)
    st.session_state["preliminaries_pct"] = PRELIMINARIES_PCT_OF_CIVIL_SUBTOTAL
    st.session_state["step"] = 1


DMTO_DERIVED_KEYS = ["dmto_scan", "dmto_rooms", "dmto_openings", "dmto_overrides", "dmto_result", "dmto_xlsx", "dmto_floors"]
COPILOT_KEYS = ["copilot_msgs", "copilot_props", "copilot_undo", "copilot_scenarios"]
BRIEF_KEYS = ["input_mode", "brief", "brief_ver", "sketch_files", "brief_ai_json", "brief_ai_got", "brief_used_ai",
              "brief_rooms_live", "sketch_uploader", "brief_added"]


def clear_dmto_review() -> None:
    """Forget reviewed rooms/openings/counts so they are re-seeded from the (new) drawing analysis."""
    for k in ["dmto_rooms", "dmto_openings", "dmto_overrides", "dmto_result", "dmto_xlsx", "dmto_floors"]:
        if k in st.session_state:
            del st.session_state[k]
    st.session_state["dmto_ver"] = st.session_state.get("dmto_ver", 0) + 1
    init_session_state()


def clear_drawing_derived_state():
    """Drops everything derived from the previously uploaded drawings
    (rendered images, OCR/PDF text hints, AI results and anything
    calculated from them). Called when the uploaded files or their view
    tags change, so Step 2 can never analyse a stale drawing."""
    for k in [
        "uploaded_images",
        "uploaded_image_labels",
        "ocr_hint_text",
        "pdf_text_hint",
        "package_facts",
        "drawing_filled",
        "extracted_params",
        "raw_ai_response",
        "ai_errors",
        "mto_items",
        "boq_items",
        "cost_summary",
        "used_ai",
        *DMTO_DERIVED_KEYS,
        *COPILOT_KEYS,
    ]:
        if k in st.session_state:
            del st.session_state[k]
    st.session_state["dmto_ver"] = st.session_state.get("dmto_ver", 0) + 1
    init_session_state()


def go_to_step(n: int):
    st.session_state["step"] = n
    st.session_state["max_step"] = max(int(st.session_state.get("max_step", 1) or 1), n)
