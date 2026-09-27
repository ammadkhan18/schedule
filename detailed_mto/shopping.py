"""
Shareable shopping list (quantities only): WhatsApp-ready text and a one-page-per-trade PDF.
Only materials to purchase in the selected scope are included, grouped by trade, in buy units.
"""
from __future__ import annotations

from datetime import datetime
from io import BytesIO
from typing import Dict, List

from detailed_mto.engine import DetailedResult, MaterialLine, purchase_qty


def _groups(res: DetailedResult) -> Dict[str, List[MaterialLine]]:
    out: Dict[str, List[MaterialLine]] = {}
    for m in res.purchase_list():
        out.setdefault(m.material.category, []).append(m)
    return out


def _qty_text(m: MaterialLine) -> str:
    q, unit = purchase_qty(m.material, m.gross_qty)
    if unit == "lump sum":
        return "lump sum"
    return (f"{q:,.2f}" if q != int(q) else f"{int(q):,}") + f" {unit}"


def shopping_text(res: DetailedResult, trades: List[str] = None, max_lines: int = 400) -> str:
    """Plain text for WhatsApp / SMS / email (WhatsApp renders *bold*)."""
    p = res.project
    lines = [f"*{p.project_name} - material list*", f"Scope: {res.scope} | {datetime.now():%d %b %Y}",
             "Quantities include wastage, rounded up to how they are sold. No prices.", ""]
    n = 0
    for cat, items in _groups(res).items():
        if trades and cat not in trades:
            continue
        lines.append(f"*{cat}*")
        for m in items:
            flag = " (check)" if m.confidence in ("Assumed", "Low") and m.material.unit.upper() != "LS" else ""
            lines.append(f"- {m.material.description}: {_qty_text(m)}{flag}")
            n += 1
            if n >= max_lines:
                lines.append("... (list shortened - see the Excel file)")
                return "\n".join(lines)
        lines.append("")
    if p.drawing_mode in ("scanned", "none", "sketch"):
        lines.append("Note: concept estimate - not based on full architectural drawings.")
    lines.append("(check) = based on default values, confirm before ordering.")
    return "\n".join(lines).strip()


def shopping_pdf(res: DetailedResult) -> bytes:
    """A4 PDF shopping list grouped by trade (reportlab, already a dependency of the app)."""
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    buf = BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=14 * mm, rightMargin=14 * mm, topMargin=14 * mm, bottomMargin=14 * mm,
                            title=f"{res.project.project_name} - material list")
    ss = getSampleStyleSheet()
    small = ss["BodyText"].clone("small", fontSize=8, leading=10)
    story = [Paragraph(f"<b>{res.project.project_name}</b> - Material shopping list", ss["Title"]),
             Paragraph(f"Scope: {res.scope} &nbsp;|&nbsp; Finish: {res.project.options.finish_tier} &nbsp;|&nbsp; "
                       f"{datetime.now():%d %b %Y} &nbsp;|&nbsp; Quantities only - no prices", small)]
    if res.project.drawing_mode in ("scanned", "none", "sketch"):
        story.append(Paragraph("<font color='#9c5700'><b>Concept estimate</b> - not based on full architectural drawings.</font>", small))
    story.append(Spacer(1, 4 * mm))
    for cat, items in _groups(res).items():
        story.append(Paragraph(f"<b>{cat}</b> ({len(items)} items)", ss["Heading4"]))
        rows = [["#", "Material", "Specification", "Buy", "Note"]]
        for i, m in enumerate(items, 1):
            note = "check" if m.confidence in ("Assumed", "Low") and m.material.unit.upper() != "LS" else ""
            rows.append([str(i), Paragraph(m.material.description, small), Paragraph(m.material.specification[:90], small),
                         Paragraph(f"<b>{_qty_text(m)}</b>", small), note])
        t = Table(rows, colWidths=[8 * mm, 62 * mm, 60 * mm, 38 * mm, 14 * mm], repeatRows=1)
        t.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1F3864")), ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("FONTSIZE", (0, 0), (-1, -1), 8), ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#D0D7E2")),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#F3F6FA")]),
            ("VALIGN", (0, 0), (-1, -1), "TOP"), ("TEXTCOLOR", (4, 1), (4, -1), colors.HexColor("#C65911")),
        ]))
        story += [t, Spacer(1, 3 * mm)]
    story.append(Paragraph("Quantities include normal wastage and are rounded up to purchase units. 'check' = based on default "
                           "values - confirm before ordering. Preliminary take-off, not a certified QS takeoff.", small))
    doc.build(story)
    return buf.getvalue()
