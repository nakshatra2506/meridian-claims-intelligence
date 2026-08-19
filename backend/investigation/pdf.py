"""
Case report as PDF.

WHY PDF AND NOT JUST MARKDOWN
A case file needs a fixed artifact - something that renders the same for
whoever opens it later, and that can be attached to a referral without a
toolchain. Markdown is kept alongside for anyone who wants to edit or diff it.

WHY REPORTLAB
It draws directly to PDF with no browser, no headless Chrome, and no system
libraries - which matters because this has to run on an investigator's laptop
and inside CI without either of those being installed.

The structure mirrors the Markdown report exactly, including the sections that
are uncomfortable: what was NOT examined, and what could explain the findings
legitimately. Fixing the structure means those cannot be dropped when the
findings look damning, which is precisely when they matter.
"""

from __future__ import annotations

from datetime import datetime, timezone
from io import BytesIO
from typing import Any

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
    HRFlowable, PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table,
    TableStyle,
)

# Same palette as the dashboard, so a printed case reads like the screen one.
NAVY = colors.HexColor("#171B34")
INK = colors.HexColor("#3D4359")
MUTED = colors.HexColor("#6B7280")
LINE = colors.HexColor("#E7E9F0")
CRIT = colors.HexColor("#DC2626")
BAND = colors.HexColor("#F7F8FB")


def _styles():
    s = getSampleStyleSheet()
    return {
        "title": ParagraphStyle("t", parent=s["Title"], fontName="Helvetica-Bold",
                                fontSize=17, leading=21, textColor=NAVY,
                                alignment=TA_LEFT, spaceAfter=2),
        "sub": ParagraphStyle("s", parent=s["Normal"], fontName="Helvetica",
                              fontSize=8.5, textColor=MUTED, spaceAfter=12),
        "h2": ParagraphStyle("h2", parent=s["Heading2"], fontName="Helvetica-Bold",
                             fontSize=11.5, textColor=NAVY, spaceBefore=14,
                             spaceAfter=5),
        "h3": ParagraphStyle("h3", parent=s["Heading3"], fontName="Helvetica-Bold",
                             fontSize=9.5, textColor=INK, spaceBefore=8,
                             spaceAfter=3),
        "body": ParagraphStyle("b", parent=s["Normal"], fontName="Helvetica",
                               fontSize=9.5, leading=14, textColor=INK,
                               spaceAfter=5),
        "small": ParagraphStyle("sm", parent=s["Normal"], fontName="Helvetica",
                                fontSize=8.5, leading=12, textColor=MUTED,
                                spaceAfter=4),
        "score": ParagraphStyle("sc", parent=s["Normal"],
                                fontName="Helvetica-Bold", fontSize=26,
                                leading=28, textColor=CRIT),
    }


def _table(rows: list[list[str]], widths: list[float], header: bool = True):
    t = Table(rows, colWidths=widths, hAlign="LEFT")
    style = [
        ("FONTNAME", (0, 0), (-1, -1), "Helvetica"),
        ("FONTSIZE", (0, 0), (-1, -1), 8.5),
        ("TEXTCOLOR", (0, 0), (-1, -1), INK),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LINEBELOW", (0, 0), (-1, -2), 0.4, LINE),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
    ]
    if header:
        style += [
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTSIZE", (0, 0), (-1, 0), 7.5),
            ("TEXTCOLOR", (0, 0), (-1, 0), MUTED),
            ("LINEBELOW", (0, 0), (-1, 0), 0.8, LINE),
        ]
    t.setStyle(TableStyle(style))
    return t


def render_pdf(case: dict[str, Any]) -> bytes:
    S = _styles()
    buf = BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=A4, leftMargin=18 * mm, rightMargin=18 * mm,
        topMargin=16 * mm, bottomMargin=16 * mm,
        title=f"Investigation case {case.get('identity', {}).get('entity_id', '')}",
    )
    F: list = []
    ent = case.get("entity_type", "entity")
    ident = case.get("identity") or {}
    eid = ident.get("entity_id", "unknown")

    F.append(Paragraph(f"Investigation case — {ent} {eid}", S["title"]))
    F.append(Paragraph(
        datetime.now(timezone.utc).strftime("Generated %d %B %Y, %H:%M UTC"),
        S["sub"]))
    F.append(HRFlowable(width="100%", color=LINE, spaceAfter=10))

    if not case.get("found"):
        F.append(Paragraph(case.get("message", "Not found."), S["body"]))
        doc.build(F)
        return buf.getvalue()

    # ---- subject ----
    F.append(Paragraph("Subject", S["h2"]))
    rows = [[k.replace("_", " ").title(), str(v)]
            for k, v in ident.items() if k != "entity_id" and v is not None]
    rows.append(["Identifier", str(eid)])
    F.append(_table([["Field", "Value"]] + rows, [55 * mm, 105 * mm]))

    # ---- risk ----
    risk = case.get("risk") or {}
    F.append(Paragraph("Risk assessment", S["h2"]))
    if risk.get("score") is None:
        F.append(Paragraph(risk.get("message", "No model output available."),
                           S["body"]))
    else:
        F.append(Paragraph(f"{risk['score']} / 100", S["score"]))
        label = risk.get("score_label", "Risk score")
        lvl = f" — {risk['level']}" if risk.get("level") else ""
        F.append(Paragraph(f"{label}{lvl}", S["small"]))
        meta = [(k, risk.get(v)) for k, v in
                (("Priority", "priority"), ("Peer group", "peer_group"),
                 ("Period scored", "period")) if risk.get(v)]
        if meta:
            F.append(Spacer(1, 4))
            F.append(_table([["Field", "Value"]] + [[k, str(v)] for k, v in meta],
                            [55 * mm, 105 * mm]))
        comps = risk.get("components") or []
        if comps:
            F.append(Paragraph("Score components", S["h3"]))
            crows = [["Component", "Value"]]
            for c in comps:
                nm = c["name"]
                if c.get("is_provider_model") and "provider risk model" not in nm.lower():
                    nm += " (provider risk model)"
                crows.append([nm, "not run" if c.get("not_run")
                              else str(c["value"])])
            F.append(_table(crows, [110 * mm, 50 * mm]))
            if any(c.get("not_run") for c in comps):
                F.append(Paragraph(
                    "Components marked <b>not run</b> were not evaluated for "
                    "this case. That is not the same as being evaluated and "
                    "found clean.", S["small"]))

    # ---- measured activity ----
    metrics = case.get("metrics") or []
    if metrics:
        F.append(Paragraph("Measured activity", S["h2"]))
        F.append(_table([["Measure", "Value"]] +
                        [[m["label"], str(m["value"])] for m in metrics],
                        [95 * mm, 65 * mm]))

    # ---- peer comparison ----
    peers = case.get("peer_comparison") or []
    if peers:
        F.append(Paragraph("Peer comparison", S["h2"]))
        F.append(_table(
            [["Metric", "Provider", "Peer median", "Ratio", "Percentile"]] +
            [[p["metric"], str(p.get("provider") or "—"),
              str(p.get("peer_median") or "—"), str(p.get("ratio") or "—"),
              str(p.get("percentile") or "—")] for p in peers],
            [58 * mm, 27 * mm, 27 * mm, 22 * mm, 26 * mm]))
        F.append(Paragraph(
            "Ratio measures magnitude; percentile measures rarity. A high "
            "percentile with a small ratio means the peer distribution is "
            "tight, not that the provider is far from normal.", S["small"]))

    # ---- findings ----
    agents = case.get("agents") or []
    active = [a for a in agents if a.get("status") != "skipped"]
    if active:
        F.append(Paragraph("Findings", S["h2"]))
        for a in active:
            F.append(Paragraph(a["name"], S["h3"]))
            for f in a.get("findings") or []:
                F.append(Paragraph(
                    f"<b>{f['title']}</b> — {f.get('detail', '')}", S["body"]))
                for e in f.get("evidence") or []:
                    F.append(Paragraph(f"• {e}", S["small"]))

    # ---- assessment ----
    ex = case.get("explanation") or {}
    if ex.get("text"):
        F.append(Paragraph("Assessment", S["h2"]))
        for para in [p for p in ex["text"].split("\n") if p.strip()]:
            clean = para.lstrip("#").replace("**", "").strip()
            if clean.startswith(("-", "*", "•")):
                F.append(Paragraph("• " + clean.lstrip("-*• "), S["body"]))
            else:
                F.append(Paragraph(clean, S["body"]))

    # ---- not examined: structural, never optional ----
    F.append(Paragraph("Not examined", S["h2"]))
    gaps = list(case.get("gaps") or [])
    for a in agents:
        if a.get("status") == "skipped":
            gaps.insert(0, f"{a['name']} did not run. {a.get('reason', '')}")
    if gaps:
        F.append(Paragraph(
            "An absent finding here is <b>not</b> a clean result.", S["body"]))
        for g in gaps:
            F.append(Paragraph(f"• {g}", S["body"]))
    else:
        F.append(Paragraph("All connected sources returned data for this case.",
                           S["body"]))

    # ---- standing statement ----
    F.append(Spacer(1, 10))
    F.append(HRFlowable(width="100%", color=LINE, spaceAfter=8))
    F.append(Paragraph(
        "<b>This report does not establish that fraud occurred.</b> The "
        "detection model was trained without fraud ground-truth labels and "
        "identifies statistical anomalies. Deviation from peers can reflect "
        "case-mix, subspecialty practice, or an imperfect peer group. Findings "
        "require verification against documentation before any conclusion is "
        "drawn.", S["small"]))

    doc.build(F)
    return buf.getvalue()
