"""Tests for the guided (no drawings) route: brief parsing, AI JSON merge, conversion to take-off inputs."""
from __future__ import annotations

import pytest

from detailed_mto import build_project, compute
from detailed_mto.brief import (ProjectBrief, add_attached_baths, apply_ai_result, brief_to_inputs, complete_programme,
                                parse_description, to_feet, typical_rooms)
from detailed_mto.edits import rows_to_openings, rows_to_rooms
from engineering import plot_templates
from knowledge import load_knowledge_base
from models.schemas import ProjectInputs


@pytest.fixture(scope="module")
def kb():
    return load_knowledge_base()


def _run(kb, brief):
    inp = brief_to_inputs(brief)
    pi = ProjectInputs(project_name="brief", plot_marla=brief.marla, marla_sqft=brief.marla_sqft, plot_storeys=brief.storeys)
    params = plot_templates.build_template_params(pi)
    params.footings.footing_type = inp["structure"]
    p = build_project(pi, params, kb, rooms_override=rows_to_rooms(inp["rooms"]),
                      openings_override=rows_to_openings(inp["openings"]), overrides=inp["overrides"],
                      drawing_mode="sketch", floors_override=inp["floors"])
    return p, compute(p, kb)


def test_parse_description_reads_key_facts():
    b = ProjectBrief()
    got = parse_description("10 marla G+1, 40x68 plot, corner, 6 bedrooms with attached baths, RCC frame, septic tank, "
                            "servant quarter, no store", b)
    assert b.marla == 10 and b.storeys == 2 and b.plot_width_ft == 40 and b.plot_depth_ft == 68
    assert b.side_walls.startswith("One side") and b.structure.startswith("RCC") and b.sewer == "Septic tank"
    assert sum(r.room_type == "Bedroom" for r in b.rooms) == 6
    assert sum(r.room_type == "Bathroom" for r in b.rooms) >= 6
    assert any(r.room_type == "Servant quarter" for r in b.rooms)
    assert not any(r.room_type == "Store / utility" for r in b.rooms)
    assert got


def test_storey_word_is_not_a_store_room():
    b = ProjectBrief()
    parse_description("5 marla double storey", b)
    assert not any(r.room_type == "Store / utility" for r in b.rooms)


def test_to_feet():
    assert to_feet("12'-6\"") == pytest.approx(12.5)
    assert to_feet(13) == 13 and to_feet("11'") == 11 and to_feet(None) is None and to_feet("abc") is None


def test_ai_json_merge_and_completion():
    b = ProjectBrief()
    got = apply_ai_result({"plot": {"marla": 7}, "storeys": 2, "structure": "rcc_frame",
                           "floors": [{"floor": "ground", "rooms": [{"name": "Bed", "type": "bedroom", "length_ft": 12, "width_ft": 12}]},
                                      {"floor": "first", "rooms": [{"name": "Master bed", "length_ft": "14'", "width_ft": 13}]}],
                           "questions": ["Corner plot?"]}, b)
    assert b.marla == 7 and b.structure.startswith("RCC") and len(b.rooms) == 2 and b.ai_questions == ["Corner plot?"]
    added = complete_programme(b)
    types = [r.room_type for r in b.rooms]
    assert "Kitchen" in types and "Staircase" in types and types.count("Bathroom") >= 2 and added


def test_attached_baths():
    b = ProjectBrief()
    b.rooms = [r for r in typical_rooms(b) if r.room_type != "Bathroom"]
    out = add_attached_baths(b.rooms)
    for fk in {r.floor for r in out}:
        beds = sum(r.floor == fk and r.room_type == "Bedroom" for r in out)
        baths = sum(r.floor == fk and r.room_type == "Bathroom" for r in out)
        assert baths >= beds


def test_brief_to_takeoff_is_plausible(kb):
    b = ProjectBrief(marla=5, marla_sqft=272.25, plot_width_ft=33, plot_depth_ft=41, storeys=2)
    b.rooms = typical_rooms(b)
    p, res = _run(kb, b)
    assert p.drawing_mode == "sketch"
    assert [f.key for f in p.floors] == ["ground", "first", "roof"]
    cov = sum(f.covered_sft for f in p.floors)
    assert 1800 < cov < 3400
    cement = res.by_id("CON-001").gross_qty / cov
    bricks = sum(m.gross_qty for m in res.materials if m.material.mat_id in ("MAS-001", "MAS-002")) / cov
    assert 0.25 < cement < 0.6 and 15 < bricks < 40
    assert all(m.gross_qty >= 0 for m in res.materials)
    # the owner's answers become the user's own inputs
    assert p.conf("H_FLOOR") == "User" and p.v("N_AC") > 0


def test_answers_change_quantities(kb):
    b = ProjectBrief(storeys=2)
    b.rooms = typical_rooms(b)
    _, r1 = _run(kb, b)
    b.boundary = "Front, back & sides"
    b.sewer = "Septic tank"
    _, r2 = _run(kb, b)
    assert r2.project.v("BOUNDARY_LEN") > r1.project.v("BOUNDARY_LEN")
    assert r2.project.v("SEPTIC_N") == 1 and r1.project.v("SEPTIC_N") == 0
    assert r2.by_id("MAS-001").gross_qty > r1.by_id("MAS-001").gross_qty


def test_floor_height_edit_follows_concept_floors(kb):
    b = ProjectBrief(storeys=2)
    b.rooms = typical_rooms(b)
    inp = brief_to_inputs(b)
    pi = ProjectInputs(project_name="x", plot_marla=5)
    params = plot_templates.build_template_params(pi)
    p = build_project(pi, params, kb, rooms_override=rows_to_rooms(inp["rooms"]), openings_override=rows_to_openings(inp["openings"]),
                      overrides={**inp["overrides"], "H_FLOOR": 13}, drawing_mode="sketch", floors_override=inp["floors"])
    assert all(f.storey_height_ft == 13 for f in p.storeys)


def test_sketch_reader_json_and_mocked_model(monkeypatch):
    import ai.groq_client as gc
    from ai import sketch_reader
    from PIL import Image
    assert sketch_reader._json_from('```json\n{"storeys": 2}\n```') == {"storeys": 2}
    assert sketch_reader._json_from("noise {\"a\": 1} tail") == {"a": 1}
    monkeypatch.setattr(gc, "call_vision_model", lambda *a, **k: '{"storeys": 2, "floors": []}')
    monkeypatch.setattr(gc, "call_text_model", lambda *a, **k: '{"storeys": 1}')
    data, err = sketch_reader.read_sketch("key", [Image.new("RGB", (10, 10))], "desc")
    assert data == {"storeys": 2, "floors": []} and not err
    data, err = sketch_reader.read_sketch("key", [], "5 marla single storey")
    assert data == {"storeys": 1}

    def boom(*a, **k):
        raise RuntimeError("quota")
    monkeypatch.setattr(gc, "call_text_model", boom)
    data, err = sketch_reader.read_sketch("key", [], "x")
    assert data == {} and "could not be reached" in err
