from aviator.vars import DEFAULT_TIER_MAPPINGS, FAST_MAX, BALANCED_MAX

def tier_for_score(score: int) -> str:
    """Map a numeric score to a tier name using the module thresholds."""
    if score < FAST_MAX:
        return "fast"
    if score < BALANCED_MAX:
        return "balanced"
    return "powerful"


def model_family_selection(score: int, tier_mappings: dict) -> str:
    """
    Given AvIator score and tier mappings, return the model family to use.
    """
    tier = tier_for_score(score)
    model_family = tier_mappings[tier]
    return model_family