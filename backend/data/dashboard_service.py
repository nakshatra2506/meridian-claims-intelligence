"""
Dashboard data service.

Aggregate queries backing the dashboard's overview, tables and queue. Every
figure comes from the ETL's curated tables or the risk model - nothing here
estimates, and nothing is hard-coded.

WHY THE TWO POPULATIONS ARE REPORTED SEPARATELY
Claims data and Medicare provider data come from different time windows and
different identifier systems (claim PROVIDER_ID vs CMS NPI), and the measured
overlap is effectively zero. Cross-joining them would produce a number that
looks authoritative and is meaningless, so counts are returned side by side and
the dashboard states the caveat rather than hiding it.

WHY CLAIM AND PROVIDER RISK BANDS ARE LABELLED DIFFERENTLY
Claim bands are quintiles - near-equal by construction. Provider bands come from
the model and are heavily skewed. Shown on the same axis without labels, the
flat claim chart reads as a finding rather than an artefact of how the bands
were cut.
"""

from __future__ import annotations

from typing import Any

from backend.data import warehouse as wh

TIER_ORDER = ["Critical", "High", "Medium", "Low"]


def _n(v) -> int:
    return int(v or 0)


def _money(v) -> str:
    v = float(v or 0)
    if v >= 1e9:
        return f"${v / 1e9:.1f}B"
    if v >= 1e6:
        return f"${v / 1e6:.1f}M"
    if v >= 1e3:
        return f"${v / 1e3:.1f}K"
    return f"${v:,.0f}"


def _kv(rows: list[dict[str, Any]]) -> list[tuple[Any, int]]:
    """
    Read (label, count) pairs from a two-column aggregate result.

    Columns are read BY POSITION, not by name. An unaliased aggregate comes
    back under whatever the engine chose to call it - `count_star()` on DuckDB -
    so `row["v"]` raised KeyError and took the whole overview down with it.
    Position is stable regardless of how the engine names an expression.
    """
    out: list[tuple[Any, int]] = []
    for r in rows or []:
        values = list(r.values())
        if len(values) >= 2:
            out.append((values[0], _n(values[1])))
    return out


def available() -> bool:
    return wh.is_built() and wh.get_connection() is not None


# ------------------------------------------------------------------ overview

def overview() -> dict[str, Any]:
    """Headline counts, plus the series behind each overview chart."""
    if not available():
        return {"available": False,
                "message": "No data source connected. Run the ETL and point "
                           "CURATED_DIR at data/curated."}

    out: dict[str, Any] = {"available": True, "cards": [], "charts": {}}

    # --- claims side ---
    claims_total = claims_paid = 0
    if wh.has("all_claims"):
        # wh.one returns None on an empty result, so every aggregate read is
        # guarded. An empty table is a legitimate state, not an error.
        row = wh.one("""SELECT COUNT(*) AS n,
                               SUM(COALESCE(charge, payment_amount)) AS amt
                        FROM all_claims""") or {}
        claims_total, claims_paid = _n(row.get("n")), float(row.get("amt") or 0)

        by_type = wh.query("""SELECT claim_type AS k, COUNT(*) AS v
                              FROM all_claims
                              GROUP BY claim_type ORDER BY v DESC""")
        out["charts"]["claims_by_type"] = [
            {"k": (k or "unknown").title(), "v": v} for k, v in _kv(by_type)]

        years = wh.query("""
            SELECT SUBSTR(CAST(claim_from_date AS VARCHAR), 1, 4) AS k,
                   COUNT(*) AS v
            FROM all_claims WHERE claim_from_date IS NOT NULL
            GROUP BY 1 HAVING k >= '2000' ORDER BY 1""")
        out["charts"]["claims_by_year"] = [
            {"k": k, "v": v} for k, v in _kv(years)]

    # --- provider side ---
    prov_total = 0
    if wh.has("provider_summary"):
        prov_total = _n(wh.scalar("SELECT COUNT(*) FROM provider_summary"))
        years = wh.query("""SELECT CAST(year AS VARCHAR) AS k,
                                   COUNT(DISTINCT npi) AS v
                            FROM fact_provider_year
                            GROUP BY year ORDER BY year""") \
            if wh.has("fact_provider_year") else []
        out["charts"]["providers_by_year"] = [
            {"k": k, "v": v} for k, v in _kv(years)]

    # --- risk distributions ---
    #
    # Counted over the INTERSECTION of the risk model and the provider table,
    # not over the risk model alone. The model scores a larger population than
    # the curated provider set, so counting it whole produced "107.9% of all
    # providers" - a figure that is arithmetically impossible and would have
    # been read as a data error rather than a definition mismatch.
    high_risk_providers = critical_providers = scored = 0
    if wh.has("provider_risk") and wh.has("provider_summary"):
        rows = wh.query("""
            SELECT r.risk_tier AS k, COUNT(*) AS v
            FROM provider_risk r JOIN provider_summary p ON p.npi = r.npi
            WHERE r.risk_tier IS NOT NULL GROUP BY r.risk_tier""")
        dist = {k: v for k, v in _kv(rows)}
        scored = sum(dist.values())
        out["charts"]["provider_risk_distribution"] = [
            {"k": t, "v": dist.get(t, 0)} for t in TIER_ORDER]
        critical_providers = dist.get("Critical", 0)
        high_risk_providers = critical_providers + dist.get("High", 0)
        out["scored_providers"] = scored

    # Claim risk bands, from the generated claim risk table. Read separately
    # from the provider bands and labelled differently on the dashboard,
    # because these are quintile cuts and those are model output - shown on one
    # axis without that distinction, a flat claim chart reads as a finding
    # rather than an artefact of how the bands were cut.
    claim_bands: list[dict[str, Any]] = []
    try:
        from pathlib import Path

        import pandas as pd

        from backend.data.curated_loader import find_curated_dir

        curated = find_curated_dir()
        roots = [p for p in (
            (curated.parent / "claims") if curated else None,
            curated,
        ) if p]
        for root in roots:
            for name in ("final_unified_claim_risk.parquet",
                         "final_unified_claim_risk.csv"):
                f = Path(root) / name
                if not f.exists():
                    continue
                df = (pd.read_parquet(f, columns=["FINAL_RISK_LEVEL"])
                      if f.suffix == ".parquet"
                      else pd.read_csv(f, usecols=["FINAL_RISK_LEVEL"]))
                counts = df["FINAL_RISK_LEVEL"].value_counts()
                claim_bands = [{"k": t_.title(), "v": int(counts.get(t_, 0))}
                               for t_ in ("CRITICAL", "HIGH", "MEDIUM", "LOW")]
                break
            if claim_bands:
                break
    except Exception:                                          # noqa: BLE001
        claim_bands = []
    out["charts"]["claim_risk_distribution"] = claim_bands

    # Percentages are taken against the scored population, which is what the
    # denominator actually is.
    pct = lambda a, b: f"{a / b * 100:.1f}%" if b else "—"
    out["cards"] = [
        {"label": "Total claims", "value": f"{claims_total:,}",
         "sub": "All claim types"},
        {"label": "Total providers", "value": f"{prov_total:,}",
         "sub": "Medicare Part B"},
        {"label": "Total claim amount", "value": _money(claims_paid),
         "sub": "Submitted charges"},
        {"label": "Avg claim amount",
         "value": _money(claims_paid / claims_total) if claims_total else "—",
         "sub": "Per claim"},
        {"label": "Scored providers", "value": f"{scored:,}",
         "sub": f"of {prov_total:,} in the provider data"},
        {"label": "High-risk providers", "value": f"{high_risk_providers:,}",
         "sub": pct(high_risk_providers, scored) + " of scored providers",
         "tone": "critical"},
        {"label": "Critical — investigate now", "value": f"{critical_providers:,}",
         "sub": pct(critical_providers, scored) + " of scored providers",
         "tone": "critical"},
    ]
    return out


# -------------------------------------------------------------------- tables

def providers(limit: int = 25, offset: int = 0, tier: str | None = None,
              state: str | None = None, specialty: str | None = None,
              search: str | None = None, min_payment: float | None = None,
              min_score: float | None = None, max_score: float | None = None
              ) -> dict[str, Any]:
    """Provider list for the table and the investigator queue."""
    if not available() or not wh.has("provider_summary"):
        return {"available": False, "rows": [], "total": 0}

    has_risk = wh.has("provider_risk")
    join = "LEFT JOIN provider_risk r ON r.npi = p.npi" if has_risk else ""
    score = "r.risk_score" if has_risk else "NULL"
    tier_col = "r.risk_tier" if has_risk else "NULL"

    where, params = ["1=1"], []
    if tier and has_risk:
        where.append("LOWER(r.risk_tier) = ?")
        params.append(tier.lower())
    if state:
        where.append("UPPER(p.state) = ?")
        params.append(state.upper())
    if specialty:
        where.append("LOWER(p.specialty) LIKE ?")
        params.append(f"%{specialty.lower()}%")
    if search:
        where.append("(p.npi LIKE ? OR LOWER(p.last_or_org_name) LIKE ? "
                     "OR LOWER(COALESCE(p.first_name,'')) LIKE ?)")
        s = f"%{search.lower()}%"
        params += [f"%{search}%", s, s]
    if min_payment is not None:
        where.append("p.total_payment >= ?")
        params.append(min_payment)
    if has_risk and min_score is not None:
        where.append("r.risk_score >= ?")
        params.append(min_score)
    if has_risk and max_score is not None:
        where.append("r.risk_score <= ?")
        params.append(max_score)

    clause = " AND ".join(where)
    total = _n(wh.scalar(
        f"SELECT COUNT(*) FROM provider_summary p {join} WHERE {clause}", params))

    order = f"{score} DESC NULLS LAST, p.total_payment DESC" if has_risk \
        else "p.total_payment DESC"
    rows = wh.query(f"""
        SELECT p.npi, p.last_or_org_name, p.first_name, p.specialty, p.state,
               p.city, p.total_payment, p.total_beneficiaries, p.total_services,
               {score} AS risk_score, {tier_col} AS risk_tier
        FROM provider_summary p {join}
        WHERE {clause} ORDER BY {order} LIMIT ? OFFSET ?
    """, params + [limit, offset])

    return {
        "available": True, "total": total,
        "rows": [{
            "npi": r["npi"],
            "name": " ".join(x for x in [r.get("first_name"),
                                         r.get("last_or_org_name")] if x) or None,
            "specialty": r.get("specialty"),
            "location": ", ".join(x for x in [r.get("city"), r.get("state")] if x),
            "state": r.get("state"),
            "risk_score": (round(r["risk_score"], 1)
                           if r.get("risk_score") is not None else None),
            "risk_tier": r.get("risk_tier"),
            "total_payment": _money(r.get("total_payment")),
            "total_payment_raw": float(r.get("total_payment") or 0),
            "beneficiaries": _n(r.get("total_beneficiaries")),
            "services": _n(r.get("total_services")),
        } for r in rows],
    }


def claims(limit: int = 25, offset: int = 0, claim_type: str | None = None,
           search: str | None = None) -> dict[str, Any]:
    if not available() or not wh.has("all_claims"):
        return {"available": False, "rows": [], "total": 0}

    where, params = ["1=1"], []
    if claim_type:
        where.append("LOWER(claim_type) = ?")
        params.append(claim_type.lower())
    if search:
        where.append("(CAST(claim_id AS VARCHAR) LIKE ? "
                     "OR COALESCE(org_npi,'') LIKE ? OR COALESCE(provider_ccn,'') LIKE ?)")
        params += [f"%{search}%"] * 3

    clause = " AND ".join(where)
    total = _n(wh.scalar(f"SELECT COUNT(*) FROM all_claims WHERE {clause}", params))
    rows = wh.query(f"""
        SELECT claim_id, claim_type, org_npi, provider_ccn, beneficiary_id,
               payment_amount, charge, claim_from_date
        FROM all_claims WHERE {clause}
        ORDER BY payment_amount DESC NULLS LAST LIMIT ? OFFSET ?
    """, params + [limit, offset])

    return {
        "available": True, "total": total,
        "rows": [{
            "claim_id": str(r["claim_id"]),
            "claim_type": (r.get("claim_type") or "").title(),
            "provider": r.get("org_npi") or r.get("provider_ccn"),
            "provider_kind": "NPI" if r.get("org_npi") else "CCN",
            "beneficiary": r.get("beneficiary_id"),
            "payment": _money(r.get("payment_amount")),
            "charge": _money(r.get("charge")),
            "service_date": (str(r["claim_from_date"])[:10]
                             if r.get("claim_from_date") else None),
        } for r in rows],
    }


def filter_options() -> dict[str, Any]:
    """Distinct values for the queue's filter rail."""
    if not available() or not wh.has("provider_summary"):
        return {"specialties": [], "states": [], "tiers": []}
    specs = wh.query("""SELECT specialty AS k, COUNT(*) AS n
                        FROM provider_summary
                        WHERE specialty IS NOT NULL GROUP BY 1
                        ORDER BY n DESC LIMIT 60""")
    states = wh.query("""SELECT state AS k FROM provider_summary
                         WHERE state IS NOT NULL GROUP BY 1 ORDER BY 1""")
    tiers = []
    if wh.has("provider_risk"):
        tiers = [list(r.values())[0] for r in wh.query(
            "SELECT risk_tier AS k FROM provider_risk WHERE risk_tier IS NOT NULL "
            "GROUP BY 1")]
    return {
        "specialties": [list(r.values())[0] for r in specs],
        "states": [list(r.values())[0] for r in states],
        "tiers": [t for t in TIER_ORDER if t in tiers] or TIER_ORDER,
    }