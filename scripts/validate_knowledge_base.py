"""
Validate the Master Material Database after editing it in Excel.

    python scripts/validate_knowledge_base.py [path/to/master_material_database.xlsx]

Checks that every formula evaluates, every recipe points to an existing
material / work item / coefficient, every wastage key exists, and that every
work item has a calculator in detailed_mto/quantities.py. Exit code 1 on error.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from detailed_mto import quantities  # noqa: E402
from knowledge.loader import DEFAULT_DB_PATH, load_knowledge_base  # noqa: E402


def main() -> int:
    path = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_DB_PATH
    try:
        kb = load_knowledge_base(path)
    except Exception as exc:
        print(f"FAILED to load {path}:\n{exc}")
        return 1
    problems = []
    missing_calc = [w for w in kb.work_items if w not in quantities.REGISTRY]
    if missing_calc:
        problems.append("Work items without a calculator (will show 'Needs input'): " + ", ".join(missing_calc))
    unused = [m for m in kb.materials.values() if m.formula_key == "RECIPE" and not any(r.mat_id == m.mat_id for r in kb.recipes)]
    if unused:
        problems.append("RECIPE materials with no recipe line: " + ", ".join(m.mat_id for m in unused))
    print(f"Loaded {path}")
    print(f"  materials={len(kb.materials)} work_items={len(kb.work_items)} recipes={len(kb.recipes)} "
          f"coefficients={len(kb.coefficients)} mixes={len(kb.mixes)} room_defaults={len(kb.room_defaults)}")
    print(f"  bricks/cft={kb.k('K_BRK_PER_CFT'):.3f}  RCC 1:2:4 cement bags/cft={kb.mixes['MX_RCC124'].values['J']:.4f}")
    for p in problems:
        print("WARNING:", p)
    print("OK" if not problems else "OK with warnings")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
