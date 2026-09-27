"""Project files: round trip, safety, and the shareable shopping list / concept plan."""
from __future__ import annotations

import json
import os

import pytest

from ai.extraction import default_building_params
from detailed_mto import Options, build_project, compute
from detailed_mto.edits import openings_to_rows, rooms_to_rows, rows_to_openings, rows_to_rooms
from drawing_processing.package_analyzer import analyze_package, apply_package_facts
from engineering import plot_templates
from knowledge import load_knowledge_base
from models.schemas import ProjectInputs
from persistence.project_file import (library_delete, library_list, library_save, project_from_json, project_to_json)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SAMPLE = os.path.join(ROOT, "sample_data", "sample_5_marla_package.pdf")


@pytest.fixture(scope="module")
def session():
    kb = load_knowledge_base()
    pi = ProjectInputs(project_name="Persist Test", plot_marla=5)
    params = plot_templates.build_template_params(pi) or default_building_params()
    files = [{"name": "s.pdf", "bytes": open(SAMPLE, "rb").read(), "view_tag": "Plan"}]
    facts = analyze_package(files, None, "FPS")
    params, _, _ = apply_package_facts(params, facts, pi)
    p = build_project(pi, params, kb, facts=facts)
    return {"project_inputs": pi, "extracted_params": params, "package_facts": facts, "dmto_scan": None,
            "dmto_rooms": rooms_to_rows(p), "dmto_openings": openings_to_rows(p), "dmto_overrides": {"N_AC": 3},
            "dmto_options": Options(masonry="Block", scope="Complete Project"), "step": 4, "max_step": 5,
            "uploaded_files": files, "input_mode": "drawings",
            "copilot_scenarios": {"A": {"rooms": rooms_to_rows(p), "openings": openings_to_rows(p), "overrides": {},
                                        "options": Options()}},
            "copilot_msgs": [{"role": "user", "content": "hello", "proposals": ["x"]}]}


def _takeoff(s):
    kb = load_knowledge_base()
    p = build_project(s["project_inputs"], s["extracted_params"], kb, facts=s["package_facts"], scan=s.get("dmto_scan"),
                      options=s["dmto_options"], rooms_override=rows_to_rooms(s["dmto_rooms"]),
                      openings_override=rows_to_openings(s["dmto_openings"]), overrides=s["dmto_overrides"])
    return compute(p, kb)


def test_round_trip_reproduces_the_takeoff(session):
    raw = project_to_json(session)
    back = project_from_json(raw)
    a, b = _takeoff(session), _takeoff(back)
    assert all(abs(m.gross_qty - b.by_id(m.material.mat_id).gross_qty) < 1e-6 for m in a.materials)
    assert back["dmto_options"].masonry == "Block" and back["dmto_overrides"] == {"N_AC": 3.0}
    assert list(back["copilot_scenarios"]) == ["A"] and back["copilot_msgs"][0] == {"role": "user", "content": "hello"}
    assert back["uploaded_files"] == [] and back["step"] == 4
    assert json.loads(raw)["drawings"] is None


def test_drawings_optional_in_file(session):
    raw = project_to_json(session, include_drawings=True)
    back = project_from_json(raw)
    assert back["uploaded_files"][0]["bytes"] == session["uploaded_files"][0]["bytes"]


def test_rejects_foreign_or_damaged_files():
    for bad in (b"not json", b'{"a": 1}', json.dumps({"format": "costlens-project", "format_version": 99}).encode()):
        with pytest.raises(ValueError):
            project_from_json(bad)


def test_library(tmp_path, session):
    path = library_save(session, tmp_path)
    items = library_list(tmp_path)
    assert items and items[0]["name"] == "Persist Test"
    assert project_from_json(path.read_bytes())["project_inputs"].project_name == "Persist Test"
    library_delete(path)
    assert not library_list(tmp_path)


def test_shopping_list_text_and_pdf(session):
    from detailed_mto.shopping import shopping_pdf, shopping_text
    res = _takeoff(session)
    txt = shopping_text(res)
    assert "Cement" in txt or "cement" in txt.lower()
    assert "bags" in txt and "No prices" in txt
    only = shopping_text(res, ["Electrical"])
    assert "*Electrical*" in only and "*Masonry*" not in only
    pdf = shopping_pdf(res)
    assert pdf[:4] == b"%PDF" and len(pdf) > 2000


def test_concept_plan_svg():
    from detailed_mto.concept_plan import floor_svg
    svg = floor_svg([{"Room": "BED", "Room type": "Bedroom", "Length (ft)": 12, "Width (ft)": 13},
                     {"Room": "PORCH", "Room type": "Porch / car porch", "Length (ft)": 12, "Width (ft)": 17},
                     {"Room": "x", "Room type": "Bathroom", "Length (ft)": "bad", "Width (ft)": 5}], 30, "Ground floor")
    assert svg.startswith("<svg") and "BED" in svg and "PORCH" in svg and svg.count("<rect") == 3
