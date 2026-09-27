"""
Default productivity norms for 5-10 marla houses in Pakistan.

Each value is the OUTPUT OF ONE GANG IN ONE WORKING DAY (8 h), in the BOQ
work-item unit, for typical private-contractor practice (manual excavation,
site-mixed concrete with a 1-bag mixer and vibrator, hired steel shuttering).
They are planning figures, not guarantees - the user can edit every one of
them in the Schedule tab.

Gang = the crew that does the work end to end, e.g. a brick-masonry gang is
2 masons + 4 labourers; a steel-fixing gang is 2 fixers + 2 helpers.
"""
from __future__ import annotations

from typing import Dict, Tuple

# wi_id -> (output per gang-day, gang composition / basis)
DEFAULT_RATES: Dict[str, Tuple[float, str]] = {
    # Earthwork
    "WI-EW-01": (400, "4 labourers (~100 cft each, manual)"),
    "WI-EW-02": (400, "4 labourers (~100 cft each, manual)"),
    "WI-EW-03": (300, "4 labourers, deeper/narrow pits"),
    "WI-EW-04": (500, "4 labourers + rammer, 6in layers"),
    "WI-EW-05": (500, "4 labourers + rammer, 6in layers"),
    "WI-EW-06": (500, "4 labourers"),
    "WI-EW-07": (2000, "1 applicator + helper"),
    "WI-EW-08": (2000, "2 labourers"),
    "WI-EW-09": (1000, "tractor-trolley + 3 labourers"),
    # Concrete (site-mixed, 1-bag mixer + vibrator, ~10-12 labour)
    "WI-CN-01": (250, "mixer gang (1 mason + 10 labour)"),
    "WI-CN-02": (250, "mixer gang (1 mason + 10 labour)"),
    "WI-CN-03": (300, "mixer gang + vibrator"),
    "WI-CN-04": (200, "mixer gang + vibrator"),
    "WI-CN-05": (150, "mixer gang, column lifts"),
    "WI-CN-06": (400, "mixer gang, poured with slab"),
    "WI-CN-07": (400, "mixer gang, one-day slab pour"),
    "WI-CN-08": (150, "mixer gang"),
    "WI-CN-09": (100, "2 masons + 4 labour, small pours"),
    "WI-CN-10": (60, "2 masons + 4 labour, small pours"),
    "WI-CN-11": (150, "mixer gang"),
    "WI-CN-12": (400, "2 masons + 4 labour"),
    "WI-CN-13": (400, "2 masons + 4 labour"),
    # Reinforcement (2 steel fixers + 2 helpers)
    "WI-RF-01": (400, "2 fixers + 2 helpers"),
    "WI-RF-02": (300, "2 fixers + 2 helpers (column cages)"),
    "WI-RF-03": (350, "2 fixers + 2 helpers"),
    "WI-RF-04": (450, "2 fixers + 2 helpers (slab mesh)"),
    "WI-RF-05": (250, "2 fixers + 2 helpers"),
    "WI-RF-06": (250, "2 fixers + 2 helpers"),
    "WI-RF-07": (250, "2 fixers + 2 helpers"),
    # Formwork
    "WI-FW-01": (400, "shuttering fundi + 4 helpers (steel plates)"),
    "WI-FW-02": (250, "2 carpenters + 2 helpers (ply/timber)"),
    # Masonry (2 masons + 4 labourers)
    "WI-MS-01": (160, "2 masons + 4 labour"),
    "WI-MS-02": (160, "2 masons + 4 labour (~1,000 bricks/mason-day)"),
    "WI-MS-03": (300, "2 masons + 4 labour"),
    "WI-MS-04": (120, "2 masons + 4 labour, at height"),
    "WI-MS-05": (600, "1 mason + 4 labour"),
    "WI-MS-06": (250, "2 masons + 4 labour"),
    # Plaster (2 masons + 3 labourers)
    "WI-PL-01": (220, "2 masons + 3 labour"),
    "WI-PL-02": (160, "2 masons + 3 labour, on scaffold"),
    "WI-PL-03": (150, "2 masons + 3 labour, overhead"),
    "WI-PL-04": (150, "2 masons + 3 labour"),
    # Waterproofing / roof
    "WI-WP-01": (500, "roof gang (1 mistri + 6 labour)"),
    "WI-WP-02": (500, "roof gang"),
    "WI-WP-03": (300, "applicator + 2 helpers"),
    "WI-WP-04": (400, "2 masons + 4 labour"),
    # Flooring & tiling (2 tile fixers + 2 helpers)
    "WI-FL-01": (180, "2 tile fixers + 2 helpers"),
    "WI-FL-02": (180, "2 tile fixers + 2 helpers"),
    "WI-FL-03": (120, "2 tile fixers + 2 helpers"),
    "WI-FL-04": (120, "2 marble fixers + 2 helpers"),
    "WI-FL-05": (200, "2 tile fixers + 2 helpers"),
    "WI-FL-06": (40, "2 marble fixers + 2 helpers"),
    "WI-FL-07": (250, "2 fixers + 3 helpers"),
    "WI-FL-08": (60, "2 marble fixers + 2 helpers"),
    # Doors, windows, metalwork
    "WI-DW-01": (120, "carpenter + helper (~8 frames/day)"),
    "WI-DW-02": (100, "2 carpenters (~5 shutters/day)"),
    "WI-DW-03": (8, "carpenter"),
    "WI-DW-04": (100, "aluminium/uPVC fabricator team"),
    "WI-DW-05": (150, "welder + helper"),
    "WI-DW-06": (30, "welder + helper"),
    "WI-DW-07": (70, "welder + helper"),
    # Ceiling / paint (3 painters)
    "WI-CL-01": (200, "2 gypsum fixers + helper"),
    "WI-PT-01": (350, "3 painters (putty, primer, 2 coats)"),
    "WI-PT-02": (300, "3 painters, overhead"),
    "WI-PT-03": (350, "3 painters on scaffold"),
    "WI-PT-04": (250, "2 painters"),
    "WI-PT-05": (120, "2 polish men"),
    # Electrical (1 electrician + 1 helper)
    "WI-EL-01": (8, "electrician + helper"),
    "WI-EL-02": (8, "electrician + helper"),
    "WI-EL-03": (8, "electrician + helper"),
    "WI-EL-04": (6, "electrician + helper"),
    "WI-EL-05": (4, "electrician + helper"),
    "WI-EL-06": (8, "electrician + helper"),
    "WI-EL-07": (60, "electrician + helper"),
    "WI-EL-08": (1, "electrician + helper"),
    "WI-EL-09": (1, "electrician + 2 labour"),
    "WI-EL-10": (20, "electrician + helper"),
    # HVAC
    "WI-HV-01": (2, "AC technician + helper"),
    "WI-HV-02": (6, "electrician + helper"),
    # Plumbing (1 plumber + 1 helper)
    "WI-PB-01": (3, "plumber + helper"),
    "WI-PB-02": (4, "plumber + helper"),
    "WI-PB-03": (4, "plumber + helper"),
    "WI-PB-04": (8, "plumber + helper"),
    "WI-PB-05": (6, "plumber + helper"),
    "WI-PB-06": (0.5, "plumber + helper (~2 days per bathroom)"),
    "WI-PB-07": (2, "plumber + helper"),
    "WI-PB-08": (4, "plumber + helper"),
    "WI-PB-09": (60, "plumber + helper"),
    "WI-PB-10": (4, "plumber + helper"),
    "WI-PB-11": (40, "plumber + 2 labour"),
    "WI-PB-12": (0.5, "mason + 2 labour (~2 days each)"),
    "WI-PB-13": (2, "mason + labour"),
    "WI-GS-01": (3, "gas fitter + helper"),
    # Kitchen / joinery
    "WI-KT-01": (8, "2 carpenters"),
    "WI-KT-02": (8, "2 carpenters"),
    "WI-KT-03": (16, "stone fixer + helper"),
    "WI-JN-01": (40, "2 carpenters"),
    # External
    "WI-EX-01": (15, "2 masons + 4 labour, incl. foundation"),
    "WI-EX-02": (0.2, "boring + mason (~5 days)"),
}

# The same work item can be priced at a different rate inside a particular activity:
# electrical points are first chased & conduited (rough-in), later wired and fitted.
ACTIVITY_RATES: Dict[Tuple[str, str], Tuple[float, str]] = {
    ("ELR", "WI-EL-01"): (10, "chasing, conduit & boxes"),
    ("ELR", "WI-EL-02"): (10, "chasing, conduit & boxes"),
    ("ELR", "WI-EL-03"): (10, "chasing, conduit & boxes"),
    ("ELR", "WI-EL-04"): (8, "chasing, conduit & boxes"),
    ("ELR", "WI-EL-05"): (6, "chasing, conduit & boxes"),
    ("ELR", "WI-EL-06"): (10, "chasing, conduit & boxes"),
    ("ELF", "WI-EL-01"): (15, "wire pulling, switches & sockets"),
    ("ELF", "WI-EL-02"): (15, "wire pulling, regulators"),
    ("ELF", "WI-EL-03"): (15, "wire pulling, sockets"),
    ("ELF", "WI-EL-04"): (12, "wire pulling, sockets"),
    ("ELF", "WI-EL-05"): (8, "wire pulling, isolators"),
    ("ELF", "WI-EL-06"): (15, "cabling & outlets"),
}

FALLBACK_RATE = (1.0, "no norm - edit")


def default_rate(template: str, wi_id: str) -> Tuple[float, str]:
    return ACTIVITY_RATES.get((template, wi_id)) or DEFAULT_RATES.get(wi_id) or FALLBACK_RATE
