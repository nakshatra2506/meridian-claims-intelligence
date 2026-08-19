"""
Locating the model outputs the stores read.

WHY THIS EXISTS
The stores originally pointed at one hardcoded path each. Both had drifted from
where the files actually live:

    provider_store  ->  models/provider/output/provider_risk_scores.csv
                        (there is no output/ directory; the file sits one
                        level up)
    claim_store     ->  data/claims/final_unified_claim_risk.csv
                        (there is no data/claims directory at all)

So the orchestrator raised FileNotFoundError before running a single agent, and
the failure read as "the agents are broken" rather than "the path moved".

A second problem compounds it: the large CSVs are stored in Git LFS. Without
`git lfs pull` a clone contains 133-byte pointer files that ARE present and DO
parse as CSV - so a path check passes and the load produces nonsense.

This module therefore searches several candidate locations, accepts Parquet as
well as CSV, and rejects any file too small to be real data. It reports what it
searched when nothing is found, so the next person fixes the right problem.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent

# A Git LFS pointer is ~130 bytes. Anything under this is not data.
MIN_REAL_BYTES = 4096


def _usable(path: Path) -> bool:
    return path.exists() and path.stat().st_size >= MIN_REAL_BYTES


def resolve(candidates: list[Path], what: str) -> Path:
    """
    First usable candidate, else a message naming every location tried.

    Raising with the full search list matters here: the same error otherwise
    appears whether the file is missing, in a different folder, or an unpulled
    LFS pointer - three problems with three different fixes.
    """
    for c in candidates:
        if _usable(c):
            return c

    pointers = [c for c in candidates
                if c.exists() and c.stat().st_size < MIN_REAL_BYTES]
    lines = [f"{what} not found.", "", "Searched:"]
    lines += [f"  - {c}" for c in candidates]
    if pointers:
        lines += [
            "",
            "These exist but are Git LFS pointers rather than data:",
            *[f"  - {p} ({p.stat().st_size} bytes)" for p in pointers],
            "",
            "Run:  git lfs install && git lfs pull",
        ]
    raise FileNotFoundError("\n".join(lines))


def load_table(path: Path) -> pd.DataFrame:
    """Read CSV or Parquet by extension."""
    if path.suffix.lower() == ".parquet":
        return pd.read_parquet(path)
    return pd.read_csv(path, low_memory=False)


def provider_candidates() -> list[Path]:
    root = PROJECT_ROOT
    return [
        root / "models" / "provider" / "provider_risk_scores.parquet",
        root / "models" / "provider" / "provider_risk_scores.csv",
        root / "models" / "provider" / "output" / "provider_risk_scores.csv",
        root / "data" / "curated" / "provider_risk_scores.parquet",
        root / "data" / "curated" / "provider_risk_scores.csv",
        root / "check_scores.csv",
    ]


def claim_candidates() -> list[Path]:
    root = PROJECT_ROOT
    return [
        root / "data" / "claims" / "final_unified_claim_risk.parquet",
        root / "data" / "claims" / "final_unified_claim_risk.csv",
        root / "data" / "curated" / "final_unified_claim_risk.parquet",
        root / "models" / "claims" / "claim_risk" / "final_unified_claim_risk.csv",
        root / "models" / "claims" / "claim_risk" / "_validation_before"
             / "final_unified_claim_risk.csv",
        root / "data" / "curated" / "fact_claim.parquet",
    ]
