# ADR-04: Interception Strategy and Model Resolution

**Date:** 2026-08-12
**Status:** Complete

---

## Context

Phase 1 produced a scorer that turns a query into an integer. Phase 2 produced a selector that turns that integer into a model family, and a config layer that knows which harness the user is running. Nothing yet connects either to a real API call.

This phase builds the layer that makes routing happen: something that observes a query in flight, scores it, substitutes a model, and gets out of the way. That requires answering four questions — how AvIator intercepts traffic at all, what process owns the interception, how a model *family* becomes a concrete model identifier, and how AvIator avoids breaking a request by substituting a model that cannot serve it.

A theme carries over from ADR-03 and shapes several decisions below. That document's recurring failure mode was silence — routing that produced a plausible answer that was not the user's. This phase adds a second failure mode with a different character: **a bad substitution does not fail silently, it fails loudly and takes the user's session with it.** Where ADR-03 optimised for "say so rather than guess", this one optimises for "do nothing rather than break".

---

## Interception strategy

AvIator's user is someone typing into a CLI harness — Claude Code, Codex, or Gemini CLI. That single fact eliminates two of the three available approaches.

### Option A — wrap the client object

The user writes `client = aviator.wrap(anthropic.Anthropic())`. AvIator returns a proxy object that forwards everything and intercepts the call method on the way through.

Explicit and honest: reading the user's code, you can see AvIator is involved. Nothing global is mutated, so two libraries cannot conflict. Trivially testable against a fake client — no network, no ports, no async.

But it only works when the user constructs the client and hands it over, it is a code change rather than a one-time setup, and — decisively — **there is no Python client to wrap.** Claude Code is a Node binary, Codex is Rust, Gemini CLI is Node.

### Option B — monkey-patch the SDK method

Replace the client's create method on the class at import time. No user code change at all, and it catches every client in the process.

It also modifies a third party's class at runtime. SDK internals are not a stable API — a minor release can rename the method, change its signature, or relocate the resource, and the patch either breaks loudly or silently stops applying. Two libraries patching the same method means last-import-wins and neither knows. Debugging is miserable: the user reads a plain SDK call and gets behaviour that is not in their code.

And it cannot serve a CLI harness either, for the same reason as A.

### Option C — local HTTP proxy (chosen)

**AvIator runs an HTTP server on loopback. The harness is redirected to it via its base-URL setting. AvIator parses the request, scores the conversation, substitutes the model, forwards upstream, and relays the response.**

It is the only option that works with the harnesses. It also operates on the provider's **published, versioned wire format** rather than on SDK internals — a materially more stable contract than Option B's, which is worth stating because it is easy to read Option C as the hackier choice when it is in fact the better-specified one.

The costs are real and are addressed in their own sections below: streaming must be relayed correctly, it introduces a process with a lifecycle, model substitution is provider-specific, and authenticated traffic passes through it.

---

## Process model

**`aviator run -- claude`** starts the proxy, launches the harness as a child process with the base-URL variable injected into the child's environment, and shuts the proxy down when the child exits.

The alternatives:

**Foreground process** — `aviator start` blocks; the user works in a second terminal. Simple, visible, Ctrl-C to stop. But it costs a second terminal, and the user must still export the base-URL variable themselves — a manual step, per shell, that fails silently when forgotten.

**Background daemon** — fork and return. Requires `aviator stop`, `aviator status`, a PID file, stale-PID detection after unclean kills, and a log destination since there is no terminal. Individually small; collectively most of a phase.

The wrapper wins on more than lifecycle: **it solves environment variable injection at the same time.** The user never sets the base URL and therefore cannot get it wrong or forget it, which is what "zero work beyond one setup command" actually requires. It also makes the process lifetime correct by construction — the proxy exists for exactly as long as the session that needs it. No PID files, no orphans, no "is it still running?"

---

## Provider adapters

The work splits unevenly, and recognising the split early prevents a costly refactor.

**Shared, written once:** HTTP server, request read, upstream forward, response relay, streaming pass-through, connection lifecycle, error handling. This is most of the code and contains no provider knowledge.

**Per-provider adapter:** each provider shapes requests differently.

| Provider | Endpoint | Model location | Conversation field |
|---|---|---|---|
| Anthropic | `POST /v1/messages` | body `model` | `messages`, with `system` as a separate top-level field |
| OpenAI | `POST /v1/chat/completions` | body `model` | `messages`, system inside the array as a role |
| Gemini | `POST /v1beta/models/{model}:streamGenerateContent` | **the URL path** | `contents`, entries hold `parts` |

**The Gemini case dictates the interface shape.** An adapter written as `rewrite_body_model(body)` works for two providers and collapses on the third. Written as `rewrite_model(request) -> request` — operating on the whole request including its path — all three fit. This is worth getting right before the first adapter exists, not after.

The adapter interface answers four questions: does this path belong to me, how do I extract the conversation, how do I apply a model substitution, and where do I forward to.

### Content is not always a string

On the wire, message content is frequently a list of typed blocks — text, image, tool use, tool result — not a plain string. `score_query(query, history)` takes strings, so extraction must flatten.

This is an upgrade in disguise. The scorer currently infers multimodality from words like "image" and "diagram" appearing in the prompt text and applies `MULTIMODAL_FLOOR` on that basis. Through the proxy, actual image blocks are visible. **A heuristic becomes a fact.** Worth revisiting the hard floor once real traffic shapes are known.

---

## Streaming

Responses stream as server-sent events: a long-lived HTTP response holding the connection open, writing blank-line-separated chunks as tokens generate.

**The routing decision is made entirely on the request. The response requires no interpretation.** Scoring happens before forwarding; everything coming back is bytes to copy from the upstream connection to the harness's connection. This phase does not parse the stream at all. It is a pipe.

Four things the pipe must get right:

1. **Never buffer.** Use an async HTTP client in streaming mode and yield chunks as they arrive. The classic failure is a client that reads the full response before handing it over — the stream still *works*, but arrives all at once after a long pause, and the tool feels broken. This is invisible on short responses; it must be tested deliberately on a long one.
2. **No `Content-Length`.** Streaming responses are chunked; copy the relevant upstream headers rather than constructing new ones.
3. **Handle client disconnect.** On Ctrl-C, close the upstream connection — otherwise sockets leak and tokens are still being paid for.
4. **Respect backpressure.** Do not accumulate chunks in a list; let the async await chain apply pressure naturally.

The cost reporter will eventually need token counts, which arrive in the stream's own events. That work must be a **tee** — observe the bytes going past, extract numbers, never alter or delay them. Keeping it separate preserves this phase's relay as a dumb pipe; blurring the two produces a proxy that can corrupt responses while trying to count tokens.

---

## Model resolution

The selector returns a family keyword. The API needs a concrete model identifier. This is the piece ADR-03 deferred, and it turns out to be smaller than expected.

**Current model identifiers are stable aliases with no date suffix** — `claude-haiku-4-5`, `claude-sonnet-5`, `claude-opus-5`. A family does not resolve to a pinned point release; it resolves to an alias that upstream keeps current. The table only changes when a *generation* ships, not on every point release, which delivers the property ADR-03 wanted from families one layer lower than anticipated.

Two sources for that table:

**A static mapping** shipped in code. Three entries per harness, no dependencies, no latency, fully testable. Goes stale when a generation ships.

**A live lookup** against the provider's models endpoint. Self-updating, no code change on release — and, more importantly, it returns two things the static table cannot: each model's **context window** and its **capability set**. Those feed the gate described in the next section.

**Decision: ship the static table, refresh it from the models endpoint when possible.** The static table always works and is the fallback; the live lookup is an upgrade behind the same interface. The lookup happens **once at startup and is cached** — it is not a per-request call, and every per-request check is a local dictionary lookup with no latency in the request path.

One call serves three purposes: current model identifiers, context windows, and capability sets.

**A note on credentials.** The only auth material AvIator holds is the credential travelling in the request passing through the proxy. Using it to make a call the user did not initiate is a decision, not an implementation detail — see the security section.

---

## Effort as a second axis

The score is a continuous value. `tier_for_score` collapses an entire band into one bucket, and a score at the bottom of a band and one at the top are not the same query.

Current models accept a request-level effort setting controlling reasoning depth and token spend. That gives AvIator **two dials rather than one**: which model (coarse, large cost steps) and how much effort (fine, within a single model). Mapping position within the tier band to effort extracts a second signal from work already done — finer granularity without inventing more tiers, and a genuine cost lever, since effort changes token spend and therefore shows up in the savings report.

Three constraints:

- **It is adapter-level, not core.** The parameter's name and shape are provider-specific. The selector emits something abstract; the adapter translates.
- **An explicit setting wins.** If the incoming request already specifies effort, AvIator leaves it alone — the same principle `resolve_harness` enforces for the harness itself.
- **It widens the blast radius.** Rewriting two fields instead of one raises the chance of producing a request the target rejects, which is what the next section exists to prevent.

---

## The capability gate

A model substitution can produce a request the target model cannot accept. Unlike a mis-scored query, this does not degrade quality — it returns an error and takes the session with it.

**The rule: if AvIator changes anything, validate the resulting request against the resulting model.**

Not "validate when downgrading." The trigger is that AvIator modified the request, and the thing validated is the **final combination**, not the individual field that changed.

| Harness sends | AvIator picks | Context check | Parameter check |
|---|---|---|---|
| opus | opus, **different effort** | not needed — model unchanged | **required** — does opus accept this effort *level* |
| opus | **sonnet** (down) | **required** — window may shrink | **required** — sonnet must accept every parameter already present |
| sonnet | **opus** (up) | cheap, run it anyway | **required** — same reason |
| opus | opus, same effort | nothing changed — pass through untouched | none |

Row 1 is why the rule is not "check when downgrading": same model, no downgrade, and still a mandatory check. Row 2 carries the subtler case — **AvIator can invalidate a parameter it never touched.** The harness sets effort for one model, AvIator substitutes another that does not accept effort, and the request fails. AvIator broke it by changing the model, not by changing effort.

Two details make this sharper than a support flag:

- **Capability is not boolean.** Effort levels vary by model — some accept a subset. The capability table stores the supported *set*, not a yes/no.
- **Constraints exist across parameters.** At least one current model rejects a disabled-thinking setting when effort is above a threshold, while accepting each field independently. A request can therefore be assembled from individually valid fields and still be rejected. This is the argument for validating the assembled request rather than each edit in isolation, and for keeping the effort mapping conservative at the top of the band.

### Context overflow

Context windows differ by up to 5× across a single harness's tier ladder. A conversation that exceeds the target model's window fails outright.

**The scorer cannot catch this**, and measurement rather than intuition establishes why. Its token contribution saturates at a low threshold, so a conversation of roughly 5,000 words and one of roughly 200,000 words produce an *identical* score. The scorer distinguishes complexity, not size. Whatever protection it appears to offer against overflow is incidental — large content happens to clear the fast threshold and then flattens — and is not a property anything guarantees or tests.

The word-count basis has a second blind spot the proxy makes visible: images, tool results, and system prompts carry substantial token weight with almost no words. A vision-heavy session scores low and is enormous.

**Resolution:** before committing a substitution, confirm the estimated request size fits the target's context window. If it does not, escalate to the next tier and re-check; if no tier fits, forward the request unmodified.

This is architecturally the same shape as the two hard floors in ADR-02 — override the score when there is a capability cliff rather than a slope — so it is a third instance of an existing pattern rather than a new concept. It differs from those two in *where* it belongs: the existing floors are semantic and live in the scorer, while this one requires knowing each model's context window and therefore must live in the selector. **The scorer stays pure.**

Estimation is deliberately conservative. The two errors are not symmetric: wrongly concluding a request fits breaks the session, while wrongly concluding it does not costs one tier of savings. The scorer's own word count must not be reused — it is calibrated for scoring, not measuring, and it saturates.

---

## Routing cadence

A model substitution is not free even when it is valid. Model switches invalidate the prompt cache the harness depends on, which means the selector's tier decision is a *proposal* rather than an instruction — a separate economic check decides whether it is acted on, and routing decisions are committed per conversation rather than per turn.

That analysis is substantial enough to stand on its own; see ADR-05. Its consequences for this document are that the request path gains a decision step after the capability gate, and that a pricing table becomes a dependency of this phase rather than of the cost reporter.

---

## Routing direction

**Downgrade by default. Upgrade is opt-in, off by default.**

Upgrading is defensible in principle — "the right model for each query" is symmetric, and a user who defaults to a cheap model and asks something genuinely hard is served badly by a tool that will not move them up.

It loses on risk asymmetry. A too-weak model produces a worse answer the user notices immediately and can retry; an unrequested increase in spend is discovered at the end of the billing period and cannot be undone. Upgrading also spends the user's money without being asked, overrides an explicit model choice the same way ADR-03 refuses to let detection override an explicit harness, and makes the savings report incoherent — a summary that sometimes shows negative savings undercuts the artefact meant to demonstrate the tool's value.

Shipping downgrade-only also makes row 3 of the table above unreachable for now, which is a smaller thing to build and test. Enabling upgrades later is additive rather than a behaviour change.

---

## Failure policy

**Any error in extraction, scoring, selection, or resolution forwards the request unmodified.**

The asymmetry is the whole argument. A request that falls back to the harness's own model is a missed optimisation — the user gets their answer and loses a few cents of savings. A request that fails to route breaks their session. Those costs are not comparable, and the error handling must reflect it.

This extends to the proxy as a whole: if it dies mid-session the harness gets connection-refused and the user is dead in the water. That is the worst thing AvIator can do, and it argues for keeping the request path conservative and small.

Upstream errors are relayed faithfully rather than swallowed or reinterpreted. AvIator is transparent in both directions.

---

## Security posture

This is the cost of Option C and it deserves stating plainly rather than being discovered by a suspicious user.

**What this is not.** The proxy runs on the user's own machine, as their own process, under their own account — the same trust ask as any local development tool. It is categorically different from a hosted gateway, where traffic genuinely leaves for a third party's servers. No remote operator is being trusted.

**The actual risks:**

1. **Binding.** Listening on a non-loopback address exposes an unauthenticated endpoint that forwards requests using the user's credentials to anyone on the network. **Loopback only, always.** This is the one that turns a design choice into a vulnerability.
2. **Logging.** The concrete risk, and it is on a collision course with the cost reporter's session files. **Log token counts, model names, tiers, and timestamps — never message content, never headers.** This must be decided before the reporter exists; retrofitting it is much harder.
3. **Upstream TLS.** Certificate verification stays on. It will be tempting to disable it while debugging; it must not survive into a commit.
4. **Credential handling.** Auth material passes through the proxy in transit. It is never persisted, never logged, and never sent anywhere other than the upstream the request was already bound for. The startup capability lookup is the one place AvIator would use that credential for a call the user did not initiate — if implemented, it goes to the same provider endpoint the request was already authenticated against, and the static table remains the fallback when it is not available.

**Mitigations:** loopback binding, header redaction applied before anything is logged, a request path short enough that a skeptical reader can audit it in ten minutes, and documentation that states all of this up front.

---

## Scope

**Three harnesses remain supported in configuration: Claude Code, Codex, and Gemini CLI.** Detection and tier mappings for all three are already built and verified.

**The Anthropic adapter ships first.** It is testable continuously on the development machine, it has the cleanest tier ladder — ADR-03 identifies it as the reference case — and one provider working end to end teaches more than three half-built adapters. A harness whose adapter does not yet exist must fail with an explicit message rather than silently passing requests through unrouted; silent non-routing is the failure class ADR-03 exists to eliminate, and it would be perverse to reintroduce it here.

### Harnesses considered and rejected

**Cursor.** Interceptable on paper — it exposes a base-URL override — but rejected on three independent grounds. Its requests are assembled and dispatched from Cursor's own backend rather than the local application, so a loopback proxy is unreachable without exposing it publicly, which defeats the security posture above. The override speaks one provider's protocol only, so the multi-provider routing that would motivate supporting it is unavailable through it. And its built-in subscription models and autocomplete bypass the override entirely, leaving a meaningful share of traffic untouchable. Cursor also already ships a cost-tier heuristic of its own, making it the one harness where AvIator would be arguing it does the job better rather than filling a gap.

**OpenCode.** Rejected on a structural mismatch rather than mechanics: a harness that can be pointed at arbitrary models has no fixed tier ladder to map to. AvIator's per-harness tier table assumes a known, small, ordered set of families, and there is nothing stable to bind `fast`/`balanced`/`powerful` to. Should it later be configured against an OpenAI-compatible endpoint, the OpenAI adapter would likely cover it without further work.

---

## Consequences

- `aviator start` is superseded by `aviator run -- <harness>`, which owns the proxy lifecycle and injects the base-URL variable into the child process.
- The selector gains a capability gate between tier selection and model substitution. The scorer is unchanged and remains pure.
- A model resolver turns a family keyword into a concrete identifier, backed by a static table and refreshed from the provider's models endpoint when available.
- The adapter interface operates on whole requests, not request bodies, so that a provider carrying the model in its URL path fits without a redesign.
- Effort becomes a second routing output alongside the model. It is emitted abstractly and translated per provider.
- AvIator never increases a request's cost unless the user opts in.

---

## Deliberately deferred

- **Codex and Gemini adapters.** Their detection and tier mappings exist; only the wire-format adapters are outstanding.
- **Effort mapping implementation.** The axis is designed in, but model-only routing ships first — rewriting one field is a smaller blast radius than two, and the capability gate should be proven against the simpler case before it is asked to validate combinations.
- **Semantic validation of model families.** ADR-03 shipped this gap knowingly, since the only check available at the time would have cost the customisation the config exists to provide. The resolver is the honest home for it — it is the first component that can say "no such model for this provider" as a fact rather than a guess — and even there it should warn rather than reject.
- **Honouring the harness-specific config-root overrides** noted in ADR-03. Still a missed detection rather than a wrong one, which keeps it inside the abstain-don't-guess contract.
