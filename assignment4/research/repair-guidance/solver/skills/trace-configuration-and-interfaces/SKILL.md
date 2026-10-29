---
name: trace-configuration-and-interfaces
description: Trace public configuration and changed interfaces through decoding, defaults, constructors, wrappers, and consumers. Use when an accepted option has no effect, values disappear between layers, schemas disagree with defaults, or a signature/dependency migration is incomplete.
---

# Connect an accepted input to its actual behavior

A field declaration, accepted configuration file, or successfully constructed object establishes only part of a feature. Use this skill when information must travel through several components before it changes behavior. The key question is where the intended value or choice first stops reaching the component that interprets it.

## Keep presence until intent is resolved

Omission, an explicit default, false, zero, an empty collection, and an absent subsection can have different meanings. A loader that applies a default whenever a value is false can erase an explicit request to disable a feature. A value struct serialized as an empty object can activate constraints for a backend the user never selected. An added pointer can preserve absence but leave existing constructors or direct callers with new nil cases.

Find the layer responsible for resolving that distinction. Some applications centralize defaulting in a configuration loader; some libraries promise that every constructor accepts raw options and applies defaults itself. Moving semantics into one path can break another legitimate entry point. Trace the public interfaces and established ownership rather than treating one location as universally correct.

For a small experiment, choose omitted, explicit false, and explicit true values that produce distinguishable behavior. Pass them through the real loader and observe the constructed dependency or final operation. A test invoking a constructor directly with false bypasses a defect in decoding or default application. The [defaults and schemas reference](references/defaults-and-schemas.md) develops presence, canonical defaults, actual encoded maps, and embedded validation assets.

## Follow the value through every implementation of the path

Trace public input to decoder, validation, options, constructor, forwarding methods, and the final consumer. Inspect the actual argument at the point where behavior is chosen. A new field can be present in the originating type and missing from one constructor, a batch request, an alternate backend, or a remote lookup path. Repeating the declaration in more places does not repair the missing edge.

When changing a callable, inspect the required name and argument order as well as the intended algorithm. Callers, mocks, interfaces, generated adapters, platform variants, and embedded examples can participate in the contract. A renamed helper with the new data does not satisfy a consumer that is required to call the original name with a new signature. Conversely, a deliberately retired name should not be kept merely to make an obsolete local check compile.

Check at the actual call sites. Two marker values sent through different entities or paths can expose dropped arguments and accidental reuse. A missing optional value can reveal whether a wrapper shifts positional arguments or silently replaces absence. For entity-specific secret material and trust choices, continue with [identity and trust](../preserve-identity-and-trust/SKILL.md).

## Verify schemas against the representation the application produces

A schema can be internally valid and still reject the application's canonical default configuration. Determine how the typed configuration is decoded or serialized into the validator's representation. Exported hooks, aliases, duration types, omitted fields, and nested zero values can all affect that intermediate map. Inspect it rather than constructing a convenient handwritten map that bypasses the issue.

Similarly, accepting a new enum or field in a schema does not establish that the runtime uses it. An embedded schema must actually be packaged and loaded through the production path. Distinguish parsing success, schema compilation, application validation, and invocation of the consuming code. Preserve the error boundary and message specified by the public API; a generic wrapped error can break a required caller-visible contract.

When the task changes a dependency, inspect the pinned version and its real API before selecting a helper. A function remembered from another version can have different signatures or behavior. Changes to module files and lockfiles should describe what a fresh build can resolve, not merely what happens to exist in a local cache.

## Construction can perform effects

An exporter, credential provider, or service client may register global state or start work during construction. Creating every supported backend eagerly can trigger conflicting registration even when only one is selected. A factory may return a usable client but omit the cleanup function its owner expects. A configured endpoint can be assembled correctly yet passed through an option that the constructor ignores.

Observe the selected implementation and relevant side effects. Use a backend with distinguishable behavior at the existing injection seam. For telemetry, check the installed sampler and propagators rather than inferring them from configuration strings. For credential discovery, preserve any dynamic resolution behavior the public contract requires. Repeat initialization only under the lifecycle the library supports; a test that assumes global reset may not represent production.

The [service construction reference](references/service-construction.md) covers endpoint parsing, telemetry, credential options, registration, shutdown, and versioned decoding interfaces. It supplies technical examples without prescribing a particular backend or configuration format.

## Handle a wider result contract at the consumer

A producer that now returns absence, an additional state, or an expanded tuple changes what existing callers must handle. Follow those results into aggregators and report builders. A new per-item field stored only in one global accumulator can make the final output depend on iteration order. A new nil result passed to a previously unconditional append or dereference can turn a valid unsupported case into a panic.

Use two items with different states and, where order should not matter, reverse their processing order. Invoke the original public operation through the changed producer. The observation should distinguish correct forwarding from a caller that accidentally keeps an old default, drops one return value, or assumes a result always exists.

A useful repair preserves meaning along the chain and places interpretation at its established owner. It need not touch every file that mentions the option. Correct the broken edge, then verify the downstream behavior and the alternative paths the task promises to support.
