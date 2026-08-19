"""
Case assembly.

Builds one investigation case from every connected source, in the shape the
dashboard renders:

    identity      who this is                    curated provider/claim data
    risk          score, level, components       agents, or the provider model
    metrics       the provider's actual figures  curated data
    peer          metric vs peer median          risk model / peer comparison
    agents        findings with evidence         multi-agent orchestrator
    explanation   what it means, what could      LLM grounded in the knowledge
                  explain it, what to examine    base + the case above
    gaps          what was NOT checked           agents, data availability

WHY GAPS ARE A FIRST-CLASS FIELD
An agent that did not run is not a clean result. Their own integration guide
says missing data must be surfaced, and the dashboard renders skipped agents as
a card rather than omitting them - an absent card would read as "nothing found
here".
"""

from __future__ import annotations

from typing import Any

from backend.data import warehouse as wh
from backend.data.profile import claim_profile, provider_profile


def _money(v) -> str | None:
    return None if v is None else f"${float(v):,.2f}"


def _pct(v) -> str | None:
    if v is None:
        return None
    p = float(v) * 100 if float(v) <= 1 else float(v)
    return f"{p:.0f}th"


def _components(info) -> list[dict[str, Any]]:
    comps = getattr(info, "component_scores", None)
    if comps:
        return comps
    out = []
    for name, value in (getattr(info, "score_components", None) or {}).items():
        out.append({"name": name, "value": value, "not_run": False,
                    "reason": None, "is_provider_model": False})
    return out


def _agents_from(info) -> list[dict[str, Any]]:
    """
    Group risk factors into agent cards.

    With the orchestrator connected, factors carry their agent. With only the
    provider risk model, they are grouped under one card named for that model -
    so the dashboard renders the same shape either way.
    """
    factors = getattr(info, "risk_factors", None) or []
    by_agent: dict[str, list] = {}
    for f in factors:
        d = f.to_dict() if hasattr(f, "to_dict") else dict(f)
        agent = d.get("agent") or "Provider risk model"
        by_agent.setdefault(agent, []).append({
            "title": d.get("name"),
            "detail": d.get("description"),
            "evidence": [x for x in [
                (f"provider {d['observed_value']}"
                 if d.get("observed_value") is not None else None),
                (f"peer median {d['peer_reference']}"
                 if d.get("peer_reference") is not None else None),
            ] if x],
        })

    ROLES = {
        "peer": "Compares against same-specialty peers",
        "billing": "Examines billing behaviour and claim structure",
        "clinical_rule": "Checks coding and clinical consistency",
        "Provider risk model": "Isolation forest over provider features",
    }

    def card_name(a: str) -> str:
        if a == "Provider risk model":
            return a
        return a.replace("_", " ").title() + " agent"

    cards = [{
        "name": card_name(a),
        "role": ROLES.get(a, ""),
        "status": "ok",
        "findings": f,
    } for a, f in by_agent.items()]

    # Skipped agents are rendered, not omitted.
    for skipped in (getattr(info, "agents_skipped", None) or []):
        reason = next(
            (l for l in (getattr(info, "limitations", None) or [])
             if skipped.lower() in l.lower()),
            f"The {skipped} agent did not run for this case, so that dimension "
            f"was never examined. This is not the same as finding it clean.")
        cards.append({
            "name": skipped.replace("_", " ").title() + " agent",
            "role": ROLES.get(skipped, ""),
            "status": "skipped",
            "reason": reason,
            "findings": [],
        })
    return cards


def _peer_table(npi: str, info) -> list[dict[str, Any]]:
    """Metric / provider value / peer median / ratio / percentile."""
    if not wh.has("provider_risk"):
        return []
    row = wh.one("SELECT * FROM provider_risk WHERE npi = ?", [str(npi)])
    if not row:
        return []
    METRICS = [
        ("Payment per service", "m_pay_per_svc", True),
        ("Charge per service", "m_chrg_per_svc", True),
        ("Services per beneficiary", "m_svc_per_bene", False),
        ("Payment-to-charge ratio", "m_pay_chrg", False),
        ("Service concentration (HHI)", "m_hhi", False),
    ]
    out = []
    for label, col, money in METRICS:
        v, p = row.get(col), row.get(f"{col}_peer")
        if v is None:
            continue
        fmt = _money if money else (lambda x: f"{float(x):,.2f}")
        dev = row.get(f"{col}_dev")
        out.append({
            "metric": label,
            "provider": fmt(v),
            "peer_median": fmt(p) if p is not None else None,
            "ratio": f"{float(dev):.2f}x" if dev else None,
            "percentile": _pct(row.get(f"{col}_pct")),
        })
    return out


def _explain(case: dict[str, Any]) -> dict[str, Any]:
    """
    Grounded explanation. Falls back to a structured summary of the case when
    the LLM is unavailable, rather than returning nothing.
    """
    from backend.llm.llm_service import get_llm_service
    from backend.llm.prompts import build_user_prompt
    from backend.rag.rag_pipeline import _format_knowledge
    from backend.rag.retriever import get_retriever

    entity = case["identity"].get("entity_id")
    question = (f"Why was {case['entity_type']} {entity} flagged, what should "
                f"an investigator examine, and what could explain it legitimately?")

    chunks = []
    try:
        terms = " ".join(f["title"] or "" for a in case["agents"]
                         for f in a.get("findings", []))
        chunks = get_retriever().retrieve(f"{question} {terms}".strip())
    except Exception:                                          # noqa: BLE001
        pass

    model_block = _case_as_model_block(case)
    prompt = build_user_prompt(question, "INVESTIGATION",
                               _format_knowledge(chunks), None, model_block)
    resp = get_llm_service().generate(prompt)
    if resp.available and resp.text:
        return {"text": resp.text, "generated": True,
                "sources": [c.as_source() for c in chunks]}

    return {"text": _fallback_text(case), "generated": False,
            "sources": [c.as_source() for c in chunks],
            "note": "Answer generation is not configured; this summary is "
                    "assembled directly from the case."}


def _case_as_model_block(case: dict[str, Any]) -> str:
    r, lines = case["risk"], []
    lines.append(f"{case['entity_type'].upper()} {case['identity'].get('entity_id')}")
    if r.get("score") is not None:
        lines.append(f"{r.get('score_label','Risk score')}: {r['score']}")
    if r.get("level"):
        lines.append(f"Risk level: {r['level']}")
    for c in r.get("components") or []:
        lines.append(f"- component {c['name']}: {c['value']}")
    for a in case["agents"]:
        if a["status"] == "skipped":
            lines.append(f"AGENT NOT RUN - {a['name']}: {a.get('reason')}")
            continue
        for f in a["findings"]:
            ev = "; ".join(f.get("evidence") or [])
            lines.append(f"- [{a['name']}] {f['title']}: {f.get('detail','')} {ev}")
    for p in case.get("peer_comparison") or []:
        lines.append(f"- peer: {p['metric']} {p['provider']} vs median "
                     f"{p['peer_median']} ({p['ratio']}, {p['percentile']} pct)")
    for g in case.get("gaps") or []:
        lines.append(f"- NOT CHECKED: {g}")
    lines.append("NOTE: this model was trained without fraud ground-truth "
                 "labels. It identifies statistical anomalies, not fraud.")
    return "\n".join(lines)


def _fallback_text(case: dict[str, Any]) -> str:
    r = case["risk"]
    parts = []
    if r.get("score") is not None:
        parts.append(f"Flagged with {r.get('score_label','a risk score')} of "
                     f"{r['score']}"
                     + (f" ({r['level']})." if r.get("level") else "."))
    fired = [f["title"] for a in case["agents"] for f in a["findings"]]
    if fired:
        parts.append("Contributing factors: " + "; ".join(fired) + ".")
    if case.get("gaps"):
        parts.append("Not examined: " + " ".join(case["gaps"]))
    parts.append("These findings warrant review. They do not establish that "
                 "fraud occurred.")
    return " ".join(parts)


# ------------------------------------------------------------------ builders

def build_provider_case(npi: str, explain: bool = True) -> dict[str, Any]:
    from backend.model.risk_engine_service import get_risk_engine

    prof = provider_profile(npi)
    info = get_risk_engine().get_risk({"provider": [str(npi)]})

    gaps: list[str] = list(getattr(info, "limitations", None) or [])
    for k, v in (getattr(info, "data_availability", None) or {}).items():
        if str(v).upper() not in ("AVAILABLE", "TRUE"):
            gaps.append(f"{k} data was unavailable ({v}).")
    gaps += prof.not_available
    if not info.available and info.message:
        gaps.append(info.message)

    case: dict[str, Any] = {
        "entity_type": "provider",
        "found": prof.found or info.available,
        "identity": {"entity_id": str(npi), **prof.identity},
        "risk": {
            "score": info.risk_score,
            "level": info.risk_level,
            "priority": getattr(info, "priority", None),
            "score_label": getattr(info, "score_label", "Risk score"),
            "score_source": getattr(info, "score_source", None),
            "peer_group": info.peer_group,
            "period": info.scored_at,
            "components": _components(info),
        } if info.available else {"score": None, "message": info.message},
        "metrics": [{"label": k, "value": v} for k, v in prof.activity.items()
                    if v is not None][:8],
        "peer_comparison": _peer_table(npi, info),
        "agents": _agents_from(info) if info.available else [],
        "top_procedures": prof.top_procedures,
        "by_year": prof.by_year,
        "exclusion": prof.exclusion,
        "gaps": gaps,
        "sources_used": prof.sources_used,
    }
    if not case["found"]:
        case["message"] = prof.message or "Not found in any connected dataset."
        return case
    if explain:
        case["explanation"] = _explain(case)
    return case


def build_claim_case(claim_id: str, explain: bool = True) -> dict[str, Any]:
    from backend.model.risk_engine_service import get_risk_engine

    prof = claim_profile(claim_id)
    info = get_risk_engine().get_risk({"claim": [str(claim_id)]})

    gaps: list[str] = list(getattr(info, "limitations", None) or [])
    gaps += prof.not_available
    if not info.available and info.message:
        gaps.append(info.message)

    case: dict[str, Any] = {
        "entity_type": "claim",
        "found": prof.found or info.available,
        "identity": {"entity_id": str(claim_id), **prof.identity},
        "risk": {
            "score": info.risk_score,
            "level": info.risk_level,
            "priority": getattr(info, "priority", None),
            "score_label": getattr(info, "score_label", "Risk score"),
            "components": _components(info),
        } if info.available else {"score": None, "message": info.message},
        "metrics": [{"label": k, "value": v} for k, v in prof.activity.items()
                    if v is not None],
        "peer_comparison": [],
        "agents": _agents_from(info) if info.available else [],
        "gaps": gaps,
        "sources_used": prof.sources_used,
    }
    if not case["found"]:
        case["message"] = prof.message or "Claim not found."
        return case
    if explain:
        case["explanation"] = _explain(case)
    return case
