"""Ranking pipeline — criteria compute, BRRRR/NPV, Pareto filter, TOPSIS.

Spec: BATCH_DESIGN.md §C. 13 criteria with directions and weights summing to
1.00. Hard-fail rows pin topsis_score=0 / pareto_efficient=false regardless
of the raw criterion values.

Weights are named constants here; any future tuning is a one-file edit.
"""
from __future__ import annotations

import math
from typing import Any

# ----- 13 criteria (see §C.1) ----------------------------------------------
# (name, direction, weight)
CRITERIA: list[tuple[str, str, float]] = [
    ("net_piti",              "cost",    0.18),
    ("cash_to_close",         "cost",    0.12),
    ("effective_rehab",       "cost",    0.10),
    ("dti_headroom",          "benefit", 0.08),
    ("coc_pct",               "benefit", 0.10),
    ("npv_5yr",               "benefit", 0.10),
    ("brrrr_equity_capture",  "benefit", 0.08),
    ("zip_tier_score",        "benefit", 0.08),
    ("cap_rate",              "benefit", 0.05),
    ("contractor_edge",       "benefit", 0.04),
    ("dom",                   "benefit", 0.03),
    ("roof_age",              "cost",    0.02),
    ("price_vs_zip_median",   "cost",    0.02),
]
CRITERION_NAMES = [c[0] for c in CRITERIA]


# ----- BRRRR + NPV formulas (§C.2) ----------------------------------------


def brrrr_equity_capture(arv: float, all_in_cost: float) -> float:
    if arv <= 0:
        return 0.0
    return max(-1.0, min(1.0, (arv - all_in_cost) / arv))


def npv_5yr(
    *,
    purchase: float,
    gross_rent_monthly: float,
    piti_monthly: float,
    opex_monthly: float,
    vacancy_pct: float = 5.0,
    maintenance_pct: float = 5.0,
    appreciation: float = 0.03,
    discount_rate: float = 0.08,
    rate_pct: float = 6.5,
    loan_amount: float | None = None,
    term_years: int = 30,
) -> float:
    """5-yr NPV of cash flows + sale - loan balance, per §C.2."""
    if purchase <= 0:
        return 0.0
    vac_frac = vacancy_pct / 100.0
    maint_frac = maintenance_pct / 100.0
    net_rent_annual = 12 * gross_rent_monthly * (1 - vac_frac - maint_frac)
    cf_annual = net_rent_annual - 12 * (piti_monthly + opex_monthly)
    npv_cf = sum(cf_annual / ((1 + discount_rate) ** t) for t in range(1, 6))
    sale = purchase * ((1 + appreciation) ** 5)
    # Loan balance after 60 payments (fixed-rate amortization).
    if loan_amount is None or loan_amount <= 0:
        loan_bal = 0.0
    else:
        n = term_years * 12
        r = rate_pct / 100 / 12
        if r == 0:
            pmt = loan_amount / n
            loan_bal = max(0.0, loan_amount - pmt * 60)
        else:
            pmt = loan_amount * (r * (1 + r) ** n) / ((1 + r) ** n - 1)
            loan_bal = loan_amount * (1 + r) ** 60 - pmt * (((1 + r) ** 60 - 1) / r)
            loan_bal = max(0.0, loan_bal)
    npv_exit = (sale - loan_bal) / ((1 + discount_rate) ** 5)
    return npv_cf + npv_exit


# ----- Pareto + TOPSIS ----------------------------------------------------


def _dir_sign(direction: str) -> int:
    return +1 if direction == "benefit" else -1


def pareto_mask(matrix: list[list[float]], directions: list[str]) -> list[bool]:
    """Return a list of booleans — True iff that row is Pareto-efficient."""
    n = len(matrix)
    if n == 0:
        return []
    signs = [_dir_sign(d) for d in directions]
    # Pre-sign the matrix so higher is always better.
    signed = [[signs[j] * matrix[i][j] for j in range(len(directions))] for i in range(n)]
    efficient = [True] * n
    for i in range(n):
        if not efficient[i]:
            continue
        for j in range(n):
            if i == j or not efficient[j]:
                continue
            # j dominates i iff j >= i on every criterion and > on at least one.
            ge_all = all(signed[j][k] >= signed[i][k] for k in range(len(directions)))
            gt_any = any(signed[j][k] > signed[i][k] for k in range(len(directions)))
            if ge_all and gt_any:
                efficient[i] = False
                break
    return efficient


def topsis_scores(
    matrix: list[list[float]],
    weights: list[float],
    directions: list[str],
) -> list[float]:
    """Vanilla TOPSIS (§C.4). Returns a score in [0,1] per row."""
    n = len(matrix)
    if n == 0:
        return []
    k = len(weights)
    # 1. Normalize columns by vector norm.
    col_norms: list[float] = []
    for j in range(k):
        s = math.sqrt(sum((row[j] or 0.0) ** 2 for row in matrix))
        col_norms.append(s if s > 0 else 1.0)
    norm = [[(matrix[i][j] or 0.0) / col_norms[j] for j in range(k)] for i in range(n)]
    # 2. Weight.
    weighted = [[norm[i][j] * weights[j] for j in range(k)] for i in range(n)]
    # 3. Ideal / anti-ideal.
    a_plus: list[float] = []
    a_minus: list[float] = []
    for j in range(k):
        col = [weighted[i][j] for i in range(n)]
        if directions[j] == "benefit":
            a_plus.append(max(col))
            a_minus.append(min(col))
        else:
            a_plus.append(min(col))
            a_minus.append(max(col))
    # 4. Distances.
    scores: list[float] = []
    for i in range(n):
        d_plus = math.sqrt(sum((weighted[i][j] - a_plus[j]) ** 2 for j in range(k)))
        d_minus = math.sqrt(sum((weighted[i][j] - a_minus[j]) ** 2 for j in range(k)))
        denom = d_plus + d_minus
        scores.append(d_minus / denom if denom > 0 else 0.0)
    return scores


# ----- Criteria extraction --------------------------------------------------


_ZIP_TIER_SCORE = {"tier1": 3, "tier2": 2, "tier3": 1}


def criteria_from_metrics(metrics: dict[str, Any]) -> dict[str, float]:
    """Build the 13-criterion row dict from the rich per-property metrics."""
    zip_tier = metrics.get("zip_tier") or "outside"
    return {
        "net_piti":              float(metrics.get("net_piti") or 0.0),
        "cash_to_close":         float(metrics.get("cash_to_close") or 0.0),
        "effective_rehab":       float(metrics.get("effective_rehab") or 0.0),
        "dti_headroom":          float(metrics.get("dti_headroom") or 0.0),
        "coc_pct":               float(metrics.get("coc_pct") or 0.0),
        "npv_5yr":               float(metrics.get("npv_5yr") or 0.0),
        "brrrr_equity_capture":  float(metrics.get("brrrr_equity_capture") or 0.0),
        "zip_tier_score":        float(_ZIP_TIER_SCORE.get(zip_tier, 0)),
        "cap_rate":              float(metrics.get("cap_rate") or 0.0),
        "contractor_edge":       float(metrics.get("contractor_edge") or 0.0),
        "dom":                   float(metrics.get("dom") or 0.0),
        "roof_age":              float(metrics.get("roof_age") or 10.0),
        "price_vs_zip_median":   float(metrics.get("price_vs_zip_median") or 0.0),
    }


# ----- Batch-wide rank -----------------------------------------------------


def rank_batch(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Mutate-and-return rows with `rank`, `topsis_score`, `pareto_efficient`.

    Each input row is expected to contain `criteria` (dict keyed by §C.1 name),
    and `hard_fail` (bool). Hard-fail rows pin score 0 / pareto false.
    """
    # Split hard-fail from scoring set.
    scoring = [(i, r) for i, r in enumerate(rows) if not r.get("hard_fail")]
    directions = [d for _, d, _ in CRITERIA]
    weights = [w for _, _, w in CRITERIA]

    if scoring:
        matrix = [
            [float(r["criteria"].get(name, 0.0)) for name in CRITERION_NAMES]
            for _, r in scoring
        ]
        pareto = pareto_mask(matrix, directions)
        scores = topsis_scores(matrix, weights, directions)
        for idx_in_scoring, (_, row) in enumerate(scoring):
            row["topsis_score"] = round(scores[idx_in_scoring], 4)
            row["pareto_efficient"] = bool(pareto[idx_in_scoring])
    for r in rows:
        if r.get("hard_fail"):
            r["topsis_score"] = 0.0
            r["pareto_efficient"] = False
        else:
            r.setdefault("topsis_score", 0.0)
            r.setdefault("pareto_efficient", False)

    # Sort — hard-fails go last. Stable.
    ordered = sorted(rows, key=lambda r: (r.get("hard_fail", False), -(r.get("topsis_score") or 0.0)))
    for rank, r in enumerate(ordered, start=1):
        r["rank"] = rank
    return ordered
