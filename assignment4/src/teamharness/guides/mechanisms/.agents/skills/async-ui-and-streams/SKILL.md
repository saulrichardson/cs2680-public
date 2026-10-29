---
name: async-ui-and-streams
description: Diagnose overlapping operations, deferred effects, stale results, timers, controlled UI events, segmented media, and streamed accounting. Use when ownership, event order, position, or per-call metadata determines correctness.
---

# Async state, UI, media, and streams

Separate value/state, events, effects, operation ownership, freshness, and accounting. Several operations can be pending while one effect is owed; an operation can be settled but obsolete; a DOM focus position is not the controlled value; a local media clock is not global time; a stream fragment is not a token or a cumulative count.

Choose the transition model before editing a callback. Use the deferred-work chapter for release/coalescing, async ordering for stale admission and cancellation, UI for actual input/selection/form events, media for chunk coordinates and playback ownership, and accounting for stream/return metadata. Force distinguishing event schedules with controllable completions and time. Do not repair only the final snapshot when the contract also specifies effect count, synchrony, or identity.

## Focused boundary checks

- [Component state, selection and ordered merged output](#component-state-boundaries)
- [Follow the event sequence the interface actually supplies](#event-contracts)
- [Distinguish request construction from execution](#api-descriptor-effects)
- [Connect action creation, handling and effects](#state-action-wiring)


## Chapter map

- [Coalesce deferred work without losing intent](#deferred-work-coalescing)
- [Async transitions, freshness, and cancellation](#async-transition-ordering)
- [Controlled UI state and user events](#controlled-input-state)
- [Segmented media timelines](#segmented-media-state)
- [Per-operation result and usage accounting](#result-accounting)

<a id="deferred-work-coalescing"></a>

## Coalesce deferred work without losing intent

<a id="deferred-work-coalescing-establish-the-concurrency-contract"></a>

### Establish the concurrency contract

Identify the operations that block work, the event that makes work owed, the event that releases it, and whether several requests may become one effect. A count represents outstanding work only if every start has exactly one matching settlement. A boolean such as `loading` cannot represent arbitrary overlap.

Discover whether failures, cancellation, or stale responses still settle the blocking operation. Determine whether a request during a running refresh requires a follow-up. Coalescing is wrong when each event must produce its own externally visible effect.

<a id="deferred-work-coalescing-diagnose-the-lost-edge"></a>

### Diagnose the lost edge

Write a short event trace with starts, owed-work requests, settlements, and actual dispatches. Force two operations to finish in reverse order. Distinguish:

- Work is never recorded while blocked.
- Work is recorded but cleared by an unrelated settlement.
- The first completion dispatches while another operation is still pending.
- A dispatch clears new intent created during the effect.
- Two callbacks both believe they own the drain.

 Do not patch only the final rendered state when the defect is a missing or duplicate effect.

<a id="deferred-work-coalescing-implement-the-smallest-adequate-model"></a>

### Implement the smallest adequate model

Keep “operations still pending,” “work is owed,” and “a drain is running” as separate concepts when the contract needs all three. Reuse existing reducer or controller ownership. Funnel release through a common path so success and failure do not implement different accounting by accident.

When consuming a boolean owed-work flag, clear the consumed flag before invoking an effect that can synchronously request more work. When a version/counter represents intent, consume only the observed version. Choose that model only if distinguishing successive requests is necessary.

Guard against reentrancy and repeated settlement. If an effect fails, apply the explicit retry policy; preserving intent and immediately rescheduling forever is not a valid substitute for a policy.

<a id="deferred-work-coalescing-verify-transitions-and-effects"></a>

### Verify transitions and effects

Check zero/one/multiple blockers, both completion orders, failure, cancellation, a new request during drain, and any required unmount behavior. Assert effect count and dispatch timing, not just final data. Every test sequence must leave accounting consistent. Use controlled promises or callbacks to select the order deterministically.

<a id="deferred-work-coalescing-mechanism-details"></a>

<a id="deferred-work-coalescing-drain-models-and-event-traces"></a>

### Drain models and event traces

<a id="deferred-work-coalescing-details-select-the-model"></a>

<a id="deferred-work-coalescing-select-the-model"></a>

#### Select the model

| Contract | Suitable concept |
|---|---|
| Only the latest settled input matters after quiet time | Debounce, with explicit timer ownership |
| One refresh is enough after any number of blocked invalidations | Pending count plus owed-work flag |
| A request during a running refresh requires another refresh | Owed flag plus drain ownership, or an intent version |
| Every action produces its own effect or result | Queue/per-operation handling, not coalescing |

The following pseudocode illustrates coalesced work in one serialized execution context. Multi-threaded mutation requires equivalent synchronization or message ownership; the pseudocode is not itself thread-safe.

```text
request_work():
    owed = true
    maybe_drain()

settle_blocker(operation):
    if operation has not already settled:
        mark it settled
        pending -= 1
    maybe_drain()

maybe_drain():
    if pending != 0 or draining or not owed or stopped:
        return
    owed = false             # consume only existing intent
    draining = true         # establish ownership before invoking effects
    start effect
    on settlement:
        draining = false
        apply the specified failure/retry policy
        maybe_drain()       # services new intent if required
```

Whether a new blocker may start during the effect, and whether the effect result can then be committed, are separate questions. If freshness can change, use the async ordering procedure as well. Do not assume “pending was zero at start” means the result is forever valid.

<a id="deferred-work-coalescing-details-worked-case-a-refresh-after-uploads"></a>

<a id="deferred-work-coalescing-worked-case-a-refresh-after-uploads"></a>

#### Worked case A: refresh after uploads

Two uploads A and B are pending. A list refresh becomes owed. A succeeds: pending becomes one, and no refresh runs. B fails: pending becomes zero. If the contract calls for refresh after all uploads settle, one refresh runs despite B's failure. Clearing `owed` in A's callback would lose the work; refreshing from A's callback would run too early.

Trace the reverse settlement order too. A count should lead to the same release condition in both orders.

<a id="deferred-work-coalescing-details-worked-case-b-rebuild-a-preview"></a>

<a id="deferred-work-coalescing-worked-case-b-rebuild-a-preview"></a>

#### Worked case B: rebuild a preview

Several edits occur while a preview rebuild is running. The contract needs one additional rebuild for the newest content after the running rebuild settles. Consume the old intent before starting. Edits during the effect set new intent. The old completion releases drain ownership without erasing that new intent.

A revision number may be clearer if the implementation must also associate each output with the input revision it used. A boolean is enough only when the exact number of requests is irrelevant.

<a id="deferred-work-coalescing-details-counterexample-audit-events"></a>

<a id="deferred-work-coalescing-counterexample-audit-events"></a>

#### Counterexample: audit events

Five purchases require five audit events. Replacing them with one “audit owed” flag loses required records. Use a queue or individual operation results instead. Coalescing is a semantic choice, not a generic concurrency optimization.

<a id="deferred-work-coalescing-details-verification-sequences"></a>

<a id="deferred-work-coalescing-verification-sequences"></a>

#### Verification sequences

Check `request → drain`, `start A → request → settle A`, and `start A → start B → request → settle either → settle other`. Add `drain starts → request again → drain settles` only if follow-up is required. Exercise synchronous completion, rejection, repeated callback, and stop/unmount if supported. Record exactly when the effect begins and the pending/owed/draining values around that transition. A timer sleep cannot reliably establish these orders.

<a id="deferred-work-coalescing-freshness-and-release-timing"></a>

### Freshness and release timing

A response can become stale when mutations begin after it was requested. Record the relevant revision at request start and evaluate admission at completion. Suppress obsolete data without losing the intent to refresh again. If the public contract requires release synchronously at the final settlement, invoking a timer or queued turn is an observable change; test before flushing that queue. Keep pending mutation count, in-flight fetch, retry ownership, and owed intent separate.

<a id="async-transition-ordering"></a>

## Async transitions, freshness, and cancellation

<a id="async-transition-ordering-build-the-event-model"></a>

### Build the event model

Identify state, events, allowed transitions, and effects separately. Include user actions, operation start/settlement, timer scheduling/firing, cancellation, replacement, and disposal only where they exist. Determine which events are synchronous and which enter an asynchronous queue.

Separate three questions: is work still running, is its result current, and may this owner still commit it? Cancellation can request that work stop without guaranteeing that its completion callback will never run.

<a id="async-transition-ordering-distinguish-competing-causes"></a>

### Distinguish competing causes

| Symptom | Distinguishing observation |
|---|---|
| Old data replaces new data | Deliver responses in reverse start order and inspect commit eligibility |
| Timer acts on stale input | Change input after scheduling but before firing |
| Action happens twice | Count listeners/timers and effect invocations across repeated setup |
| Disposed view receives an update | Complete outstanding work after disposal |
| A retry never happens | Trace error classification, timer ownership, and state at the release event |

<a id="async-transition-ordering-repair-the-transition-boundary"></a>

### Repair the transition boundary

Use existing reducer/controller ownership. Guard the commit at the point where state is written, not only when the request starts. Reuse an operation identity or revision when available. Comparing payload equality is not a reliable substitute for identifying the operation that produced it.

For timer replacement, clear or supersede the old timer according to the contract. Decide whether callbacks should use captured input or current input; both are valid under different semantics. For cancellation, release bookkeeping even when the completion is ignored. Gate state writes and effects separately when disposal rules differ.

Keep retry policy explicit: eligible failures, trigger, attempt ownership, and stopping condition. Do not add retries to every error path or infer exponential backoff from a nearby example.

<a id="async-transition-ordering-verify-observable-ordering"></a>

### Verify observable ordering

Use controllable completions and a fake clock. Cover success/failure in both orders, replacement, stale completion, cancellation, and disposal as relevant. Assert intermediate states, number of effects, and whether an effect happens before or after the required event. A final snapshot alone cannot establish correct ordering. If the actual issue is waiting for several blockers to settle, use the deferred-work chapter rather than treating one freshness token as a pending counter.

<a id="async-transition-ordering-mechanism-details"></a>

<a id="async-transition-ordering-ordering-models-and-worked-traces"></a>

### Ordering models and worked traces

<a id="async-transition-ordering-details-freshness-is-independent-of-completion"></a>

<a id="async-transition-ordering-freshness-is-independent-of-completion"></a>

#### Freshness is independent of completion

For a latest-result-wins contract, a request identity can guard commits:

```text
start(input):
    token = new operation identity
    current = token
    invoke work(input)
    on completion(result):
        release bookkeeping belonging to token
        if owner is disposed or token != current:
            ignore state commit
        else:
            apply result using the required success/error transition
```

This is unsuitable when every result must contribute, such as ordered event processing or accumulating independent uploads. There, use per-operation storage, sequence handling, or a queue according to the contract.

<a id="async-transition-ordering-details-worked-case-a-search-suggestions"></a>

<a id="async-transition-ordering-worked-case-a-search-suggestions"></a>

#### Worked case A: search suggestions

Query A starts, then query B starts. B returns first and displays suggestions. A returns later. Under latest-query-wins semantics, A must not overwrite B. Check the commit path for both success and failure: an old error can also replace a newer successful display incorrectly.

Cancellation of A may reduce work, but a commit guard is still needed if cancellation does not guarantee callback suppression. Empty-input/reset events may also invalidate the current operation even when they start no new network request.

<a id="async-transition-ordering-details-worked-case-b-a-delayed-save-indicator"></a>

<a id="async-transition-ordering-worked-case-b-a-delayed-save-indicator"></a>

#### Worked case B: a delayed save indicator

An editor schedules an indicator after saving begins to avoid flicker. A short save completes before the timer. A replacement save begins. If the old timer fires without ownership checks, it may show an indicator for the wrong save. Associate timer state with its owning operation, or use the existing lifecycle cancellation mechanism. When a timer fires, invalidate its handle before code that can reschedule it.

Do not assume every timer should read the newest input. A delayed action that explicitly belongs to a particular captured document must retain that identity.

<a id="async-transition-ordering-details-counterexample-additive-results"></a>

<a id="async-transition-ordering-counterexample-additive-results"></a>

#### Counterexample: additive results

Two independent tasks each contribute an item to a collection. Ignoring the first task's result merely because the second started later loses valid data. Freshness should be scoped by the entity/request whose state is replaceable. A global generation counter can over-cancel independent work.

<a id="async-transition-ordering-details-controlled-schedule"></a>

<a id="async-transition-ordering-controlled-schedule"></a>

#### Controlled schedule

Write a trace such as `start A → start B → settle B → settle A`, with expected state and effects after every arrow. Also run `start A → dispose → settle A`, and `schedule T1 → replace T1 → fire old callback` if stale callbacks are possible. Test before/at/after a timing boundary only when the contract defines that boundary. Use the test framework's actual scheduling model for promise callbacks and timers; advancing the clock may not flush every microtask automatically.

<a id="async-transition-ordering-details-state-machine-discipline"></a>

<a id="async-transition-ordering-state-machine-discipline"></a>

#### State-machine discipline

Keep a transition's preconditions visible. An event valid in “loading” may be invalid in “disposed” or may affect a different operation. Separate pure state calculation from external effects where the architecture supports that separation. Inspect setup/teardown pairing for listeners: duplicate subscriptions can mimic a bad reducer even when each transition is individually correct.

<a id="async-transition-ordering-specialized-event-domains"></a>

### Specialized event domains

For segmented fields, selection, payment-like polling, or authentication cancellation, use the [controlled-input skill](#controlled-input-state). For a multi-player timeline, use [segmented media](#segmented-media-state). Generic stale-response guards do not supply their domain state transitions.

<a id="controlled-input-state"></a>

## Controlled UI state and user events

Identify the authoritative value, derived rendered slots/selection, focus position, validation mode, and async state separately. Do not make DOM-local values the source of truth when props control the component.

For segmented input, specify what one input/change/paste event does to each position. Discover whether invalid characters are rejected, filtered, or preserve position; whether overflow is truncated; and when completion fires. Normalizing the whole string before deciding positions can change the contract. Keep navigation and deletion semantics independent of ordinary character input.

For selectable lists, store stable identities, derive checked state and count, and isolate nested action events from parent toggles. Define reset behavior when the filter/source changes. A rendered checkbox and its containing tile can both fire handlers; one click must not accidentally toggle twice.

For async forms, model loading, available data, submission/polling, success, failure, authentication cancellation, and disposal. Associate results/timers with the operation/token they belong to. Do not let a cancelled authentication leave loading true, or an old timer validate a new token. Read the async-transition chapter when response freshness is involved.

Test real DOM events and controlled prop updates: typing, deletion, arrows, paste, invalid input, boundaries, nested clicks, filter changes, rejection/cancellation, and unmount where supported. Assert value, focus, callbacks, API arguments, and visible state. A snapshot of the final inputs does not prove correct event semantics.

<a id="controlled-input-state-mechanism-details"></a>

<a id="controlled-input-state-input-selection-and-asynchronous-form-models"></a>

### Input, selection, and asynchronous form models

<a id="controlled-input-state-details-controlled-versus-local-state"></a>

<a id="controlled-input-state-controlled-versus-local-state"></a>

#### Controlled versus local state

React distinguishes controlled `value`/`checked` from initial defaults; controlled values require corresponding change handling. Source: [React input reference](https://react.dev/reference/react-dom/components/input).

Derive rendered slots from the authoritative value on every render, including external resets. Local focus can be stateful without becoming a second uncoordinated value store. Keep a consistent empty representation under the component contract; switching between controlled and uncontrolled modes is usually a sign of an unresolved presence boundary.

<a id="controlled-input-state-details-segmented-input-event-table"></a>

<a id="controlled-input-state-segmented-input-event-table"></a>

#### Segmented-input event table

For each event record precondition, accepted characters, replacement span, emitted value, next focus, and completion behavior. Example dimensions are insertion into a full slot versus empty slot, deletion in an empty slot, navigation at either edge, paste into the middle, and an external prop shrink/reset.

An invented four-slot identifier can receive `A-7B` in one paste. Under “filter invalid characters” the mapping differs from “each source position corresponds to one slot.” The task must choose; do not silently filter because it seems user-friendly. Likewise, a backspace can clear the current position or move left first depending on the defined interaction. Construct one transition, emit one resulting value, then move focus under that policy.

Completion may depend on every position being valid, a user action, or a transition from incomplete to complete. A callback on every render of an already complete controlled value can duplicate submissions. Record the required trigger, not just the condition “length is full.”

<a id="controlled-input-state-details-stable-selection"></a>

<a id="controlled-input-state-stable-selection"></a>

#### Stable selection

Use stable IDs rather than indices when order can change. Derive selected count from the same set that drives checked state. Handle a tile click, checkbox click, and nested action as distinct event routes. Event propagation must not cause duplicate toggles or select an item when the user invoked another action.

When filters or data change, determine whether selection clears, intersects visible items, or persists across pages. Read the public contract instead of choosing the easiest set operation. Submit selected identities together only if the API supports that operation, then update the list through its actual refresh path. A count-only assertion can pass while the wrong IDs are submitted.

<a id="controlled-input-state-details-async-form-and-polling-state"></a>

<a id="controlled-input-state-async-form-and-polling-state"></a>

#### Async form and polling state

Model `idle → loading token/data → ready → awaiting/validating → complete`, plus error/cancel/disposed transitions, adapting to the actual component. Persist the response's required fields together so display and validation use one token identity. A new token invalidates timers/results associated with the old one.

Discover whether polling waits before the first request, how repeat intervals are scheduled, whether requests can overlap, what response stops it, and whether completion fires once. A recursive timeout schedules relative to completion; an interval schedules relative to time. Those differ under slow responses. Use the contract's model rather than substituting one silently.

If validation becomes disabled or the component unmounts, release timer ownership and gate stale callbacks. A successful API response must still lead to the required visible state and callback arguments. An authentication cancellation is a settlement path, not a permanently pending operation.

<a id="controlled-input-state-details-counterexample-and-checks"></a>

<a id="controlled-input-state-counterexample-and-checks"></a>

#### Counterexample and checks

A plain freeform text input does not need a segmented-slot machine, and a one-shot request does not need a polling subsystem. Apply only supported modes. Use a real DOM test with distinct identities and controlled time. Assert emitted values and focus separately, inspect request payloads, and settle promises after cancellation/unmount to expose stale effects.

<a id="controlled-input-state-details-external-state-reconciliation"></a>

<a id="controlled-input-state-external-state-reconciliation"></a>

#### External-state reconciliation

Distinguish an absent update from an authoritative empty collection. An empty remote list may require clearing stale local membership, while an omitted list can mean no update. Treat the current user/device and remote sources as explicit identities rather than relying on array position. Reconcile only within the correct account/room/resource scope.

For a recording or other multi-stage operation, list every named event and legal predecessor state. Start, resume, pause, finish, and replacement can have different side effects even when two transitions end in the same visible state. Test an empty authoritative snapshot and each legal transition with mocked connection/recorder events, observing both stored state and emitted actions. Do not infer behavior for an unrecognized event from the nearest existing branch.

<a id="segmented-media-state"></a>

## Segmented media timelines

Choose one internal time unit and write every conversion boundary. Separate global position, total duration, active chunk identity, chunk-local position, requested play/pause intent, and readiness.

Build cumulative offsets from chunk durations. Determine interval boundaries, unknown-length policy, and end/out-of-range behavior from the contract. A global seek target is not a local chunk time. An observed chunk clock update is not a complete global position until combined with that chunk's offset.

For a cross-chunk seek, transition ownership deliberately: invalidate prior work, stop/pause the old player as required, obtain the target player, set local time, and apply the retained play/pause policy. If player creation is asynchronous, confirm the request is still current before committing it. Attaching clock listeners only to the first-created chunk leaves later seek targets unobserved.

Clock updates must identify their producing chunk. Ignore an old chunk's updates when it no longer owns playback. Recompute duration when the chunk collection changes, under the application's live/ended policy. Preserve the exact public units and accessor/event shape.

Test first/middle/end boundaries, both playback intents, an unloaded target, rapid successive seeks, a late old clock, newly appended chunks, and any required zero-length or empty behavior. Assert player calls and order, global/local positions, duration, state, and UI observation. Do not introduce clamping or rounding unless specified.

<a id="segmented-media-state-mechanism-details"></a>

<a id="segmented-media-state-timeline-arithmetic-and-asynchronous-seek-transitions"></a>

### Timeline arithmetic and asynchronous seek transitions

<a id="segmented-media-state-details-coordinate-systems"></a>

<a id="segmented-media-state-coordinate-systems"></a>

#### Coordinate systems

For durations `d[0..n-1]`, define `prefix[0]=0` and `prefix[i+1]=prefix[i]+d[i]`. Under half-open intervals, chunk i covers `[prefix[i], prefix[i+1])`; its local time for global t is `t-prefix[i]`. Exact end behavior and empty/unknown-duration handling need explicit policy.

An invented recording with chunk lengths 4, 7, and 2 units has offsets 0, 4, and 11. Global time 5 belongs to the second chunk at local time 1. Global time 4 is the next chunk's start under that policy. A binary search on prefix endpoints can locate a chunk efficiently, but a linear scan is often adequate; choose based on actual scale and zero-length semantics.

Keep units named in variables or interfaces. HTML media currentTime uses seconds; source metadata and event buses can use other units. Source: [HTML media time](https://developer.mozilla.org/en-US/docs/Web/API/HTMLMediaElement/currentTime). Convert once at each boundary and test fractional values; premature integer rounding can drift across chunks.

<a id="segmented-media-state-details-seek-transaction"></a>

<a id="segmented-media-state-seek-transaction"></a>

#### Seek transaction

```text
record requested global position and playback intent
resolve target chunk and local position under the boundary policy
give this seek a current operation identity
release/stop the previous player according to the contract
obtain or create the target player's observable bindings
when ready, commit only if this seek still owns the operation
apply local position and the required play/pause action
publish the required global position/state notifications
```

The contract chooses whether an operation temporarily plays an unloaded target before pausing, whether an old player must stop unconditionally, and when notifications are synchronous. Those are observable behaviors; do not infer them from the final paused/playing state alone.

<a id="segmented-media-state-details-clock-ownership"></a>

<a id="segmented-media-state-clock-ownership"></a>

#### Clock ownership

Attach observation when each player is created, not only in an initial start path that seeking bypasses. A clock callback should carry or close over the producing chunk identity. Only the owner selected by the current playback model may update global position. Use that owner's current offset, particularly if the chunk collection can change.

For rapid seek A then B, deliver A's creation result or clock after B. It must not steal current state. A generic latest-response token and chunk identity answer related but distinct questions: which seek may commit and which player's ongoing clock may publish.

<a id="segmented-media-state-details-duration-and-live-data"></a>

<a id="segmented-media-state-duration-and-live-data"></a>

#### Duration and live data

Total duration can change when chunks arrive, independently of a current clock tick. Recompute at the collection transition that makes the information available and notify consumers through the established observable. Loading state should not erase a known requested position unless the contract says so. Keep display duration formatting separate from internal precision.

<a id="segmented-media-state-details-verification-and-counterexample"></a>

<a id="segmented-media-state-verification-and-counterexample"></a>

#### Verification and counterexample

Use synthetic durations and controllable player creation, not real audio sleeps. Exercise exact boundaries, a fractional target, a target reached while paused, an unloaded target, reverse creation order, and an old player's late update. Observe both the seek controller and a real UI subscriber so a correctly updated private field cannot conceal missing notifications.

A single continuous media element may already implement seeking correctly; adding a chunk abstraction there is unnecessary. This model applies when multiple players/events are deliberately stitched into a timeline.

<a id="result-accounting"></a>

## Per-operation result and usage accounting

Define what is counted, the owning operation, the unit, and whether incoming values are deltas or cumulative snapshots. Separate per-message overhead, per-request overhead, prompt/input, completion/output, retries, and externally reported totals under the current contract.

Trace changed return values through every caller and branch: ordinary result, stream, command, empty/short-circuit, partial failure, and error. A new result tuple compiling at one call site does not establish that metadata reaches the public operation.

For streaming, own the counter at the model/request operation. Include the initial fragment exactly once and each subsequent fragment exactly once. Establish when the final count is available and whether it can be read while work is ongoing. Avoid shared mutable counters across concurrent requests unless the contract explicitly aggregates them.

Do not assume chunk token counts are additive or that character length equals tokens. Use the actual tokenizer/provider semantics and error behavior. If events carry cumulative values, replace/update their operation snapshot rather than adding every snapshot as a delta. If they carry deltas, account once per admitted event.

Test empty output, several chunks, initial chunk handling, an error during counting, two independent calls, a retry, a command/short-circuit, and a metadata-free non-error path where supported. Use an injected deterministic tokenizer or provider fixture. Assert the returned public result and both counter components, not just a total printed in logs.

<a id="result-accounting-mechanism-details"></a>

<a id="result-accounting-streaming-accounting-and-return-contract-migration"></a>

### Streaming accounting and return-contract migration

<a id="result-accounting-details-define-the-unit-and-ownership"></a>

<a id="result-accounting-define-the-unit-and-ownership"></a>

#### Define the unit and ownership

A count can mean characters, tokens, bytes, records, attempts, or provider-reported usage. A stream chunk is a transport unit, not necessarily one token or record. Record which operation owns the count and what its lifecycle is. A retry can count as extra consumed work even when only the final attempt's result is returned; report according to the current API rather than mixing policies.

For a model conversation, prompt accounting can include the complete message list submitted on each call plus message/role/request framing. Counting only the newly added user text misses repeated context. The actual tokenizer and overhead rules determine the calculation; never substitute another provider's constants from an example.

<a id="result-accounting-details-delta-versus-cumulative"></a>

<a id="result-accounting-delta-versus-cumulative"></a>

#### Delta versus cumulative

Suppose one operation reports snapshots 3, 6, and 8. Its final cumulative value is 8; adding all snapshots gives 17. Delta events 3, 3, and 2 instead sum to 8. Both can look monotonic, so inspect the event contract rather than inferring from values. If snapshots can arrive out of order, “latest by arrival” and “max” are not universally correct; use sequence/version identity or the provider's explicit completion event.

Keep per-operation values separate before aggregate reporting. An invented two-request run with final counts 8 and 5 has aggregate 13 under a sum-of-final-snapshots policy. A single counter overwritten at each callback or seeded again from every fragment cannot represent that ownership cleanly.

<a id="result-accounting-details-tokenization-boundaries"></a>

<a id="result-accounting-tokenization-boundaries"></a>

#### Tokenization boundaries

Tokenizers can make decisions across fragment boundaries. Tokenizing each arbitrary chunk independently and summing may disagree with tokenizing the logical completion. Use a tokenizer/counter API that defines incremental behavior, or retain enough text/state to produce the contract's required result. Test with a deliberately non-additive fake tokenizer to expose an implementation that merely adds per-chunk lengths.

An asynchronous counter needs explicit initial state, append semantics, read/finalization semantics, and error propagation. A constructor that consumes the first fragment and a caller that immediately appends it again double-count. A read method should not silently omit pending work if its contract says the count is settled.

<a id="result-accounting-details-changed-result-types"></a>

<a id="result-accounting-changed-result-types"></a>

#### Changed result types

Write a caller ledger for the public completion/service method, then migrate wrappers and all branch returns. Non-error empty paths may require a nil metadata object, an empty object, or zero totals; choose the existing public rule. Never dereference a missing counter solely because the ordinary model path always supplies one.

For partial failure, distinguish provider work already consumed from a valid completed result. Preserve required error identity and any promised partial metadata. Do not invent successful accounting after a tokenizer error.

<a id="result-accounting-details-verification"></a>

<a id="result-accounting-verification"></a>

#### Verification

Use a deterministic fake encoder whose output can be independently counted, a stream with distinguishable initial/subsequent fragments, two calls with different inputs, and a short-circuit branch. Check each operation before aggregate totals. Reverse event arrival where supported. Validate that message framing is added at the right scope and that public callers receive the new metadata without changing the result's type semantics.

## Optional deeper cookbook

The mechanism and required technical facts are in this file. For an unusual language/runtime failure, the optional cookbook offers additional recipes: [typescript](../../../.repair-memory/cookbooks/typescript.md). Select a recipe by its trigger; do not load the whole memory layer by default.


<a id="component-state-boundaries"></a>

## Component state, selection and ordered merged output

Use this chapter for lifted state, context providers, prop forwarding, optional component interfaces, multi-selection, and filters coupled to selection. Decide which layer owns the state and how consumers receive it; a prop added to a child but never forwarded by a wrapper is not an integrated feature. When a UI merges groups or timeline items, check order, a single shared pivot, metadata ownership and repeated rendering. Test a chain of adjacent merges and a nonmergeable gap. A shorter array or plausible snapshot does not establish correct ordering or preservation.

<a id="event-contracts"></a>

## Follow the event sequence the interface actually supplies

Native DOM events, React synthetic events, keyboard input and input-method/composition behavior are not interchangeable. A task or test may supply only a native input event for same-value entry, or only a paste event with no later change/input. Handle that promised event sequence directly rather than relying on a follow-up event or keydown workaround. These are task-specific event contracts, not universal claims that browsers never emit keydown/change for same-value input.

Read input data from the appropriate event/current state before asynchronous work. Multi-character change can require distribution just like paste; do not implement distribution only in the paste branch if the public input/change contract also admits it. For synchronous paste handling, obtain clipboard data in the handler and prevent the browser default only when the component owns the insertion. Check replacement, unchanged character, multi-character input, paste, bounds, focus and resulting state independently.

Visibility, availability and selected preference are independent. Render a disabled control when the interface promises visibility with unavailable interaction; hide it only when visibility itself is conditional. Lack of keys, permissions or data can disable one action without erasing an independent saved preference. Assert both rendered presence and enabled/disabled behavior.

<a id="api-descriptor-effects"></a>

## Distinguish request construction from execution

An API builder may return a plain request descriptor; awaiting that object does not perform a request. Trace the value into the established executor/wrapper, which may own authentication, headers, error normalization and cancellation. Return type alone is not enough: inspect the implementation and a neighboring real call. Some functions do execute I/O directly, so do not universally wrap every API helper.

A useful focused check asserts that the executor receives the expected descriptor, then observes the response-driven UI/state effect. Mock the binding used by that executor, or use the repository's existing request interception seam. A mock that replaces the whole component path can let descriptor-only code appear correct. Check failure, cancellation and polling continuation at the same boundary when they are part of the change.

<a id="state-action-wiring"></a>

## Connect action creation, handling and effects

An action creator produces an action; a reducer consumes state and an action. Follow each required new action from dispatching caller to the actual handler. An external action may be handled by a slice, listener, middleware or another established consumer—there is no universal rule that every createAction belongs in one slice's extraReducers. Confirm the required path instead of checking symbol existence.

Keep reducers pure; timers and external effects belong in the existing effect/thunk/listener or process layer. Timer handles in serializable state can create new problems, so use the established lifecycle owner. For retry/freshness behavior, distinguish recording intent from executing it, consume only observed intent, and check repeated settlement, cancellation and a new request during an in-flight effect. A new action nobody dispatches and a helper nobody calls do not change the public flow.
