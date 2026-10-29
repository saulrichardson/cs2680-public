# Effects and scheduling

Use this when changing API calls, polling, retries, callbacks, event streams, or
tests whose completion depends on time.

Follow a value all the way to its effect. Some functions return an HTTP request
descriptor, SQL statement, command object, or deferred operation. Another layer
executes it. `await makeRequestDescription()` does not cause I/O when the function
returns a plain object. Inspect callers and the actual executor. Check the
observable request, state transition, or stored result rather than the presence
of the factory name in source.

For asynchronous code, identify state, events, and ownership separately. Determine
when a request begins, who may commit its result, how stale results are handled,
who schedules the next attempt, and what disposal or replacement means. A cancel
request does not guarantee that a completion callback cannot arrive. A source
comment that says "once" is not evidence that a callback runs exactly once.

Tests must use the repository's actual environment. A scheduling primitive can
exist in one runtime and be absent in another. Fake timer implementations can
control more than `setTimeout`: inspect the configured version and mocked APIs.
Do not assume `MessageChannel`, `queueMicrotask`, `setImmediate`, or a promise
flush helper is universally available or independent of the mock scheduler.

Before choosing a primitive to avoid a suspected scheduler problem, run a small
probe under the same test configuration. Advance the intended clock, observe the
request/callback sequence, and establish that the awaited operation can settle.
Bound the probe explicitly when a hang is possible, recording that bound as part
of the experiment. A timeout identifies a symptom; it does not by itself prove
which await or timer caused it. Inspect the first missing event.

Useful cases depend on the task: initial loading; pending then successful status;
repeated success; error then retry; disposal before completion; input replacement
while a previous request is in flight. Verify a concrete sequence instead of
adding every case mechanically. Typechecking cannot establish any of these event
orderings. If the test runner fails during configuration or compilation, report
zero executed behavioral tests and investigate that setup separately.
