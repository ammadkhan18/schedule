"""
FPS system tests.

1. FPS is the default and uses Pakistani round feet-inch values.
2. Every FPS quantity equals an INDEPENDENT hand calculation done purely in
   feet/inches/cft/sqft (no metric anywhere in the expected values).
3. Nothing metric leaks into FPS output (MTO text, traces, BOQ, validation,
   Excel, PDF), and FPS amounts reconcile (qty x rate).
4. SI still behaves as the metric system.
"""
import io
import math
import re

import pymupdf
import pytest
from openpyxl import load_workbook

from ai.extraction import default_building_params
from engineering import rules
from engineering.validation import validate_params
from export.excel_export import build_excel_workbook
from export.pdf_export import build_pdf_report
from models.schemas import ConfidenceLevel, EngineeringAssumptions, Estimate, ProjectInputs, Source, WastageFactors
from mto_boq.boq_generator import boq_to_dataframe, generate_boq
from mto_boq.mto_generator import compute_reinforcement_summary, generate_mto, mto_to_dataframe
from mto_boq.rates import load_default_rates
from utils import units

FT = 0.3048
IN = 0.0254
CFT = FT ** 3
SQFT = FT ** 2


def rate_book():
    return {r.item_code: r for r in load_default_rates()}


def fps_default():
    pi = ProjectInputs()
    p = default_building_params(pi.wall_thickness_mm, pi.wall_material, pi.unit_system)
    A = EngineeringAssumptions.for_unit_system("FPS")
    return p, pi, A


def by_code(items):
    return {i.item_code: i for i in items}


def cft(item):
    return item.quantity / CFT


def sqft(item):
    return item.quantity / SQFT


# --------------------------------------------------------------------------
# 1. Defaults
# --------------------------------------------------------------------------


def test_fps_is_the_default_with_pakistani_labels():
    pi = ProjectInputs()
    assert pi.unit_system == "FPS"
    assert pi.concrete_grade_slab == "3000 psi" and pi.pcc_grade == "1500 psi"
    assert pi.steel_grade == "Grade 60 (60,000 psi)"
    assert math.isclose(pi.wall_thickness_mm / 25.4, 9.0)  # 9" wall
    assert math.isclose(pi.plaster_thickness_internal_mm / 25.4, 0.5)  # 1/2"
    assert math.isclose(pi.plaster_thickness_external_mm / 25.4, 0.75)  # 3/4"


def test_si_project_keeps_metric_defaults():
    pi = ProjectInputs(unit_system="SI")
    assert (pi.concrete_grade_slab, pi.pcc_grade, pi.steel_grade) == ("M20", "M10", "Fe415")
    assert (pi.wall_thickness_mm, pi.plaster_thickness_internal_mm, pi.plaster_thickness_external_mm) == (230, 12, 18)


def test_grade_labels_map_between_systems():
    assert ProjectInputs(unit_system="FPS", concrete_grade_slab="M25").concrete_grade_slab == "3600 psi"
    assert ProjectInputs(unit_system="SI", steel_grade="Grade 40 (40,000 psi)").steel_grade == "Fe250"


def test_fps_default_dimensions_are_round_feet_inches():
    p, _, A = fps_default()
    assert math.isclose(p.plinth_area_per_floor_sqm.value / SQFT, 1080)
    assert math.isclose(p.footings.length_m.value / FT, 4) and math.isclose(p.footings.depth_m.value / IN, 18)
    assert math.isclose(p.footings.founding_depth_m.value / FT, 5)
    assert math.isclose(p.columns.width_m.value / IN, 9) and math.isclose(p.columns.depth_m.value / IN, 18)
    assert math.isclose(p.columns.height_per_floor_m.value / FT, 10)
    assert math.isclose(p.slabs.thickness_m.value / IN, 5)
    assert math.isclose(p.openings.avg_door_area_sqm.value / SQFT, 21)  # 3'x7'
    assert math.isclose(A.pcc_thickness_m / IN, 3) and math.isclose(A.plinth_height_m / FT, 2)
    assert math.isclose(A.parapet_height_m / FT, 3) and math.isclose(A.excavation_working_space_m / IN, 6)
    assert validate_params(p, ProjectInputs(), A) == ([], [])


# --------------------------------------------------------------------------
# 2. Independent FPS hand calculations (feet / cft / sqft only)
# --------------------------------------------------------------------------


def test_fps_quantities_match_feet_inch_hand_calculation():
    p, pi, A = fps_default()
    q = by_code(generate_mto(p, pi, A))
    slope = rules.SOIL_SIDE_SLOPE_FACTOR[pi.soil_type]

    # Footings: 9 nos 4'x4'x1'-6", founding depth 5'-0", PCC 3", working space 6"
    assert math.isclose(cft(q["EXC-01"]), 9 * (4 + 1) * (4 + 1) * (5 + 0.25) * slope, rel_tol=1e-4)
    assert math.isclose(cft(q["PCC-01"]), 9 * (4 + 0.5) * (4 + 0.5) * 0.25, rel_tol=1e-4)
    assert math.isclose(cft(q["FTG-CONC-01"]), 9 * 4 * 4 * 1.5, rel_tol=1e-4)
    # Columns 9"x18": 10' storey + 2' plinth + (5' - 1'-6") below-ground stub
    assert math.isclose(cft(q["COL-CONC-01"]), 9 * 0.75 * 1.5 * (10 + 2 + 3.5), rel_tol=1e-4)
    # Beams 9"x18", 12 x 13': plinth level full depth + roof level below the 5" slab
    assert math.isclose(cft(q["BEAM-CONC-01"]), 12 * 13 * 0.75 * (1.5 + (1.5 - 5 / 12)), rel_tol=1e-4)
    assert math.isclose(cft(q["SLAB-CONC-01"]), 1080 * 5 / 12, rel_tol=1e-4)
    # Staircase: 10' storey, 7" max riser -> 18 risers; 10" treads; 3'-6" wide; 6" waist; 4" gap
    riser_ft = 10 / 18
    going = 8 * 10 / 12
    inclined = math.hypot(going, 5)
    stair = 2 * inclined * 3.5 * 0.5 + 18 * 0.5 * riser_ft * (10 / 12) * 3.5 + (7 + 4 / 12) * 3.5 * 0.5
    assert math.isclose(cft(q["STAIR-CONC-01"]), stair, rel_tol=1e-4)
    # Lintels 9" x 6" deep, 6" bearing: 4 doors 3' wide (21 sqft / 7'), 5 windows 4' wide; chajja 18" x 3"
    lintel = (4 * (3 + 1) + 5 * (4 + 1)) * 0.75 * 0.5
    chajja = 5 * (4 + 1) * 1.5 * 0.25
    assert math.isclose(cft(q["LINTEL-CONC-01"]), lintel + chajja, rel_tol=1e-4)
    # Masonry: 250' x 10' walls - openings (4x21 + 5x16 sqft), 9" thick, - lintels + 3' parapet on 132'
    net_area = 250 * 10 - (4 * 21 + 5 * 16)
    masonry = net_area * 0.75 - lintel + 132 * 3 * 0.75
    assert math.isclose(cft(q["MAS-03"]), masonry, rel_tol=1e-4)
    nominal_brick_cft = (0.2 * 0.1 * 0.1) / CFT  # modular brick incl. joint, in cft
    assert abs(q["MAS-01"].quantity - masonry / nominal_brick_cft) <= 0.5 + 1e-6 * masonry / nominal_brick_cft
    # Plaster: external = 132' x 10' - (5 windows x 16 + main door 21) + parapet both faces
    ext = 132 * 10 - (5 * 16 + 21) + 2 * 132 * 3
    assert math.isclose(sqft(q["PLAS-02"]), ext, rel_tol=1e-4)
    assert math.isclose(sqft(q["PLAS-01"]), 2 * net_area - (132 * 10 - (5 * 16 + 21)), rel_tol=1e-4)
    assert math.isclose(sqft(q["PLAS-03"]), 1080, rel_tol=1e-4)
    # Ground floor: fill area = 1080 - 250' x 9"; plinth fill depth = 2' - 3" - 3"
    fill_area = 1080 - 250 * 0.75
    assert math.isclose(sqft(q["GF-SOL-01"]), fill_area, rel_tol=1e-4)
    assert math.isclose(cft(q["PLINTH-FILL-01"]), fill_area * (2 - 0.5), rel_tol=1e-4)
    # DPC 1" thick under 250' of 9" wall
    assert math.isclose(cft(q["DPC-01"]), 250 * 0.75 * (1 / 12), rel_tol=1e-3)
    # Steel: footing concrete (cft) x 80 kg/m3 expressed as kg/cft
    assert math.isclose(q["FTG-STEEL-01"].quantity, 216 * (80 * CFT), rel_tol=1e-3)


def test_fps_trace_values_are_feet_inches_and_reproduce_the_quantity():
    p, pi, A = fps_default()
    exc = by_code(generate_mto(p, pi, A))["EXC-01"]
    t = exc.inputs_used
    assert t["footing_length_ft"] == 4.0 and t["founding_depth_ft"] == 5.0
    assert t["working_space_in"] == 6.0 and t["pcc_thickness_in"] == 3.0
    recomputed = (
        t["footing_count"]
        * (t["footing_length_ft"] + 2 * t["working_space_in"] / 12)
        * (t["footing_width_ft"] + 2 * t["working_space_in"] / 12)
        * (t["founding_depth_ft"] + t["pcc_thickness_in"] / 12)
        * t["soil_side_slope_factor"]
    )
    assert math.isclose(recomputed, cft(exc), rel_tol=1e-4)


# --------------------------------------------------------------------------
# 3. No metric in FPS output; FPS amounts reconcile
# --------------------------------------------------------------------------

METRIC = re.compile(
    r"(\d\s?(mm|m|m2|m3|sqm|cm)\b)|m²|m³|\bsqm\b|\bm3\b|\bm2\b|kg/m3|_m\b|_m3\b|_sqm\b|\bmetric\b|\bM(7\.5|10|15|20|25)\b|\bFe\d{3}\b"
)
# Metric shown ONLY as a parenthetical cross-reference next to the inch value
# (e.g. brick "7.48x3.54x3.54in (190x90x90mm)") is allowed.
ALLOWED = [r"\(\d+(\.\d+)?x\d+(\.\d+)?x\d+(\.\d+)?mm\)", r"\(\d+x\d+x\d+ mm\)", r"\(\d+ per m3, before wastage\)", r"3/8\" \(10 mm\)"]


def _metric_leaks(text: str) -> list:
    t = str(text)
    for a in ALLOWED:
        t = re.sub(a, "", t)
    return [m.group(0) for m in METRIC.finditer(t)]


def _fps_outputs():
    p, pi, A = fps_default()
    mto = generate_mto(p, pi, A)
    boq, cs = generate_boq(mto, rate_book(), WastageFactors(), ProjectInputs(concrete_grade_slab="3600 psi"), preliminaries_pct=8)
    return p, pi, mto, boq, cs


def test_no_metric_text_anywhere_in_fps_output():
    p, pi, mto, boq, cs = _fps_outputs()
    texts = []
    for i in mto:
        texts += [i.description, i.formula, *i.assumptions, *i.inputs_used.keys()]
    for b in boq:
        texts += [b.description, b.remarks]
    texts.append(compute_reinforcement_summary(mto, "FPS")["message"])
    texts += list(mto_to_dataframe(mto, "FPS")["Unit"]) + list(boq_to_dataframe(boq, "FPS")["Unit"])
    wb = load_workbook(io.BytesIO(build_excel_workbook(pi, p, mto, boq, cs, preliminaries_pct=8)))
    for ws in wb:
        if ws.title == "Assumptions & Notes":
            continue
        texts += [c.value for row in ws.iter_rows() for c in row if isinstance(c.value, str) and not c.value.startswith("=")]
    pdf = pymupdf.open(stream=build_pdf_report(pi, p, mto, boq, cs), filetype="pdf")
    texts += [line for page in pdf for line in page.get_text().splitlines()]
    leaks = [(t, _metric_leaks(t)) for t in texts if _metric_leaks(t)]
    assert leaks == []


def test_fps_validation_messages_are_in_feet():
    p, pi, A = fps_default()
    p.openings.door_count_per_floor = Estimate(value=200, confidence=ConfidenceLevel.HIGH, source=Source.USER_INPUT)
    p.walls.external_perimeter_m = Estimate(value=400 * FT, confidence=ConfidenceLevel.HIGH, source=Source.USER_INPUT)
    errors, _ = validate_params(p, pi, A)
    assert errors and all(not _metric_leaks(e) for e in errors)
    assert any("sqft" in e for e in errors) and any("400'-0\"" in e for e in errors)


def test_fps_display_amounts_reconcile_and_rates_are_per_cft():
    _, _, _, boq, cs = _fps_outputs()
    df = boq_to_dataframe(boq, "FPS")
    assert set(df["Unit"]) <= {"cft", "sqft", "kg", "Nos", "LS", "bags"}
    assert math.isclose((df["Qty (incl. Wastage)"] * df["Rate"]).sum() if "Qty (incl. Wastage)" in df else df["Amount"].sum(),
                        sum(b.amount for b in boq), rel_tol=1e-4)
    exc = next(b for b in boq if b.item_code == "EXC-01")
    assert math.isclose(units.display_rate(exc.rate, "m3", "FPS"), 1060 / (1 / CFT) , rel_tol=1e-9)  # ~PKR 30/cft
    grade = next(b for b in boq if b.item_code == "SLAB-CONC-01")
    assert "/cft for grade 3600 psi (rate book priced at 3000 psi)" in grade.remarks


def test_fps_excel_formulas_recompute_to_app_totals():
    p, pi, mto, boq, cs = _fps_outputs()
    wb = load_workbook(io.BytesIO(build_excel_workbook(pi, p, mto, boq, cs, preliminaries_pct=8)))
    ws = wb["BOQ"]
    total = 0.0
    for row in ws.iter_rows(min_row=3):
        code, qty, w, rate = row[0].value, row[4].value, row[5].value, row[7].value
        if isinstance(qty, (int, float)) and isinstance(rate, (int, float)):
            total += qty * (1 + w / 100) * rate
    non_prelim = sum(b.amount for b in boq if b.item_code != "PRELIM-01")
    assert math.isclose(total, non_prelim, rel_tol=1e-6)
    assert wb["MTO"]["D3"].value in {"cft", "sqft", "kg", "Nos", "bags", "LS"}


# --------------------------------------------------------------------------
# 4. SI unchanged in character
# --------------------------------------------------------------------------


def test_si_output_stays_metric():
    pi = ProjectInputs(unit_system="SI")
    p = default_building_params(pi.wall_thickness_mm, pi.wall_material, "SI")
    q = by_code(generate_mto(p, pi))
    assert "footing_length_m" in q["EXC-01"].inputs_used and "working_space_m" in q["EXC-01"].inputs_used
    assert q["PCC-01"].description.endswith("75mm thick")
    assert math.isclose(q["FTG-CONC-01"].quantity, 9 * 1.2 * 1.2 * 0.45, rel_tol=1e-3)


@pytest.mark.parametrize("us", ["SI", "FPS"])
def test_same_physical_building_costs_within_half_percent_across_systems(us):
    """Same plan in both systems; the only differences are each system's
    round standard details (6" vs 150 mm lintels, 3" vs 75 mm PCC, ...)."""
    p, _, _ = fps_default()
    pi_fps = ProjectInputs()
    pi_si = ProjectInputs(unit_system="SI", wall_thickness_mm=pi_fps.wall_thickness_mm,
                          plaster_thickness_internal_mm=pi_fps.plaster_thickness_internal_mm,
                          plaster_thickness_external_mm=pi_fps.plaster_thickness_external_mm)
    _, fps_total = generate_boq(generate_mto(p, pi_fps, EngineeringAssumptions.for_unit_system("FPS")), rate_book(), WastageFactors(), pi_fps)
    _, si_total = generate_boq(generate_mto(p, pi_si, EngineeringAssumptions.for_unit_system("SI")), rate_book(), WastageFactors(), pi_si)
    assert abs(fps_total.grand_total - si_total.grand_total) / si_total.grand_total < 0.005


def test_rate_editor_descriptions_use_fps_grades():
    from ui.components import _grade_labels_for_system

    assert _grade_labels_for_system("PCC (M10) bedding below footings", "FPS") == "PCC (1500 psi) bedding below footings"
    assert _grade_labels_for_system("PCC (M10) bedding below footings", "SI") == "PCC (M10) bedding below footings"
