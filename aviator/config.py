"""
AvIator — harness detection and configuration.

Source of truth is the user's config file (written by `aviator init`).
Detection is a best-effort convenience for first run only; it never
silently overrides an explicit choice.

NOTE: claude_code's signals are verified against a real install; codex and
gemini_cli are still starting guesses (see _HARNESS_SIGNALS). Verify each
against the actual CLI before trusting it — run the CLI and inspect its
environment and dotfiles. These values drift between versions.
"""

import os
import shutil
from pathlib import Path
import yaml


VALID_HARNESSES = {"claude_code", "codex", "gemini_cli"}

# Where AvIator stores its config. Kept in the user's home so it persists
# across projects and sessions, not tied to any one working directory.
CONFIG_DIR = Path.home() / ".aviator"
CONFIG_PATH = CONFIG_DIR / "config.yaml"


# ─────────────────────────────────────────────────────────────
#  Detection
# ─────────────────────────────────────────────────────────────

# Each entry: signals that suggest a given harness is active.
# Ordered from strongest signal (set by the running process) to weakest
# (merely installed).
#
# Status per harness is marked below. Anything still UNVERIFIED is a
# structural guess and must be confirmed by running that CLI and inspecting
# its environment (`env | grep -i <name>`) and dotfiles.
_HARNESS_SIGNALS = {
    # VERIFIED 2026-08-11 against Claude Code 2.1.222 (desktop entrypoint).
    "claude_code": {
        # CLAUDECODE=1 is the canonical marker — set in every entrypoint.
        # CLAUDE_CODE_ENTRYPOINT carries which surface is running
        # ("cli", "claude-desktop", ...); presence alone is the signal.
        # NOTE: the session var is CLAUDE_CODE_SESSION_ID, not
        # CLAUDE_CODE_SESSION. It is deliberately not listed — it is a
        # per-session detail, and CLAUDECODE already covers the same case.
        "runtime_env": ["CLAUDECODE", "CLAUDE_CODE_ENTRYPOINT"],
        # ~/.claude confirmed (holds projects/, sessions/, plugins/, ...).
        # No XDG variant is created; ~/.config/claude does not exist.
        "config_paths": ["~/.claude"],
        "binary": "claude",
    },
    # UNVERIFIED — no Codex install available to inspect.
    # Beware when checking by hand: macOS ships an unrelated Apple cryptex
    # path containing "codex" in PATH, so grep PATH for it at your peril.
    # `shutil.which` is unaffected.
    "codex": {
        "runtime_env": ["CODEX_SANDBOX", "CODEX_SESSION"],
        "config_paths": ["~/.codex/config.toml", "~/.codex"],
        "binary": "codex",
    },
    # UNVERIFIED — no Gemini CLI install available to inspect.
    "gemini_cli": {
        "runtime_env": ["GEMINI_CLI", "GEMINI_SESSION"],
        "config_paths": ["~/.gemini", "~/.config/gemini"],
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
    """Load AvIator config, or return an empty dict if none exists yet."""
    if not CONFIG_PATH.exists():
        return {}
    with open(CONFIG_PATH) as f:
        return yaml.safe_load(f) or {}


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
    if harness in VALID_HARNESSES:
        return harness

    # 2. No valid config — try detection as a convenience.
    detected = detect_harness()
    if detected:
        return detected

    # 3. Can't tell — ask, don't guess.
    raise RuntimeError(
        "Could not determine which harness to use.\n"
        "Run `aviator init` to set it, or add `harness: <name>` to "
        f"{CONFIG_PATH}.\nOptions: {', '.join(sorted(VALID_HARNESSES))}"
    )