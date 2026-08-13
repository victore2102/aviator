<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="assets/aviator-dark.svg">
    <img src="assets/aviator-light.svg" alt="AvIator" width="620">
  </picture>
</p>

<p align="center">
  <a href="LICENSE"><img alt="License: MIT" src="https://img.shields.io/badge/license-MIT-blue.svg"></a>
  <img alt="Python 3.11+" src="https://img.shields.io/badge/python-3.11%2B-blue.svg">
  <img alt="Status: pre-alpha" src="https://img.shields.io/badge/status-pre--alpha-orange.svg">
</p>

<p align="center">
  <b>An LLM routing library that automatically selects the right model for each query —<br>
  so you stop paying frontier prices for simple tasks.</b>
</p>

---

> **⚠️ Work in progress — not yet usable.**
>
> The scoring engine, model selector, and configuration layer are complete and
> tested. The interception layer that would make routing actually happen is
> **under construction**. There is no working end-to-end path yet, and no
> release on PyPI. Watch the repo rather than installing it.

---

## The problem

Agentic coding harnesses — Claude Code, Codex, Gemini CLI — send every query to
whichever model you selected, whether you asked it to rename a variable or to
design a distributed system. The cheap questions are billed at frontier rates.

Switching manually is possible and nobody does it, because deciding which model
a question deserves is itself work, and it interrupts the one you were doing.

## The idea

AvIator scores each query, maps that score to a model tier, and substitutes the
model on the way out — without the harness, or you, doing anything differently.

```
query ──▶ scorer ──▶ score ──▶ selector ──▶ tier ──▶ resolver ──▶ model
                                              │
                                              └── capability gate ──▶ safe substitution
```

Two properties shape the whole design:

- **Harness-native.** Existing tools (LiteLLM, Portkey, OpenRouter) are gateways
  that sit in front of API calls you write yourself. AvIator intercepts the
  traffic of a CLI harness you are already running, so there is no code to
  change and no second API key.
- **Zero ongoing work.** One setup command. No per-query configuration, no
  rewriting your project, no new credentials.

## Status

| Component | State | Notes |
|---|---|---|
| Scoring engine | ✅ Complete | 95% tier accuracy on a 40-case labelled benchmark |
| Model selector | ✅ Complete | score → tier → model family |
| Configuration & harness detection | ✅ Complete | detects Claude Code, Codex, Gemini CLI; abstains when ambiguous |
| CLI (`init`, `start`) | ✅ Complete | |
| **Interception proxy** | 🚧 **In progress** | design complete, instrumented, not yet built |
| Provider adapters | ⬜ Not started | Anthropic first |
| Cost reporting | ⬜ Not started | |
| PyPI release | ⬜ Not started | |

**155 tests passing.** The scorer's calibration accuracy is itself a test, so a
weight change that degrades routing quality fails CI rather than shipping
quietly.

## What works today

```bash
git clone https://github.com/victore2102/aviator
cd aviator
python -m venv venv && venv/bin/pip install -e .
```

```bash
venv/bin/aviator init
```

`init` detects your harness (or asks), writes `~/.aviator/config.yaml`, and
seeds the tier mappings:

```yaml
harness: claude_code
tiers:
  fast: haiku
  balanced: sonnet
  powerful: opus
```

You can score queries directly:

```python
from aviator import score_query, tier_for_score

score = score_query("rename this variable to something clearer", history=())
tier_for_score(score)   # 'fast'
```

`aviator start` reads the config and validates it, but the routing handoff is
where the unbuilt proxy would go.

## Design

Every significant decision is written down before it is built. The reasoning,
the alternatives considered, and the tradeoffs accepted all live in
[`docs/decisions/`](docs/decisions/):

| ADR | Subject |
|---|---|
| [01](docs/decisions/adr-01.md) | Name and repository structure |
| [02](docs/decisions/adr-02.md) | Scoring criteria, weights, and calibration discipline |
| [03](docs/decisions/adr-03.md) | Model selection, harness detection, package namespace |
| [04](docs/decisions/adr-04.md) | Interception strategy, adapters, capability gate |
| [05](docs/decisions/adr-05.md) | Routing cadence and prompt-cache economics |

## What measurement changed

Before building the proxy, the wire traffic was instrumented — a pair of
throwaway diagnostics that record every request and response passing between the
harness and the provider, then substitute models to see what actually happens.
Findings are recorded in
[`docs/learnings/observation_testing.md`](docs/learnings/observation_testing.md).

Several overturned decisions that had already been made on reasoning alone:

- **A cheaper model can cost more.** Agentic harnesses depend on prompt caching,
  and switching models discards the entire cached prefix. Measured on a real
  session: a 54K-token conversation reads ~98% of its input from cache. Routing
  a simple question to a cheaper model can cost *more* than leaving it alone.
  Routing is therefore committed per conversation, not per turn.
- **A fresh session is not a cold cache.** Every session shares an identical
  ~37K-token system-and-tools prefix, so sessions warm each other's caches.
  "Route at session start, it's free" was assumed, and is false.
- **Substitution is viable — with limits.** Model substitution works through a
  live harness under subscription OAuth. But the cheapest tier rejects the
  request shape the harness produces, so the usable tier ladder is narrower than
  the design assumed.
- **Capabilities must be declared, not discovered.** The provider's models
  endpoint reports support at the *value* level. Discovering the same facts by
  trial and error costs a round trip per parameter and can never prove the set
  is complete.

This is the part of the project I would point at first. The routing heuristic is
ordinary; refusing to build on unverified assumptions is what kept the design
honest.

## Roadmap

- [x] **Phase 1** — Scoring engine
- [x] **Phase 2** — Model selection, harness detection, configuration
- [ ] **Phase 3** — Interception layer ← *current*
- [ ] **Phase 4** — Cost reporting and savings summary
- [ ] **Phase 5** — CLI and developer experience
- [ ] **Phase 6** — Documentation and PyPI release

Supported harnesses are Claude Code, Codex, and Gemini CLI. Detection and tier
mappings exist for all three; the Anthropic adapter ships first. Cursor and
OpenCode were investigated and rejected — reasoning in
[ADR-04](docs/decisions/adr-04.md).

## Security

The proxy binds to loopback only, never persists or logs credentials, keeps TLS
verification on for upstream traffic, and logs token counts and model names
rather than message content. The full posture, including what it deliberately
does not do, is in [ADR-04](docs/decisions/adr-04.md).

## Development

```bash
venv/bin/python -m pytest -q
```

The scorer's calibration report — per-case scores and tier accuracy, not
pass/fail:

```bash
venv/bin/python -m tests.test_scorer
```

After changing `pyproject.toml`, reinstall so the console script is regenerated:

```bash
venv/bin/pip install -e .
```

## License

MIT — see [LICENSE](LICENSE).
