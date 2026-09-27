"""
Generates sample_data/sample_5_marla_package.pdf - a CAD-style drawing set
for a 5 marla (25'x45') G+1 house, drawn at 1/8"=1'-0" on A3 sheets:
  p1  Ground + First floor plans (double-line walls, door swings, room
      labels with sizes, plot/covered-area notes)
  p2  Section A-A (level marks, slab note) + Front elevation
  p3  Foundation plan (F1 tags) with footing & column schedules
Used by the tests and handy for demoing the app. Run:
    python sample_data/make_sample_package.py
"""
import os

from reportlab.lib.pagesizes import A3, landscape
from reportlab.pdfgen import canvas

PT_PER_FT = 9.0  # 1/8" = 1'-0"  ->  1 ft = 0.125 in = 9 pt
EXT_T, INT_T = 9 / 12, 4.5 / 12  # wall thickness in ft

# Truth used by tests
TRUTH = {
    "ground": {"W": 25.0, "D": 37.0, "rooms": 6, "baths": 2, "kitchens": 1, "doors": 6},
    "first": {"W": 25.0, "D": 34.0, "rooms": 6, "baths": 2, "kitchens": 1, "doors": 6},
    "floor_height_ft": 10.5, "plinth_ft": 1.5, "parapet_ft": 3.0, "founding_ft": 5.0, "slab_in": 5.0,
    "footings": 12, "footing_ft": 5.0, "footing_t_ft": 1.5, "column_in": (9.0, 18.0),
}


class Sheet:
    def __init__(self, c, ox, oy):
        self.c, self.ox, self.oy = c, ox, oy

    def P(self, x, y):
        return self.ox + x * PT_PER_FT, self.oy + y * PT_PER_FT

    def line(self, x0, y0, x1, y1):
        a, b = self.P(x0, y0), self.P(x1, y1)
        self.c.line(a[0], a[1], b[0], b[1])

    def hwall(self, y, x0, x1, t, gaps=()):
        """Horizontal wall, centreline y, from x0 to x1, with openings."""
        cuts = sorted(gaps)
        pieces, cur = [], x0
        for g0, g1 in cuts:
            pieces.append((cur, g0))
            cur = g1
        pieces.append((cur, x1))
        for a, b in pieces:
            if b - a > 0.05:
                self.line(a, y - t / 2, b, y - t / 2)
                self.line(a, y + t / 2, b, y + t / 2)

    def vwall(self, x, y0, y1, t, gaps=()):
        cuts = sorted(gaps)
        pieces, cur = [], y0
        for g0, g1 in cuts:
            pieces.append((cur, g0))
            cur = g1
        pieces.append((cur, y1))
        for a, b in pieces:
            if b - a > 0.05:
                self.line(x - t / 2, a, x - t / 2, b)
                self.line(x + t / 2, a, x + t / 2, b)

    def door(self, x, y, r=3.0):
        cx, cy = self.P(x, y)
        R = r * PT_PER_FT
        self.c.arc(cx - R, cy - R, cx + R, cy + R, 0, 90)

    def text(self, x, y, s, size=7):
        px, py = self.P(x, y)
        self.c.setFont("Helvetica", size)
        self.c.drawCentredString(px, py, s)


def plan(sh, D, first=False):
    W = 25.0
    e = EXT_T / 2
    # external walls (centrelines inset by half thickness), window gaps
    sh.hwall(e, 0, W, EXT_T, gaps=[(4, 7.5)])            # front, main door gap
    sh.hwall(D - e, 0, W, EXT_T, gaps=[(9, 13)])         # rear window
    sh.vwall(e, 0, D, EXT_T, gaps=[(20, 24)])
    sh.vwall(W - e, 0, D, EXT_T, gaps=[(8, 12)])
    # internal walls
    sh.vwall(12.5, EXT_T, D - EXT_T, INT_T, gaps=[(5, 8), (26, 29)])
    sh.hwall(14, EXT_T, 12.5, INT_T, gaps=[(8, 11)])
    sh.hwall(14, 12.5, W - EXT_T, INT_T, gaps=[(15, 18)])
    sh.hwall(24, EXT_T, 12.5, INT_T, gaps=[(3, 6)])
    sh.hwall(24, 12.5, W - EXT_T, INT_T, gaps=[(20, 23)])
    sh.vwall(6.5, 24, D - EXT_T, INT_T, gaps=[(27, 30)])
    for (dx, dy) in [(4, 0.4), (12.3, 5), (8, 13.8), (15, 13.8), (3, 23.8), (20, 23.8)]:
        sh.door(dx, dy)
    rooms = [
        ("DRAWING ROOM" if not first else "LOUNGE", "11'-9\" x 13'-3\"", 6.3, 7),
        ("BED ROOM" if not first else "MASTER BED", "11'-9\" x 13'-3\"", 18.8, 7),
        ("KITCHEN" if not first else "KITCHEN", "11'-6\" x 9'-6\"", 6.3, 19),
        ("TV LOUNGE" if not first else "BED ROOM", "11'-6\" x 9'-6\"", 18.8, 19),
        ("BATH", "5'-3\" x 9'-0\"" if not first else "5'-3\" x 6'-0\"", 3.3, 28 if not first else 27),
        ("TOILET" if not first else "BATH", "5'-3\" x 9'-0\"" if not first else "5'-3\" x 6'-0\"", 9.5, 28 if not first else 27),
    ]
    for name, dims, x, y in rooms:
        sh.text(x, y + 0.8, name)
        sh.text(x, y - 0.6, dims)


def make(path, pt_per_ft=9.0, scale_note="SCALE: 1/8\"=1'-0\""):
    """pt_per_ft=9 -> 1/8"=1'-0"; 18 -> 1/4"=1'-0". scale_note=None omits it."""
    global PT_PER_FT
    PT_PER_FT = pt_per_ft
    c = canvas.Canvas(path, pagesize=landscape(A3))
    Wp, Hp = landscape(A3)
    # ---- page 1: plans
    gf, ff = Sheet(c, 60, 120), Sheet(c, 60 + 30 * PT_PER_FT, 120)
    plan(gf, 37.0)
    plan(ff, 34.0, first=True)
    gf.text(12.5, -4, "GROUND FLOOR PLAN", 12)
    ff.text(12.5, -4, "FIRST FLOOR PLAN", 12)
    c.setFont("Helvetica", 9)
    for i, s in enumerate([
        scale_note or "",
        "PLOT SIZE: 25'-0\" X 45'-0\" (5 MARLA)",
        "GROUND FLOOR COVERED AREA = 925 SFT",
        "FIRST FLOOR COVERED AREA = 850 SFT",
        "PROPOSED RESIDENCE - HOUSE NO. 12, BLOCK C",
    ]):
        c.drawString(900, 160 - i * 14, s)
    c.showPage()
    # ---- page 2: section + elevation
    sec = Sheet(c, 150, 150)
    for y in [0, 1.5, 12.0, 22.5, 25.5]:
        sec.line(-2, y, 27, y)
    sec.line(0, -5, 0, 25.5)
    sec.line(25, -5, 25, 25.5)
    for lvl, y in [("+25'-6\" PARAPET TOP", 25.5), ("+22'-6\" ROOF LEVEL", 22.5), ("+12'-0\" FIRST FLOOR LEVEL", 12.0),
                   ("+1'-6\" F.F.L / PLINTH", 1.5), ("±0'-0\" N.G.L", 0), ("-5'-0\" FOUNDATION LEVEL", -5)]:
        sec.text(34, y, lvl, 7)
    sec.text(12.5, 17, "5\" THK R.C.C SLAB", 7)
    sec.text(12.5, -9, "SECTION A-A", 12)
    ele = Sheet(c, 700, 150)
    for y in [0, 12.0, 22.5, 25.5]:
        ele.line(0, y, 25, y)
    ele.line(0, 0, 0, 25.5)
    ele.line(25, 0, 25, 25.5)
    ele.text(12.5, -9, "FRONT ELEVATION", 12)
    c.setFont("Helvetica", 9)
    c.drawString(900, 60, scale_note or "")
    c.showPage()
    # ---- page 3: foundation plan + schedules
    fnd = Sheet(c, 150, 200)
    xs, ys = [0.75, 12.5, 24.25], [0.75, 12.5, 24.5, 36.25]
    for x in xs:
        for y in ys:
            fnd.text(x, y, "F1", 8)
            fnd.text(x, y - 1.5, "C1", 7)
    fnd.text(12.5, -5, "FOUNDATION PLAN", 12)
    c.setFont("Helvetica", 9)
    c.drawString(700, 500, "FOOTING SCHEDULE")
    c.drawString(700, 485, "F1  5'-0\" x 5'-0\" x 1'-6\"  (12 NOS)")
    c.drawString(700, 450, "COLUMN SCHEDULE")
    c.drawString(700, 435, "C1  9\" x 18\"  4 BARS #5 + #3 TIES @ 6\" C/C")
    c.drawString(700, 400, scale_note or "")
    c.showPage()
    c.save()


if __name__ == "__main__":
    out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "sample_5_marla_package.pdf")
    make(out)
    print("written", out)
