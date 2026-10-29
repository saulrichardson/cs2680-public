---
name: repair-storage-and-reconciliation
description: Repair cache policy, persistent state, migrations, indexed reads, and desired-state updates without losing scope or ownership. Use when repeated operations, stale results, missing records, defaults, or incomplete convergence are the defect.
---

# Establish which state exists and who may change it

A successful helper can leave a storage feature incomplete because a read path, index, migration, or policy boundary still uses another representation. Use this skill when repeated requests produce unexpected work, persisted data cannot be retrieved correctly, or a desired-state change generates the wrong effects. Begin with the public operation and trace state reads and writes together.

## Separate policy dimensions before selecting a cache fix

Read bypass, write suppression, invalidation, clearing existing data, disabling initialization, joining pending work, and retaining a settled failure are different choices. A request asking not to store its result does not necessarily ask to ignore an existing cache entry. A cache initialized empty is different from one deliberately disabled. A missing backing directory can mean uninitialized state or corrupted state under different contracts.

Trace lookup, backend call, encoding, storage, and invalidation using the same identity. Include the user, namespace, resource, credentials mode, or other scope that the operation actually distinguishes. A key that works for one resource can cause cross-resource failures later. Cache metadata should not expose secret material merely because a provider needs it to authenticate.

For an external directive, follow the incoming transport through middleware into the handler's context. An outgoing response metadata object can contain similar fields while being the wrong source for request policy. Verify the marked request through the actual middleware and observe whether the storage operation writes. The [cache policy reference](references/cache-policy.md) develops independent cache dimensions, disk initialization, pending sharing, selective failure retention, and invalidation races.

## Give each retained object its own lifetime

A pending request and its settled outcome may have different retention policies. Leaving a rejected promise in a pending map can prevent future work indefinitely. Clearing every rejection can defeat a deliberate short-lived failure cache. A late completion can repopulate state after an invalidation unless ownership or a revision is checked.

Observe sequential calls and overlapping calls separately. Sequential backend calls after a completed eligible failure suggest missing outcome retention; simultaneous duplicate calls suggest missing in-flight sharing. Use a controlled clock or explicit expiration seam when time is relevant. Keep the required error shape and identity visible to callers rather than inventing a sentinel that silently changes behavior.

A cache is not the answer to every repeated request. The caller may require current authorization or fresh data, or the underlying issue may be a loop issuing unintended operations. Establish that retention is permitted and addresses the cause before adding it.

## Treat persistence as a read–write system

A new table, field, or writer does not establish a complete persistent feature. Inspect direct writes, bulk insertion, readers, filters, ordering, index keys, backfills, and historical records. The same value may need propagation through both an ordinary write and a batch path. A migration that fills old records but omits new writes leaves the defect recurring.

For time-based search, establish the timestamp unit, time zone, partition calculation, range inclusivity, sort direction, and pagination behavior. A date partition can make queries efficient while introducing missing boundary records or a different order. Compute selection from the requested interval, inspect the actual backend key conditions, and verify final records across a partition boundary. A source string containing the index name proves only that the name exists.

Optional values require special care during upgrades. Zero time, absent time, database null, and a real epoch value can differ. Preserve the domain's empty or absent semantics through mapping and serialization. The [persistence and API reference](references/persistence-and-api.md) covers migration consistency, nullable scalars, ordered projections, prerequisite validation, and API response boundaries.

## Validate public operations before relying on a successful write

Authentication, prerequisite checks, and duplicate detection can be part of the public contract. Determine the required order and the layer that owns each decision. A condition that should fail before database access must not depend on a database exception to discover it. A test double should allow the condition to be checked without accidentally constructing a real connection.

After the model or storage operation returns, inspect the response the caller receives through serializers and decorators. Correct persistence does not compensate for an incompatible body type or error shape. The [contract skill](../verify-contracts-and-tests/SKILL.md) and [representation skill](../preserve-representations/SKILL.md) develop those final boundaries.

## Reconcile intent with observed and default state

Device output and user input can both omit a value for different reasons. Output omission may mean the device uses a default; user omission may mean preserve existing state. Applying one truthiness rule to both can generate the wrong command. Identify whether an object is explicitly configured, implicitly present, absent, or at its default before computing a change.

Separate semantic normalization, comparison, and execution. Normalize only where the public mode permits equivalent forms. A merge can preserve unspecified fields, while replace or reset may require restoring defaults. Repeated reconciliation should converge when that is the operation's contract; an imperative restart or append operation need not be idempotent.

Use a small state transition with exact effects: the original observed state, the intended request, the commands or writes produced, and the state after applying them. Repeating the same desired-state operation can reveal redundant effects or failure to recognize convergence. The [reconciliation reference](references/reconciliation.md) develops these distinctions, including implicit defaults and mode-specific command ownership.

A dependable repair preserves policy scope, identity, and lifecycle from input to final read-back. It should make the consequential decision understandable without adding state, retries, or storage merely to make the trace appear more orderly.
