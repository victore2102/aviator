# ADR-05: Routing Cadence and Prompt Cache Economics

**Date:** 2026-08-12
**Status:** Complete

---

## Context

AvIator's premise is that scoring each query and substituting a cheaper model saves money. That premise is straightforwardly true for a single isolated request. It is not automatically true inside an agentic session, because agentic harnesses depend heavily on prompt caching — and **changing the model destroys the cache.**

This document establishes what a model switch actually costs, and what that implies for how often AvIator is allowed to change its mind.

The finding is significant enough to qualify a claim made elsewhere in this project: routing is *evaluated* per turn, but it cannot be *committed* per turn without losing money. The distinction matters and is worth stating plainly rather than discovering it in production.

---

## What invalidates a cache

Cached content is matched as a prefix, rendered in the order `tools` → `system` → `messages`. A change invalidates its own tier and every tier after it.

| Change | Tools | System | Messages |
|---|:--:|:--:|:--:|
| Tool definitions added, removed, or reordered | ✗ | ✗ | ✗ |
| **Model switch** | **✗** | **✗** | **✗** |
| System prompt content | ✓ | ✗ | ✗ |
| `tool_choice`, images, thinking toggled | ✓ | ✓ | ✗ |
| Message content | ✓ | ✓ | ✗ |

A model switch is the worst row on the table. Along with editing the tool list, it is the only change that forces a complete rebuild of every tier.

### There is no "stay within the family" escape

**Caches are keyed on the exact model identifier, not the family.** Moving between two models of the same family invalidates exactly as thoroughly as moving between families.

This matters specifically because of ADR-03's family abstraction: the resolver turns a family keyword into a concrete identifier, and the cache is keyed on that identifier. **Every tier change is therefore a full cache loss.** There is no cheap-switch tier and no partial invalidation to exploit.

---

## What a switch costs

Take turn N of a session: `P` tokens of prefix already cached, a small new query, `O` tokens of output. Cached reads cost roughly 0.1× the input rate; cache writes carry a premium of roughly 1.25×.

Using current list prices — Sonnet at $3/$15 per million, Haiku at $1/$5:

| | Input | Output |
|---|---|---|
| Stay on the current model (prefix cached) | P × $0.30/M | O × $15/M |
| Substitute the cheaper model (cache lost) | P × $1.00/M | O × $5/M |

Substituting wins only when the output saving beats the lost cache discount — `O × $10/M` against `P × $0.70/M`, or roughly:

> **switching pays only when the prefix is smaller than about 14× the output**

With a 1,000-token answer that means a prefix under ~14K tokens. A coding harness's system prompt and tool definitions plausibly exceed that before the user has typed anything. Taken at face value, this says downgrading is a net loss for most turns of most agentic sessions.

That conclusion is correct for a *single turn viewed in isolation*, and it is the wrong way to look at it.

---

## The cost is the switch, not the destination

The figures above price the *moment of switching*. They do not price *being on the cheaper model*.

Once AvIator switches and stays, the cache rebuilds on the new model and every subsequent turn reads at the cheaper model's cached rate — $0.10/M rather than $0.30/M in the example — on top of the ongoing output saving. The one-time switch cost amortises.

**A single switch pays for itself in a small number of turns — typically two to four.** The input side alone recovers the cost in about three and a half turns at the rates above; counting the output saving as well brings it down further, and longer answers shorten it more.

That yields the governing principle:

> **Routing that changes its mind every turn is strictly destructive. Routing that decides once and holds is cheap, and then profitable.**

The enemy is not the cheaper model. The enemy is churn.

---

## Decision: routing is sticky

**AvIator decides a model for a conversation, not for a turn.** A tier change is a deliberate act with a cost, not an automatic consequence of the current turn's score.

The scorer still runs on every turn — its output is needed for effort selection, for the capability gate, and for the cost report — but a *different* tier result does not by itself trigger a substitution.

This refines rather than contradicts the turn-level premise: the decision is evaluated continuously and committed sparingly.

---

## Decision: switching is an economic comparison, evaluated over a horizon

When the score indicates a different tier, the question is not "is this tier more appropriate" but "is changing worth what it costs". For a single turn:

```
cost_stay   = P × cached_rate(current) + new × input_rate(current) + O × output_rate(current)
cost_switch = P × input_rate(target)                              + O × output_rate(target)
```

`P` is the size of the conversation already in flight. The only structural difference between the two lines is the first term: staying receives the cache discount on `P`, switching pays full price for it.

**Compared over a single turn, this test is too strict**, and it fails for a reason worth naming: it prices the moment of switching while ignoring that the cost amortises. A switch that loses on this turn and wins on every turn afterwards is a good switch, and a single-turn comparison rejects it.

The correction is to extend the window rather than to loosen the test — pay the rebuild once, then collect the cheaper rate `N` times:

```
switch when   cost of switching over the next N turns
            < cost of staying   over the next N turns
```

**A percentage tolerance was considered and rejected.** It reaches for the same outcome by loosening the threshold, but it is a fudge factor compensating for the wrong time horizon rather than a fix for it. A horizon has three advantages: `N` denotes something real (how much longer the session is expected to run) rather than an invented margin; it is calibratable from the session records the cost reporter already produces, which is the discipline ADR-02 applies to the scoring thresholds; and it self-corrects at the edges, correctly declining to switch near the end of a session where a fixed tolerance cannot express that.

Until session-length data exists, `N` is a conservative constant. It is a hyperparameter to be calibrated, not tuned by intuition.

Two things make this cheaper to build than it appears:

- **`P` is already computed.** The capability gate in ADR-04 needs a token estimate for its context check. The same number feeds this decision.
- **The pricing table is shared.** The cost reporter needs per-model input, output, and cached rates regardless. Building it here means one table serving both the routing decision and the savings report, rather than two that drift apart.

### Determining `N`

`N` is **expected remaining turns**, not typical session length. Those are different quantities, and conflating them is the usual mistake — the question is not "how long is a session" but "given that we are at turn 12, how many more turns are likely?"

**A property worth testing early, because it may invert the intuition.** For many human-driven processes, expected *remaining* length rises with age rather than falling: a session that has already run forty turns is evidence of a long working session and probably has more left than one at turn three. Short sessions end early and select themselves out, leaving a long tail. If coding sessions behave this way, then being deep in a session makes switching *more* attractive rather than less — which is convenient, since deep sessions are exactly where the prefix is large enough for the decision to matter at all.

**The session records the cost reporter produces are already the required data.** For each turn position `t`, take the mean remaining turns across all sessions that reached `t`; that yields a lookup table `N(t)`. Segment by harness if the distributions differ, which they plausibly do. This is a query over data collected for another purpose, not a separate modelling effort.

**Signals that a session is ending are weak and should be treated as such.** Widening gaps between turns suggest disengagement; a declining score trend suggests deep work giving way to cleanup, and the scorer already produces that signal per turn even though its *trajectory* is currently discarded. Compaction events point the other way — they indicate a long session rather than an ending one.

None of these are strong, and the error costs are asymmetric in the way that recurs throughout this project: **overestimating `N` buys a switch that never amortises, which costs real money; underestimating it skips a switch that would have paid, which costs only an opportunity.** `N` should therefore be biased downward, accepting that some savings are left on the table. A crude estimate is acceptable — what is required is that it be conservative and calibrated, not that it be accurate.

Until session data exists, `N` is a flat conservative constant documented as uncalibrated. This is the same treatment the scoring thresholds receive in ADR-02: ship a defensible number, let data replace it, and never let intuition tune it in between.

### The test applies asymmetrically by direction

A downgrade and an upgrade are motivated by different things, so the same comparison plays a different role in each.

**Downgrades are always gated on cost, regardless of the upgrade flag.** A downgrade exists to save money; a downgrade that costs more is self-defeating. Here the comparison is checking the decision against its own premise, so it applies unconditionally.

**Upgrades under `allow_upgrade` are gated on quality, not cost.** Enabling the flag is the user stating they will pay more for a better answer when the query warrants it. The cache penalty is part of that cost and is therefore already accepted, so an upward substitution clears on the score's recommendation rather than on the arithmetic.

What the flag does **not** do is remove the question. Stickiness and cold-cache timing still apply in both directions, because those prevent *waste* rather than preventing quality — oscillating between tiers across turns pays the rebuild repeatedly for churn rather than for a better answer, and no setting makes that sensible.

---

## Cold-cache checkpoints

The switch penalty is proportional to how much cache is being discarded. When the cache is *already* cold, switching is free — and there are predictable moments when that is true:

- **The first request of a conversation.** No cache exists yet. This is also where the scorer has the cleanest signal, since no history has accumulated to skew it.
- **Immediately after compaction.** Summarising rewrites the message history, which invalidates the messages tier on its own. The switch penalty at that moment is already paid.
- **After a tool-set change.** Adding or removing a tool invalidates every tier. Rarer and harder to detect, but structurally the same opportunity.

**AvIator should prefer to make its routing decisions at these points.** This turns an economic constraint into a scheduling rule: rather than resisting switches everywhere, concentrate them where they cost nothing.

The first checkpoint is the most valuable, and it is available immediately — routing the opening request of every session is unambiguously free and needs none of the machinery above.

**This is also the shipping order.** Restricting substitutions to cold-cache checkpoints collapses the economic comparison entirely: with no cached prefix to discard, `cost_switch` and `cost_stay` converge and there is nothing to weigh. Checkpoint-only routing therefore delivers most of the available saving with none of the pricing, horizon, or cache-tracking machinery, and it is the correct first thing to make work. The horizon comparison becomes the refinement that unlocks mid-session substitution once there is session-length data to calibrate `N` against.

---

## Compaction requests

Compaction is itself an API call and therefore passes through the proxy. It has an awkward shape: it is a summarisation task, which the scorer's formatting signals push *downward*, while carrying the entire conversation, which makes it one of the largest requests in the session. The most predictable large request in any long session is thus the one the scorer most wants to route to the smallest model.

That is the capability gate from ADR-04 firing on a routine event rather than an edge case, and it is an argument for the gate shipping alongside the first adapter.

There is also a quality argument for leaving compaction alone entirely: a poor summary degrades every subsequent turn in the session, and the saving on a single call is not worth that risk. **Compaction requests should be passed through unmodified where they can be identified.** Whether they are identifiable on the wire is unresolved — a provider-side compaction mechanism is visible in the request body, but a harness performing its own summarisation resembles an ordinary request with a summarisation prompt.

---

## Cache state is observable

AvIator does not have to guess how much of a prefix is cached. Responses report cache read and write token counts in their usage data, so the usage observer described in ADR-04 can track real cache state per conversation and feed it into the next routing decision.

Until that exists, **assume the entire prefix is cached.** That biases the comparison against switching, which is the safe direction: an unnecessary switch costs real money, while a skipped switch costs only an opportunity.

---

## Consequences

- The selector's output is a *proposal*. A separate decision, informed by cache state and price, determines whether the proposal is acted on. Under `allow_upgrade`, that decision defers to the proposal for upward moves; downward moves are always tested on cost.
- The first shippable form of this is checkpoint-only substitution, which needs no pricing table at all. Mid-session substitution is a later increment gated on having a calibrated horizon.
- A pricing table becomes a Phase 3 dependency rather than a cost-reporter concern, since the routing decision cannot be made without it.
- The savings report must account for cache economics. A report that counts only the difference in per-token rates will show savings that do not appear on the user's bill — the most damaging possible failure for the artefact intended to demonstrate the tool's value.
- Session start is the highest-value routing opportunity and requires none of the machinery above. It is the correct first thing to make work.

---

## Deliberately deferred and explicitly unmeasured

- **Whether the effort parameter invalidates the cache.** Not established. The reasonable assumption is that it behaves like the other request-level parameters in the table above — preserving tools and system, invalidating messages — which is far cheaper than a model switch but not free. This is directly measurable: issue two identical requests differing only in effort and read the second response's cache-read count. A nonzero value means the cache survived. This must be measured before effort routing ships, and is a further argument for shipping it after model-only routing.
- **Confirming the harness uses prompt caching at all**, and what proportion of a typical prefix is cached in practice. Near-certain for an agentic harness, but it underpins every number here and should be observed rather than assumed.
- **Detecting compaction requests on the wire.**
- **Cache-state tracking** via the usage observer. Until it lands, the conservative default above applies.
- **Calibrating the horizon `N`** from observed session lengths, once the cost reporter is recording them — see *Determining `N`* above for the method and its cautions. A conservative constant stands in until then.
- **Per-provider cache discount rates.** Prefix caching keyed to a specific model is a universal design, so a model switch discards it on every provider — but the *depth* of the discount differs, and the depth is what determines how expensive a switch is. A shallower discount than Anthropic's makes switching substantially cheaper and permits more aggressive routing. This is a constant in the shared pricing table rather than a structural difference, so AvIator can be more or less aggressive per provider without divergent code. The figures must be read from each provider's documentation rather than assumed.
