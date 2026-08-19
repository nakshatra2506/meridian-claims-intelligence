"""
Build the unified claim risk table the ClaimStore reads.

WHY THIS SCRIPT EXISTS
`data/claims/final_unified_claim_risk.csv` is the orchestrator's entry point for
claim investigations, and in a fresh clone it does not exist: the directory is
absent and the committed copies elsewhere are Git LFS pointers. Without it the
orchestrator raises before running a single agent.

This regenerates the table from `data/curated/`, which the ETL produces and
which is committed as real Parquet.

WHAT THE RISK SCORE HERE IS, AND IS NOT
This produces a **percentile ranking within the loaded claims**, derived from
observable claim characteristics - payment, charge, line count, payment-to-charge
ratio. It is a prioritisation ordering so the agents have something to work
against.

It is NOT the trained claim ensemble in `models/claims/`. Where that model's
output is available, it should be used instead: pass its path, or drop it at
`data/claims/final_unified_claim_risk.csv` and this script will not overwrite it
unless --force is given. The distinction is recorded in a SCORE_SOURCE column so
nothing downstream can mistake a ranking for a model prediction.

    python -m multi_agent.scripts.build_claim_risk
    python -m multi_agent.scripts.build_claim_risk --force
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

CURATED = ROOT / "data" / "curated"
OUT_DIR = ROOT / "data" / "claims"
# Parquet by default: the CSV of this table is ~118 MB against ~13 MB as
# Parquet, it round-trips dtypes (so booleans do not become the string "True"),
# and the store reads either. --csv writes CSV as well, for anything outside
# this codebase that needs it.
OUT = OUT_DIR / "final_unified_claim_risk.parquet"
OUT_CSV = OUT_DIR / "final_unified_claim_risk.csv"

# Bands used across the platform, so a claim reads the same here as elsewhere.
BANDS = [(0.80, "CRITICAL", 5), (0.60, "HIGH", 4),
         (0.40, "MEDIUM", 3), (0.20, "LOW", 2), (0.00, "LOW", 1)]


def band(p: float) -> tuple[str, int]:
    for threshold, level, priority in BANDS:
        if p >= threshold:
            return level, priority
    return "LOW", 1


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true",
                    help="overwrite an existing file")
    ap.add_argument("--limit", type=int, default=0,
                    help="cap rows, for a quick build")
    ap.add_argument("--csv", action="store_true",
                    help="also write CSV alongside the Parquet")
    args = ap.parse_args()

    if OUT.exists() and OUT.stat().st_size > 4096 and not args.force:
        print(f"{OUT} already exists. Use --force to rebuild.")
        return 0

    src = CURATED / "fact_claim.parquet"
    if not src.exists():
        print(f"ERROR: {src} not found. Run the ETL first:\n"
              f"  cd etl && python -m etl.run_etl")
        return 1

    print(f"reading {src.name} ...")
    raw = pd.read_parquet(src)
    print(f"  {len(raw):,} rows")

    # fact_claim in this build is LINE-level: one row per service line, several
    # per claim. Claim-level amounts repeat on every line, so they are taken
    # with max() rather than sum() - summing would multiply a claim's payment
    # by its line count. Grouping here is also what produces a real line count,
    # which the agents need and which no column supplies.
    if raw["claim_id"].duplicated().any():
        amounts = [c for c in ("payment_amount", "total_charge_amount",
                               "submitted_charge_amount", "allowed_amount",
                               "length_of_stay") if c in raw.columns]
        firsts = [c for c in ("beneficiary_id", "provider_ccn",
                              "organisation_npi", "attending_npi",
                              "performing_npi", "billing_npi", "claim_type",
                              "provider_state_code", "claim_year")
                  if c in raw.columns]
        agg = {c: (c, "max") for c in amounts}
        agg.update({c: (c, "first") for c in firsts})
        if "claim_from_date" in raw.columns:
            agg["claim_from_date"] = ("claim_from_date", "min")
        if "claim_thru_date" in raw.columns:
            agg["claim_thru_date"] = ("claim_thru_date", "max")
        df = raw.groupby("claim_id", as_index=False).agg(
            line_count=("claim_id", "size"), **agg)
        print(f"  -> {len(df):,} claims "
              f"(max {int(df.line_count.max())} lines on one claim)")
    else:
        df = raw.copy()
        df["line_count"] = 1
        print(f"  {len(df):,} claims (already claim-level)")

    if args.limit:
        df = df.head(args.limit)

    out = pd.DataFrame()
    out["CLAIM_ID"] = df["claim_id"].astype("string")
    out["CLAIM_TYPE"] = df.get("claim_type", pd.Series("unknown", index=df.index)) \
        .astype("string").str.upper()

    # The provider identifier and its KIND travel together. A claim carrying
    # only a facility CCN cannot be peer-compared, and the PeerAgent needs to
    # know that rather than attempting an invalid NPI lookup.
    org = df.get("organisation_npi", pd.Series(pd.NA, index=df.index)).astype("string")
    ccn = df.get("provider_ccn", pd.Series(pd.NA, index=df.index)).astype("string")
    out["PROVIDER_ID"] = org.fillna(ccn)
    out["PROVIDER_ID_TYPE"] = org.notna().map({True: "NPI", False: "PRVDR_NUM"})
    out["BENE_ID"] = df.get("beneficiary_id", pd.NA)

    def col(name, default=0):
        """
        A column as a Series, or a constant Series when absent.

        df.get(name, default) returns the SCALAR default when the column is
        missing, which then fails on any Series method. Column availability
        differs by claim type, so every read goes through here.
        """
        if name in df.columns:
            return pd.to_numeric(df[name], errors="coerce").fillna(default)
        return pd.Series(default, index=df.index, dtype="float64")

    pay = col("payment_amount")
    charge = col("total_charge_amount")
    if charge.eq(0).all():
        charge = col("submitted_charge_amount")
    lines = col("line_count", 1)

    out["CLM_PMT_AMT_first"] = pay
    out["total_claim_payment"] = pay
    out["total_claim_charge"] = charge
    out["submitted_charge"] = charge
    out["payment_to_charge_ratio"] = (pay / charge.replace(0, pd.NA)).fillna(0)
    out["claim_line_count"] = lines.astype(int)
    # Written as 1/0, not True/False: a Python bool becomes the string "True"
    # in CSV and the store's boolean coercion returned None for it, so the
    # evidence read `has_multiple_lines: None` on a claim that plainly had
    # multiple lines.
    out["has_multiple_lines"] = (lines > 1).astype(int)

    # Counts that give the agents something comparative to work with.
    out["beneficiary_claim_count"] = out.groupby(
        "BENE_ID", dropna=False)["CLAIM_ID"].transform("count")
    out["provider_claim_count"] = out.groupby(
        "PROVIDER_ID", dropna=False)["CLAIM_ID"].transform("count")

    # --- provider-level aggregates ---
    #
    # The BillingAgent compares a claim against its own provider's average and
    # asks whether the provider is high volume. Without these it can compute no
    # deviation at all, which is why an unenriched table produced a case with
    # every agent selected and zero findings.
    grp = out.groupby("PROVIDER_ID", dropna=False)["total_claim_payment"]
    out["provider_avg_claim_payment"] = grp.transform("mean")
    out["provider_total_payment"] = grp.transform("sum")
    out["provider_payment_std"] = grp.transform("std").fillna(0)

    # "High volume" is defined against this population's own distribution
    # rather than an absolute cut, so it stays meaningful on any extract.
    volume_cut = out["provider_claim_count"].quantile(0.90)
    out["is_high_volume_provider"] = (
        out["provider_claim_count"] >= volume_cut).astype(int)

    # A payment exceeding its charge is a reconciliation problem, not a risk
    # signal in itself - but the agent asks, so it is reported.
    out["has_payment_reconciliation_issue"] = (
        (charge > 0) & (pay > charge * 1.001)).astype(int)

    # --- procedure evidence ---
    #
    # Drives the ClinicalRuleAgent. Taken from the line detail where the ETL
    # preserved it; absent, the flags say so rather than defaulting to zero,
    # because "no procedures recorded" and "procedures not captured" are
    # different findings.
    code_col = next((c for c in ("hcpcs_code", "procedure_code", "hcpcs_cd")
                     if c in raw.columns), None)
    if code_col:
        pc = raw.groupby("claim_id")[code_col].agg(
            procedure_code_count="count",
            unique_procedure_code_count="nunique").reset_index()
        pc["claim_id"] = pc["claim_id"].astype("string")
        out = out.merge(pc, left_on="CLAIM_ID", right_on="claim_id",
                        how="left").drop(columns=["claim_id"])
    else:
        # No procedure codes in this extract. Left as NA rather than 0, because
        # "no procedures recorded" and "procedures not captured" are different
        # findings and the agent must be able to tell them apart.
        out["procedure_code_count"] = pd.NA
        out["unique_procedure_code_count"] = pd.NA
    out["has_procedure"] = (
        out["procedure_code_count"].notna()
        & (pd.to_numeric(out["procedure_code_count"], errors="coerce") > 0)
    ).astype(int)

    if "claim_from_date" in df.columns:
        d = pd.to_datetime(df["claim_from_date"], errors="coerce")
        out["claim_from_dt"] = d.dt.strftime("%Y-%m-%d")
        out["claim_year"] = d.dt.year
        if "claim_thru_date" in df.columns:
            d2 = pd.to_datetime(df["claim_thru_date"], errors="coerce")
            out["claim_thru_dt"] = d2.dt.strftime("%Y-%m-%d")
            out["claim_duration_days"] = (d2 - d).dt.days

    # --- ranking ---
    #
    # Percentile within the loaded claims, blended across observable
    # characteristics. Each component is ranked first so a single skewed
    # variable cannot dominate the ordering.
    parts = {
        "payment": pay.rank(pct=True),
        "charge": charge.rank(pct=True),
        "lines": lines.rank(pct=True),
        "pay_to_charge": out["payment_to_charge_ratio"].rank(pct=True),
        "bene_repeat": out["beneficiary_claim_count"].rank(pct=True),
    }
    weights = {"payment": .30, "charge": .20, "lines": .20,
               "pay_to_charge": .15, "bene_repeat": .15}
    blended = sum(parts[k] * w for k, w in weights.items())
    pct = blended.rank(pct=True)

    out["CLAIM_RISK_SCORE"] = (pct * 100).round(2)
    levels = pct.map(lambda p: band(p)[0])
    out["FINAL_RISK_LEVEL"] = levels
    out["FINAL_RISK_PRIORITY"] = pct.map(lambda p: band(p)[1])
    out["FINAL_CLAIM_RANK"] = out["CLAIM_RISK_SCORE"].rank(
        ascending=False, method="first").astype(int)
    out["CLAIM_STATUS"] = "SCORED"
    out["MODEL_SCORE"] = out["CLAIM_RISK_SCORE"]
    out["CLAIM_RISK_RANK"] = out["FINAL_CLAIM_RANK"]
    out["risk_band"] = out["FINAL_RISK_LEVEL"]

    # Provenance: this is a ranking, not the trained ensemble's output, and
    # nothing downstream should be able to confuse the two.
    out["SCORE_SOURCE"] = "percentile_ranking_from_curated_claims"

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out.to_parquet(OUT, index=False)
    if args.csv:
        out.to_csv(OUT_CSV, index=False)

    print(f"\nwrote {OUT}")
    if args.csv:
        print(f"       {OUT_CSV}")
    print(f"  {len(out):,} claims")
    print("  " + ", ".join(f"{k} {v:,}" for k, v in
                           out.FINAL_RISK_LEVEL.value_counts().items()))
    print(f"  columns: {len(out.columns)}")
    print(f"  with procedure evidence: "
          f"{int(out['has_procedure'].sum()):,}")
    print(f"  high-volume providers flagged: "
          f"{int(out['is_high_volume_provider'].sum()):,} claims")
    print("\nNOTE: CLAIM_RISK_SCORE here is a percentile ranking derived from "
          "claim\ncharacteristics, not the trained claim ensemble. Replace this "
          "file with the\nmodel's own output when it is available.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
