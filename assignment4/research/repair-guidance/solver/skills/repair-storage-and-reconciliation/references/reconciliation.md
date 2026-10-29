# Desired state, implicit defaults, and effects

These are conditional technical examples. Apply the installed library, public contract, and actual environment to choose the relevant behavior. A worked case illustrates a mechanism; it is not evidence about the repository being repaired.

<a id="desired-state-reconciliation"></a>

## Desired-state reconciliation and idempotence

<a id="desired-state-reconciliation-define-the-state-model"></a>

### Define the state model

Separate user intent, observed state, and platform defaults. Determine whether an omitted field means inherit, leave unchanged, reset, or use a mode-specific default. Explicit false/empty is not automatically omission. Preserve fully qualified identifiers when short names can collide.

List supported modes and their treatment of unspecified fields and resources. Read the repository's actual merge/replace/delete/check-mode contracts. Do not infer semantics solely from a mode's name.

<a id="desired-state-reconciliation-separate-normalization-diff-and-execution"></a>

### Separate normalization, diff, and execution

Normalize equivalent representations without erasing presence or identity. Compute the required change as data before issuing effects. Inspect the exact generated command sequence, including absence of commands for an already-correct resource.

<a id="desired-state-reconciliation-repair-the-comparison-rather-than-masking-effects"></a>

### Repair the comparison rather than masking effects

If repeated runs keep reporting changes, find the difference that never converges: wrong default, asymmetric normalization, incomplete device parsing, reordered collections, or an unconditional command. Suppressing a changed flag while still emitting effects hides the defect.

Use mode-specific desired-state construction and the existing platform adapter. Distinguish deleting a resource from resetting one property. Preserve command ordering where dependencies require it. If the platform's reported state omits defaults, compare effective state using the supported default model rather than treating every absent field as false.

<a id="desired-state-reconciliation-verify-convergence-and-exact-effects"></a>

### Verify convergence and exact effects

Test absent, existing-correct, and existing-different resources; omitted and explicit values; applicable modes; and check mode. Assert emitted commands, reported change, and resulting state where a simulator or fixture is available. Reconcile again against the resulting observed state and expect no further effects when idempotence is promised. Do not claim convergence by running twice against the same unchanged fake current state.

If the requested operation is explicitly imperative and must run every time, do not force desired-state semantics onto it. Use idempotence only where the public contract requires it.

<a id="desired-state-reconciliation-mechanism-details"></a>

<a id="desired-state-reconciliation-defaults-modes-and-reconciliation-examples"></a>

### Defaults, modes, and reconciliation examples

<a id="desired-state-reconciliation-details-keep-presence-until-intent-is-resolved"></a>

<a id="desired-state-reconciliation-keep-presence-until-intent-is-resolved"></a>

#### Keep presence until intent is resolved

```text
intent = parse user input while preserving omitted fields
current = parse observed platform state
desired = apply the documented mode and defaults to intent/current
changes = compare semantically equivalent desired/current values
commands = render changes using the platform's required order
execute only outside check mode
```

Some systems compute changes against a partial desired state rather than constructing a full state. Preserve the existing model if it expresses the contract clearly.

<a id="desired-state-reconciliation-details-decision-dimensions"></a>

<a id="desired-state-reconciliation-decision-dimensions"></a>

#### Decision dimensions

| Dimension | Distinctions |
|---|---|
| Presence | Omitted, explicit enabled/disabled, explicit empty/reset |
| Current state | Absent, already correct, different, omitted default |
| Mode | Only modes actually supported and their documented semantics |
| Identity | Full namespace/path, resource name, property |
| Execution | Normal, check/dry-run, repeated application |

<a id="desired-state-reconciliation-details-worked-case-a-feature-enabled-by-the-device-default"></a>

<a id="desired-state-reconciliation-worked-case-a-feature-enabled-by-the-device-default"></a>

#### Worked case A: feature enabled by the device default

A device omits a feature flag when it has its default enabled state. The caller explicitly requests enabled. Treating absent device output as disabled produces a redundant enable command every run. Resolve the observed omission using the actual platform/version default, then compare effective values. An explicit request to disable still needs the appropriate command.

If the caller omits the flag, determine the mode's policy: it may leave current state untouched rather than reasserting the platform default. User omission and device-output omission are different facts.

<a id="desired-state-reconciliation-details-worked-case-b-merge-versus-replace-of-a-named-resource"></a>

<a id="desired-state-reconciliation-worked-case-b-merge-versus-replace-of-a-named-resource"></a>

#### Worked case B: merge versus replace of a named resource

A resource has a label and a description. A merge request supplies only a new label; under an inherit-unspecified contract, preserve the description. A replace request may clear it if that mode explicitly resets unspecified properties. Construct expected state separately for each mode, then render the diff.

Use two fully qualified resource identities sharing a short name to expose accidental cross-resource comparison. Compare ordered versus unordered collections according to the domain instead of sorting every list automatically.

<a id="desired-state-reconciliation-details-counterexample-imperative-action"></a>

<a id="desired-state-reconciliation-counterexample-imperative-action"></a>

#### Counterexample: imperative action

A command requests a one-time diagnostic capture on every invocation. Returning no effects because a previous capture exists violates its semantics. Idempotence is a property of the operation's contract, not a universal rule to impose on all commands.

<a id="desired-state-reconciliation-details-convergence-check"></a>

<a id="desired-state-reconciliation-convergence-check"></a>

#### Convergence check

Given current state C, compute changes D and the resulting state C'. Reconcile the same intent against C'. Under an idempotent contract, the second diff is empty. A check-mode run should report the predicted D without mutating state. Test the adapter's parsing of C' too: a correct abstract diff can still fail to converge when emitted command state and observed device representation are normalized differently.

<a id="desired-state-reconciliation-details-exact-effect-assertions"></a>

<a id="desired-state-reconciliation-exact-effect-assertions"></a>

#### Exact effect assertions

Check command contents and order, absence of unnecessary commands, changed/no-change status, and required errors. Do not repair only the reported status. If effects fail partway through, follow the module's established failure/rollback policy and preserve enough evidence to diagnose the remaining state.

<a id="desired-state-reconciliation-details-explicit-modes-and-command-ownership"></a>

<a id="desired-state-reconciliation-explicit-modes-and-command-ownership"></a>

#### Explicit modes and command ownership

A common family of modes merges specified fields, replaces fields within selected resources, overrides a managed resource set, or resets selected resources to defaults. Those are candidate semantics to verify, not universal definitions. Write separate desired-state construction for each supported mode and distinguish a new resource from an existing default-only one.

Keep device-wide defaults, resource-type defaults, current explicit settings, and user intent as separate inputs. Changing a mode can change the effective baseline of another property; command order then matters. Render dependency-changing commands before dependent settings when required. Collect reset/set operations for one resource before emitting its header so the header appears only as often as the protocol permits.

Test the pure diff independently from transport, then test its public executor once. A no-change result needs both empty effects and the correct changed flag. Check mode reports the predicted diff without applying it. Apply a modeled change to obtain the next observed state before testing convergence; reusing an unchanged fake device response does not establish a second-run no-op.

<a id="desired-state-reconciliation-observation-before-comparison"></a>

### Observation before comparison

Verify the fact collector and config manager use their actual connection contracts. A mapping of command outputs, an executor, and an absent connection are different interfaces. Preserve default-only resources as existing, even when they have no non-default attributes, and avoid leaking internal fact metadata into public argument-shaped facts. Derive mode/platform default tables from the permitted contract instead of importing a default from another device family.

<a id="persistence-paths"></a>

## Check shared persistence paths and policy at the boundary

Changing one document representation can affect several codecs, importers, storage paths and query projections. Select a check that follows the value into persistence and back to the public consumer, plus an unchanged document type sharing the processor. Testing only the edited codec can make generation and verification share the same mistake.

Place a policy at every boundary it promises to govern. If direct cache-write helpers are public/testable operations covered by a no-store contract, a guard only in one retrieval caller is insufficient. Enumerate actual write operations and classify intentional exemptions; do not assume every caller must share the same policy. For file writes, distinguish in-place truncation from temp-file replacement. Preserve required mode/ownership on the new inode before publication, and verify existing-mode versus new-file creation behavior separately. Avoid a world-readable publication window followed by chmod as a blanket permission fix.
