"""
Schematic concept plan (SVG) for the sketch / requirements route, so the user can
see the rooms laid out on the plot before calculating. It is a simple zoned
arrangement (front: porch & drawing room; middle: lounge, stairs, kitchen; back:
bedrooms & baths) packed into the building width - NOT an architectural design.
"""
from __future__ import annotations

from html import escape
from typing import List

ZONE = {"Porch / car porch": 0, "Drawing room": 1, "Planter / lawn": 0, "Lounge / TV lounge": 2, "Staircase": 2,
        "Kitchen": 3, "Laundry": 3, "Store / utility": 3, "Servant quarter": 4, "Bedroom": 5, "Bathroom": 5,
        "Terrace / balcony": 0, "Mumty": 2}
FILL = {"Bedroom": "#DCEBFA", "Bathroom": "#D9F2EC", "Kitchen": "#FFF1CC", "Laundry": "#FFF1CC", "Drawing room": "#EDE3FA",
        "Lounge / TV lounge": "#EDE3FA", "Staircase": "#EEEEEE", "Porch / car porch": "#F5F5F5", "Terrace / balcony": "#F5F5F5",
        "Store / utility": "#FDE8E8", "Servant quarter": "#FDE8E8", "Mumty": "#EEEEEE", "Planter / lawn": "#E6F4E1"}


def floor_svg(rooms: List[dict], width_ft: float, title: str, px_per_ft: float = 9.0) -> str:
    """rooms: dicts with 'Room', 'Room type', 'Length (ft)', 'Width (ft)'. Returns an SVG string."""
    items = []
    for r in rooms:
        try:
            L, W = float(r.get("Length (ft)") or 0), float(r.get("Width (ft)") or 0)
        except (TypeError, ValueError):
            continue
        if L > 0 and W > 0:
            a, b = max(L, W), min(L, W)
            items.append((ZONE.get(r.get("Room type"), 3), -a * b, str(r.get("Room", "")), r.get("Room type", ""), a, b))
    items.sort()
    width_ft = max(width_ft, max((i[4] for i in items), default=10))
    x = y = row_h = 0.0
    placed = []
    for _z, _a, name, rt, a, b in items:
        w, h = (a, b) if a <= width_ft else (b, a)
        if x + w > width_ft + 0.01:
            x, y, row_h = 0.0, y + row_h, 0.0
        placed.append((x, y, w, h, name, rt))
        x += w
        row_h = max(row_h, h)
    depth = y + row_h
    s = px_per_ft
    pad = 20
    W, H = width_ft * s + 2 * pad, depth * s + 2 * pad + 22
    out = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W:.0f} {H:.0f}" width="100%" style="max-width:{W:.0f}px;'
           f'font-family:Arial,sans-serif">',
           f'<text x="{pad}" y="16" font-size="13" font-weight="bold" fill="#1F3864">{escape(title)}'
           f' ({width_ft:.0f} ft wide)</text>',
           f'<rect x="{pad}" y="{pad + 8}" width="{width_ft * s:.1f}" height="{depth * s:.1f}" fill="none" stroke="#1F3864" stroke-width="3"/>']
    for x0, y0, w, h, name, rt in placed:
        px, py = pad + x0 * s, pad + 8 + y0 * s
        out.append(f'<rect x="{px:.1f}" y="{py:.1f}" width="{w * s:.1f}" height="{h * s:.1f}" fill="{FILL.get(rt, "#FFFFFF")}" '
                   f'stroke="#44546A" stroke-width="1.2"/>')
        fs = max(7.0, min(11.0, w * s / max(len(name), 6) * 1.6))
        out.append(f'<text x="{px + w * s / 2:.1f}" y="{py + h * s / 2 - 2:.1f}" font-size="{fs:.1f}" text-anchor="middle" '
                   f'fill="#1F2937">{escape(name[:22])}</text>')
        out.append(f'<text x="{px + w * s / 2:.1f}" y="{py + h * s / 2 + fs + 1:.1f}" font-size="{max(fs - 1, 7):.1f}" '
                   f'text-anchor="middle" fill="#4B5563">{w:.1f}\' x {h:.1f}\'</text>'.replace(".0'", "'"))
    out.append("</svg>")
    return "".join(out)
