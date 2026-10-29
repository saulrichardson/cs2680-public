# TypeScript and JavaScript repair cookbook

This optional memory layer offers uncommon implementation/testing techniques. The main async, data, and identity skills contain the required repair models. The executable examples use plain JavaScript so their mechanics can be checked independently of a framework. Preserve the target's TypeScript types, runtime version, public synchrony, and ownership rules when adapting them.

## Additional focused recipes

- [T6 — Request descriptors, executors and observable effects](#t6-request-execution)


## Recipe map

| Trigger | Recipe |
|---|---|
| A race test depends on sleeps or an old request overwrites new state | [T1: choose the completion schedule](#t1-deferred) |
| Cleanup is correct but an unhandled rejection appears, or old cleanup removes a new entry | [T2: own the returned chain](#t2-finally) |
| Byte values change later or a supposedly identical key misses its map | [T3: views, copies, and key identity](#t3-identity) |
| Keyboard validation breaks composition or multi-unit text | [T4: input is not just keydown](#t4-composition) |
| Fake time advances but state is still pending | [T5: distinguish queues and observable transitions](#t5-scheduling) |

<a id="t1-deferred"></a>

## T1 — Choose response order instead of waiting for a race

**Use when:** causality depends on A/B completion order, cancellation, or replacement.

Create controllable promises and explicitly drive the schedule. The example gate represents a single latest-result-wins scope; it does not cancel underlying work or count overlapping operations.

```javascript recipe=controlled-completion
function deferred() {
    let resolve, reject;
    const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
    return { promise, resolve, reject };
}

function latestGate() {
    let generation = 0;
    return {
        begin() { return ++generation; },
        accepts(token) { return token === generation; },
        invalidate() { ++generation; },
    };
}
```

Start A, record its token, start B, then settle B before A. Await the actual observer chains and assert that only admitted work commits. Add an invalidation with no replacement request; merely checking that another request started can miss reset/disposal behavior. Reject a request as well as resolving it so error commits cannot bypass the freshness guard.

**Counterexample:** independent uploads whose results all belong in a collection must not share one global latest-only gate. Scope identity appropriately or use a per-operation model. A generation is not a pending count, and a test demonstrating stale admission does not establish cleanup or cancellation behavior.

<a id="t2-finally"></a>

## T2 — Observe the promise produced by cleanup

**Use when:** an ignored cleanup chain creates a rejection, or an old completion erases replacement bookkeeping.

`finally()` returns a new promise. It generally preserves the original settlement, but a throwing/rejecting cleanup can change the resulting failure. Source: [Promise.finally](https://developer.mozilla.org/en-US/docs/Web/JavaScript/Reference/Global_Objects/Promise/finally).

```javascript recipe=owned-cleanup
function withOwnedCleanup(pending, key, operation) {
    return operation.finally(() => {
        if (pending.get(key) === operation) pending.delete(key);
    });
}
```

This model assumes the map contains the original operation promise, installed by its owner before this helper is used. The caller must return or await the derived promise. Awaiting only the original while ignoring a rejecting derived chain can still leave an unhandled failure. If the store instead uses wrapper identities, compare the corresponding owned identity consistently.

Test rejection by awaiting the returned cleanup chain. Then install a newer operation under the same key before the old one settles; the old cleanup must leave the new entry intact. Also test normal settlement removes its own entry. Do not add a catch that swallows the original error merely to quiet a test runner.

For a factory that can throw synchronously or re-enter the lookup, establish ownership at the contractually correct point. Moving `start()` into a `.then()` changes initiation timing. That may be a bug when release/start is required in the same call stack; do not hide it in a convenience wrapper.

<a id="t3-identity"></a>

## T3 — Separate object identity, byte equality, shared storage, and lookup identity

**Use when:** cryptographic/opaque values mutate unexpectedly, a wrapper copies a value that must remain identical, or map keys collide/miss.

A typed-array `subarray()` is a view of underlying storage. A copy and a view can start with equal bytes but have different later behavior. Source: [typed-array subarray](https://developer.mozilla.org/en-US/docs/Web/JavaScript/Reference/Global_Objects/TypedArray/subarray).

```javascript recipe=byte-and-key-identity
function byteViews() {
    const original = new Uint8Array([11, 22, 33]);
    const view = original.subarray(1);
    const copy = original.slice(1);
    return { original, view, copy };
}

function scopedStringKey(scope, identity) {
    if (typeof scope !== "string" || typeof identity !== "string") {
        throw new TypeError("this model requires string components");
    }
    return JSON.stringify([scope, identity]);
}
```

Check the contract before copying. It may require the same buffer object, merely equal bytes, or deliberate isolation from mutation. A base64 conversion changes representation and cannot prove same-object transport. Use harmless marker bytes and test identity plus content separately.

For maps, concatenating components with a delimiter can collide when components contain that delimiter. The illustrated key encoding is scoped to strings and is not a universal serializer for arbitrary objects. A newly created tuple/array used as a Map key has a new object identity; reproducing its elements does not reproduce that key object. A supplied `undefined` value can also differ from no entry, so use presence checks where the interface distinguishes them.

**Checks:** mutate the original and inspect view/copy; compare delimiter-collision pairs; exercise absent versus present-with-undefined; process a missing entity after a supplied entity. Do not introduce a new key format without a compatibility/invalidation policy for existing stored data.

<a id="t4-composition"></a>

## T4 — Diagnose composition and text boundaries before filtering events

**Use when:** input works for simple key presses but fails for composition, paste, replacement, or non-ASCII text.

An input event can indicate that composition is in progress. Source: [InputEvent.isComposing](https://developer.mozilla.org/en-US/docs/Web/API/InputEvent/isComposing). A keyboard event, an accepted text edit, and a completed composition are different observations.

Determine which events the component treats as authoritative and what value the parent controls. Filtering on every keydown can interfere with an input method; filtering an entire string after composition can change slot positions under a positional contract. A JavaScript string's code units, code points, and user-perceived characters are also different units. Do not solve a Unicode requirement with a byte count or assume splitting a string yields the desired character unit.

For an ASCII-only verification field, preserve its declared validation policy rather than expanding scope to all scripts. Use a real browser/DOM harness for the event sequence the bug requires: composition start/update/end, input, paste, selection replacement, external prop reset, and focus. Assert the emitted value and callback count, not just the final visible text.

This recipe is diagnostic guidance; the plain-JavaScript checks in the authoring repository do not simulate a browser's complete composition behavior. Report that boundary explicitly.

<a id="t5-scheduling"></a>

## T5 — Test the boundary between synchronous work, microtasks, and timers

**Use when:** a fake-clock test looks stalled or a repair subtly changes public timing.

Write which transition must occur before the function returns, after a promise settles, and after a timer. Observe those boundaries independently. A fake clock may advance timers without settling every promise chain; a few calls to a generic “flush” helper may accidentally depend on current nesting depth. Await known observer promises or the framework's supported scheduling utilities.

For a synchronous-release contract, assert the effect immediately after the final settlement callback and before advancing timers. For delayed work, assert that it did not happen early, then advance to the required boundary. For an interval versus recursively scheduled timeout, use a deliberately slow response to expose whether calls overlap or cadence shifts.

Do not make production code asynchronous merely to accommodate a fragile test. Conversely, do not assume a browser/framework updates rendering synchronously because a plain function mutates state immediately. Keep initiation, state commit, rendering, and callback notification as separate observable events.


<a id="t6-request-execution"></a>

## T6 — Request descriptors, executors and observable effects

**Use when:** a component builds API calls through a helper/wrapper layer.

Inspect a real call and the pinned helper definitions. This illustrative builder performs no I/O:

```typescript
type Request = { method: "POST"; url: string; body: unknown };
const buildRequest = (body: unknown): Request => ({ method: "POST", url: "/operation", body });
// Awaiting buildRequest(payload) merely returns the descriptor.
// Pass it to the repository's actual executor when that is the API contract:
// const response = await api(buildRequest(payload));
```

The executor may own credentials, cancellation and error normalization; preserve that boundary. Other helpers execute requests themselves, so do not double-wrap them. A focused fake should observe the executor call and supply a response that exercises the downstream state/UI transition. Verify arguments and effects, not just whether a function returned an object. For polling, observe the initial request, status query and terminal callback under controlled scheduling instead of relying on arbitrary waits.
