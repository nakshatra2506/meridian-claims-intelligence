"""
Dashboard API.

    GET /api/overview            headline counts and chart series
    GET /api/providers           provider table / investigator queue
    GET /api/claims              claims table
    GET /api/filters             distinct values for the queue filter rail
    GET /api/investigate/provider/{npi}
    GET /api/investigate/claim/{claim_id}
    GET /api/report/provider/{npi}     case report (markdown)
    GET /api/report/claim/{claim_id}

Every figure is read from the curated tables, the risk model, or the
multi-agent orchestrator. Nothing is computed here, and a source that is not
connected is reported as such rather than filled in.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import Response

from backend.data import dashboard_service as ds

router = APIRouter()


@router.get("/overview")
def overview() -> dict[str, Any]:
    return ds.overview()


@router.get("/filters")
def filters() -> dict[str, Any]:
    return ds.filter_options()


@router.get("/providers")
def providers(
    limit: int = Query(25, ge=1, le=200),
    offset: int = Query(0, ge=0),
    tier: str | None = None,
    state: str | None = None,
    specialty: str | None = None,
    search: str | None = None,
    min_payment: float | None = None,
    min_score: float | None = None,
    max_score: float | None = None,
) -> dict[str, Any]:
    return ds.providers(limit=limit, offset=offset, tier=tier, state=state,
                        specialty=specialty, search=search,
                        min_payment=min_payment, min_score=min_score,
                        max_score=max_score)


@router.get("/claims")
def claims(
    limit: int = Query(25, ge=1, le=200),
    offset: int = Query(0, ge=0),
    claim_type: str | None = None,
    search: str | None = None,
) -> dict[str, Any]:
    return ds.claims(limit=limit, offset=offset, claim_type=claim_type,
                     search=search)


@router.get("/investigate/provider/{npi}")
def investigate_provider(npi: str, explain: bool = True) -> dict[str, Any]:
    from backend.investigation.case_builder import build_provider_case

    try:
        return build_provider_case(npi, explain=explain)
    except Exception as exc:                                   # noqa: BLE001
        raise HTTPException(
            status_code=500,
            detail=f"Investigation failed: {type(exc).__name__}: {exc}") from exc


@router.get("/investigate/claim/{claim_id}")
def investigate_claim(claim_id: str, explain: bool = True) -> dict[str, Any]:
    from backend.investigation.case_builder import build_claim_case

    try:
        return build_claim_case(claim_id, explain=explain)
    except Exception as exc:                                   # noqa: BLE001
        raise HTTPException(
            status_code=500,
            detail=f"Investigation failed: {type(exc).__name__}: {exc}") from exc


def _safe(name: str) -> str:
    """Filename-safe id. Claim ids are negative, so the sign is dropped."""
    return "".join(c for c in str(name) if c.isalnum() or c in "-_").lstrip("-")


@router.get("/report/provider/{npi}")
def report_provider(npi: str, format: str = "pdf"):
    """Case report. PDF by default; markdown for anyone who wants to edit it."""
    from backend.investigation.case_builder import build_provider_case

    case = build_provider_case(npi, explain=True)
    if format.lower() == "md":
        from backend.investigation.report import render_markdown
        return {"filename": f"case-provider-{_safe(npi)}.md",
                "markdown": render_markdown(case)}

    from backend.investigation.pdf import render_pdf
    return Response(
        content=render_pdf(case), media_type="application/pdf",
        headers={"Content-Disposition":
                 f'attachment; filename="case-provider-{_safe(npi)}.pdf"'})


@router.get("/report/claim/{claim_id}")
def report_claim(claim_id: str, format: str = "pdf"):
    from backend.investigation.case_builder import build_claim_case

    case = build_claim_case(claim_id, explain=True)
    if format.lower() == "md":
        from backend.investigation.report import render_markdown
        return {"filename": f"case-claim-{_safe(claim_id)}.md",
                "markdown": render_markdown(case)}

    from backend.investigation.pdf import render_pdf
    return Response(
        content=render_pdf(case), media_type="application/pdf",
        headers={"Content-Disposition":
                 f'attachment; filename="case-claim-{_safe(claim_id)}.pdf"'})
