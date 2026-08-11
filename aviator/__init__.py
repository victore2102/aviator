"""
AvIator — query scoring and model routing.

Public API:
    score_query(query, history)             -> int    score a query in context
    score_history(history)                  -> float  score prior turns alone
    code_score(text)                        -> float  code-likelihood of text
    tier_for_score(score)                   -> str    map a score to a tier
    model_family_selection(score, harness)  -> str    map a score to a family

    FAST_MAX, BALANCED_MAX                            tier threshold constants
"""

from aviator.scorer import (
    score_query,
    score_history,
    code_score,
)
from aviator.selector import (
    tier_for_score,
    model_family_selection,
)
from aviator.vars import (
    FAST_MAX,
    BALANCED_MAX,
)

__all__ = [
    "score_query",
    "score_history",
    "code_score",
    "tier_for_score",
    "model_family_selection",
    "FAST_MAX",
    "BALANCED_MAX",
]

__version__ = "0.1.0"
