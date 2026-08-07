# ADR-02: Scoring Criteria and Heuristic Values

**Date:** 2026-08-07
**Status:** Complete

---

## Context

AvIator routes each query to one of three model tiers — `fast`, `balanced`, or `powerful` — based on a single integer complexity score. This document explains what the scorer measures, why each signal exists, and how the specific weights and thresholds were chosen.

The core principle: the score is **additive and history-aware**. Every signal contributes to one running total, and conversation history always counts. There are no per-query special cases beyond two deliberate hard-floor overrides. This keeps the scorer maintainable — a new contributor can reason about the whole system by reading one function.

---

## Tiers and thresholds

```
fast      score < 25
balanced  25 <= score < 64
powerful  score >= 64
```

The thresholds were not chosen up front. They were derived by calibrating against a labelled benchmark of 40 representative queries (see `tests/test_scorer.py`) and tuning until each tier held above ~90% accuracy. The final values reached 95% overall.

`FAST_MAX = 25` sits just above where simple lookups and formatting tasks land. `BALANCED_MAX = 64` is the equilibrium point between single technical queries and genuinely complex work — it was moved from an earlier value of 60 when the code-detection vocabulary was expanded, which shifted the whole score distribution upward. That move is documented here because it illustrates the key discipline: **thresholds follow the calibration data, they are not set by intuition.**

---

## What the scorer measures

The score is built from five contributions plus two overrides.

### 1. Base query weight (token count)

Longer queries are, on average, more complex. The base score keys off an estimated token count — word count multiplied by a factor that rises with code content, since code tokenizes more densely than prose.

```
tokens > 500  → +50
tokens > 200  → +20
tokens > 80   → +8
```

A flat `RELEVANCE_BONUS = 10` is added to the live query only. The query the user just asked matters more than any single history item, so it gets a small floor that history items do not.

### 2. Code presence (direct bonus)

Code content is detected independently by `code_score`, which combines three signals: programming keywords, syntax patterns (operators, comments, `object.method` access), and formatting (markdown fences, indentation). The result drives a direct score bonus in bands:

```
code_score > 15  → +20   substantial code block
code_score > 8   → +12   some code syntax
code_score > 3   → +6    light code signals
```

Code content affects the score twice — once by inflating the token multiplier, once through this direct bonus. This is intentional: a query containing a code block is almost always a genuine engineering task, and double-counting reflects that a code block is a stronger signal than its length alone suggests. The direct bonuses were reduced from an earlier `(25/15/8)` to the current `(20/12/6)` when the detection regex was widened, to prevent single code queries from over-shooting into the powerful tier.

### 3. Keyword signals (capped)

The scorer maintains keyword lists that push the score in either direction:

```
STEP_WORDS          +10 each   sequencing / workflow language
REASONING_WORDS     +15 each   explanation, comparison, analysis, design
DOMAIN_DEPTH_SIGNALS +18 each   cross-references, prior-context, validation asks
LOOKUP_WORDS        -2 each    definitions, simple factual queries
FORMATTING_WORDS    -3 each    summarize, rewrite, translate, reformat
```

Reasoning and domain-depth signals are weighted highest because they most reliably indicate a query needs a capable model. Lookup and formatting signals subtract, because those tasks are handled well by cheaper models — a definition or a reformat rarely needs frontier capability.

The net keyword contribution is capped at `KEYWORD_SCORE_CAP = 40`. Without the cap, a compound query that happens to hit many keywords ("explain, compare, and analyze the tradeoffs...") would overshoot on keyword matches alone. The cap ensures keywords inform the score without dominating it.

### 4. History depth and content

History contributes in two ways. First, a depth bonus scaling with conversation length:

```
depth_bonus = min(turns * 5, 35)
```

Longer conversations need stronger models to maintain coherence across accumulated context. The cap (`MAX_DEPTH_BONUS = 35`) prevents a very long but trivial conversation from inflating the score on length alone.

Second, a recency-weighted sum of each history item's own complexity, computed via the recurrence:

```
S(history) = base(latest) + RECENCY_DECAY * S(prior)
```

with `RECENCY_DECAY = 0.8`. Recent turns carry near-full weight; older turns decay geometrically. This reflects that a query's context is shaped most by what was just discussed, and progressively less by earlier turns. The recurrence form is deliberate — it lets the weighted sum be computed incrementally and cached, so appending a turn costs one new computation rather than a full re-scan (see the caching notes in `scorer.py`).

### 5. Code-heavy session bonus

If enough of the recent turns contain code, the session is clearly technical and gets an extra nudge:

```
if 2+ of the last 4 turns have code_score > 8:
    bonus = code_heavy_turns * 5
```

This captures a case the per-turn scoring misses: a user deep in an implementation session asking a short follow-up question. The question itself may be trivial, but answering it well requires holding the technical context — so the session's code density lifts the score.

---

## Hard-floor overrides

Two signals bypass additive scoring entirely and force a minimum tier. These exist because some capabilities are cliffs, not slopes — no amount of other signals should route around them.

### Multimodal → floor at 85 (powerful)

```
if query references an image/diagram/figure:
    score = max(score, 85)
```

Cross-modal reasoning (parsing an image, correlating it to text) is a capability that weaker models handle poorly regardless of how simple the surrounding text looks. A query referencing an image is forced to the powerful tier even if every other signal reads as trivial.

### Architecture → floor at BALANCED_MAX (powerful)

```
if 2+ architecture signals present (design, distributed, multi-tenant, ...):
    score = max(score, 64)
```

System design and architecture questions are inherently high-value reasoning tasks. The `2+` requirement prevents a single casual "design" from over-triggering — the floor only applies when multiple architecture signals co-occur, indicating a genuine design question rather than incidental word use.

Both overrides are applied *last*, after all additive scoring, so nothing computed earlier can undercut them.

---

## Why these values, and why they will change

The specific numbers above are calibrated, not fundamental. They were tuned against a 40-case benchmark and represent the best fit found for that set. Two things follow from this:

First, **the values should not be changed casually.** Any edit to a weight, a threshold, or the keyword vocabulary shifts the score distribution and can move borderline cases across a tier boundary. The calibration suite exists to catch exactly this — it must be re-run after any change, with every tier confirmed above ~90% before the change is committed.

Second, **the heuristic is a v1 baseline, not a final answer.** Rule-based scoring against a hand-labelled set risks overfitting to that set. The real signal about whether the weights generalize will come from production traffic and the cascade fallback (a cheap model answers first, escalating only when a confidence check fails). That feedback loop — not further hand-tuning against synthetic examples — is the intended path to improving the scorer.

---

## Consequences

- The scorer is fully transparent and debuggable: every routing decision can be explained by listing which signals fired and their weights.
- Tuning is centralized: all values live as named constants at the top of `router/vars.py`, changed in one place.
- The calibration suite gates changes, so the scoring criteria cannot silently drift.
- The design accepts occasional borderline mis-routing (a query scoring 63 vs 64) as the inherent fuzziness at tier boundaries, rather than adding special cases to force specific outcomes.