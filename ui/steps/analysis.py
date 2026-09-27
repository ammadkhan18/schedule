"""Step 2 (drawing route) - CAD text/vector reading, label scan, optional AI."""
from __future__ import annotations

import hashlib

from io import BytesIO

import pandas as pd
import streamlit as st
from PIL import Image

import config
from ai.llm import keys_from_mapping
from ai.extraction import default_building_params, extract_building_params
from detailed_mto import scan_pdf_bytes
from drawing_processing.image_processor import clean_drawing_image, estimate_is_drawing_like
from drawing_processing.ocr_engine import build_ocr_hint_text, ocr_available
from drawing_processing.package_analyzer import analyze_package, apply_package_facts, pages_for_ai, render_pages
from drawing_processing.pdf_processor import process_pdf
from engineering import plot_templates
from models.schemas import ProjectInputs
from ui import mto_views
from ui.state import clear_dmto_review, go_to_step


# ---------------------------------------------------------------------------
# STEP 2 — AI analysis
# ---------------------------------------------------------------------------
def _load_images_from_upload(facts=None) -> tuple[list[Image.Image], list[str], str]:
    """Images to show/send to the AI, their labels, and a text hint.

    With a drawing package (PDFs), every page has already been sorted by
    drawing_processing.package_analyzer, so only the most useful pages
    (ground plan, section, other plans, elevation, then unrecognised/scanned
    pages) are rendered - at most config.MAX_TOTAL_IMAGES, the AI's
    per-request image limit. Plain image uploads fill any remaining slots.
    """
    uploaded_files = st.session_state.get("uploaded_files") or []
    images: list[Image.Image] = []
    labels: list[str] = []
    pdf_text_parts: list[str] = []
    selected = pages_for_ai(facts, config.MAX_TOTAL_IMAGES) if facts is not None else []

    for fi, f in enumerate(uploaded_files):
        name, file_bytes, view_tag = f["name"], f["bytes"], f["view_tag"]
        if name.lower().endswith(".pdf"):
            wanted = [pg for (i, pg) in selected if i == fi]
            if facts is None:
                wanted = list(range(config.MAX_PAGES_PER_FILE))
            if not wanted:
                continue
            for pg, img in zip(wanted, render_pages(file_bytes, wanted[: config.MAX_TOTAL_IMAGES])):
                if len(images) >= config.MAX_TOTAL_IMAGES:
                    break
                kind = (facts.page_kinds.get((fi, pg), "") if facts is not None else "").split(":")
                label = f"{kind[1].title() + ' floor ' if len(kind) > 1 and kind[1] else ''}{kind[0] if kind[0] and kind[0] != 'unknown' else view_tag} ({name} p{pg + 1})"
                images.append(clean_drawing_image(img))
                labels.append(label)
            result = process_pdf(file_bytes, dpi=72, max_pages=1)
            text = " ".join(result.combined_text.split())
            if text:
                pdf_text_parts.append(f"[{name}] {text}")
        elif len(images) < config.MAX_TOTAL_IMAGES:
            images.append(clean_drawing_image(Image.open(BytesIO(file_bytes))))
            labels.append(f"{view_tag} ({name})")

    hint_parts = []
    if facts is not None:
        from drawing_processing.package_analyzer import ai_hint_from_facts

        hint_parts.append(ai_hint_from_facts(facts))
    if pdf_text_parts:
        joined = " | ".join(pdf_text_parts)[: config.MAX_PDF_TEXT_HINT_CHARS]
        hint_parts.append("PDF text-layer content (exact text embedded in the drawing PDF): " + joined)
    return images, labels, "\n".join(h for h in hint_parts if h)


@st.cache_resource(show_spinner=False)
def _analysis_cache() -> dict:
    """Process-wide cache of drawing analyses keyed by file content (re-uploading the same set is instant)."""
    return {}


def _analyze_cached(files: list, plot_width_ft, unit_system: str):
    key = (tuple((f["name"], hashlib.sha256(f["bytes"]).hexdigest(), f.get("view_tag", "")) for f in files),
           plot_width_ft, unit_system)
    cache = _analysis_cache()
    if key in cache:
        return cache[key]
    bar = st.progress(0.0, text="Reading the drawing set...")
    try:
        facts = analyze_package(files, plot_width_ft, unit_system,
                                progress=lambda frac, msg: bar.progress(frac * 0.9, text=msg))
        bar.progress(0.95, text="Reading plumbing labels, door schedule and tank details...")
        try:
            scan = scan_pdf_bytes(files)
        except Exception:  # noqa: BLE001 - the label scan is optional
            scan = None
    finally:
        bar.empty()
    if len(cache) >= 8:  # keep memory bounded on small hosts
        cache.pop(next(iter(cache)))
    cache[key] = (facts, scan)
    return facts, scan


def base_params(pi: ProjectInputs):
    """Starting parameters: the 5-10 marla plot template when a plot size is
    chosen, else the generic defaults of the unit system."""
    return plot_templates.build_template_params(pi) or default_building_params(
        wall_thickness_mm=pi.wall_thickness_mm, wall_material=pi.wall_material, unit_system=pi.unit_system
    )


def _finish_step_2(params, used_ai: bool) -> None:
    """Values read from the drawings override AI/template/default values;
    section levels also update the plinth/parapet assumptions."""
    pi = st.session_state["project_inputs"]
    facts = st.session_state.get("package_facts")
    filled: list[str] = []
    if facts is not None and facts.has_facts():
        params, asm_updates, filled = apply_package_facts(params, facts, pi)
        if asm_updates:
            st.session_state["assumptions"] = st.session_state["assumptions"].model_copy(update=asm_updates)
    st.session_state["drawing_filled"] = filled
    st.session_state["extracted_params"] = params
    st.session_state["used_ai"] = used_ai
    clear_dmto_review()  # rooms/openings/counts are re-seeded from this analysis in Step 3
    go_to_step(3)


def step_2():
    st.header("Step 2 \u00b7 Drawing Analysis")
    pi: ProjectInputs = st.session_state["project_inputs"]

    uploaded_files = st.session_state.get("uploaded_files") or []
    if not uploaded_files:
        if pi.plot_marla:
            st.info(
                f"No drawing uploaded - a typical {pi.plot_marla:g} marla house will be used as the starting point. "
                "You can review and edit every value in Step 3, or go back and upload the drawings."
            )
        else:
            st.info("No drawing was uploaded. You can go back to upload one, or skip straight to manual entry with standard defaults.")
        if st.button("\u2190 Back to Step 1"):
            go_to_step(1)
            st.rerun()
        if st.button("Skip AI \u2014 enter parameters manually", type="primary"):
            _finish_step_2(base_params(pi), used_ai=False)
            st.rerun()
        return

    # ---- 1. Free analysis of the whole package (text + vectors, no AI) ----
    if st.session_state.get("package_facts") is None or st.session_state.get("dmto_scan") is None:
        facts, scan = _analyze_cached(uploaded_files, pi.plot_width_ft, pi.unit_system)
        st.session_state["package_facts"] = facts
        st.session_state["dmto_scan"] = scan
    facts = st.session_state["package_facts"]

    if "uploaded_images" not in st.session_state or not st.session_state["uploaded_images"]:
        with st.spinner("Preparing the most useful pages for the AI..."):
            images, labels, pdf_text_hint = _load_images_from_upload(facts)
            st.session_state["uploaded_images"] = images
            st.session_state["uploaded_image_labels"] = labels
            st.session_state["pdf_text_hint"] = pdf_text_hint

    images = st.session_state["uploaded_images"]
    labels = st.session_state.get("uploaded_image_labels") or []

    st.markdown("##### \U0001f4d0 Drawing set analysis (free \u2014 no AI)")
    st.caption("CAD-exported PDFs are read directly: sheet types, room names and sizes, wall lengths by thickness, levels, "
               "foundation sections, schedules and service labels. The AI is optional.")
    if facts.has_facts():
        _, _, preview = apply_package_facts(base_params(pi), facts, pi)
        st.success(
            f"Read {len(preview)} value(s) directly from the drawings: {', '.join(preview)}. "
            "These override AI and template values and are marked 'From drawings' in Step 3."
        )
    else:
        mto_views.render_drawing_mode_banner("scanned")
    sheet_rows = [
        {"File": sh.file_name, "Page": sh.page_no, "Detected views": ", ".join(sh.views),
         "Read from drawing": " | ".join(sh.findings) if sh.findings else "-"}
        for sh in facts.sheets
    ]
    if sheet_rows:
        st.dataframe(pd.DataFrame(sheet_rows), width="stretch", hide_index=True)
    for c in facts.conflicts:
        st.warning(c)
    mto_views.render_scan_summary(st.session_state.get("dmto_scan"))

    if images:
        st.markdown(f"##### Pages the AI will read ({len(images)} of max {config.MAX_TOTAL_IMAGES})")
        cols = st.columns(min(len(images), 4) or 1)
        for i, img in enumerate(images):
            with cols[i % len(cols)]:
                st.image(img, caption=labels[i] if i < len(labels) else f"Page {i+1}", width="stretch")
                if not estimate_is_drawing_like(img):
                    st.caption("\u26a0\ufe0f This doesn't look like a typical line drawing \u2014 results may be unreliable.")

    if ocr_available():
        with st.expander("OCR text hints (used to help the AI, not for calculations)"):
            if not st.session_state.get("ocr_hint_text"):
                with st.spinner("Running OCR..."):
                    hints = [build_ocr_hint_text(img) for img in images]
                    st.session_state["ocr_hint_text"] = "\n".join(h for h in hints if h)
            st.text(st.session_state["ocr_hint_text"] or "No text detected.")
    else:
        st.caption(
            "Optional OCR (EasyOCR) is not installed in this deployment \u2014 it is heavy for free hosting. "
            "Install it with `pip install -r requirements-ocr.txt` to enable. Vector PDFs are read directly (above)."
        )

    if st.session_state.get("pdf_text_hint"):
        with st.expander("Hints passed to the AI (known values + PDF text)"):
            st.text(st.session_state["pdf_text_hint"])

    project_context = st.text_area(
        "Additional context for the AI (optional)",
        placeholder="e.g. 'This is a G+1 house, footings are isolated RCC pad footings, drawing is not to exact scale...'",
    )
    # Wall material/thickness are rarely labelled on a simple line drawing;
    # pass the Step 1 choice as a prior the AI uses unless the drawing
    # clearly shows otherwise (see ai/prompts.py rules 1-3).
    _wall_hint = (
        f"Unless the drawing clearly shows otherwise, assume wall material "
        f"'{pi.wall_material}' and wall thickness {pi.wall_thickness_mm}mm "
        "(both chosen in Step 1)."
    )
    plot_hint = (
        f"The house is on a {pi.plot_marla:g} marla plot ({pi.plot_marla * pi.marla_sqft:,.0f} sqft), {pi.plot_storeys} storey(s)."
        if pi.plot_marla else ""
    )
    project_context_for_ai = "\n".join(p for p in [project_context, plot_hint, _wall_hint] if p)

    c1, c2 = st.columns(2)
    with c1:
        _readable = facts.has_facts()
        skip_clicked = st.button(
            "Continue with drawing data \u2192" if _readable else "Continue with typical-house values \u2192",
            type="primary" if _readable else "secondary", width="stretch",
            help="Recommended for CAD PDFs: everything read from the drawings is used; defaults fill the rest." if _readable
            else "Nothing could be read from these drawings - Step 3 will start from a typical house that you must correct.")
    with c2:
        analyze_clicked = st.button("\U0001f9e0 Also ask the AI for missing values", width="stretch",
                                    help="Optional. Sends the most useful pages (automatically resized to fit the free AI limits) to the vision model "
                                         "for values not read from the drawings.")

    if skip_clicked:
        _finish_step_2(base_params(pi), used_ai=False)
        st.rerun()

    if analyze_clicked:
        if not keys_from_mapping(st.session_state).any():
            st.error(
                "No AI key is configured. Paste a free Groq key (console.groq.com/keys) or a Gemini key in the sidebar, "
                "or continue with the drawing data - values read from the drawings are still used."
            )
        elif not images:
            st.error("No pages to send to the AI - use 'Continue without AI'.")
        else:
            with st.spinner("Calling the vision model for the values not already read from the drawings... up to ~30s"):
                params, raw_text, errors = extract_building_params(
                    api_key=st.session_state["groq_api_key"],
                    images=images,
                    project_context=project_context_for_ai,
                    ocr_hint="\n".join(
                        h for h in [st.session_state.get("ocr_hint_text", ""), st.session_state.get("pdf_text_hint", "")] if h
                    ),
                    image_labels=labels,
                    wall_thickness_mm=pi.wall_thickness_mm,
                    wall_material=pi.wall_material,
                    unit_system=pi.unit_system,
                    fallback_params=base_params(pi),
                    keys=keys_from_mapping(st.session_state),
                )
            st.session_state["raw_ai_response"] = raw_text
            st.session_state["ai_errors"] = errors
            _finish_step_2(params, used_ai=True)
            st.rerun()

    if st.button("\u2190 Back to Step 1"):
        go_to_step(1)
        st.rerun()
