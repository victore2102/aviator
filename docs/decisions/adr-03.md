# ADR-03: Model Selection, Harness Detection, and Package Namespace

**Date:** 2026-08-11
**Status:** Complete

**Supersedes:** the *Repository Structure* section of ADR-01.

---

## Context

Phase 1 produced a scorer that turns a query plus its history into an integer. That number is inert on its own. Phase 2 is the layer that makes it actionable: mapping a score to a concrete model, and knowing which harness the user is actually running so the mapping means something.

Three decisions came out of this phase — the tier-to-model mapping, the harness detection strategy, and a restructure of the package namespace. The third was not planned. It surfaced while writing `pyproject.toml` and is documented here because the reasoning generalizes.

---

## Model families, not pinned versions

The selector maps `score → tier → model family`, where a family is a keyword like `haiku`, `sonnet`, or `opus` rather than a pinned string like `claude-haiku-4-5`.

```yaml
claude_code:  fast: haiku       balanced: sonnet   powerful: opus
codex:        fast: mini        balanced: gpt      powerful: codex
gemini_cli:   fast: flash-lite  balanced: flash    powerful: pro
```

Model lineups change on a monthly cadence. Anthropic is on Opus 4.8 with a Fable tier above it; OpenAI's Codex naming has churned repeatedly; Gemini shipped 3.6 Flash while 3.5 Pro slipped. Pinning versions in config means every point release is a code change and a release of AvIator. Keying on the family name means the config outlives the lineup, and a separate resolver — not the config — owns turning a family keyword into whatever concrete model string is current.

**Known weaknesses in the mapping.** Claude Code is the reference case: it is a genuine three-rung capability ladder and maps cleanly. The other two do not.

- **Codex** is a general model, a mini, and a coding *specialist*. That is not a strict ladder — the specialist is not uniformly superior to the flagship, it is differently shaped.
- **Gemini** is fuzzy in a different way: newer Flash models now outperform older Pro models, so Flash no longer implies lower quality. The tier ordering holds on **cost**, which is what AvIator optimizes, but it is a weaker claim on capability than the Claude mapping.

These are accepted rather than solved. The tiers are a cost ladder first; where capability and cost diverge, cost wins, because saving money without losing quality is the tool's premise and cost is the axis it can actually measure.

---

## Harness detection abstains rather than guesses

**The principle: explicit config is the source of truth. Detection is a first-run convenience that must confirm, never assume.**

A pure-detection approach was rejected. The target user is an engineer who plausibly has all three CLIs installed and multiple API keys exported — the worst possible input for detection. And the failure mode is bad in a specific way: a routing tool that silently sends queries to the wrong provider fails *invisibly*. The user gets an answer. It just came from somewhere they did not intend, on a bill they did not expect.

`detect_harness()` therefore uses tiered confidence:

1. **Runtime env vars** set by the running CLI — strongest, because they mean the process is genuinely executing.
2. **Config directory presence** — medium; the CLI is set up, not necessarily active.
3. **Binary on PATH** — weakest; several can coexist indefinitely.

At each tier: exactly one match returns it, multiple matches return `None`. The function is built to abstain. `resolve_harness()` then runs explicit config → detection → raise with an actionable message. Detection never overrides an explicit choice.

**Signal verification.** The env var names and config paths began as structural guesses, which is a poor foundation for the thing that decides where a user's queries go. All three have since been verified — `claude_code` against a real install (Claude Code 2.1.222), `codex` and `gemini_cli` against upstream source, since neither CLI was available locally.

| Harness | Runtime marker | Config path | Source |
|---|---|---|---|
| `claude_code` | `CLAUDECODE=1` | `~/.claude` | live install |
| `codex` | `CODEX_SESSION_ID`, `CODEX_THREAD_ID` | `~/.codex/config.toml` | `codex-rs/core/src/exec_env.rs`, `protocol/src/shell_environment.rs` |
| `gemini_cli` | `GEMINI_CLI=1` | `~/.gemini/settings.json` | `packages/core/src/services/shellExecutionService.ts` |

Three of the six original guesses were wrong, and the failure mode was consistent: a plausible name off by a suffix. `CLAUDE_CODE_SESSION` is really `CLAUDE_CODE_SESSION_ID`; `CODEX_SESSION` is really `CODEX_SESSION_ID`; `CODEX_SANDBOX` does not exist at all. A near-miss on an environment variable name fails silently — the check simply never matches, detection abstains, and nothing signals that the constant is wrong. That is a strong argument for reading source rather than trusting recall, including one's own.

Two deliberate exclusions. `CODEX_SANDBOX_NETWORK_DISABLED` is real but only set when network sandboxing is off, so its absence proves nothing. `CLAUDE_CODE_SESSION_ID` is real but fires in exactly the cases `CLAUDECODE` already covers, so it adds no detection power. A signal is only worth listing if its presence is decisive and its absence is meaningful.

**A known limitation, now better understood.** Tier 1 only fires when AvIator runs inside the harness's process tree. Reading the upstream source sharpened this: in all three cases these variables are set specifically on child processes spawned by the harness's *shell tool*. So tier 1 fires when the harness runs AvIator for the user, and not when the user runs it themselves from their own terminal — which is the more common case, and precisely where an engineer with three CLIs installed gets `None`. Detection will therefore abstain more often than its design implies, and the interactive prompt in `aviator init` is the primary path rather than the fallback. This is acceptable because it fails toward asking rather than toward guessing.

---

## Defaults are code, config is data

An early version kept the tier mappings in a `router.yml` file shipped inside the package. That file was deleted and the mappings moved into `vars.py` as `DEFAULT_TIER_MAPPINGS`.

The distinction is ownership. `DEFAULT_TIER_MAPPINGS` is not user configuration — it is a constant that `aviator init` reads once to seed `~/.aviator/config.yaml`. The file users edit already exists, in their home directory, where it persists across projects and survives upgrades. A second YAML inside the installed package would live somewhere under `site-packages/`, where no user will find it and any edit is wiped by the next install. It would deliver none of the editability that motivated it, while costing a loader, package-data configuration, and error handling for a file whose contents ship with the code and therefore cannot be malformed.

Parsing a file you yourself control the contents of is strictly worse than a dict literal.

---

## One top-level package name

**ADR-01 specified `router/` and `cli/` as sibling top-level packages. This was wrong, and has been replaced by a single `aviator/` package.**

```
aviator/
├── __init__.py     public API surface
├── scorer.py       heuristic scoring engine
├── vars.py         weights, thresholds, vocabulary
├── selector.py     score → tier → model family
├── config.py       harness detection and config I/O
└── cli.py          Typer entrypoint
```

### Why the original layout was a defect

Python's import namespace is flat and global. Installing a distribution that ships a top-level `router/` copies that directory to the root of `site-packages`, which claims the bare word `router` for the entire environment. AvIator would not have been claiming `aviator.router` — it would have been claiming `router`, for every package in every environment that installs it. `cli` is worse still; it is about as generic as an identifier gets.

Two concrete failure modes follow. Another distribution shipping the same top-level name overwrites files in place, silently, because pip does not detect the collision. More likely, and worse: `sys.path` places the user's working directory ahead of `site-packages`, so a developer with their own `router.py` shadows the installed package entirely. A user integrating a *routing library* is unusually likely to have exactly that file. They would see an `ImportError` naming a file they wrote, and AvIator would look broken.

A library should own exactly one top-level name, and it should be the name it is installed under.

### Why rename rather than nest

Nesting the existing directories under a parent — `aviator/router/scorer.py` — would have fixed the collision. It was rejected because it stutters. The library *is* the router; `aviator.router.scorer` spends a path segment restating the package name, and every consumer pays for it on every import, permanently. Renaming `router/` to `aviator/` gives `from aviator import score_query` and makes the import path match the install name.

### What this costs from ADR-01

ADR-01 justified separating `cli/` from `router/` so the core library could be imported without the command-line interface. That goal is preserved: `aviator/cli.py` is a module nothing imports unless the CLI is wanted, and the core modules have no dependency on it. What is lost is only the directory boundary, which was enforcing a separation that a single module already achieves. Collapsing a one-file package into a module is honest about what it is.

The rename was done before the first `pip install -e .`, which matters — editable installs write path metadata keyed to directory names, and doing this later would have meant reinstalling as well as rewriting every import.

---

## Consequences

- `pyproject.toml` declares a single package, `aviator`, and a console script at `aviator.cli:app`.
- Every internal import is package-qualified as `from aviator.x import y`.
- `tier_for_score` moved from `scorer.py` to `selector.py`, where mapping a score to a tier belongs. The scorer now only produces numbers.
- `tests/__init__.py` is empty. It previously re-exported from the test module, which meant any `import tests` executed the full suite as a side effect.
- ADR-01's structure diagram is retained as a record of the original decision; this document supersedes it.

---

## Deliberately deferred

- **Config validation on startup.** Error messages here are the first thing a user sees when setup fails, so they warrant their own pass rather than being bolted on.
- **Selector boundary tests** at 0, 24, 25, 63, 64, 100 — the thresholds are the contract between the scorer and the selector, and nothing currently guards them.
- **The family → concrete model resolver.** The mapping stops at a family keyword today. Turning `opus` into a current model string is the piece that makes routing executable, and it belongs with the proxy layer that will consume it.
- **Honouring `CODEX_HOME` and `GEMINI_CLI_HOME`.** Both CLIs let the user relocate their config root. The tier-2 path check assumes the defaults, so a user who has moved theirs will simply not match at that tier — a missed detection, not a wrong one, which keeps it inside the abstain-don't-guess contract.
