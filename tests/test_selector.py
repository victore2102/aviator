"""
AvIator — selector tests.

The selector is the contract between the scorer (which produces numbers) and
everything downstream (which needs model strings). Two things are pinned here:

  1. Threshold SEMANTICS — the boundaries are half-open, [low, high), and the
     three tiers partition the whole score range with no gap and no overlap.
     These tests are written against FAST_MAX / BALANCED_MAX symbolically, so
     they keep passing when calibration moves the thresholds.

  2. Threshold VALUES — one test pins the literal 25 / 64. It exists to fail
     when the numbers change. That is deliberate: recalibration should be an
     explicit, visible act (see ADR-02), not something that slips through
     because every test was written in terms of the constants it changed.

Run with:  pytest tests/test_selector.py
"""

import pytest

from aviator.selector import tier_for_score, model_family_selection
from aviator.vars import FAST_MAX, BALANCED_MAX, DEFAULT_TIER_MAPPINGS

TIERS = ("fast", "balanced", "powerful")


# ═════════════════════════════════════════════════════════════
#  1. tier_for_score — boundary semantics
# ═════════════════════════════════════════════════════════════

class TestTierBoundaries:
    """
    Boundaries are half-open: a tier owns its lower bound and excludes its
    upper. FAST_MAX itself is the first 'balanced' score, not the last 'fast'
    one. Off-by-one here silently misroutes every query that lands on a
    boundary, so each edge is asserted from both sides.
    """

    @pytest.mark.parametrize(
        "score, expected",
        [
            (0, "fast"),                    # floor — score_query clamps at 0
            (1, "fast"),
            (FAST_MAX - 1, "fast"),         # last fast score
            (FAST_MAX, "balanced"),         # first balanced score
            (FAST_MAX + 1, "balanced"),
            (BALANCED_MAX - 1, "balanced"), # last balanced score
            (BALANCED_MAX, "powerful"),     # first powerful score
            (BALANCED_MAX + 1, "powerful"),
            (100, "powerful"),
            (10_000, "powerful"),           # no upper clamp — must not wrap
        ],
    )
    def test_boundary_values_map_to_expected_tier(self, score, expected):
        assert tier_for_score(score) == expected

    def test_negative_scores_fall_to_fast(self):
        """
        score_query clamps at 0 so this should be unreachable, but the
        selector is a public function and must not have an undefined region.
        """
        assert tier_for_score(-1) == "fast"


class TestTierPartition:
    """The three tiers must cover the range exhaustively and without overlap."""

    def test_every_score_maps_to_exactly_one_known_tier(self):
        for score in range(0, BALANCED_MAX + 50):
            assert tier_for_score(score) in TIERS

    def test_mapping_is_monotonic(self):
        """
        A higher score must never route to a cheaper tier. This is the
        property the whole cost argument rests on.
        """
        rank = {tier: i for i, tier in enumerate(TIERS)}
        scores = range(0, BALANCED_MAX + 50)
        ranks = [rank[tier_for_score(s)] for s in scores]
        assert ranks == sorted(ranks)

    def test_all_three_tiers_are_reachable(self):
        """Guards against a threshold change collapsing a tier to empty."""
        produced = {tier_for_score(s) for s in range(0, BALANCED_MAX + 50)}
        assert produced == set(TIERS)


class TestThresholdValuesArePinned:
    """
    Deliberately duplicates the constants as literals.

    Every other test here is symbolic and will follow the thresholds wherever
    calibration moves them. This one will not — it fails loudly on any change.
    If you changed FAST_MAX or BALANCED_MAX on purpose, re-run the calibration
    suite, confirm every tier still clears ~90%, then update these numbers in
    the same commit.
    """

    def test_fast_max_is_25(self):
        assert FAST_MAX == 25

    def test_balanced_max_is_64(self):
        assert BALANCED_MAX == 64

    def test_thresholds_are_ordered(self):
        assert 0 < FAST_MAX < BALANCED_MAX


# ═════════════════════════════════════════════════════════════
#  2. model_family_selection — score + harness → family
# ═════════════════════════════════════════════════════════════

class TestModelFamilySelection:
    """
    NOTE: these pin the CURRENT behaviour, which reads DEFAULT_TIER_MAPPINGS
    rather than the user's ~/.aviator/config.yaml. When the function is
    changed to honour user config, these tests must change with it — they are
    a description of today's contract, not an argument for keeping it.
    """

    @pytest.mark.parametrize("harness", sorted(DEFAULT_TIER_MAPPINGS))
    @pytest.mark.parametrize(
        "score, tier",
        [
            (0, "fast"),
            (FAST_MAX - 1, "fast"),
            (FAST_MAX, "balanced"),
            (BALANCED_MAX - 1, "balanced"),
            (BALANCED_MAX, "powerful"),
            (100, "powerful"),
        ],
    )
    def test_returns_the_family_mapped_to_the_scores_tier(self, harness, score, tier):
        expected = DEFAULT_TIER_MAPPINGS[harness][tier]
        assert model_family_selection(score, harness) == expected

    @pytest.mark.parametrize(
        "harness, score, expected",
        [
            ("claude_code", 10, "haiku"),
            ("claude_code", 40, "sonnet"),
            ("claude_code", 90, "opus"),
            ("codex", 10, "mini"),
            ("codex", 40, "gpt"),
            ("codex", 90, "codex"),
            ("gemini_cli", 10, "flash-lite"),
            ("gemini_cli", 40, "flash"),
            ("gemini_cli", 90, "pro"),
        ],
    )
    def test_known_pairs_resolve_to_expected_families(self, harness, score, expected):
        """Spelled out literally so a typo in the mapping table is visible."""
        assert model_family_selection(score, harness) == expected

    def test_unknown_harness_raises(self):
        """
        Currently a bare KeyError from the dict lookup. Pinned so that
        changing it to a clearer error is a deliberate, visible edit.
        """
        with pytest.raises(KeyError):
            model_family_selection(50, "not_a_harness")


class TestTierMappingTableIsWellFormed:
    """
    The mapping table is data, and data drifts. These guard the shape rather
    than the contents, so adding a harness stays cheap but adding a broken one
    does not.
    """

    def test_every_harness_defines_all_three_tiers(self):
        for harness, mapping in DEFAULT_TIER_MAPPINGS.items():
            assert set(mapping) == set(TIERS), f"{harness} tier keys are wrong"

    def test_every_family_is_a_non_empty_string(self):
        for harness, mapping in DEFAULT_TIER_MAPPINGS.items():
            for tier, family in mapping.items():
                assert isinstance(family, str) and family.strip(), (
                    f"{harness}.{tier} is not a usable model family"
                )

    def test_families_within_a_harness_are_distinct(self):
        """
        Two tiers sharing a family means routing between them changes nothing
        and the savings for that pair are illusory.
        """
        for harness, mapping in DEFAULT_TIER_MAPPINGS.items():
            families = list(mapping.values())
            assert len(set(families)) == len(families), (
                f"{harness} maps two tiers to the same family: {families}"
            )

    def test_table_covers_every_valid_harness(self):
        """DEFAULT_TIER_MAPPINGS and VALID_HARNESSES must not drift apart."""
        from aviator.config import VALID_HARNESSES

        assert set(DEFAULT_TIER_MAPPINGS) == VALID_HARNESSES
