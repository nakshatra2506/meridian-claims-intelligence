"""
Case report.

Renders one investigation case as a document an investigator can attach to a
case file. Markdown, because it reads as plain text, converts to PDF or Word
without a toolchain, and diffs cleanly if a case is revisited.

THE STRUCTURE IS FIXED ON PURPOSE
Every report carries the same sections in the same order, including the ones
that are uncomfortable: what was NOT examined, and what could explain the
findings legitimately. A report that only lists what looks bad is an accusation
with a header on it. Fixing the structure means those sections cannot be
dropped when the findings look damning - which is exactly when they matter.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


def _fmt_dt() -> str:
    return datetime.now(timezone.utc).strftime("%d %B %Y, %H:%M UTC")


def render_markdown(case: dict[str, Any]) -> str:
    ent = case.get("entity_type", "entity")
    ident = case.get("identity") or {}
    eid = ident.get("entity_id", "unknown")
    L: list[str] = []

    L.append(f"# Investigation case — {ent} {eid}")
    L.append("")
    L.append(f"*Generated {_fmt_dt()}*")
    L.append("")

    if not case.get("found"):
        L.append(case.get("message", "Not found in any connected dataset."))
        return "\n".join(L)

    # ---- identity ----
    L.append("## Subject")
    L.append("")
    for k, v in ident.items():
        if k == "entity_id" or v is None:
            continue
        L.append(f"- **{k.replace('_', ' ').title()}:** {v}")
    L.append(f"- **Identifier:** `{eid}`")
    L.append("")

    # ---- risk ----
    risk = case.get("risk") or {}
    L.append("## Risk assessment")
    L.append("")
    if risk.get("score") is None:
        L.append(risk.get("message", "No model output available for this case."))
    else:
        L.append(f"**{risk.get('score_label', 'Risk score')}: "
                 f"{risk['score']} / 100**"
                 + (f" — {risk['level']}" if risk.get("level") else ""))
        L.append("")
        if risk.get("priority"):
            L.append(f"- Priority: {risk['priority']}")
        if risk.get("peer_group"):
            L.append(f"- Peer group: {risk['peer_group']}")
        if risk.get("period"):
            L.append(f"- Period scored: {risk['period']}")
        comps = risk.get("components") or []
        if comps:
            L.append("")
            L.append("### Score components")
            L.append("")
            L.append("| Component | Value |")
            L.append("| --- | ---: |")
            for c in comps:
                tag = " *(provider risk model)*" if c.get("is_provider_model") else ""
                L.append(f"| {c['name']}{tag} | {c['value']} |")
    L.append("")

    # ---- measured activity ----
    metrics = case.get("metrics") or []
    if metrics:
        L.append("## Measured activity")
        L.append("")
        L.append("| Measure | Value |")
        L.append("| --- | ---: |")
        for m in metrics:
            L.append(f"| {m['label']} | {m['value']} |")
        L.append("")

    # ---- peer comparison ----
    peers = case.get("peer_comparison") or []
    if peers:
        L.append("## Peer comparison")
        L.append("")
        L.append("| Metric | This provider | Peer median | Ratio | Percentile |")
        L.append("| --- | ---: | ---: | ---: | ---: |")
        for p in peers:
            L.append(f"| {p['metric']} | {p.get('provider') or '—'} | "
                     f"{p.get('peer_median') or '—'} | {p.get('ratio') or '—'} | "
                     f"{p.get('percentile') or '—'} |")
        L.append("")
        L.append("*Ratio measures magnitude; percentile measures rarity. A high "
                 "percentile with a small ratio means the peer distribution is "
                 "tight, not that the provider is far from normal.*")
        L.append("")

    # ---- findings ----
    agents = case.get("agents") or []
    active = [a for a in agents if a.get("status") != "skipped"]
    if active:
        L.append("## Findings")
        L.append("")
        for a in active:
            L.append(f"### {a['name']}")
            if a.get("role"):
                L.append(f"*{a['role']}*")
            L.append("")
            for f in a.get("findings") or []:
                L.append(f"- **{f['title']}** — {f.get('detail', '')}")
                for e in f.get("evidence") or []:
                    L.append(f"  - {e}")
            L.append("")

    # ---- top procedures ----
    procs = case.get("top_procedures") or []
    if procs:
        L.append("## Highest-value procedures")
        L.append("")
        keys = [k for k in ("code", "description", "services", "payment",
                            "vs state average") if any(k in p for p in procs)]
        L.append("| " + " | ".join(k.title() for k in keys) + " |")
        L.append("| " + " | ".join("---" for _ in keys) + " |")
        for p in procs:
            L.append("| " + " | ".join(str(p.get(k, "—")) for k in keys) + " |")
        L.append("")

    # ---- explanation ----
    ex = case.get("explanation") or {}
    if ex.get("text"):
        L.append("## Assessment")
        L.append("")
        L.append(ex["text"])
        L.append("")
        if not ex.get("generated") and ex.get("note"):
            L.append(f"*{ex['note']}*")
            L.append("")

    # ---- what was not examined: never optional ----
    gaps = case.get("gaps") or []
    L.append("## Not examined")
    L.append("")
    if gaps:
        L.append("The following were not checked. **An absent finding here is "
                 "not a clean result.**")
        L.append("")
        for g in gaps:
            L.append(f"- {g}")
    else:
        L.append("All connected sources returned data for this case.")
    L.append("")

    skipped = [a for a in agents if a.get("status") == "skipped"]
    if skipped:
        for a in skipped:
            L.append(f"- **{a['name']}** did not run. {a.get('reason', '')}")
        L.append("")

    # ---- sources ----
    srcs = case.get("sources_used") or []
    if srcs:
        L.append("## Sources consulted")
        L.append("")
        for s in srcs:
            L.append(f"- {s}")
        L.append("")
    kb = (ex.get("sources") or [])
    if kb:
        L.append("Domain knowledge referenced:")
        L.append("")
        for s in kb[:6]:
            L.append(f"- {s.get('title')} — {s.get('section')}")
        L.append("")

    # ---- standing statement ----
    L.append("---")
    L.append("")
    L.append("**This report does not establish that fraud occurred.** The "
             "detection model was trained without fraud ground-truth labels and "
             "identifies statistical anomalies. Deviation from peers can reflect "
             "case-mix, subspecialty practice, or an imperfect peer group. "
             "Findings require verification against documentation before any "
             "conclusion is drawn.")
    L.append("")
    return "\n".join(L)
