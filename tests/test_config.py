"""
AvIator — config resolution tests.

Covers the two resolvers directly, without going through the CLI. The rule
under test throughout is the one ADR-03 is built on: an explicit setting is
the source of truth, and anything AvIator cannot honour is reported rather
than quietly replaced with something reasonable.

Run with:  pytest tests/test_config.py
"""

import pytest

from aviator import config as config_module
from aviator.config import ConfigError, resolve_harness, resolve_tiers
from aviator.vars import DEFAULT_TIER_MAPPINGS


@pytest.fixture
def no_detection(monkeypatch):
    """Force detection to abstain, isolating the explicit-config paths."""
    monkeypatch.setattr(config_module, "detect_harness", lambda: None)


@pytest.fixture
def detects_claude(monkeypatch):
    """Detection confidently returns claude_code."""
    monkeypatch.setattr(config_module, "detect_harness", lambda: "claude_code")


# ═════════════════════════════════════════════════════════════
#  resolve_harness
# ═════════════════════════════════════════════════════════════

class TestExplicitHarnessWins:
    @pytest.mark.parametrize("harness", sorted(DEFAULT_TIER_MAPPINGS))
    def test_valid_harness_is_returned(self, harness, detects_claude):
        assert resolve_harness({"harness": harness}) == harness

    def test_explicit_beats_detection(self, detects_claude):
        """Detection says claude_code; the config says codex. Config wins."""
        assert resolve_harness({"harness": "codex"}) == "codex"


class TestInvalidHarnessIsRejected:
    """
    The regression guard for the last silent-override hole: an unrecognised
    harness used to fall through to detection, so a typo routed the user's
    queries to a different provider with no indication anything was wrong.
    """

    def test_unknown_harness_raises(self, detects_claude):
        with pytest.raises(ConfigError):
            resolve_harness({"harness": "claude"})

    def test_error_names_the_bad_value_and_suggests_a_fix(self, detects_claude):
        with pytest.raises(ConfigError) as excinfo:
            resolve_harness({"harness": "claude"})
        message = str(excinfo.value)
        assert "claude" in message
        assert "claude_code" in message  # the 'did you mean' suggestion

    def test_error_lists_the_valid_options(self, detects_claude):
        with pytest.raises(ConfigError) as excinfo:
            resolve_harness({"harness": "nonsense"})
        message = str(excinfo.value)
        for harness in DEFAULT_TIER_MAPPINGS:
            assert harness in message

    def test_non_string_harness_raises(self, detects_claude):
        with pytest.raises(ConfigError):
            resolve_harness({"harness": 42})


class TestDetectionFallback:
    """Detection applies only when nothing is set at all."""

    def test_absent_harness_falls_back_to_detection(self, detects_claude):
        assert resolve_harness({}) == "claude_code"

    def test_null_harness_falls_back_to_detection(self, detects_claude):
        """`harness:` with no value parses to None, which means 'unset'."""
        assert resolve_harness({"harness": None}) == "claude_code"

    def test_raises_when_nothing_is_set_and_detection_abstains(self, no_detection):
        with pytest.raises(RuntimeError):
            resolve_harness({})


# ═════════════════════════════════════════════════════════════
#  resolve_tiers
# ═════════════════════════════════════════════════════════════

class TestResolveTiersDefaults:
    @pytest.mark.parametrize("harness", sorted(DEFAULT_TIER_MAPPINGS))
    def test_absent_tiers_block_uses_defaults(self, harness):
        config = {"harness": harness}
        assert resolve_tiers(config, harness) == DEFAULT_TIER_MAPPINGS[harness]

    def test_always_returns_all_three_tiers(self):
        resolved = resolve_tiers({"harness": "codex", "tiers": {}}, "codex")
        assert set(resolved) == {"fast", "balanced", "powerful"}


class TestResolveTiersMerging:
    """User values win per key; defaults fill the rest."""

    def test_partial_override_keeps_the_other_tiers(self):
        config = {"harness": "claude_code", "tiers": {"powerful": "fable"}}
        resolved = resolve_tiers(config, "claude_code")
        assert resolved["powerful"] == "fable"
        assert resolved["fast"] == DEFAULT_TIER_MAPPINGS["claude_code"]["fast"]
        assert resolved["balanced"] == DEFAULT_TIER_MAPPINGS["claude_code"]["balanced"]

    def test_missing_key_is_filled_rather_than_raising(self):
        """
        The fix for `config.get("tiers", DEFAULT)`, which was all-or-nothing:
        a block missing one key never triggered the fallback and died later
        inside the selector.
        """
        config = {"harness": "codex", "tiers": {"fast": "mini", "powerful": "codex"}}
        resolved = resolve_tiers(config, "codex")
        assert resolved["balanced"] == DEFAULT_TIER_MAPPINGS["codex"]["balanced"]

    def test_values_are_stripped(self):
        config = {"harness": "codex", "tiers": {"fast": "  mini  "}}
        assert resolve_tiers(config, "codex")["fast"] == "mini"


class TestResolveTiersRejectsMalformed:
    """
    A present-but-malformed block raises. Substituting defaults would discard
    what the user wrote and give them no way to notice.
    """

    def test_non_mapping_tiers_raises(self):
        with pytest.raises(ConfigError):
            resolve_tiers({"harness": "codex", "tiers": "mini"}, "codex")

    def test_unknown_tier_key_raises_with_a_suggestion(self):
        with pytest.raises(ConfigError) as excinfo:
            resolve_tiers({"harness": "codex", "tiers": {"ballanced": "gpt"}}, "codex")
        message = str(excinfo.value)
        assert "ballanced" in message
        assert "balanced" in message

    @pytest.mark.parametrize("bad_value", [None, 42, "", "   ", ["mini"]])
    def test_unusable_family_values_raise(self, bad_value):
        with pytest.raises(ConfigError):
            resolve_tiers({"harness": "codex", "tiers": {"fast": bad_value}}, "codex")

    def test_unknown_harness_raises(self):
        with pytest.raises(ConfigError):
            resolve_tiers({"harness": "nope"}, "nope")


class TestResolveTiersDoesNotMutateDefaults:
    """
    resolve_tiers copies the defaults before merging. Without the copy, one
    user override would permanently corrupt the shipped table for the rest of
    the process — a bug that would only appear on the second call.
    """

    def test_defaults_table_is_unchanged_after_an_override(self):
        before = dict(DEFAULT_TIER_MAPPINGS["claude_code"])
        resolve_tiers(
            {"harness": "claude_code", "tiers": {"fast": "opus"}}, "claude_code"
        )
        assert DEFAULT_TIER_MAPPINGS["claude_code"] == before
