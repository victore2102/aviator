"""
AvIator — scorer test + calibration suite.

Two layers:

  1. Unit tests (pytest)      — verify individual scorer components behave.
     Run with:  pytest test_scorer.py

  2. Calibration suite         — the labelled tier-accuracy benchmark.
     Run with:  python test_scorer.py
     (also exposed as test_calibration_accuracy for pytest/CI gating)

The calibration cases are the source of truth for tier tuning. When you change
weights in scorer.py, run the calibration and confirm every tier stays above
~90% before committing.
"""

import pytest

from router.scorer import (
    code_score,
    score_query,
    score_history,
    tier_for_score,
    _base_item_score,
    _keyword_score,
    FAST_MAX,
    BALANCED_MAX,
)


# ═════════════════════════════════════════════════════════════
#  Shared history building blocks
# ═════════════════════════════════════════════════════════════

# --- Prose turns ---
h_simple_question = "what kind of license do i need for this public repo? Do I need one?"

h_adr_request = """
i made a file named adr-01 which should be aimed to explain the name reasoning
and the file structure decision. help me draft this, this shouldn't be super
robust and should be straight to the point.
"""

h_explain_concept = """
explain what MCP servers are and how they differ from a standard REST API.
I want to understand when I would reach for one over the other.
"""

h_summarize = "summarize my requests to you thus far into bullet points"

h_definition = "what is gradient descent?"

# --- Code turns ---
h_code_explain = """
explain this code in detail and what it does:
    def forward(self, x):
        outs = None
        x = self.conv1(x)
        x = self.relu2(x)
        x = self.conv3(x)
        x = self.bn3(x)
        x = self.relu3(x)
        x = self.pool1(x)
        return outs
"""

h_code_snippet = """
why the * 1.3 in
tokens = len(query.split()) * 1.3
explain each piece to me and lets collaborate to put together a solid heuristic
"""

h_code_debug = """
this function keeps returning None and i cant figure out why:

    def get_user(db, user_id):
        result = db.query(f"SELECT * FROM users WHERE id = {user_id}")
        if result:
            return result[0]

can you spot the issue and fix it?
"""

h_code_refactor = """
refactor this class to separate concerns and improve readability:

    class DataProcessor:
        def __init__(self, path):
            self.path = path
            self.data = open(path).read()
            self.cleaned = self.data.strip().lower().split()
            self.counts = {}
            for w in self.cleaned:
                self.counts[w] = self.counts.get(w, 0) + 1

        def report(self):
            for k, v in sorted(self.counts.items(), key=lambda x: -x[1])[:10]:
                print(f"{k}: {v}")
"""

h_architecture_question = """
i'm designing a multi-tenant SaaS backend. should i use row-level security
in postgres, separate schemas per tenant, or separate databases? walk me through
the tradeoffs and what you would recommend for a team of 3 engineers that expects
to scale to 500 tenants within 18 months.
"""

h_pytorch_question = """
help me understand how the structure of a pytorch forward function works and
how to implement it in a custom model. I want to understand the flow of data
through the layers and how to properly define the forward method for my own
neural network architecture.
"""

# --- Pre-built history lists by length ---
history_empty = []
history_short = [h_simple_question, h_definition]
history_medium = [h_simple_question, h_adr_request, h_code_snippet, h_explain_concept]
history_long = [
    h_simple_question, h_adr_request, h_code_snippet, h_explain_concept,
    h_code_explain, h_code_debug, h_summarize, h_definition,
]
history_code_heavy = [h_code_explain, h_code_debug, h_code_refactor, h_code_snippet]
history_mixed = [
    h_simple_question, h_code_explain, h_explain_concept, h_code_debug,
    h_architecture_question, h_summarize,
]


# ═════════════════════════════════════════════════════════════
#  1. Unit tests — individual components
# ═════════════════════════════════════════════════════════════

class TestCodeScore:
    """code_score should distinguish prose from code."""

    def test_plain_prose_scores_low(self):
        assert code_score("what is the capital of france") < 3

    def test_code_block_scores_high(self):
        code = "def add(a, b):\n    return a + b"
        assert code_score(code) > 8

    def test_markdown_fence_detected(self):
        assert code_score("```python\nprint('hi')\n```") > code_score("print hi")

    def test_empty_string(self):
        assert code_score("") == 0

    def test_keyword_presence_raises_score(self):
        assert code_score("import os and from sys") > code_score("a walk in the park")

    def test_is_deterministic(self):
        text = "class Foo: pass"
        assert code_score(text) == code_score(text)


class TestKeywordScore:
    """_keyword_score should reward reasoning, penalise lookup/formatting."""

    def test_reasoning_words_add(self):
        assert _keyword_score("explain why this happens") > 0

    def test_lookup_words_subtract(self):
        # a pure lookup with no positive signal should be <= 0
        assert _keyword_score("what is a variable") <= 0

    def test_formatting_words_subtract(self):
        assert _keyword_score("summarize this into bullet points") <= 0

    def test_cap_is_enforced(self):
        # many reasoning + domain signals should not exceed the cap
        loaded = (
            "explain why compare contrast analyze design architect "
            "evaluate the tradeoffs and now that we discussed it"
        )
        assert _keyword_score(loaded) <= 40


class TestBaseItemScore:
    """_base_item_score reflects length and the current-query relevance bonus."""

    def test_current_query_gets_relevance_bonus(self):
        short = "hello there"
        assert _base_item_score(short, True) > _base_item_score(short, False)

    def test_longer_text_scores_higher(self):
        short = "short question here"
        long = " ".join(["word"] * 250)
        assert _base_item_score(long, False) > _base_item_score(short, False)


class TestHistoryScore:
    """score_history should grow with depth and content."""

    def test_empty_history_is_zero(self):
        assert score_history(()) == 0.0

    def test_more_turns_scores_higher(self):
        two = score_history(tuple(history_short))
        eight = score_history(tuple(history_long))
        assert eight > two

    def test_recency_weighting_favours_recent(self):
        # same two items, swapped order — the code-heavy item last should score
        # at least as high as code-heavy item first (recent weight >= old weight)
        code_last = score_history((h_simple_question, h_code_refactor))
        code_first = score_history((h_code_refactor, h_simple_question))
        assert code_last >= code_first


class TestTierMapping:
    """tier_for_score boundaries."""

    def test_below_fast_max_is_fast(self):
        assert tier_for_score(FAST_MAX - 1) == "fast"

    def test_at_fast_max_is_balanced(self):
        assert tier_for_score(FAST_MAX) == "balanced"

    def test_below_balanced_max_is_balanced(self):
        assert tier_for_score(BALANCED_MAX - 1) == "balanced"

    def test_at_balanced_max_is_powerful(self):
        assert tier_for_score(BALANCED_MAX) == "powerful"


class TestScoreQueryBehaviour:
    """End-to-end behavioural guarantees, independent of exact thresholds."""

    def test_score_is_non_negative(self):
        assert score_query("hi", []) >= 0

    def test_multimodal_forces_high_score(self):
        s = score_query("what does this diagram in the image show?", [])
        assert s >= 85

    def test_architecture_floors_at_powerful(self):
        s = score_query(
            "design a distributed system that can scale to millions of users", []
        )
        assert s >= BALANCED_MAX

    def test_simple_query_stays_fast(self):
        assert tier_for_score(score_query("what is a closure?", [])) == "fast"

    def test_code_query_beats_prose_query(self):
        prose = score_query("tell me about your day", [])
        code = score_query(h_code_debug, [])
        assert code > prose

    def test_history_raises_score(self):
        no_hist = score_query(h_summarize, [])
        with_hist = score_query(h_summarize, history_code_heavy)
        assert with_hist > no_hist


# ═════════════════════════════════════════════════════════════
#  2. Calibration suite — labelled tier accuracy
# ═════════════════════════════════════════════════════════════

# (label, query, history, expected_tier)
CALIBRATION_CASES = [

    # ── FAST — simple lookups, no history ──────────────────────────────
    ("Simple license question",
     "what kind of license do i need for this public repo?", history_empty, "fast"),
    ("Single word definition",
     "what is a closure?", history_empty, "fast"),
    ("Short factual question",
     "what does MCP stand for?", history_empty, "fast"),
    ("Formatting task — short input",
     "summarize this into three bullet points: the sky is blue, water is wet, fire is hot",
     history_empty, "fast"),
    ("Translation task",
     "translate this to spanish: hello, how are you today?", history_empty, "fast"),
    ("Simple yes/no framing",
     "do i need a virtual environment for a small python script?", history_empty, "fast"),
    ("Short rewrite task",
     "rewrite this sentence to be more formal: hey can u send me the doc",
     history_empty, "fast"),
    # extra fast cases
    ("Unit conversion lookup",
     "how many bytes are in a kilobyte?", history_empty, "fast"),
    ("Command lookup",
     "what is the command to list files in a directory?", history_empty, "fast"),
    ("Spelling check",
     "how do you spell necessary?", history_empty, "fast"),

    # ── FAST — simple queries with shallow prose history ───────────────
    ("Simple lookup + short prose history",
     "what is the default python version on ubuntu 24?", history_short, "balanced"),
    ("Formatting + short prose history",
     "format this as a numbered list: cats, dogs, birds, fish", history_short, "fast"),

    # ── BALANCED — code explanation, no history ────────────────────────
    ("Code explain — forward function", h_code_explain, history_empty, "balanced"),
    ("Code debug — single function", h_code_debug, history_empty, "balanced"),
    ("Explain technical concept in depth",
     "explain how attention mechanisms work in transformers. I want to understand the math behind scaled dot-product attention.",
     history_empty, "balanced"),
    ("Multi-part prose reasoning",
     "compare row-level security vs separate schemas in postgres. what are the tradeoffs for a SaaS product?",
     history_empty, "balanced"),
    ("Step-by-step implementation request",
     "walk me through how to implement jwt authentication in a fastapi app from scratch",
     history_empty, "balanced"),
    ("Pytorch forward function explanation", h_pytorch_question, history_empty, "balanced"),
    ("Code refactor — class separation", h_code_refactor, history_empty, "balanced"),
    ("Why question on tokenization", h_code_snippet, history_empty, "balanced"),
    # extra balanced cases
    ("Debug prose description",
     "my flask app returns a 500 error only in production but works locally. help me figure out what could be different and why this might be happening.",
     history_empty, "balanced"),
    ("Explain a design pattern",
     "explain the repository pattern and when I should use it over an active record approach",
     history_empty, "balanced"),

    # ── BALANCED — prose queries with code history ─────────────────────
    ("Simple lookup + code-heavy history",
     "what is the default python version on ubuntu 24?", history_code_heavy, "balanced"),
    ("Short formatting + code-heavy history",
     "summarize what we have discussed", history_code_heavy, "balanced"),
    ("Factual question + medium mixed history",
     "what is gradient descent?", history_medium, "balanced"),

    # ── BALANCED — multi-step with short history ───────────────────────
    ("Multi-step setup + short history",
     "walk me through setting up a postgres database with docker, creating a user, and connecting from python",
     history_short, "balanced"),
    ("Code explain + short history", h_code_explain, history_short, "balanced"),

    # ── POWERFUL — complex code + code history ─────────────────────────
    ("Pytorch explanation + code-heavy history",
     h_pytorch_question, history_code_heavy, "powerful"),
    ("Code refactor + code-heavy history",
     h_code_refactor, history_code_heavy, "powerful"),
    ("Architecture design + code-heavy history",
     h_architecture_question, history_code_heavy, "powerful"),
    ("Code debug + long history", h_code_debug, history_long, "powerful"),

    # ── POWERFUL — architectural + mixed long history ──────────────────
    ("Architecture tradeoffs + long mixed history",
     h_architecture_question, history_long, "powerful"),
    ("Architecture tradeoffs + mixed history",
     h_architecture_question, history_mixed, "powerful"),

    # ── POWERFUL — dense technical prose, no history ───────────────────
    ("Transformer deliverable — multimodal correlation",
     """
     Does this correlate with this equation in this image? Explain this equation to me.
     Deliverable 3 — feedforward layer. Concept: attention mixed information across tokens.
     The feedforward layer now processes each token's vector independently — no cross-token
     interaction. Constructor — two nn.Linear and one nn.LayerNorm. Method — 3 lines:
     linear1 → torch.relu → linear2, then norm(inputs + result). Same LayerNorm(x + Sublayer(x))
     pattern you already wrote in D2.
     """,
     history_empty, "powerful"),
    ("System design — multi-tenant SaaS, no history",
     h_architecture_question, history_empty, "powerful"),

    # ── POWERFUL — compound prior context references ───────────────────
    ("Prior context reference + code history",
     "based on the refactor you did earlier, now design a caching layer that sits in front of the DataProcessor class and handles ttl expiry",
     history_code_heavy, "powerful"),
    ("Deliverable chain reference + medium history",
     "now that we established the forward pass in d2, implement the full training loop with gradient clipping, a cosine lr scheduler, and mixed precision using torch.cuda.amp",
     history_medium, "powerful"),

    # ── POWERFUL — long history regardless of query simplicity ─────────
    ("Simple question + very long mixed history",
     "what should i do next?", history_long, "powerful"),
    ("Summarize + long code-heavy history",
     "summarize everything we have built so far", history_long, "powerful"),
    # extra powerful case
    ("Distributed system design, no history",
     "design a fault tolerant distributed job queue with high availability and horizontal scaling across regions",
     history_empty, "powerful"),
]


# Accuracy floor the suite must clear to pass in CI.
MIN_OVERALL_ACCURACY = 0.88


def _evaluate():
    """Run every calibration case, return (results, passed, total)."""
    results = []
    passed = 0
    for label, query, history, expected in CALIBRATION_CASES:
        score = score_query(query, history)
        actual = tier_for_score(score)
        ok = actual == expected
        passed += int(ok)
        results.append((label, len(history), score, expected, actual, ok))
    return results, passed, len(CALIBRATION_CASES)


def test_calibration_accuracy():
    """CI gate: overall tier accuracy must stay above the floor."""
    _, passed, total = _evaluate()
    accuracy = passed / total
    assert accuracy >= MIN_OVERALL_ACCURACY, (
        f"Calibration accuracy {accuracy:.1%} fell below "
        f"{MIN_OVERALL_ACCURACY:.0%} floor ({passed}/{total})"
    )


@pytest.mark.parametrize("case", CALIBRATION_CASES, ids=lambda c: c[0])
def test_individual_cases_are_within_one_tier(case):
    """No single case should be off by more than one tier (never fast<->powerful)."""
    _, query, history, expected = case
    order = {"fast": 0, "balanced": 1, "powerful": 2}
    actual = tier_for_score(score_query(query, history))
    assert abs(order[actual] - order[expected]) <= 1


# ═════════════════════════════════════════════════════════════
#  3. Standalone calibration report  (python test_scorer.py)
# ═════════════════════════════════════════════════════════════

def run_calibration():
    COL_LABEL, COL_HIST, COL_SCORE, COL_EXP, COL_ACT, COL_PASS = 45, 10, 7, 12, 12, 5

    header = (
        f"{'Label':<{COL_LABEL}} {'History':>{COL_HIST}} {'Score':>{COL_SCORE}} "
        f"{'Expected':<{COL_EXP}} {'Actual':<{COL_ACT}} {'Pass':>{COL_PASS}}"
    )
    divider = "─" * len(header)

    results, passed, total = _evaluate()
    accuracy = (passed / total) * 100 if total else 0

    tier_totals = {"fast": [0, 0], "balanced": [0, 0], "powerful": [0, 0]}
    for _, _, _, expected, _, ok in results:
        tier_totals[expected][1] += 1
        tier_totals[expected][0] += int(ok)

    print()
    print("  AvIator — Scoring Calibration Report")
    print(f"  Thresholds: fast < {FAST_MAX}  |  balanced < {BALANCED_MAX}  |  powerful >= {BALANCED_MAX}")
    print()
    print(divider)
    print(header)
    print(divider)

    prev_expected = None
    for label, hist_len, score, expected, actual, ok in results:
        if expected != prev_expected:
            if prev_expected is not None:
                print()
            tag = f"── {expected.upper()} "
            print(f"{tag}{'─' * (len(divider) - len(tag))}")
            prev_expected = expected

        mark = "✓" if ok else "✗"
        color = "\033[32m" if ok else "\033[31m"
        reset = "\033[0m"
        print(
            f"{color}{label:<{COL_LABEL}} {hist_len:>{COL_HIST}} {score:>{COL_SCORE}} "
            f"{expected:<{COL_EXP}} {actual:<{COL_ACT}} {mark:>{COL_PASS}}{reset}"
        )

    print()
    print(divider)
    print(f"  Overall accuracy: {passed}/{total}  ({accuracy:.1f}%)")
    print()
    print(f"  {'Tier':<12} {'Passed':>8} {'Total':>8} {'Accuracy':>10}")
    print(f"  {'─'*12} {'─'*8} {'─'*8} {'─'*10}")
    for tier, (p, t) in tier_totals.items():
        acc = (p / t * 100) if t else 0
        print(f"  {tier:<12} {p:>8} {t:>8} {acc:>9.1f}%")
    print()

    failing = [r for r in results if not r[5]]
    if failing:
        print("  Failing cases:")
        for label, hist_len, score, expected, actual, _ in failing:
            print(f"    ✗  {label}")
            print(f"       score={score}  expected={expected}  got={actual}  history_len={hist_len}")
        print()


if __name__ == "__main__":
    run_calibration()