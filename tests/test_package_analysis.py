"""
Drawing-package analysis (no AI) and 5-10 marla plot templates.

The sample package (sample_data/make_sample_package.py) is a CAD-style
5 marla G+1 drawing set with known dimensions, so every value read from it
can be checked against ground truth. Real drawings are messier - these
tests prove the parsing/measurement logic, not accuracy on every CAD style.
"""
import io
import math
import os
import sys

import pymupdf
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "sample_data"))
import make_sample_package as sample  # noqa: E402

from ai.extraction import _map_json_to_params, default_building_params  # noqa: E402
from drawing_processing.package_analyzer import (  # noqa: E402
    analyze_package,
    apply_package_facts,
    classify_title,
    pages_for_ai,
    parse_ftin,
    parse_room_dims,
    parse_scale,
    room_kind,
)
from engineering import plot_templates  # noqa: E402
from engineering.validation import validate_params  # noqa: E402
from models.schemas import ConfidenceLevel, EngineeringAssumptions, Estimate, ProjectInputs, Source  # noqa: E402

FT, IN, SQFT = 0.3048, 0.0254, 0.3048 ** 2
T = sample.TRUTH


def _pdf(tmp_path, **kw):
    path = tmp_path / "pkg.pdf"
    sample.make(str(path), **kw)
    return {"name": "pkg.pdf", "bytes": path.read_bytes(), "view_tag": "Plan"}


@pytest.fixture(scope="module")
def facts(tmp_path_factory):
    f = _pdf(tmp_path_factory.mktemp("pkg"))
    return analyze_package([f])


# --------------------------------------------------------------------------
# Parsing helpers
# --------------------------------------------------------------------------


def test_parse_helpers():
    assert parse_ftin("12'-6\"") == 12.5 and parse_ftin("12'") == 12.0 and parse_ftin('9"') == 0.75
    assert parse_ftin("5'-4 1/2\"") == pytest.approx(5 + 4.5 / 12)
    assert parse_room_dims("BED ROOM 12'-0\" x 14'-6\"") == (12.0, 14.5)
    assert parse_room_dims("11’-9” X 13’-3”") == (11.75, 13.25)  # CAD curly quotes
    assert parse_scale('SCALE: 1/8"=1\'-0"')[1] == pytest.approx(8 / 72)
    assert parse_scale("SCALE 1:100")[1] == pytest.approx(100 / 72 / 12)
    assert room_kind("TOILET") == "bathroom" and room_kind("W.C") == "bathroom" and room_kind("KITCHEN") == "kitchen"
    assert room_kind("CAR PORCH") == "open" and room_kind("MASTER BED") == "bedroom"


def test_title_classification():
    assert classify_title("GROUND FLOOR PLAN") == ("plan", "ground")
    assert classify_title("1ST FLOOR PLAN") == ("plan", "first")
    assert classify_title("SECTION A-A")[0] == "section"
    assert classify_title("FRONT ELEVATION")[0] == "elevation"
    assert classify_title("FOUNDATION PLAN")[0] == "structural"
    assert classify_title("ELECTRICAL LAYOUT GROUND FLOOR")[0] == "mep"
    # a sentence that merely mentions a section is a note, not a title
    assert classify_title("ALL DIMENSIONS ARE IN FEET AND INCHES REFER TO SECTION A-A FOR LEVELS") is None


# --------------------------------------------------------------------------
# Sample package - ground truth
# --------------------------------------------------------------------------


def test_sheets_are_sorted(facts):
    views = {s.page_no: s.views for s in facts.sheets}
    assert views[1] == ["plan (Ground floor)", "plan (First floor)"]
    assert set(views[2]) == {"section", "elevation"}
    assert "structural" in views[3]
    assert facts.storeys == 2 and facts.conflicts == []


@pytest.mark.parametrize("floor", ["ground", "first"])
def test_plan_geometry_and_rooms(facts, floor):
    f, t = facts.floors[floor], T[floor]
    assert f.width_ft == pytest.approx(t["W"], abs=0.1) and f.depth_ft == pytest.approx(t["D"], abs=0.1)
    assert f.perimeter_ft == pytest.approx(2 * (t["W"] + t["D"]), abs=0.5)
    # centreline truth: external 2*(W-0.75 + D-0.75) + internal walls (see generator)
    internal = (t["D"] - 1.5) + 2 * 23.5 + (t["D"] - 0.75 - 24)
    truth_len = 2 * ((t["W"] - 0.75) + (t["D"] - 0.75)) + internal
    assert f.wall_length_ft == pytest.approx(truth_len, rel=0.05)
    assert set(f.thickness_breakdown) == {9.0, 4.5}
    assert f.doors == t["doors"]
    assert len(f.rooms) == t["rooms"] and f.count("bathroom") == t["baths"] and f.count("kitchen") == t["kitchens"]
    assert f.covered_sqft == {"ground": 925, "first": 850}[floor]  # stated on the drawing wins


def test_labels_on_one_line_are_split(facts):
    names = [r[0] for r in facts.floors["ground"].rooms]
    assert "TOILET" in names and "BATH" in names


def test_section_structural_plot(facts):
    s = {k: v[0] for k, v in facts.section.items()}
    assert s["floor_height_ft"] == T["floor_height_ft"] and s["plinth_height_ft"] == T["plinth_ft"]
    assert s["parapet_height_ft"] == T["parapet_ft"] and s["founding_depth_ft"] == T["founding_ft"]
    assert s["slab_thickness_ft"] * 12 == T["slab_in"]
    st = {k: v[0] for k, v in facts.structural.items()}
    assert st["footing_count"] == T["footings"] and st["column_count"] == T["footings"]
    assert st["footing_length_ft"] == T["footing_ft"] and st["footing_thickness_ft"] == T["footing_t_ft"]
    assert (st["column_width_ft"] * 12, st["column_depth_ft"] * 12) == T["column_in"]
    p = {k: v[0] for k, v in facts.plot.items()}
    assert p["marla"] == 5 and (p["plot_width_ft"], p["plot_depth_ft"]) == (25, 45)


def test_other_scale_and_inferred_scale(tmp_path):
    quarter = analyze_package([_pdf(tmp_path, pt_per_ft=18.0, scale_note="SCALE: 1/4\"=1'-0\"")])
    g = quarter.floors["ground"]
    assert (g.width_ft, g.depth_ft) == (pytest.approx(25, abs=0.1), pytest.approx(37, abs=0.1))
    none = analyze_package([_pdf(tmp_path, scale_note=None)], plot_width_hint_ft=25)
    g = none.floors["ground"]
    assert "inferred" in g.scale_label and g.geometry_confidence == ConfidenceLevel.LOW
    assert g.width_ft == pytest.approx(25, abs=0.1)


def test_scanned_package_yields_no_facts_and_goes_to_ai(tmp_path):
    src = pymupdf.open(stream=_pdf(tmp_path)["bytes"], filetype="pdf")
    out = pymupdf.open()
    for pg in src:
        page = out.new_page(width=pg.rect.width, height=pg.rect.height)
        page.insert_image(page.rect, pixmap=pg.get_pixmap(dpi=60))
    facts = analyze_package([{"name": "scan.pdf", "bytes": out.tobytes(), "view_tag": "Plan"}])
    assert not facts.has_facts()
    assert len(pages_for_ai(facts, 3)) == 3


def test_pages_for_ai_prefers_ground_plan_then_section(facts):
    assert pages_for_ai(facts, 3)[:2] == [(0, 0), (0, 1)]


# --------------------------------------------------------------------------
# Merging into parameters
# --------------------------------------------------------------------------


def test_apply_package_facts_overrides_with_provenance(facts):
    pi = ProjectInputs(plot_marla=5)
    base = plot_templates.build_template_params(pi)
    p, asm, filled = apply_package_facts(base, facts, pi)
    assert p.num_floors.value == 2 and p.num_floors.source == Source.DRAWING_READ
    assert p.plinth_area_per_floor_sqm.value / SQFT == pytest.approx((925 + 850) / 2)
    assert p.plinth_area_per_floor_sqm.confidence == ConfidenceLevel.HIGH
    assert p.footings.count.value == 12 and p.footings.length_m.value == pytest.approx(5 * FT)
    assert p.columns.height_per_floor_m.value == pytest.approx(10.5 * FT)
    assert p.services.bathroom_count_total.value == 4 and p.services.kitchen_count_total.value == 2
    assert asm == {"plinth_height_m": pytest.approx(1.5 * FT), "parapet_height_m": pytest.approx(3 * FT)}
    assert all(e.note.startswith("From drawings") for e in [p.walls.total_length_per_floor_m, p.slabs.thickness_m])
    assert len(filled) >= 18
    assert validate_params(p, pi)[0] == []


def test_marla_mismatch_and_stale_ai_warnings(facts):
    pi = ProjectInputs(plot_marla=10)
    ai = _map_json_to_params({"num_floors": [2, "M"]}, 228.6, pi.wall_material, "FPS", plot_templates.build_template_params(pi))
    assert any(w.startswith("AI did not return:") for w in ai.extraction_warnings)
    p, _, _ = apply_package_facts(ai, facts, pi)
    missing = next(w for w in p.extraction_warnings if w.startswith("AI did not return:"))
    assert "footing count" not in missing and "beam count" in missing  # footings came from the drawings
    assert any("drawings say 5 marla" in w for w in p.extraction_warnings)


# --------------------------------------------------------------------------
# Plot templates & plausibility
# --------------------------------------------------------------------------


@pytest.mark.parametrize("marla", plot_templates.PLOT_SIZES_MARLA)
@pytest.mark.parametrize("storeys", [1, 2])
def test_templates_are_valid_and_plausible(marla, storeys):
    pi = ProjectInputs(plot_marla=marla, plot_storeys=storeys)
    p = plot_templates.build_template_params(pi)
    errors, warnings = validate_params(p, pi)
    assert errors == [] and warnings == []
    covered = p.plinth_area_per_floor_sqm.value / SQFT
    assert 0.6 * marla * 225 <= covered <= marla * 225


def test_marla_standards_and_frontage():
    assert plot_templates.plot_dimensions_ft(5, 225) == (25, 45)
    assert plot_templates.plot_area_sqft(5, 272.25) == pytest.approx(1361.25)
    assert plot_templates.plot_dimensions_ft(10, 225, width_ft=30)[1] == pytest.approx(75)


def test_template_in_si_uses_metric_notes():
    p = plot_templates.build_template_params(ProjectInputs(unit_system="SI", plot_marla=5))
    assert " m" in p.plinth_area_per_floor_sqm.note and "'" not in p.plinth_area_per_floor_sqm.note


def test_plausibility_flags_impossible_area():
    pi = ProjectInputs(plot_marla=5)
    p = plot_templates.build_template_params(pi)
    p.plinth_area_per_floor_sqm = Estimate(value=3000 * SQFT, confidence=ConfidenceLevel.HIGH, source=Source.USER_INPUT)
    assert any("larger than the whole 5 marla plot" in w for w in validate_params(p, pi)[1])


def test_no_plot_means_no_template_and_no_plot_checks():
    pi = ProjectInputs()
    assert plot_templates.build_template_params(pi) is None
    p = default_building_params(pi.wall_thickness_mm, pi.wall_material, "FPS")
    assert plot_templates.plausibility_warnings(p, pi) == []


# --------------------------------------------------------------------------
# Real-world drawing conventions (learned from an actual 5 marla set)
# --------------------------------------------------------------------------
from drawing_processing.package_analyzer import (  # noqa: E402
    TextLine,
    _area_table,
    _door_schedule,
    _elevation_facts,
    _section_facts,
    _strip_foundation_facts,
    calibrate_scale,
)
from engineering.calculations import generate_all_quantities, strip_footing_section  # noqa: E402


def _ln(text, cx, cy, w=40, h=10):
    return TextLine(text, cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2)


def test_double_apostrophe_inches_and_rotated_labels():
    assert parse_room_dims("13'-4''x8'-9''") == (pytest.approx(13 + 4 / 12), 8.75)
    from drawing_processing.package_analyzer import _rooms_from_lines

    # vertical (rotated) text: name sits beside the size, not above it
    rooms = _rooms_from_lines([_ln("KITCHEN", 302, 366, 9, 41), _ln("8'-6''x9'-3''", 293, 368, 9, 48)])
    assert rooms[0][0] == "KITCHEN" and rooms[0][3] == "kitchen"


def test_area_statement_table_rows():
    lines = [
        _ln("AREA OF GROUND FLOOR", 500, 198, 127), _ln("1512.00", 670, 197, 36), _ln("SQFT", 703, 197, 27),
        _ln("AREA OF FIRST FLOOR", 495, 221, 111), _ln("1362.00", 670, 220, 36),
        _ln("AREA OF PLOT", 482, 299, 86), _ln("1353.00", 669, 299, 43),
        _ln("TOTAL BUILT UP AREA", 504, 270, 130), _ln("3087.00", 675, 269, 43),
    ]
    t = _area_table(lines)
    assert t == {"covered_ground_sqft": 1512.0, "covered_first_sqft": 1362.0, "plot_area_sqft": 1353.0}


def test_door_schedule_table():
    lines = [
        _ln("4'-6\" X 8'-0\" 10\"", 242, 185, 185, 25), _ln("1", 492, 185, 14, 25),
        _ln("3'-6\" X 7'-0\"", 216, 258, 133, 25), _ln("6", 492, 258, 14, 25),
        _ln("2'-6\" X 7'-0\"", 216, 331, 133, 25), _ln("4", 492, 331, 14, 25),
    ]
    total, area = _door_schedule(lines, "SIZE QTY\nTOTAL = 11")
    assert total == 11 and area == pytest.approx((4.5 * 8 + 6 * 3.5 * 7 + 4 * 2.5 * 7) / 11)


def test_elevation_chain_gives_floor_to_floor_slab_and_plinth():
    # vertical chain (texts rotated): 9", 8'-6" mumty, 6" slab, 11'-6", 6" slab, 11'-6", 1'-6" plinth
    chain = [("9\"", 171, 119), ("8'-6\"", 185, 156), ("6\"", 174, 197), ("11'-6\"", 185, 259),
             ("6\"", 176, 315), ("11'-6\"", 185, 377), ("1'-6\"", 185, 441)]
    lines = [_ln(t, x, y, 11, 8 if len(t) <= 3 else 20) for t, x, y in chain]
    e = _elevation_facts(lines)
    assert e["floor_height_ft"] == 12.0 and e["slab_thickness_ft"] == 0.5 and e["plinth_height_ft"] == 1.5
    assert e["levels_storeys"] == 2


def test_level_marks_after_letters():
    assert _section_facts([_ln("F.L+1'-0''", 100, 100), _ln("R.L+0'-0''", 100, 140)])["plinth_height_ft"] == 1.0


def test_strip_foundation_detected_from_wall_sections():
    lines = [_ln(t, 100 + 30 * i, 100) for i, t in enumerate(["3'-6\"", "3'", "2'-6\"", "3'-6\"", "3'-6\""])]
    f = _strip_foundation_facts(lines, "X-SECTION OF 9'' THICK EXTERNAL WALL\nP.C.C 1:4:8\nD.P.C.")
    assert f["strip"] == 1.0 and f["founding_depth_ft"] == 3.5 and f["strip_width_ft"] == 3.0


def test_scale_calibrated_from_dimension_lines():
    s = 7.2  # pt per ft ("N.T.S" sheet, arbitrary plot scale)
    H = sorted([(100.0 + 40 * i, 50.0, 50.0 + L * s) for i, L in enumerate([33, 12, 9.25, 21, 18.75])])
    lines = [_ln(t, 50 + L * s / 2, 100 + 40 * i - 5, 20, 8) for i, (t, L) in enumerate(
        [("33'", 33), ("12'", 12), ("9'-3\"", 9.25), ("21'", 21), ("18'-9\"", 18.75)])]
    ft_per_pt, support, n = calibrate_scale(lines, H, [])
    assert ft_per_pt == pytest.approx(1 / s, rel=1e-3) and support == n == 5


def test_strip_footing_section_hand_calc():
    ft = 0.3048
    sec = strip_footing_section(3 * ft, 0.75 * ft, 5 * ft, "FPS")  # 3' PCC, 9" wall, 5' of brickwork
    # bottom 3' - 2x6" = 2'; steps 2', 1'-7.5", 1'-3", 10.5" (6" each), then 9" wall for 3'
    assert sec["area_m2"] / ft ** 2 == pytest.approx((2 + 1.625 + 1.25 + 0.875) * 0.5 + 0.75 * 3.0)


def test_strip_foundation_quantities_fps():
    ft, cft = 0.3048, 0.3048 ** 3
    pi = ProjectInputs()
    p = default_building_params(pi.wall_thickness_mm, pi.wall_material, "FPS")
    E = lambda v: Estimate(value=v, confidence=ConfidenceLevel.HIGH, source=Source.USER_INPUT)  # noqa: E731
    p.footings.footing_type = "strip"
    p.walls.total_length_per_floor_m, p.footings.width_m = E(100 * ft), E(3 * ft)
    p.walls.external_perimeter_m = E(80 * ft)
    p.footings.depth_m, p.footings.founding_depth_m, p.walls.thickness_m = E(0.5 * ft), E(3.5 * ft), E(0.75 * ft)
    q = {i.item_code: i for i in generate_all_quantities(p, pi, EngineeringAssumptions.for_unit_system("FPS"))}
    slope = __import__("engineering.rules", fromlist=["x"]).SOIL_SIDE_SLOPE_FACTOR[pi.soil_type]
    assert q["EXC-01"].quantity / cft == pytest.approx(100 * 3 * 3.5 * slope, rel=1e-6)
    assert q["PCC-01"].quantity / cft == pytest.approx(150, rel=1e-6)
    assert q["FDN-MAS-01"].quantity / cft == pytest.approx(100 * 5.125, rel=1e-6)  # plinth 2'-0" -> 5' of brickwork
    assert q["BACKFILL-01"].quantity / cft == pytest.approx(100 * 3 * 3.5 * slope - 150 - 362.5, rel=1e-6)
    assert "FTG-CONC-01" not in q and "FTG-STEEL-01" not in q and "FORM-FTG-01" not in q
    assert validate_params(p, pi)[0] == []


def test_ai_gets_one_page_per_view(facts):
    kinds = [facts.page_kinds[k] for k in pages_for_ai(facts, 3)]
    assert len(kinds) == len(set(kinds))
