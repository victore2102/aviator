"""
AvIator — query scoring and model routing.

Public API:
    score_query(query, history) -> int      score a query in context
    tier_for_score(score)       -> str       map a score to a tier name
    code_score(text)            -> float     code-likelihood of a piece of text

    FAST_MAX, BALANCED_MAX                    tier threshold constants
"""

from router.scorer import (
    score_query,
    tier_for_score,
    score_history,
    code_score,
    FAST_MAX,
    BALANCED_MAX,
)

__all__ = [
    "score_query",
    "tier_for_score",
    "score_history",
    "code_score",
    "FAST_MAX",
    "BALANCED_MAX",
]

__version__ = "0.1.0"