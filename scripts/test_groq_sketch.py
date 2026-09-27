"""
Live test of the AI sketch route against the real Groq API (run on your own machine).

    set GROQ_API_KEY=gsk_...        (Windows)   |   export GROQ_API_KEY=gsk_...   (Mac/Linux)
    python scripts/test_groq_sketch.py [optional/path/to/your_sketch.jpg]

Without a file it draws a hand-sketch-like test plan (rooms with sizes written on them),
sends it to the vision model, merges the reading into a brief, asks the follow-up
questions, and runs the full material take-off. Prints every stage.
"""
from __future__ import annotations

import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PIL import Image, ImageDraw, ImageFont  # noqa: E402


def make_test_sketch() -> Image.Image:
    img = Image.new("RGB", (1400, 1000), "white")
    d = ImageDraw.Draw(img)
    try:
        font = ImageFont.truetype("DejaVuSans.ttf", 26)
        big = ImageFont.truetype("DejaVuSans.ttf", 34)
    except OSError:
        font = big = ImageFont.load_default()
    d.text((40, 20), "5 MARLA HOUSE  33' x 41'  -  GROUND FLOOR (first floor: 2 bed + bath, TV lounge)", fill="black", font=big)
    rooms = [((60, 90, 420, 420), "CAR PORCH", "12'x17'"), ((420, 90, 760, 330), "DRAWING", "13'x11'"),
             ((760, 90, 1300, 330), "LOUNGE", "18'x11'"), ((420, 330, 760, 600), "BED ROOM", "12'x13'"),
             ((760, 330, 960, 600), "BATH", "5'x8'"), ((960, 330, 1300, 600), "KITCHEN", "10'x9'"),
             ((60, 420, 420, 700), "STAIR", "12'x9'"), ((420, 600, 1300, 900), "BED ROOM 2", "12'x14'")]
    for (x0, y0, x1, y1), name, size in rooms:
        d.rectangle((x0, y0, x1, y1), outline="black", width=5)
        d.text((x0 + 20, y0 + 20), name, fill="black", font=font)
        d.text((x0 + 20, y0 + 60), size, fill="blue", font=font)
    return img


def main() -> int:
    key = os.environ.get("GROQ_API_KEY", "").strip()
    if not key:
        print("Set the GROQ_API_KEY environment variable first.")
        return 1
    from ai.sketch_reader import read_sketch
    from detailed_mto.brief import ProjectBrief, apply_ai_result, brief_summary, brief_to_inputs, complete_programme
    from detailed_mto import build_project, compute
    from detailed_mto.edits import rows_to_openings, rows_to_rooms
    from engineering import plot_templates
    from knowledge import load_knowledge_base
    from models.schemas import ProjectInputs

    img = Image.open(sys.argv[1]).convert("RGB") if len(sys.argv) > 1 else make_test_sketch()
    if len(sys.argv) == 1:
        img.save("test_sketch.png")
        print("Test sketch saved as test_sketch.png")
    desc = "Double storey, load bearing, municipal sewer, ACs in bedrooms."

    t = time.time()
    data, err = read_sketch(key, [img], desc)
    print(f"\n1) Vision model reading ({time.time() - t:.1f}s):", err or "")
    print(json.dumps(data, indent=2)[:3000])
    if err:
        return 2

    b = ProjectBrief(marla=5, marla_sqft=272.25)
    print("\n2) Understood:", apply_ai_result(data, b))
    print("   Added:", complete_programme(b))
    for r in b.rooms:
        print(f"   {r.floor:7s} {r.name:22s} {r.room_type:20s} {r.length_ft:5.1f} x {r.width_ft:5.1f}  [{r.source}]")
    print("   AI questions:", b.ai_questions)

    if b.ai_questions:
        answers = [(q, "Yes" if i == 0 else "") for i, q in enumerate(b.ai_questions)]
        t = time.time()
        data2, err2 = read_sketch(key, [img], desc, previous=data, answers=answers)
        print(f"\n3) Follow-up with answers ({time.time() - t:.1f}s):", err2 or "ok",
              "- rooms now:", sum(len(f.get("rooms") or []) for f in data2.get("floors") or []))

    inp = brief_to_inputs(b)
    pi = ProjectInputs(project_name="Groq live test", plot_marla=b.marla, marla_sqft=b.marla_sqft, plot_storeys=b.storeys)
    params = plot_templates.build_template_params(pi)
    params.footings.footing_type = inp["structure"]
    kb = load_knowledge_base()
    p = build_project(pi, params, kb, rooms_override=rows_to_rooms(inp["rooms"]), openings_override=rows_to_openings(inp["openings"]),
                      overrides=inp["overrides"], drawing_mode="sketch", floors_override=inp["floors"])
    res = compute(p, kb)
    g = lambda ids: sum(m.gross_qty for m in res.materials if m.material.mat_id in ids)  # noqa: E731
    print("\n4) Brief:", *[f"{k}: {v}" for k, v in brief_summary(b)], sep="\n   ")
    print(f"\n5) Take-off: covered {sum(f.covered_sft for f in p.floors):,.0f} sft | cement {g({'CON-001'}):,.0f} bags | "
          f"steel {g({'RBR-001', 'RBR-002', 'RBR-003', 'RBR-004'}) / 1000:,.2f} t | bricks {g({'MAS-001', 'MAS-002'}):,.0f}")
    print("   Status:", res.status_counts())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
