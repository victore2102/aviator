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

model_family_selection takes a flat {tier: family} mapping, so these tests
build that mapping directly rather than reading config from disk. That is the
whole benefit of keeping the selector pure — no filesystem, no home directory,
no mocking.

Run with:  pytest tests/test_selector.py
"""

import pytest

from aviator.selector import tier_for_score, model_family_selection
from aviator.vars import FAST_MAX, BALANCED_MAX, DEFAULT_TIER_MAPPINGS

TIERS = ("fast", "balanced", "powerful")

# A mapping with no relationship to any real model, so a test failure points
# at the lookup logic rather than at whatever the defaults happen to say.
SENTINEL_TIERS = {"fast": "F", "balanced": "B", "powerful": "P"}


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
            (0, "fast"),                     # floor — score_query clamps at 0
            (1, "fast"),
            (FAST_MAX - 1, "fast"),          # last fast score
            (FAST_MAX, "balanced"),          # first balanced score
            (FAST_MAX + 1, "balanced"),
            (BALANCED_MAX - 1, "balanced"),  # last balanced score
            (BALANCED_MAX, "powerful"),      # first powerful score
            (BALANCED_MAX + 1, "powerful"),
            (100, "powerful"),
            (10_000, "powerful"),            # no upper clamp — must not wrap
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
        ranks = [rank[tier_for_score(s)] for s in range(0, BALANCED_MAX + 50)]
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
#  2. model_family_selection — score + tier mapping → family
# ═════════════════════════════════════════════════════════════

class TestModelFamilySelection:
    """Score picks the tier; the caller's mapping picks the family."""

    @pytest.mark.parametrize(
        "score, expected",
        [
            (0, "F"),
            (FAST_MAX - 1, "F"),
            (FAST_MAX, "B"),
            (BALANCED_MAX - 1, "B"),
            (BALANCED_MAX, "P"),
            (100, "P"),
        ],
    )
    def test_returns_the_family_for_the_scores_tier(self, score, expected):
        assert model_family_selection(score, SENTINEL_TIERS) == expected

    def test_uses_the_caller_supplied_mapping_not_the_defaults(self):
        """
        The regression guard for the bug this signature was introduced to fix:
        the selector previously read DEFAULT_TIER_MAPPINGS directly, so edits
        to the user's config had no effect on routing. A custom mapping must
        win, even when it contradicts the shipped defaults.
        """
        custom = {"fast": "opus", "balanced": "opus", "powerful": "haiku"}
        assert model_family_selection(10, custom) == "opus"
        assert model_family_selection(90, custom) == "haiku"

    def test_selector_does_not_depend_on_the_defaults_table(self):
        """
        Nothing in a routing decision should reference DEFAULT_TIER_MAPPINGS.
        Guards against the import quietly creeping back in.
        """
        import aviator.selector as selector

        assert not hasattr(selector, "DEFAULT_TIER_MAPPINGS"), (
            "selector.py should no longer import DEFAULT_TIER_MAPPINGS — "
            "tier mappings arrive as an argument"
        )

    @pytest.mark.parametrize("harness", sorted(DEFAULT_TIER_MAPPINGS))
    @pytest.mark.parametrize(
        "score, tier",
        [
            (0, "fast"),
            (FAST_MAX, "balanced"),
            (BALANCED_MAX, "powerful"),
        ],
    )
    def test_works_with_each_shipped_default_mapping(self, harness, score, tier):
        """
        The defaults are what init seeds, so every one of them must be a
        usable argument even though the selector no longer reaches for them.
        """
        mapping = DEFAULT_TIER_MAPPINGS[harness]
        assert model_family_selection(score, mapping) == mapping[tier]

    def test_incomplete_mapping_raises_on_the_missing_tier(self):
        """
        Pins today's behaviour: a partial mapping fails at routing time with a
        bare KeyError naming the tier. This is the failure config validation
        is meant to prevent from ever reaching here — when resolve_tiers()
        lands and fills gaps from the defaults, this test should be revisited.
        """
        partial = {"fast": "haiku", "powerful": "opus"}
        assert model_family_selection(10, partial) == "haiku"
        with pytest.raises(KeyError, match="balanced"):
            model_family_selection(FAST_MAX, partial)


# ═════════════════════════════════════════════════════════════
#  3. The shipped defaults table
# ═════════════════════════════════════════════════════════════

class TestTierMappingTableIsWellFormed:
    """
    The mapping table is data, and data drifts. These guard its shape rather
    than its contents, so adding a harness stays cheap but adding a broken one
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
