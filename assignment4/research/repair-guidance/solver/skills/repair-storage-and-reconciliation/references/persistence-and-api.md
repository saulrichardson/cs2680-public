# Persistence, migrations, indexes, and public APIs

These are conditional technical examples. Apply the installed library, public contract, and actual environment to choose the relevant behavior. A worked case illustrates a mechanism; it is not evidence about the repository being repaired.

<a id="persistence-and-migrations"></a>

## Persistence, API boundaries, and migration consistency

<a id="persistence-and-migrations-trace-the-complete-operation"></a>

### Trace the complete operation

Start at the route, command, or public API. Follow decoding, validation, actor/target authorization, mutation, storage, query selection, and response shaping. For schema/index changes, add old records, new writes, backfill, and migration registration/replay to the map.

Define missing/null/zero/populated values separately. Determine whether a value is transient request input, persisted state, computed output, or an indexed projection. Do not persist a request-only credential or selector merely because it appears in the input model.

<a id="persistence-and-migrations-diagnose-the-inconsistency"></a>

### Diagnose the inconsistency

Write a requirement-to-path ledger. Read back an actual write through the consumer that uses it. If old records fail while new ones work, inspect decoding/default/backfill assumptions. If reads work but queries miss records, inspect projection/index selection rather than only the model definition.

<a id="persistence-and-migrations-implement-all-required-edges"></a>

### Implement all required edges

Use existing transaction and migration mechanisms. Decide which writes must be atomic and which failures must leave no partial visible change, based on the contract. Preserve established error mapping and authorization order so a helper-only change does not bypass public checks.

For a new field/index, inspect write paths, readers, queries, serialization, old-record compatibility, and migration execution. Registering a migration without invoking it, or backfilling without updating new writes, leaves one population wrong. Make replay behavior explicit; use checkpoints or idempotence where the repository requires them.

If an actor acts on a different target, build an actor/target/action matrix from public policy. Keep checks at the correct public boundary and use the required identity. Do not generalize one branch's privilege into a universal bypass.

<a id="persistence-and-migrations-verify-visible-behavior-and-stored-results"></a>

### Verify visible behavior and stored results

Test required successful and failure responses through the public entry point. Read back state, query using the real selection path, and inspect response shape. Include old/new records and repeated migration only when relevant. Check failure between coupled writes when atomicity is promised. A model field, helper test, or index declaration alone is not completion.

<a id="persistence-and-migrations-mechanism-details"></a>

<a id="persistence-and-migrations-persistent-data-and-public-api-cases"></a>

### Persistent data and public API cases

<a id="persistence-and-migrations-details-consistency-ledger"></a>

<a id="persistence-and-migrations-consistency-ledger"></a>

#### Consistency ledger

| Concern | Questions |
|---|---|
| Public input | Which fields are accepted, required, and request-only? |
| Authorization | Who acts, on which target, for which action? |
| Mutation | Which values change, and which operations must be atomic? |
| Stored representation | How do old, missing, zero, and new values decode? |
| Query/index | Does the actual query use the new projection or key? |
| Response | Are public fields/errors shaped correctly? |
| Migration | Is it registered, executed, resumable/replayable as required? |

<a id="persistence-and-migrations-details-worked-case-a-normalized-search-field"></a>

<a id="persistence-and-migrations-worked-case-a-normalized-search-field"></a>

#### Worked case A: normalized search field

A directory adds a normalized lookup field. Updating the model and new writes can leave old records absent from queries. Backfilling old rows alone can leave later writes missing the projection. Trace both populations through the actual query, preserving the source value if the display contract requires it.

Use an old record without the field, a new record, a changed record, and a value whose normalization distinction matters. Run the migration under the repository's mechanism. If replay is supported, run it again and assert no unintended changes or duplicate side effects. A database index does not populate the field it indexes.

<a id="persistence-and-migrations-details-worked-case-b-actor-dependent-update"></a>

<a id="persistence-and-migrations-worked-case-b-actor-dependent-update"></a>

#### Worked case B: actor-dependent update

An API permits different operations depending on whether the authenticated actor is the target or has a specified administrative role. Build explicit rows for each allowed/denied actor-target-action combination. Trace the request through the public route so validation and authorization are exercised. A persistence helper called with prevalidated inputs cannot establish that the route enforces the policy.

Separate request-only verification input from stored update fields. On a rejected update, assert both the required response and unchanged persistent state. Preserve error behavior for missing targets and malformed input according to the contract.

<a id="persistence-and-migrations-details-counterexample-unnecessary-migration"></a>

<a id="persistence-and-migrations-counterexample-unnecessary-migration"></a>

#### Counterexample: unnecessary migration

A field is computed on read and the contract requires no persistent projection. Adding a stored column and backfill creates synchronization work without solving the actual behavior. Classify the value's role before choosing a migration.

<a id="persistence-and-migrations-details-atomicity-and-replay"></a>

<a id="persistence-and-migrations-atomicity-and-replay"></a>

#### Atomicity and replay

Identify the commit boundary. If two writes must become visible together, use the existing transaction/rollback mechanism rather than hoping failures will not occur between them. If a migration emits external effects, repeating it may need a checkpoint or deduplication key under the established design. “Upsert every row” is not universally idempotent when triggers or side effects exist.

<a id="persistence-and-migrations-details-checks"></a>

<a id="persistence-and-migrations-checks"></a>

#### Checks

Observe public result, stored value, query result, and response serialization for the relevant paths. Test old/new/missing values and a failure that could leave partial state. Verify migration registration and the actual selection of data to migrate. Preserve the original operation error when cleanup also fails, using the repository's error convention.

<a id="persistence-and-migrations-projection-optional-data-and-route-contracts"></a>

### Projection, optional-data, and route contracts

Read [persistence and API contracts](#domain-persistence-and-api) for pagination, date partitions, old optional data, prerequisites, ordered projections, and inspectable batches. Read [identity and credentials](../../preserve-identity-and-trust/references/credentials-and-crypto.md#domain-identity-and-credentials) for actor/target checks and signed resource identity.

<a id="domain-persistence-and-api"></a>

<a id="domain-persistent-projections-time-ranges-optional-data-and-public-operations"></a>

### Persistent projections, time ranges, optional data, and public operations

Use for changes whose correctness spans input, stored state, queries, and response shaping. Concrete table/index names, date formats, route names, and authorization decisions belong to the current issue, not this reusable guide.

<a id="domain-persistence-and-api-evolve-a-projection-as-one-system"></a>

<a id="domain-evolve-a-projection-as-one-system"></a>

#### Evolve a projection as one system

List producers, stored attributes, readers, query selection, indexes, migration registration, backfill execution, and old-record compatibility. A new indexed field needs both future writes and existing records handled. A new index that no query uses is disconnected; a query that uses it before backfill completes can miss old data.

Choose the completion contract explicitly: synchronous backfill before returning, asynchronous migration with readiness state, or another established process. Pagination belongs to the scan/query API, not an assumption that one call saw the whole dataset. Test more than one page when traversal is relevant. Replaying a migration can repeat external effects even when a row upsert is idempotent.

Determine which fields must always be materialized, which can be absent, and how types are encoded. A storage API that expects numeric attributes must not receive a string merely because the value looks numeric. Use the existing adapter and verify the actual bound query attributes and public results.

<a id="domain-persistence-and-api-partitioned-time-searches"></a>

<a id="domain-partitioned-time-searches"></a>

#### Partitioned time searches

Separate coarse partition selection from exact timestamp filtering. Normalize time in the storage contract's timezone before deriving a day/month partition. Enumerate every partition touched by the requested interval, including month/year boundaries. Apply exact lower/upper bounds under the required inclusive/exclusive semantics; fetching all selected days is not equivalent to filtering the interval.

```text
normalize the requested endpoints according to the API's timezone policy
enumerate the storage partitions intersecting that interval
query each partition with its actual key/range constraints
collect pages and enforce the required ordering/deduplication
return only records admitted by the exact endpoint policy
```

Do not hardcode a current date or use a display-timezone string as a key. Test a same-partition interval, a boundary instant, a cross-month interval, and a migrated old record. A fixed clock makes lifecycle/date checks reproducible.

<a id="domain-persistence-and-api-preserve-optional-scalars-across-upgrades"></a>

<a id="domain-preserve-optional-scalars-across-upgrades"></a>

#### Preserve optional scalars across upgrades

For timestamps or optional numbers, trace database null → model representation → helper assignment → update path → API serialization. A pointer helper that replaces nil with a pointer to zero can change the public response without any explicit migration. An omitted field and an epoch/zero timestamp are not automatically equivalent.

Build a table for old absent, explicit zero, populated, new absent, and updated values. Test read and write paths. Match JSON/database omission rules to the contract; equality of internal numeric values does not prove identical public representation. If nil versus empty collection is observable, preserve that distinction rather than normalizing it incidentally.

<a id="domain-persistence-and-api-public-authorization-and-prerequisites"></a>

<a id="domain-public-authorization-and-prerequisites"></a>

#### Public authorization and prerequisites

Map route parameter parsing, authenticated actor, target, prerequisite queries, mutation, and response. A persistence helper accepting prevalidated data cannot establish that the route enforces a prerequisite or rejects an unauthorized action. Check a required failure before mutating state, then assert both the error response and unchanged storage.

A read prerequisite may depend on work/edition/resource identity rather than the route's display ID. Trace actual IDs through lookups and ensure the requested record is the one checked. Do not invent permissive fallback when a required related record is missing. Response shape and operation return values remain part of the public contract.

<a id="domain-persistence-and-api-ordered-projections-and-inspectable-update-batches"></a>

<a id="domain-ordered-projections-and-inspectable-update-batches"></a>

#### Ordered projections and inspectable update batches

Separate gathering updates from executing them. A batch should preserve explicit adds, deletes, commit options, and execution results if consumers inspect those. Mutable default collections shared across instances can cross-contaminate independent batches. Test two batches created separately and change one.

For asynchronous per-record work, retain each result's record identity while aggregating into one execution command. Decide ordering from the API, not completion timing. If deletion and addition for the same identity can coexist, use the established reconciliation rule rather than implicitly choosing whichever callback finished last.

Catalog-driven ordered output needs an explicit join: selected IDs, catalog order, label lookup, unknown-ID behavior, and persisted representation. Sorting labels alphabetically can violate a catalog order even when every label is present. A full output assertion should include positions and missing values.

<a id="domain-persistence-and-api-independent-verification"></a>

<a id="domain-independent-verification"></a>

#### Independent verification

Use old/new records; at least two query pages if supported; a time range across a partition boundary; absent/zero/set values; a rejected public operation; two independent batches; and out-of-order completion. Choose relevant cases, not every cell. Observe stored values, actual query arguments, selected index, public response, and returned execution result. Passing only a helper or schema test establishes one edge of this system.

<a id="batch-identity"></a>

## Match batch lookup keys and forwarding contracts

For per-entity metadata accompanying a batch, use the same identity function at insertion and lookup, including the documented treatment of composite IDs. Avoid assuming a tuple's last component is globally unique. Carry the map separately through the existing positional or named interface, including explicit absence when the public contract requires it. Check mixed entities, duplicate components from different collections, missing keys and no-map calls. Do not infer runtime behavior from a clean TypeScript build; execute the lookup and downstream consumer in a focused test when available.
