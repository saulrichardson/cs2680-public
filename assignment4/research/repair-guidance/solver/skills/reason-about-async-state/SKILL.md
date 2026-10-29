---
name: reason-about-async-state
description: Explain asynchronous behavior through event order, state ownership, and visible effects. Use for UI propagation, timers, polling, streaming counters, stale responses, cancellation, or work deferred until other operations settle.
---

# Observe the event sequence and the state it owns

Asynchronous code can appear correct when each callback is read alone and still fail because callbacks observe different state, settle in another order, or act after ownership changes. Use this skill when an operation starts correctly but its result, timing, cleanup, or UI effect is inconsistent. The evidence should describe the actual sequence and the state around consequential transitions.

## Separate the states that answer different questions

An operation being requested, pending, completed, eligible to publish, and already reflected in the UI are different facts. A single loading boolean can collapse them and make one completion clear another operation's work. A response may be valid for the request that produced it and obsolete for the current view. A stream may have emitted several chunks while its final returned result reports only the last chunk's accounting.

Identify who owns each state and what event is allowed to change it. If two independent operations share a counter or flag, determine whether the state describes their aggregate or one specific operation. Preserve identities so an older completion cannot remove bookkeeping belonging to its replacement. A callback that captures one entity must not publish another entity's latest global value.

For UI work, trace the source state through props, derived values, effects, render conditions, and user events. Rendering an indicator in the DOM does not establish visibility under the relevant CSS or lifecycle. A callback prop being defined does not establish that the child invokes it or that the parent responds. Test through the interaction where the public behavior is observed.

## Use controlled ordering instead of elapsed sleeps

A sleep can make one execution pass without establishing the causal order. Prefer controlled promises, fake services, event emitters, or scheduler facilities that allow the relevant operations to settle deliberately. Keep the production scheduling boundary in the check. If the contract requires an effect synchronously when the last operation settles, flushing a later queued task before asserting can conceal an unintended delay.

Consider two pending mutations and a refresh request. If the requirement is to refresh after both settle, resolving the first should not start the refresh. Resolving or rejecting the second may release it, depending on the failure policy. If another refresh request arrives while a refresh is running, determine whether one follow-up is owed, each request must remain distinct, or cancellation is appropriate. A boolean can coalesce intent only when multiplicity does not matter.

The [event and UI patterns reference](references/event-and-ui-patterns.md) develops coalescing, freshness, cancellation, media timelines, and real consumer checks. Its examples describe explicit policies; use the task to decide whether those policies apply.

## Inspect the installed scheduler

Timer and promise behavior depends on the environment and test configuration. A function named as a familiar scheduling primitive may be mocked, polyfilled, absent, or governed by a different queue. Do not assume that switching from one scheduling API to another escapes fake timers or that one primitive always runs before another in every context.

Inspect the installed version and test setup, then run a small sequence that exercises the actual helper used by the component. Distinguish flushing promise continuations from advancing scheduled timers and from delivering an external callback. A polling check should verify the first request's timing, repeat interval, stop condition, and callback ownership under the configured scheduler. The host's elapsed time or a test timeout alone cannot diagnose which transition failed.

Where a scheduling utility is unavailable, preserve that limitation rather than adding a global polyfill solely to make one check pass. If a portable fallback is required, verify both branches under the environments the application actually supports.

## Count the event represented by the number

A sequence number may identify the next item, the current item, or the count already sent. A streaming usage value may be a cumulative total, a delta, or a per-response snapshot. Determine the invariant before choosing when to increment or aggregate. Starting at zero and incrementing before emission is correct for some contracts and wrong for others.

Use an empty stream, one item, and several items with distinct counts. Check the final public status as well as intermediate events. If a helper returns a counter while also retaining a mutable one, identify which source callers use and whether both can diverge. Repeated chunks, retries, cancellation, and batches require explicit decisions about what is counted once and what belongs to another attempt.

For playback or ordered media, keep the source identity, unit, local position, cumulative position, and duration separate. An update from an old segment should not overwrite the active segment's position. Boundary cases such as exact transitions between segments often distinguish an inclusive from an exclusive selection rule. Verify the public seek or progress behavior through its actual event source.

## Preserve terminal behavior and outstanding obligations

Failure, cancellation, unmount, and replacement can leave work that still needs releasing. An in-flight request may continue even when its result should no longer publish. A pending refresh can remain owed after a mutation fails. Cleanup can release resources while retaining enough outcome information for a caller to distinguish finished work from unknown work.

Follow the application's lifecycle and the host's process rules. A quiet operation need not be stuck, and returning from a conversation does not itself establish that background work stopped. Use available receipts and identities to observe what remains. If the public contract permits work to continue, do not introduce an arbitrary deadline or cancellation merely to simplify a test.

The useful result is a state model supported by observed sequences and consumer-visible effects. It should explain why the proposed change handles the relevant orderings and which unobserved environments or races remain uncertain, without requiring an exhaustive interleaving test for unrelated work.
