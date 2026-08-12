"""
AvIator — query scoring engine.

Scores an incoming query (plus its conversation history) on a 0(N) complexity
scale. A downstream selector maps that score to a model tier:

    fast      score < FAST_MAX        cheap model  (e.g. Haiku)
    balanced  FAST_MAX <= score < BALANCED_MAX   mid model (e.g. Sonnet)
    powerful  score >= BALANCED_MAX    top model    (e.g. Opus)

The weights below were calibrated against a 34-case benchmark (see
test_scorer.py) to ~94% tier accuracy. They are deliberate, not arbitrary —
do not change them casually. Re-run the calibration after any change and
confirm every tier stays above ~90%.
"""

import re
from functools import lru_cache

from aviator.vars import (
    FAST_MAX,
    BALANCED_MAX,
    RECENCY_DECAY,
    MAX_DEPTH_BONUS,
    DEPTH_BONUS_PER_TURN,
    RELEVANCE_BONUS,
    CODE_BONUS_HEAVY,
    CODE_BONUS_SOME,
    CODE_BONUS_LIGHT,
    CODE_SCORE_HEAVY,
    CODE_SCORE_SOME,
    CODE_SCORE_LIGHT,
    WEIGHT_STEP,
    WEIGHT_REASONING,
    WEIGHT_LOOKUP,
    WEIGHT_FORMATTING,
    WEIGHT_DOMAIN_DEPTH,
    KEYWORD_SCORE_CAP,
    CODE_HEAVY_TURN_THRESHOLD,
    CODE_HEAVY_MIN_TURNS,
    CODE_HEAVY_BONUS_PER_TURN,
    HISTORY_RECENT_WINDOW,
    MULTIMODAL_FLOOR,
    ARCHITECTURE_SIGNAL_MIN,
    CODE_INDICATORS,
    CODE_KEYWORDS,
    CODE_FORMAT_INDICATORS,
    REASONING_WORDS,
    STEP_WORDS,
    LOOKUP_WORDS,
    FORMATTING_WORDS,
    DOMAIN_DEPTH_SIGNALS,
    MULTIMODAL_SIGNALS,
    ARCHITECTURE_SIGNALS,
)


# ─────────────────────────────────────────────────────────────
#  Code detection
# ─────────────────────────────────────────────────────────────

@lru_cache(maxsize=256)
def code_score(text: str) -> float:
    """
    Return a code-likelihood score for a piece of text.

    Combines three independent signals:
      - keyword hits:    programming-language reserved words
      - syntax hits:     operators, comments, object.method access, etc.
      - formatting hits: markdown fences and indented blocks

    Higher means more likely to contain code. Not normalised — the absolute
    value is only meaningful relative to the thresholds in score_query.
    """
    lowered = text.lower()

    # Keyword score — count tokens that are reserved words
    words = re.findall(r"\b\w+\b", lowered)
    keyword_hits = sum(word in CODE_KEYWORDS for word in words)

    # Syntax score — regex over operators, comments, method access, indentation
    syntax_hits = len(CODE_INDICATORS.findall(text))

    # Formatting score — markdown fences plus indented blocks
    fence_hits = len(re.findall(CODE_FORMAT_INDICATORS["markdown_fence"], text))
    indent_hits = len(re.findall(CODE_FORMAT_INDICATORS["indentation"], text))
    formatting_hits = fence_hits + indent_hits

    return keyword_hits * 2 + syntax_hits * 1.5 + formatting_hits * 3


# code_score is already cached; this alias exists for call-site clarity where
# we mean "the cached lookup" rather than "compute the score".
cached_code_score = code_score


# ─────────────────────────────────────────────────────────────
#  History scoring (recurrence + caching)
# ─────────────────────────────────────────────────────────────

@lru_cache(maxsize=256)
def _base_item_score(text: str, is_current_query: bool) -> float:
    """
    Raw complexity score for a single turn, with no recency weighting.

    Recency is applied by the recurrence in _history_weighted_sum; this
    function only measures the turn's own weight from token count (inflated
    by any code content) plus a flat bonus if it is the live query.

    Cached on (text, is_current_query) so identical turns never recompute.
    """
    score = 0.0

    multiplier = min(1.3 + (code_score(text) / 100), 4.0)
    tokens = len(text.split()) * multiplier

    if tokens > 500:
        score += 50
    elif tokens > 200:
        score += 20
    elif tokens > 80:
        score += 8

    if is_current_query:
        score += RELEVANCE_BONUS

    return score


@lru_cache(maxsize=256)
def _history_weighted_sum(history: tuple) -> float:
    """
    Recency-weighted sum of history items via the recurrence:

        S(h) = base(h[-1]) + RECENCY_DECAY * S(h[:-1])

    Because each call only needs the cached sum of the prior sub-history,
    appending one new turn costs a single new computation — every earlier
    sub-history is already memoised. Weights of older turns decay by one
    factor of RECENCY_DECAY per turn automatically.

    Takes a tuple (not a list) so the argument is hashable and cacheable.
    """
    if not history:
        return 0.0

    prior_sum = _history_weighted_sum(history[:-1])   # cache hit after first pass
    return _base_item_score(history[-1], False) + RECENCY_DECAY * prior_sum


def score_history(history: tuple) -> float:
    """Depth bonus plus recency-weighted content sum for a full history tuple."""
    if not history:
        return 0.0
    depth_bonus = min(len(history) * DEPTH_BONUS_PER_TURN, MAX_DEPTH_BONUS)
    return depth_bonus + _history_weighted_sum(history)


# ─────────────────────────────────────────────────────────────
#  Signal helpers
# ─────────────────────────────────────────────────────────────

def _code_presence_bonus(query_code: float) -> int:
    """Direct score bonus for code in the live query, keyed off code_score bands."""
    if query_code > CODE_SCORE_HEAVY:
        return CODE_BONUS_HEAVY
    if query_code > CODE_SCORE_SOME:
        return CODE_BONUS_SOME
    if query_code > CODE_SCORE_LIGHT:
        return CODE_BONUS_LIGHT
    return 0


def _keyword_score(lowered: str) -> int:
    """
    Net keyword contribution, capped at KEYWORD_SCORE_CAP.

    Positive: step words, reasoning words, domain-depth signals.
    Negative: lookup words, formatting words (these route toward cheaper models).
    The cap stops compound queries that hit many keywords from overshooting.
    """
    raw = 0
    raw += sum(WEIGHT_STEP for w in STEP_WORDS if w in lowered)
    raw += sum(WEIGHT_REASONING for w in REASONING_WORDS if w in lowered)
    raw -= sum(WEIGHT_LOOKUP for w in LOOKUP_WORDS if w in lowered)
    raw -= sum(WEIGHT_FORMATTING for w in FORMATTING_WORDS if w in lowered)
    raw += sum(WEIGHT_DOMAIN_DEPTH for w in DOMAIN_DEPTH_SIGNALS if w in lowered)
    return min(raw, KEYWORD_SCORE_CAP)


def _code_heavy_history_bonus(history: list) -> int:
    """
    Bonus for an ongoing technical session: if enough of the most recent turns
    are code-heavy, the conversation is clearly technical and deserves a nudge.
    Only the last HISTORY_RECENT_WINDOW turns are considered — older turns have
    already decayed through the recency-weighted sum.
    """
    recent = history[-HISTORY_RECENT_WINDOW:]
    code_heavy_turns = sum(1 for h in recent if code_score(h) > CODE_HEAVY_TURN_THRESHOLD)
    if code_heavy_turns >= CODE_HEAVY_MIN_TURNS:
        return code_heavy_turns * CODE_HEAVY_BONUS_PER_TURN
    return 0


# ─────────────────────────────────────────────────────────────
#  Public API
# ─────────────────────────────────────────────────────────────

def score_query(query: str, history: list) -> int:
    """
    Score a query in the context of its conversation history.

    Returns a non-negative integer complexity score. Map it to a tier with
    tier_for_score(). Higher means a more capable (and more expensive) model
    is warranted.

    Args:
        query:   the live user message.
        history: prior turns as a list of strings, oldest first. May be empty.
    """
    lowered = query.lower()
    score = 0.0

    # --- Live query base + code presence ---
    score += _base_item_score(query, True)
    score += _code_presence_bonus(code_score(query))

    # --- Keyword signals (capped) ---
    score += _keyword_score(lowered)

    # --- History ---
    if history:
        score += score_history(tuple(history))
        score += _code_heavy_history_bonus(history)

    # --- Hard-floor overrides (applied last so nothing can undercut them) ---
    if any(sig in lowered for sig in MULTIMODAL_SIGNALS):
        score = max(score, MULTIMODAL_FLOOR)

    arch_hits = sum(1 for w in ARCHITECTURE_SIGNALS if w in lowered)
    if arch_hits >= ARCHITECTURE_SIGNAL_MIN:
        score = max(score, BALANCED_MAX)

    return max(0, int(score))
