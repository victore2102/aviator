# Learnings: Wire Observation

**Status:** Living document. Appended to after each observation run.
**Instruments:** `scripts/observe.py` (passive), `scripts/substitute.py` (active)
**Last updated:** 2026-08-12, after Run 3.

---

## Purpose

The ADRs were written before any real traffic had been seen. Several of their
load-bearing claims were reasoned rather than measured, and ADR-05 in particular
rests on assumptions about prompt caching that were explicitly flagged as
unverified.

This document records what the wire actually shows. It is the evidence layer
beneath the decision records: where a finding here contradicts an ADR, the ADR
is wrong and should be amended, and the amendment should cite this file.

**Nothing identifying goes in this document.** Captures contain real prompts,
source code, file paths, session identifiers, and account identifiers. Only
structure, field names, and aggregate numbers are recorded here. `captures/` is
gitignored and must stay that way.

---

## Method

So that runs stay comparable as the instrument and the harness both change.

| | |
|---|---|
| Instrument | `scripts/observe.py`, stdlib only, forwards verbatim |
| Active variant | `scripts/substitute.py`, subclasses the observer's one `transform()` hook |
| Redirection | `ANTHROPIC_BASE_URL=http://127.0.0.1:8080 claude` |
| Deliberate deviation | `Accept-Encoding` stripped on forward, so captures are readable |
| Redaction | credential headers replaced with `<redacted {scheme} len={n}>` at write time |
| Artefacts | `captures/run-TIMESTAMP/NNN-TIMESTAMP.request.capture.json` and `.response.capture.sse` |

Captures always record **what the harness sent**, never what was forwarded, plus
a `transform` note describing any modification. A substitution run whose captures
recorded the modified body could not be compared against a passive run.

Sequence numbers are assigned by the instrument and reset with the process, so
captures are grouped into one subdirectory per run. A flat directory interleaved
runs under every sort order and made the numbering meaningless — found and fixed
after Run 2, and the Run 1 and Run 2 captures were retrofitted into the new
layout.

---

## Run log

| Run | Date | Setup | Requests | Purpose |
|---|---|---|---|---|
| 1 | 2026-08-12 | fresh session, one trivial question | 6 | does redirection work; first request shape |
| 2 | 2026-08-12 | conversation resumed after several days, "resume from summary" | 6 | long prefix, cold cache — **and unintentionally covered 3a** |
| 3 | 2026-08-12 | four substitution passes + capability lookup | 33 | **can AvIator change the model at all** |
| 4a | — | forced `/compact` | — | does the explicit command match the resume-summary shape |
| 4b | — | long organic working session | — | warm-cache behaviour, content-block diversity, auto-compaction |
| 5 | — | `opus → sonnet` substitution | — | is a *within-generation* downgrade viable where Haiku is not |
| 6 | — | replayed captures, mutated by hand | — | does `effort` invalidate the cache |

Run 2 offered a choice between resuming from a summary and pulling in full
context. Choosing the summary produced a summarisation request, so the run
covers most of what 3a was for. **What it does not establish is whether the
`/compact` command and automatic compaction produce the same shape** — three
paths that plausibly share code, observed once.

---

## Run 1 — fresh session, one trivial question

**Six API calls resulted from one question.** A connectivity probe, a title
generation, and four iterations of the agentic tool loop.

### Verified

**Redirection works, and subscription auth survives it.**
`Authorization: <redacted Bearer len=115>` with `oauth-2025-04-20` among the
beta headers. This closes ADR-04's largest open risk: the interception strategy
does not require an API key and does not require `--bare`.

**The harness caches aggressively, and the conservative default in ADR-05 is
correct.** Measured across the tool loop:

| Request | New input | Cache read | Cache write | Cached share |
|---|---|---|---|---|
| 003 | 2 | 0 | 41,825 | 0% — cold start |
| 004 | 2 | 41,825 | 833 | 98.0% |
| 005 | 2 | 42,658 | 11,009 | 79.5% |
| 006 | 2 | 53,667 | 827 | 98.5% |

New input is **2 tokens** against a prefix of ~54K. ADR-05's instruction to
"assume the entire prefix is cached" is now a measurement rather than a
defensible guess.

**Content is typed blocks.** `text`, `tool_use`, and `tool_result` all appear
within a single short session. One message carried a bare string, so both forms
occur and neither can be assumed.

### Contradicts the ADRs

**1. The effort axis is unreachable for this harness.**

Every substantive request already carries `output_config: {"effort": "medium"}`,
and `effort-2025-11-24` is in the harness's beta header list. ADR-04 designs
effort as a second routing dial and then constrains it with: *"An explicit
setting wins. If the incoming request already specifies effort, AvIator leaves
it alone."*

Claude Code always specifies it. Under ADR-04's own rule, AvIator may never
touch effort on this harness, and the second dial does not exist.

The rule was written with a user's deliberate choice in mind, not a harness
default the user never saw and cannot observe. Those are different things and
the ADR does not distinguish them. Resolving this requires either qualifying
the rule or striking effort routing for Claude Code. **Unresolved.**

**2. A user turn is not an API call.**

One question produced six requests. ADR-05 denominates its horizon `N` in
"turns" without saying which quantity is meant, and the two differ by roughly
5×.

The consequence runs both ways. A switch amortises over API calls, so it repays
several times faster in wall-clock terms than ADR-05 implies. But a switch must
also hold for an entire tool loop — changing model partway through pays the
cache rebuild in the middle of the work, for no benefit.

**3. The prefix is roughly 3× larger than ADR-05 estimated.**

ADR-05 hedges that system and tools *"plausibly exceed 14K tokens"* before the
user has typed anything. Measured on the first substantive request:
**41,825 tokens** written to cache, from **38 tools** (~97,250 characters of
tool JSON) and an 11,239-character system block.

Under the document's own 14× rule, a mid-session switch now needs an output of
more than ~3,000 tokens to break even. ADR-05's conclusion is correct and its
figure is too generous.

**4. Cache TTL is one hour on conversational requests**, not the five-minute
default — `{"type": "ephemeral", "ttl": "1h"}`, with
`extended-cache-ttl-2025-04-11` in the beta headers. A conversation resumed
within an hour is still warm, which widens the window in which a switch is
expensive. *Refined by Run 2: the TTL is not uniform across request types.*

### Cache breakpoint placement

Three breakpoints. **None are on the tools array**, which is covered implicitly —
caching is prefix-matched and tools render before system, so the `system[1]`
breakpoint encloses them.

| Position | Size | Breakpoint |
|---|---|---|
| `tools` (38 entries) | ~97,250 chars | — (covered by the next) |
| `system[0]` | 70 chars | none — a billing header line |
| `system[1]` | 57 chars | ephemeral, 1h |
| `system[2]` | 11,239 chars | ephemeral, 1h |
| last message block | — | ephemeral, 1h |

### New affordances

**Conversation identity is on the wire.** `metadata.user_id` is a JSON string
containing a `session_id`, stable across every request in a session. ADR-05
requires routing to be committed per conversation but never says how a
conversation is identified. This answers it at no cost.

**A cacheless request category exists.** Two of the six requests had
`cache_read = 0` **and** `cache_write = 0`:

- a connectivity probe — `max_tokens: 1`, no system, no tools
- a title generation — `output_config: {"effort": "high", "format": {"type": "json_schema", ...}}`, `thinking: {"type": "disabled"}`, 704 input tokens, 15 output tokens

Both ran on the **powerful** model. Neither has a cached prefix to destroy, so
routing them downward costs nothing and the economic comparison in ADR-05 does
not apply to them at all.

This is a distinct category from ADR-05's cold-cache *checkpoints*, which are
framed as moments within a session. These are calls that never had a stake, and
the detection rule differs: a checkpoint is inferred from session position,
whereas these are identifiable from the request itself.

### Adapter constraints

- **The path carries a query string:** `/v1/messages?beta=true`. "Does this path
  belong to me" cannot be an equality check.
- **A `system` role appears inside the `messages` array**, enabled by
  `mid-conversation-system-2026-04-07`. Extraction cannot assume the array
  contains only `user` and `assistant`. *Run 2 identifies these as harness-injected
  reminders.*
- **Every substantive request streams.** Only the `max_tokens: 1` probe did not.

### Beta headers observed

Recorded verbatim because several are load-bearing above, and because the set
will drift between harness versions:

```
claude-code-20250219, oauth-2025-04-20, interleaved-thinking-2025-05-14,
redact-thinking-2026-02-12, thinking-token-count-2026-05-13,
context-management-2025-06-27, prompt-caching-scope-2026-01-05,
mid-conversation-system-2026-04-07, advisor-tool-2026-03-01,
effort-2025-11-24, fallback-credit-2026-06-01, extended-cache-ttl-2025-04-11
```

Harness version at time of capture: 2.1.221.

---

## Run 2 — conversation resumed after several days, via summary

Six requests: a probe, the summarisation call, three token-count calls, and one
ordinary post-summary turn.

### Compaction is identifiable from the request

**This closes the open question in ADR-05.** The discriminator is the TTL on the
`cache_control` breakpoints:

| | `cache_control` | Response accounting |
|---|---|---|
| Summarisation request | `{"type": "ephemeral"}` — **no `ttl` key** | `ephemeral_5m_input_tokens: 143,571`, `1h: 0` |
| Ordinary request | `{"type": "ephemeral", "ttl": "1h"}` | `ephemeral_1h_input_tokens: 73,438`, `5m: 0` |

Omitting `ttl` falls back to the five-minute default, and the response
accounting confirms the request was honoured that way. The signal is present in
the **request**, which is what matters — routing decides before the response
exists.

**This is an inference about intent, not a declared field.** The harness is not
announcing a compaction; it is saying "do not keep this long", which is the
correct thing to say about a summarisation prefix that will never be reused.
That reasoning is principled enough to expect stability, but it is a heuristic
and another one-off request type could share it. Treat it as a strong signal,
not a guarantee, and prefer it as a reason to **pass through** rather than as a
trigger for action — the failure direction is then a missed optimisation.

Other candidate discriminators were checked and rejected. `context_management`,
`output_config`, `thinking`, `system` block count, and tool count are all
identical between the two. Message count differs enormously (106 vs 4) but is
useless as a rule, since an ordinary deep-session request also carries many
messages.

### Post-compaction cache is genuinely cold — verified

The ordinary request following the summarisation shows
`cache_read_input_tokens: 0` against `cache_creation_input_tokens: 73,438`.

ADR-05 lists "immediately after compaction" as a cold-cache checkpoint on
reasoning alone. It is now measured. This is the strongest evidence yet for
shipping checkpoint-only routing first: the checkpoint is real, it is detectable
from the preceding request, and the switch there is free.

### The compaction request is the most expensive call in the session

| | |
|---|---|
| Messages | 106 |
| New input tokens | 2,723 |
| Cache write | 143,571 |
| **Output tokens** | **14,122** |
| Wall clock | ~3 minutes |

ADR-05 argues that the most predictable large request in a long session is the
one the scorer most wants to route to the smallest model, and that a poor
summary degrades every subsequent turn. Both halves now have numbers behind
them. The 14,122-token output is also the one place where a cheaper model's
output rate would genuinely matter — which makes the quality argument for
leaving it alone load-bearing rather than cautious.

### A third endpoint, and a request that must never be rewritten

**`POST /v1/messages/count_tokens?beta=true`** — three calls in this run, all
non-streaming, returning a single JSON object.

The body carries `model`, `messages`, and `tools`. **If AvIator substitutes the
model here, the harness receives a token count for a model it is not using**,
and then makes its own context-management decisions on that number. The damage
is entirely outside AvIator's view and would be near-impossible to attribute.

This endpoint must be matched by path and passed through untouched. It is the
first concrete instance of a rule the adapter needs generally: *recognising a
path is not the same as owning it.*

### Long-session prefix vs the context window

The resumed conversation's prefix was **143,571 tokens** before summarisation,
against 41,825 for a fresh session.

Haiku 4.5's window is 200K. A deep session's prefix is therefore already ~72% of
the cheap tier's entire context, before output. ADR-04's capability gate is not
guarding an edge case.

### New content block type

`thinking` blocks appear in the messages array — 35 of them in the summarisation
request, absent from Run 1 entirely. Flattening must handle them, and their
interaction with `context_management`'s `clear_thinking` edit is worth
understanding before the extractor is written.

### The `system` role inside `messages` is harness-injected

Run 1 recorded the role's existence. Run 2 shows what it is: reminders injected
by the harness mid-conversation, appearing frequently through a long session.
They are not user content and not a compaction signal — both request types end
on one.

### Rate limiting is real and survivable

The connectivity probe returned `rate_limit_error`. The session continued
normally. Two things follow: the error relay path is verified against a genuine
upstream error rather than a synthetic one, and the probe is non-critical, so
its failure is not a session-ending event.

### The usage payload is richer than assumed

Fields observed beyond the four ADR-05 reasons about:

```
cache_creation: {ephemeral_5m_input_tokens, ephemeral_1h_input_tokens}
iterations: [ ... per-iteration usage ... ]
output_tokens_details: {thinking_tokens}
service_tier, inference_geo
```

The per-TTL breakdown is directly useful to the cost reporter, and is what made
the compaction discriminator confirmable.

---

## Run 3 — model substitution

Four passes, each risking more than the last, plus a capability lookup. The
question underneath the whole project: **can AvIator change the model at all,
through this harness, with this credential?**

### The answer is yes

A title generation was substituted from Opus to Haiku and answered normally:

```
resp model = claude-haiku-4-5-20251001   stop = end_turn   out = 13
```

**Subscription OAuth permits arbitrary model selection.** This was ADR-04's
largest unstated risk — the interception strategy assumed a substitution would
be accepted, and nothing had tested it. It is now closed.

Note the response reports a **dated** identifier from an **undated** request.
The alias is what you send; a pinned version is what answers. The resolver
should not assume the two are interchangeable, and it is worth establishing
which of them the cache is keyed on.

### Re-serialising a request does not break caching

Pass 1 rewrote `model` to *itself* and forwarded the body through
`json.dumps`, changing key order and whitespace. Caching was unaffected —
`cache_read` of 37,489 / 44,544 / 44,759 across the tool loop.

Caching matches on semantic content, not on the exact bytes the harness
emitted. `proxy.py` can re-serialise freely.

### A fresh session is not a cold cache

The first substantive request of a **brand-new conversation** read **37,489**
cached tokens — and the same figure appeared in four separate runs on four
separate conversations.

The tools and system prefix is identical across every Claude Code session, so
sessions warm each other's caches within the 1h TTL.

**ADR-05's most valuable checkpoint is stated as:**

> *"The first request of a conversation. No cache exists yet."*

That is false for anyone who uses Claude Code more than once an hour, which is
the entire target user. Two consequences:

1. **Session-start substitution is not free.** It discards ~37K of already-warm
   shared prefix, so the switch cost ADR-05 says collapses to nothing at this
   checkpoint is real.
2. **The cost is not confined to the routed session.** Routing one session to a
   different model builds a second cache while other concurrent sessions
   continue reading the first.

Post-compaction coldness (Run 2) is unaffected and remains genuinely free.

### Three incompatibilities, discovered one at a time

Substituting a **substantive** request — 39 tools, real conversation — failed
three times, each time on a different parameter:

| Pass | Repair applied | Error returned |
|---|---|---|
| 2 | none | `This model does not support the effort parameter` |
| 3 | none | `adaptive thinking is not supported on this model` |
| 3 | `effort` stripped | `adaptive thinking is not supported on this model` |
| 4 | `effort` + `thinking` stripped | `role 'system' is not supported on this model` |

**The API reports the first incompatibility it finds and stops.** Pass 2 only
surfaced the effort error because the title generation happened to send
`thinking: disabled`, which Haiku accepts. Every substantive request sends
`thinking: adaptive`, which fails earlier and masks it.

This is the operational argument for a declared capability table over
error-driven discovery: each attempt costs a round trip, reveals exactly one
problem, and no number of successes proves the set is complete.

### A failed substitution does not break the session

Every 400 was followed by a clean retry on the original model and the session
ran to completion. ADR-04 states the fear plainly —

> *"a bad substitution does not fail silently, it fails loudly and takes the
> user's session with it"*

— and for these errors that is not what happens. The harness catches the error
and retries. The cost is one round trip.

This is a materially softer failure mode than the ADR assumes, and it should
temper how conservative the gate needs to be. It is **not** a licence to skip
the gate: the recovery is the harness's behaviour, not a guarantee, and nothing
says it holds for every error class.

### The third failure is different in kind

`role 'system' is not supported on this model` is not a parameter. It is the
harness's mid-conversation reminder messages (Run 1, Run 2), and they are
**conversation content**, not request configuration.

That makes it unrepairable by the strategy that fixed the first two:

- **`effort`** — removal is a clean repair. Haiku has no effort concept, so
  there is nothing to preserve.
- **`thinking: adaptive`** — removal silently disables thinking. A behaviour
  change, not a repair. Mapping to a supported mode would preserve intent.
- **`role: system` messages** — cannot be removed without deleting instructions
  the harness placed, and cannot be rewritten without changing what the model
  is told.

**As Claude Code constructs its requests, they cannot run on Haiku at all.**
Not because of a parameter, but because of the shape of the conversation. What
is *not* established is whether a restructured request would succeed — only
that the request as sent does not.

### Consequence: the remove-only rule does not survive

The proposed replacement for ADR-04's "an explicit setting wins" was:

> *AvIator may remove a parameter the target model cannot accept. It may not
> change a parameter to a different value the model would accept.*

The capability table (below) shows why that fails. `thinking` is supported by
both models, but with **inverted** value support — Opus accepts `adaptive` and
not `enabled`; Haiku accepts `enabled` and not `adaptive`. The correct repair
is to *map* `adaptive → enabled`, which is precisely what remove-only forbids.
Removal is available but strictly worse: it turns thinking off.

The evidence has pushed the same direction twice — once on the effort dial,
once here. **A rule permitting value substitution, constrained by the
capability table, is better supported than the remove-only version.**

---

## Model capability endpoint

`GET /v1/models` was called with the credential from an in-flight request, which
is exactly the startup lookup ADR-04 describes and flags as a decision. It
**works with subscription OAuth** and returns everything the resolver and the
capability gate need.

Per-model fields: `id`, `display_name`, `type`, `created_at`, `max_input_tokens`,
`max_tokens`, `capabilities`.

### Capabilities are declared at the value level

Not a boolean. ADR-04 predicted this and it is confirmed:

```
haiku    effort.supported = false            (all of low/medium/high/xhigh/max false)
         thinking.types.enabled  = true
         thinking.types.adaptive = false
opus     effort.supported = true             (all five levels true)
         thinking.types.enabled  = false
         thinking.types.adaptive = true
```

**Two of the three Run 3 failures were predictable from this table** without a
single failed request. The third — the `system` role — is not represented
anywhere in the payload (`system`, `role`, and `mid_conversation` do not appear
in it at all).

So the table drives the gate for **parameters**, and something else is still
needed for **message-structure** features. That distinction did not exist before
this run.

### Context windows

The capability gate's other input, now exact:

| Model | Input | Output |
|---|---|---|
| opus-5, sonnet-5, fable-5, opus-4-8, opus-4-7, opus-4-6, sonnet-4-6 | 1,000,000 | 128,000 |
| sonnet-4-5 | 1,000,000 | 64,000 |
| **haiku-4-5**, opus-4-5 | **200,000** | 64,000 |

The 5× gap that motivates the gate is confirmed. Note the harness sends
`max_tokens: 64000`, which is exactly Haiku's ceiling — at the boundary, with no
headroom.

### A declared compaction mechanism exists

`capabilities.context_management` lists `compact_20260112` alongside the
`clear_thinking_20251015` edit observed in Runs 1–3. Supported on Opus and
Sonnet, **not supported on Haiku**.

Two consequences. If the harness ever uses that edit type, compaction becomes
identifiable from a declared field rather than the `ttl` heuristic from Run 2.
And a model that cannot perform provider-side compaction is a poor substitution
target for a long session regardless of anything else.

---

## Open: re-laddering the Claude Code tiers

**Not a decision. Recorded here because Run 3 removed the assumption the current
ladder rests on, and the evidence bearing on the replacement is in this
document.** The decision belongs in ADR-06.

The shipped mapping is `fast: haiku`, `balanced: sonnet`, `powerful: opus`.
Haiku is unreachable for conversational traffic, so the bottom rung is empty and
the ladder has to move.

The candidate under consideration: **`fast: sonnet`, `balanced: opus`,
`powerful: fable`.**

### What the capability data says

Every 5-series model is capability-identical, and identical to Opus:

| | Window (in/out) | effort | thinking | `compact_20260112` | code exec |
|---|---|---|---|---|---|
| fable-5 | 1M / 128K | all 5 levels | adaptive | ✓ | ✓ |
| opus-5 | 1M / 128K | all 5 levels | adaptive | ✓ | ✓ |
| sonnet-5 | 1M / 128K | all 5 levels | adaptive | ✓ | ✓ |
| haiku-4-5 | 200K / 64K | **none** | **enabled only** | **✗** | **✗** |

Haiku is the outlier on every axis, not merely on the two that produced errors.
Within the 5-series there is nothing for a capability gate to catch: any
substitution among them is capability-safe, and the gate's parameter checks
become no-ops. That is a real simplification of the first shippable increment.

### Three complications this raises

**1. The `powerful` tier stops being reachable.** Every observed request used
`claude-opus-5`, so the harness default is Opus. Under the proposed ladder:

| Tier | Model | Relative to the Opus default |
|---|---|---|
| fast | sonnet | downgrade — allowed |
| balanced | opus | no change — pass through |
| powerful | fable | **upgrade — forbidden by default** |

ADR-04 ships downgrade-only with `allow_upgrade` off. So `powerful` would be
dead unless the user opts in, and the three-tier system collapses to a binary:
*substitute to Sonnet, or leave it alone.*

This is not an argument against the ladder — it may simply be what routing on
this harness honestly is. But a config presenting three tiers where one is
unreachable is the kind of decorative setting ADR-03 already had to fix once.

**2. The starting point is per-user, not per-harness.** The tier table is keyed
on harness, but what AvIator can *do* depends on the model the user has selected
inside that harness. A user defaulting to Sonnet has no downgrade target at all
once Haiku is excluded. The config models the ladder but not the rung the user
is standing on, and the two are not the same fact.

**3. The savings profile changes shape.** The old ladder promised a large
multiple between rungs. Opus → Sonnet is a smaller step, and whether it clears
the switch cost measured in Run 3 (~37K of shared warm prefix discarded) is an
arithmetic question that cannot be answered without the pricing table — already
a Phase 3 dependency under ADR-05.

### What must be established first

- **Run 5** — that `opus → sonnet` actually succeeds, including the `system`
  role. Capability-identical on paper; unverified on the wire.
- **Pricing** for fable / opus / sonnet, including cached-read rates, to confirm
  the ladder is still a *cost* ladder in the ADR-03 sense.
- **Whether the harness's selected model is observable**, so the config can key
  on the rung the user occupies rather than assuming one.

---

## Still unmeasured

- **Whether `opus → sonnet` succeeds where `opus → haiku` fails.** Sonnet 5
  supports `effort`, `adaptive` thinking, a 1M window, and `compact_20260112` —
  every capability Haiku lacks. If the `system` role is likewise accepted, a
  within-generation downgrade is viable and the tier ladder shrinks to two rungs
  rather than collapsing. **This is the highest-value open question**, and it is
  a one-line change to `SUBSTITUTE`.
- **Whether a restructured request runs on Haiku.** Established: the request as
  Claude Code sends it does not. Not established: whether rewriting the `system`
  role messages would work, or what that would cost in fidelity.
- **An unexplained full cache miss.** In Run 3 pass 2, an untouched substantive
  request read 0 and rewrote 44,587 two requests after reading 37,489. Verified:
  `tools` and `system` byte-identical; `messages[1]` changed container type only
  (block list → bare string, same 8,724 characters); the harness moved its own
  breakpoint from `msg[1]` to `msg[3]`. That explains invalidation from
  `msg[1]` onward but **not** the tools+system head, which sits behind its own
  breakpoint. The observed miss is larger than the diff accounts for. No
  mechanism; only the evidence. If the harness self-invalidates periodically,
  ADR-05's baseline is optimistic.
- **Which identifier the cache is keyed on** — the alias sent, or the dated id
  returned.
- **Whether `/compact` and automatic compaction match the resume-summary shape.**
  One summarisation path observed; three plausibly share code.
- **Whether `effort` invalidates the cache.** Cannot be tested through the
  harness, which sets the value itself. Requires replay. Priority is downstream
  of the ADR-06 rule decision: only needed if AvIator may change effort *without*
  changing the model.
- **Whether `image` blocks appear in practice**, and whether they justify keeping
  `MULTIMODAL_FLOOR` as a hard floor. Not seen in any run.
- **Warm-cache behaviour across a long organic session.** Every run so far
  started cold.
- **Codex's base-URL redirection mechanism.** Still unconfirmed, still
  design-invalidating for that harness.
- **Per-provider cache discount depths.**
- **`N` calibration.** Additionally blocked on deciding whether `N` counts user
  turns or API calls.

---

## Changes owed to the ADRs

Tracked here until made, so a contradiction is never silently carried.

| ADR | Section | Change |
|---|---|---|
| 04 | Effort as a second axis | **"An explicit setting wins" does not survive.** It treats a harness default the user never saw as a user decision, and the two are indistinguishable on the wire. Replace with a compatibility rule, permitting value substitution constrained by the capability table |
| 04 | The capability gate | Gate needs a **repair** branch, not only a veto — declining every incompatible substitution costs the entire feature on this harness |
| 04 | The capability gate | Table covers **parameters only**. Message-structure features (the `system` role) are absent from it and need separate handling |
| 04 | The capability gate | Cite the measured windows; 143K deep-session prefix against Haiku's 200K |
| 04 | Model resolution | `GET /v1/models` **verified working** under subscription OAuth, returning value-level capabilities. The live lookup is now the primary source, not an upgrade |
| 04 | Model resolution | An undated alias resolves to a dated id in the response; do not assume they are interchangeable |
| 04 | Failure policy | A failed substitution is **retried by the harness**, not session-ending. Softer than stated |
| 04 | Security posture | Add body metadata to the logging exclusions |
| 04 | Provider adapters | Path matching must tolerate query strings; message roles are not limited to user/assistant; `thinking` is a content block type |
| 04 | Provider adapters | Add `count_tokens` as a recognised-but-not-owned path; state the general rule |
| 04 | Scope | Record that `opus → haiku` is **blocked** as Claude Code constructs requests, pending Run 5 |
| 05 | What a switch costs | Replace the 14K estimate with the measured ~42K fresh / ~143K deep |
| 05 | Cold-cache checkpoints | **Session start is not a free checkpoint** — sessions share a ~37K cached prefix across conversations. Correct the claim and the shipping-order argument that rests on it |
| 05 | Cold-cache checkpoints | Post-compaction coldness is now **verified**, not assumed |
| 05 | Cold-cache checkpoints | Add the cacheless-request category as distinct from positional checkpoints |
| 05 | Determining `N` | State whether `N` counts user turns or API calls |
| 05 | Compaction requests | Identifiable via absent `ttl` on `cache_control`; record the caveat, and note the declared `compact_20260112` edit type as a better signal if it is ever used |
| 05 | Cache state is observable | Record 1h TTL on conversational requests, 5m on summarisation |
