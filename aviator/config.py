"""
AvIator — harness detection and configuration.

Source of truth is the user's config file (written by `aviator init`).
Detection is a best-effort convenience for first run only; it never
silently overrides an explicit choice.

NOTE: all three harnesses' signals are verified — claude_code against a real
install, codex and gemini_cli against their upstream source (see
_HARNESS_SIGNALS for provenance per entry). These values drift between
versions, so re-check them before each release.
"""

import difflib
import os
import shutil
from pathlib import Path
import yaml

from aviator.vars import DEFAULT_TIER_MAPPINGS


VALID_HARNESSES = {"claude_code", "codex", "gemini_cli"}

TIER_NAMES = ("fast", "balanced", "powerful")


class ConfigError(RuntimeError):
    """
    A user-fixable problem with the config file.

    Subclasses RuntimeError so existing `except RuntimeError` handlers still
    catch it, but lets callers distinguish "your config is wrong" from any
    other runtime failure. The message is user-facing: it should name the
    file, the offending key, and the fix.
    """

CONFIG_DIR = Path.home() / ".aviator"
CONFIG_PATH = CONFIG_DIR / "config.yaml"


# ─────────────────────────────────────────────────────────────
#  Detection
# ─────────────────────────────────────────────────────────────

# Each entry: signals that suggest a given harness is active.
# Ordered from strongest signal (set by the running process) to weakest
# (merely installed).
_HARNESS_SIGNALS = {
    "claude_code": {
        "runtime_env": ["CLAUDECODE", "CLAUDE_CODE_ENTRYPOINT"],
        "config_paths": ["~/.claude"],
        "binary": "claude",
    },
    "codex": {
        "runtime_env": ["CODEX_SESSION_ID", "CODEX_THREAD_ID"],
        "config_paths": ["~/.codex/config.toml", "~/.codex"],
        "binary": "codex",
    },
    "gemini_cli": {
        "runtime_env": ["GEMINI_CLI"],
        "config_paths": ["~/.gemini/settings.json", "~/.gemini"],
        "binary": "gemini",
    },
}


def _check_runtime_env(signals) -> bool:
    """True if any runtime env var for this harness is set and non-empty."""
    return any(os.environ.get(var) for var in signals["runtime_env"])


def _check_config_paths(signals) -> bool:
    """True if any of this harness's config files/dirs exist."""
    return any(Path(p).expanduser().exists() for p in signals["config_paths"])


def _check_binary(signals) -> bool:
    """True if this harness's CLI binary is on PATH."""
    return shutil.which(signals["binary"]) is not None


def detect_harness() -> str | None:
    """
    Best-effort detection of the active harness.

    Tiered by signal strength:
      1. Runtime env vars set by the running CLI — strongest, unambiguous
         when present because they mean the process is actually executing.
      2. Config-dir presence — medium; means the CLI is set up, not that
         it's the active one.
      3. Binary on PATH — weakest; many CLIs can coexist.

    Returns a single harness name only when the evidence is UNAMBIGUOUS at
    the strongest available tier. If multiple harnesses tie at the same
    tier, returns None — better to ask than to guess wrong.
    """
    # Tier 1 — runtime env (strongest). If exactly one matches, trust it.
    env_matches = [
        name for name, sig in _HARNESS_SIGNALS.items()
        if _check_runtime_env(sig)
    ]
    if len(env_matches) == 1:
        return env_matches[0]
    if len(env_matches) > 1:
        return None  # conflicting runtime signals — don't guess

    # Tier 2 — config dirs. Only decisive if exactly one harness is set up.
    config_matches = [
        name for name, sig in _HARNESS_SIGNALS.items()
        if _check_config_paths(sig)
    ]
    if len(config_matches) == 1:
        return config_matches[0]
    if len(config_matches) > 1:
        return None  # multiple CLIs configured — ambiguous

    # Tier 3 — binary presence. Only decisive if exactly one is installed.
    binary_matches = [
        name for name, sig in _HARNESS_SIGNALS.items()
        if _check_binary(sig)
    ]
    if len(binary_matches) == 1:
        return binary_matches[0]

    # Zero matches or an ambiguous tie — caller should ask the user.
    return None


# ─────────────────────────────────────────────────────────────
#  Config load / save
# ─────────────────────────────────────────────────────────────

def load_config() -> dict:
    """
    Load AvIator config, or return an empty dict if none exists yet.

    A missing file is not an error — it means "not set up yet", and callers
    distinguish that from a broken file. Anything that IS an error is raised
    as ConfigError so that yaml and OS exceptions stay an implementation
    detail of this module rather than leaking to the CLI.
    """
    if not CONFIG_PATH.exists():
        return {}

    try:
        with open(CONFIG_PATH) as f:
            loaded = yaml.safe_load(f)
    except yaml.YAMLError as e:
        # The parser error carries line/column info that is genuinely useful,
        # so it is surfaced rather than replaced with a generic message.
        raise ConfigError(
            f"Could not parse {CONFIG_PATH} — it is not valid YAML.\n\n{e}"
        ) from e
    except OSError as e:
        raise ConfigError(f"Could not read {CONFIG_PATH}: {e}") from e

    # An empty file parses to None, which is a legitimate "nothing set yet".
    if loaded is None:
        return {}

    # A scalar or list at the top level parses fine but is not a config.
    if not isinstance(loaded, dict):
        raise ConfigError(
            f"{CONFIG_PATH} should contain a mapping of settings, but its "
            f"top level is a {type(loaded).__name__}.\n"
            "Expected something like:\n"
            "  harness: claude_code\n"
            "  tiers:\n"
            "    fast: haiku\n"
            "Delete the file and re-run `aviator init` to regenerate it."
        )

    return loaded


def save_config(config: dict) -> None:
    """Write config to disk, creating the config dir if needed."""
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    with open(CONFIG_PATH, "w") as f:
        yaml.safe_dump(config, f, default_flow_style=False, sort_keys=False)


def resolve_harness(config: dict | None = None) -> str:
    """
    Return the harness to use. Explicit config wins; detection is fallback.
    Raises if neither yields a confident answer.
    """
    config = config if config is not None else load_config()

    # 1. Explicit config is the source of truth.
    harness = config.get("harness")
    if harness is not None:
        if harness in VALID_HARNESSES:
            return harness
        # Set, but not a harness we know. Falling through to detection here
        # would silently route the user's queries to a different provider
        # than the one they named — the worst failure this tool can have,
        # because it succeeds. An explicit wrong answer beats a silent one.
        raise ConfigError(
            f"{CONFIG_PATH}: '{harness}' is not a known harness."
            f"{_suggest(harness, VALID_HARNESSES)}\n"
            f"Valid options: {', '.join(sorted(VALID_HARNESSES))}"
        )

    # 2. Nothing set at all — try detection as a convenience.
    detected = detect_harness()
    if detected:
        return detected

    # 3. Can't tell — ask, don't guess.
    raise RuntimeError(
        "Could not determine which harness to use.\n"
        "Run `aviator init` to set it, or add `harness: <name>` to "
        f"{CONFIG_PATH}.\nOptions: {', '.join(sorted(VALID_HARNESSES))}"
    )

def _suggest(value: str, options) -> str:
    """Return a ' Did you mean X?' fragment, or '' if nothing is close."""
    close = difflib.get_close_matches(str(value).lower(), sorted(options), n=1)
    return f" Did you mean '{close[0]}'?" if close else ""


def resolve_tiers(config: dict | None = None, harness: str | None = None) -> dict:
    """
    Return a complete {tier: model_family} mapping for the active harness.

    User config wins per key, defaults fill the gaps. Partial customisation is
    legitimate — overriding only `powerful` should not cost you the other two.

    The distinction that matters: an ABSENT `tiers` block is normal and falls
    back to defaults silently. A PRESENT but malformed one raises, because
    silently substituting defaults would discard what the user wrote and give
    them no way to tell — the same invisible-failure class that made the
    selector ignore config in the first place.
    """
    config = config if config is not None else load_config()
    harness = harness if harness is not None else resolve_harness(config)

    if harness not in DEFAULT_TIER_MAPPINGS:
        raise ConfigError(
            f"No default tier mapping exists for harness '{harness}'."
            f"{_suggest(harness, DEFAULT_TIER_MAPPINGS)}"
        )

    resolved = dict(DEFAULT_TIER_MAPPINGS[harness])

    user_tiers = config.get("tiers")
    if user_tiers is None:
        return resolved

    if not isinstance(user_tiers, dict):
        raise ConfigError(
            f"{CONFIG_PATH}: `tiers` should be a mapping of tier names to "
            f"model families, but it is a {type(user_tiers).__name__}.\n"
            "Expected:\n"
            "  tiers:\n"
            "    fast: haiku\n"
            "    balanced: sonnet\n"
            "    powerful: opus"
        )

    for tier, family in user_tiers.items():
        # An unrecognised key is almost always a typo. Falling through to the
        # default here would silently ignore the user's edit, so it raises.
        if tier not in TIER_NAMES:
            raise ConfigError(
                f"{CONFIG_PATH}: '{tier}' is not a known tier."
                f"{_suggest(tier, TIER_NAMES)}\n"
                f"Valid tiers: {', '.join(TIER_NAMES)}"
            )

        if not isinstance(family, str) or not family.strip():
            raise ConfigError(
                f"{CONFIG_PATH}: tier '{tier}' should map to a model family "
                f"name, but its value is {family!r}.\n"
                f"For example:  {tier}: {DEFAULT_TIER_MAPPINGS[harness][tier]}"
            )

        resolved[tier] = family.strip()

    return resolved
