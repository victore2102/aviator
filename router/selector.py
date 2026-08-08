from router.vars import FAST_MAX, BALANCED_MAX
from router.scorer import score_query

def tier_for_score(score: int) -> str:
    """Map a numeric score to a tier name using the module thresholds."""
    if score < FAST_MAX:
        return "fast"
    if score < BALANCED_MAX:
        return "balanced"
    return "powerful"


model_tier = tier_for_score(query_score)