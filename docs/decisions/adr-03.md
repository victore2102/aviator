# ADR-03: Model Selection, Harness Detection, and Package Namespace

**Date:** 2026-08-11
**Status:** Complete

**Supersedes:** the *Repository Structure* section of ADR-01.

---

## Context

Phase 1 produced a scorer that turns a query plus its history into an integer. That number is inert on its own. Phase 2 is the layer that makes it actionable: mapping a score to a concrete model, and knowing which harness the user is actually running so the mapping means something.

Five decisions came out of this phase — the tier-to-model mapping, the harness detection strategy, where configuration defaults live, how invalid configuration is handled, and a restructure of the package namespace. The last was not planned. It surfaced while writing `pyproject.toml` and is documented here because the reasoning generalizes.

A theme runs through most of them, and it is worth naming up front: **the failure mode this phase kept producing was silence.** A tier table that was read from the wrong place. A near-miss environment variable that could never match. A `setdefault` that preserved values belonging to a different provider. None of these raised, logged, or looked wrong — each simply produced a plausible answer that was not the user's. Several decisions below are shaped by that, and the recurring rule is: when AvIator cannot honour what the user wrote, it must say so rather than substitute something reasonable.

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

At each tier: exactly one match returns it, multiple matches return `None`. The function is built to abstain. `resolve_harness()` then runs explicit config → detection → raise with an actionable message. Detection never overrides an explicit choice — including a wrong one. A `harness:` value that is set but unrecognised raises rather than falling through to detection, because falling through would route the user's queries to a different provider than the one they named, and would do it successfully. The two states are distinct: `harness:` absent means "not configured, please detect"; `harness: claude` means "configured, and wrong". Only the first is a question detection is entitled to answer.

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

### The bug this arrangement initially caused

Moving the defaults into code made them easy to reach, and the selector reached for them. Its first working version looked up `DEFAULT_TIER_MAPPINGS[harness][tier]` directly, which meant the `tiers:` block `aviator init` wrote to `~/.aviator/config.yaml` was never read by anything. Editing it changed nothing. `init` printed "edit the config to change" while the config was, in fact, decorative.

The fix was to make the selector take the mapping as an argument:

```python
model_family_selection(score: int, tier_mappings: dict) -> str
```

Two things fell out of that. The `harness` parameter disappeared, because the `tiers:` block written to config is already flattened for the chosen harness — the double lookup collapses to `tier_mappings[tier]`. And the selector became a pure function in the same sense the scorer is: no filesystem access, no module-level state, testable by passing a dict.

The alternative — having the selector call `load_config()` itself — was rejected for three reasons. It would perform disk I/O in a hot path called once per routed query. It would make the selector untestable without mocking a home directory. And its output would depend on invisible global state, which is exactly what the scorer was designed to avoid. **Configuration is loaded once at startup and passed down. Components that make decisions do not read files.**

---

## Invalid configuration is reported, not absorbed

`load_config()` originally returned whatever `yaml.safe_load` produced. Five distinct failure classes passed through it silently: an unrecognised harness value, a `tiers` block missing a key, a wrong type where a mapping was expected, malformed YAML, and unknown top-level keys.

Three pieces address this.

**`ConfigError(RuntimeError)`** marks a problem the user can fix by editing their file, as opposed to any other runtime failure. It subclasses `RuntimeError` so existing handlers keep working, while letting callers distinguish "your config is wrong" from a genuine bug. Its messages are user-facing and are expected to name the file, the offending key, and the fix.

**`load_config()`** now converts `yaml.YAMLError` and `OSError` into `ConfigError`, and rejects a non-mapping top level. The YAML parser's own line and column information is passed through rather than replaced, because it is the single most useful thing you can tell someone with a bad indent. This keeps `yaml` an implementation detail of `config.py` — the CLI never imports it.

**`resolve_tiers(config, harness)`** returns a complete `{tier: family}` mapping, merging user values over defaults per key. The earlier `config.get("tiers", DEFAULT_TIER_MAPPINGS[harness])` was all-or-nothing: a `tiers` block missing `balanced` never triggered the fallback and died later inside the selector. Per-key merging also means overriding one tier does not cost you the other two.

The rule it enforces is asymmetric, and deliberately so:

- An **absent** `tiers` block falls back to defaults silently. That is a normal fresh config.
- A **present but malformed** one raises. Substituting defaults here would discard what the user wrote and give them no way to notice.

Unknown tier keys raise rather than being ignored, for the same reason. Writing `ballanced: sonnet` and having AvIator quietly use the default `balanced` is undetectable from the user's side; `difflib.get_close_matches` turns it into `Did you mean 'balanced'?` instead.

**`init` is the escape hatch.** Its own `load_config()` call is wrapped, because the command you reach for to fix a broken config must not itself fail on one. It reports the parse error, confirms, then regenerates — confirming first because regeneration discards whatever was there.

### Switching harness resets the tier table

`init` seeds tiers with `setdefault` so re-running it never wipes a user's customisations. That is correct for a re-run against the *same* harness and wrong for a switch: changing `harness: codex` to `harness: gemini_cli` left the Codex families in place, producing a config that named models the new provider cannot resolve.

The discriminator is whether the harness changed. Same harness preserves existing tiers; a switch replaces them with the new harness's defaults and says so on stdout, because a silent reset is indistinguishable from lost data. Customisations are not migrated — they were written in the old provider's vocabulary and do not carry over.

### What validation deliberately does not check

It checks **shape, not meaning**. The broken config above — Gemini harness, Codex families — passes every check: three correct tier keys, non-empty string values, correct types. Structurally flawless, semantically undeliverable.

Cross-referencing families against `DEFAULT_TIER_MAPPINGS[harness]` would catch it and would also destroy the customisation the config exists to enable: a user who wants a family outside the shipped defaults is not making a mistake. **This gap ships knowingly.** The honest place for a meaning check is the family → concrete model resolver, which is the first component that can say "no such model for this provider" as a fact rather than a guess — and even there it should probably warn rather than reject.

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

### Declare what you import

A related packaging lesson landed the same way. `cli.py` used `click.IntRange` to bound the harness prompt, relying on `click` arriving transitively through Typer. Typer 0.27 vendors its own click and no longer declares it as a dependency, so the import failed and the entire CLI died at load — while all 105 tests stayed green, because nothing in the suite imported `aviator.cli`.

The prompt now uses `typer.prompt(..., value_proc=...)`, raising `typer.BadParameter` to re-ask on invalid input. That is public Typer API, needs no third-party import, and moves the index arithmetic into the parser so the caller only ever receives a validated harness name. Typer does expose the vendored library as `typer._click`, which is not used: a leading underscore is a promise of nothing, and reaching for it would recreate the same breakage one release later.

The general rule: **if you import it, declare it.** A transitive dependency is a dependency of someone else's package, and they are free to drop it.

---

## Consequences

- `pyproject.toml` declares a single package, `aviator`, and a console script at `aviator.cli:app`.
- Every internal import is package-qualified as `from aviator.x import y`.
- `tier_for_score` moved from `scorer.py` to `selector.py`, where mapping a score to a tier belongs. The scorer now only produces numbers.
- `model_family_selection` takes a flat `{tier: family}` mapping rather than a harness name, and no longer imports `DEFAULT_TIER_MAPPINGS`. The selector has no path to the defaults table.
- `config.py` owns all YAML handling and raises `ConfigError` for anything user-fixable. No other module imports `yaml`.
- `tests/__init__.py` is empty. It previously re-exported from the test module, which meant any `import tests` executed the full suite as a side effect.
- ADR-01's structure diagram is retained as a record of the original decision; this document supersedes it.

---

## Testing

The suite is 155 tests across four files.

`test_scorer.py` (66) is unchanged from Phase 1 and still gates tier accuracy at the calibration floor.

`test_selector.py` (39) pins the threshold contract. Most of it is written symbolically against `FAST_MAX` and `BALANCED_MAX`, so it follows calibration wherever it moves. One class deliberately duplicates the literals `25` and `64` so that a threshold change fails loudly and has to be acknowledged in the same commit — symbolic tests alone cannot distinguish an intended recalibration from an accidental one. The most valuable test in the file asserts **monotonicity**: a higher score must never route to a cheaper tier, which is the property the entire cost argument rests on.

`test_config.py` (27) covers both resolvers directly, without going through the CLI. The rule under test throughout is the one this document is built on — an explicit setting is honoured or reported, never quietly replaced.

`test_cli.py` (23) covers `init`'s resolution priority, the clobber guards, the harness-switch reset, and `start`'s failure paths. It exists because `aviator/cli.py` is the one module nothing else imports, which makes an import-time failure there invisible to every other test — a lesson learned the expensive way. Every test redirects `CONFIG_PATH` to a `tmp_path`; a suite that overwrites the developer's own `~/.aviator/config.yaml` would be worse than no suite.

---

## Deliberately deferred

- **The family → concrete model resolver.** The mapping stops at a family keyword today. Turning `opus` into a current model string is the piece that makes routing executable, and it belongs with the proxy layer that will consume it.
- **Model family semantic validation.** Shipped as a known gap, for the reason given above: the only check available today would cost the customisation the config exists to provide. It belongs with the resolver.
- **Honouring `CODEX_HOME` and `GEMINI_CLI_HOME`.** Both CLIs let the user relocate their config root. The tier-2 path check assumes the defaults, so a user who has moved theirs will simply not match at that tier — a missed detection, not a wrong one, which keeps it inside the abstain-don't-guess contract.
