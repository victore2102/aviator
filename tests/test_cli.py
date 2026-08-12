"""
AvIator — CLI tests.

Deliberately a starting point, not a full suite. It covers the two things
that have actually broken so far:

  1. The module imports at all. `aviator.cli` is the only module nothing else
     imports, so an ImportError in it is invisible to every other test — that
     is exactly how an undeclared `click` dependency took the whole CLI down
     while 105 tests stayed green.

  2. `init` resolves the harness in the documented priority order:
     --harness flag > confirmed detection > interactive prompt (ADR-03).
     Each of those is a branch, and the branch that overwrote the other two
     shipped once already.

Config I/O is redirected to a tmp_path in every test. Nothing here may read
or write the real ~/.aviator — a test suite that clobbers the developer's
own config is worse than no suite.

Run with:  pytest tests/test_cli.py
"""

import pytest
from typer.testing import CliRunner

from aviator import cli as cli_module
from aviator.cli import app
from aviator.vars import DEFAULT_TIER_MAPPINGS

runner = CliRunner()


@pytest.fixture
def config_path(tmp_path, monkeypatch):
    """
    Point config reads and writes at a throwaway file.

    aviator.config resolves CONFIG_PATH at import time, so both the config
    module and the CLI's own imported copy have to be patched — patching one
    leaves the other pointing at the user's home directory.
    """
    from aviator import config as config_module

    path = tmp_path / "config.yaml"
    monkeypatch.setattr(config_module, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(config_module, "CONFIG_PATH", path)
    monkeypatch.setattr(cli_module, "CONFIG_PATH", path)
    return path


@pytest.fixture
def no_detection(monkeypatch):
    """Force detection to abstain, so the prompt path is reachable."""
    monkeypatch.setattr(cli_module, "detect_harness", lambda: None)


def read_config(path):
    import yaml

    return yaml.safe_load(path.read_text())


# ═════════════════════════════════════════════════════════════
#  1. The module loads
# ═════════════════════════════════════════════════════════════

class TestModuleImports:
    """
    Cheap, and catches the failure class that the rest of the suite is blind
    to: anything that breaks at import time.
    """

    def test_app_is_importable(self):
        assert app is not None

    def test_help_runs(self):
        result = runner.invoke(app, ["--help"])
        assert result.exit_code == 0

    def test_both_commands_are_registered(self):
        result = runner.invoke(app, ["--help"])
        assert "init" in result.output
        assert "start" in result.output


# ═════════════════════════════════════════════════════════════
#  2. init — harness resolution priority
# ═════════════════════════════════════════════════════════════

class TestInitFlagPath:
    """--harness is the highest-priority source and must not be overridden."""

    def test_flag_writes_that_harness(self, config_path):
        result = runner.invoke(app, ["init", "--harness", "codex"])
        assert result.exit_code == 0
        assert read_config(config_path)["harness"] == "codex"

    def test_flag_wins_over_detection(self, config_path, monkeypatch):
        """
        The regression guard for the bug where the prompt result overwrote
        every earlier decision. Detection says claude_code, the flag says
        codex, and the flag must win.
        """
        monkeypatch.setattr(cli_module, "detect_harness", lambda: "claude_code")
        result = runner.invoke(app, ["init", "--harness", "codex"])
        assert result.exit_code == 0
        assert read_config(config_path)["harness"] == "codex"

    def test_flag_seeds_matching_tier_defaults(self, config_path):
        runner.invoke(app, ["init", "--harness", "gemini_cli"])
        written = read_config(config_path)
        assert written["tiers"] == DEFAULT_TIER_MAPPINGS["gemini_cli"]

    def test_unknown_harness_exits_nonzero_and_writes_nothing(self, config_path):
        result = runner.invoke(app, ["init", "--harness", "claude"])
        assert result.exit_code != 0
        assert not config_path.exists()


class TestInitDetectionPath:
    """Detection is offered, never assumed."""

    def test_confirmed_detection_is_used(self, config_path, monkeypatch):
        monkeypatch.setattr(cli_module, "detect_harness", lambda: "claude_code")
        result = runner.invoke(app, ["init"], input="y\n")
        assert result.exit_code == 0
        assert read_config(config_path)["harness"] == "claude_code"

    def test_declined_detection_falls_through_to_the_prompt(
        self, config_path, monkeypatch
    ):
        """Answering 'n' must not silently accept the detected value anyway."""
        monkeypatch.setattr(cli_module, "detect_harness", lambda: "gemini_cli")
        # decline detection, then pick option 2 (codex) from the sorted list
        result = runner.invoke(app, ["init"], input="n\n2\n")
        assert result.exit_code == 0
        assert read_config(config_path)["harness"] == "codex"


class TestInitPromptPath:
    """The numbered list must index the same order it displays."""

    @pytest.mark.parametrize(
        "choice, expected",
        [
            ("1", "claude_code"),
            ("2", "codex"),
            ("3", "gemini_cli"),
        ],
    )
    def test_each_number_selects_the_listed_harness(
        self, config_path, no_detection, choice, expected
    ):
        result = runner.invoke(app, ["init"], input=f"{choice}\n")
        assert result.exit_code == 0
        assert read_config(config_path)["harness"] == expected

    def test_out_of_range_choice_reprompts(self, config_path, no_detection):
        """An invalid entry must ask again, not crash or exit."""
        result = runner.invoke(app, ["init"], input="9\n1\n")
        assert result.exit_code == 0
        assert read_config(config_path)["harness"] == "claude_code"


class TestInitDoesNotClobber:
    """An existing config is protected unless the user says otherwise."""

    def test_declining_overwrite_leaves_config_untouched(self, config_path):
        runner.invoke(app, ["init", "--harness", "codex"])
        before = config_path.read_text()

        result = runner.invoke(app, ["init", "--harness", "claude_code"], input="n\n")
        assert result.exit_code == 0
        assert config_path.read_text() == before

    def test_force_overwrites_without_prompting(self, config_path):
        runner.invoke(app, ["init", "--harness", "codex"])
        result = runner.invoke(app, ["init", "--harness", "claude_code", "--force"])
        assert result.exit_code == 0
        assert read_config(config_path)["harness"] == "claude_code"

    def test_custom_tiers_survive_reinit_on_the_same_harness(self, config_path):
        """
        Re-running init against the SAME harness must not wipe a user's tier
        customisations.
        """
        runner.invoke(app, ["init", "--harness", "claude_code"])
        config_path.write_text(
            "harness: claude_code\ntiers:\n  fast: haiku\n  balanced: opus\n"
            "  powerful: opus\n"
        )
        runner.invoke(app, ["init", "--harness", "claude_code", "--force"])
        assert read_config(config_path)["tiers"]["balanced"] == "opus"


class TestInitHarnessSwitch:
    """
    Switching harness must replace the tier table, not preserve it.

    Keeping the old table produces a config that passes every structural
    check — right keys, non-empty string values — while naming models from
    the wrong provider. resolve_tiers cannot catch that, because 'mini' is a
    perfectly well-formed family name; it is just the wrong one.
    """

    def test_switching_harness_replaces_the_tier_defaults(self, config_path):
        runner.invoke(app, ["init", "--harness", "codex"])
        assert read_config(config_path)["tiers"] == DEFAULT_TIER_MAPPINGS["codex"]

        runner.invoke(app, ["init", "--harness", "gemini_cli", "--force"])
        written = read_config(config_path)
        assert written["harness"] == "gemini_cli"
        assert written["tiers"] == DEFAULT_TIER_MAPPINGS["gemini_cli"]

    def test_switching_harness_discards_customised_tiers(self, config_path):
        """
        Customisations are written in the old provider's vocabulary, so they
        do not survive a switch — the alternative is a config that silently
        names models the new harness cannot resolve.
        """
        runner.invoke(app, ["init", "--harness", "codex"])
        config_path.write_text(
            "harness: codex\ntiers:\n  fast: mini\n  balanced: mini\n"
            "  powerful: codex\n"
        )
        runner.invoke(app, ["init", "--harness", "claude_code", "--force"])
        assert read_config(config_path)["tiers"] == DEFAULT_TIER_MAPPINGS["claude_code"]

    def test_switch_is_announced(self, config_path):
        """A silent reset would look like lost data. Say it happened."""
        runner.invoke(app, ["init", "--harness", "codex"])
        result = runner.invoke(app, ["init", "--harness", "gemini_cli", "--force"])
        assert "codex" in result.output and "gemini_cli" in result.output

    def test_no_reset_message_on_a_fresh_config(self, config_path):
        result = runner.invoke(app, ["init", "--harness", "codex"])
        assert "reset to the defaults" not in result.output


# ═════════════════════════════════════════════════════════════
#  3. start — reads config, fails clearly
# ═════════════════════════════════════════════════════════════

class TestStart:
    def test_start_after_init_succeeds(self, config_path):
        runner.invoke(app, ["init", "--harness", "codex"])
        result = runner.invoke(app, ["start"])
        assert result.exit_code == 0
        assert "codex" in result.output

    def test_malformed_yaml_exits_cleanly(self, config_path, no_detection):
        """
        A broken config must produce a readable message and a non-zero exit,
        not a YAML parser traceback.
        """
        config_path.write_text("harness: claude_code\n\ttiers: bad\n")
        result = runner.invoke(app, ["start"])
        assert result.exit_code != 0
        assert result.exception is None or isinstance(result.exception, SystemExit)

    def test_unknown_tier_key_is_reported(self, config_path):
        """resolve_tiers should name the bad key rather than ignore it."""
        config_path.write_text(
            "harness: claude_code\ntiers:\n  ballanced: sonnet\n"
        )
        result = runner.invoke(app, ["start"])
        assert result.exit_code != 0
        assert "ballanced" in result.output
