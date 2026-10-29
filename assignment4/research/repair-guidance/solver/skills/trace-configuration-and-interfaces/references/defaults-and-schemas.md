# Configuration presence and actual schema inputs

These are conditional technical examples. Apply the installed library, public contract, and actual environment to choose the relevant behavior. A worked case illustrates a mechanism; it is not evidence about the repository being repaired.

<a id="configuration-runtime-wiring"></a>

## Configuration and interface wiring

<a id="configuration-runtime-wiring-create-a-path-ledger"></a>

### Create a path ledger

Trace schema/documented input → parser → decoded representation → validation → defaults/merge → options → constructor → consumer. Include embedded/generated schemas and alternative constructors when they exist. For each hop record type, name, presence, and effective value.

Distinguish omitted, explicit default, explicit zero/false/empty, valid alternative, and unsupported value. A language zero value does not establish whether the caller supplied the field. Find the layer where absence is intentionally resolved.

<a id="configuration-runtime-wiring-diagnose-the-gap"></a>

### Diagnose the gap

Inject a distinct value at the public configuration entry point and observe the actual runtime dependency or behavior. If validation passes but behavior is unchanged, identify the first ledger edge where the value disappears or changes. If only one backend works, inspect the backend selection table and every construction branch.

<a id="configuration-runtime-wiring-implement-a-complete-option"></a>

### Implement a complete option

Reuse the existing option and dependency injection conventions. Keep default application at the established boundary rather than independently defaulting in every wrapper. Preserve previously omitted-value behavior unless the public contract changes it.

When adding a selector, define supported values and required errors, route to the intended implementation, and pass backend-specific dependencies through the real constructor. Updating an enum/schema without constructor selection produces a feature that validates but does nothing.

When a signature changes, inspect callers, wrappers, mocks, generated adapters, interface assertions, and platform variants. Use the existing generator for generated code. Match APIs to the versions pinned by the repository; do not upgrade a dependency to avoid learning the installed API.

<a id="configuration-runtime-wiring-verify-public-input-to-behavior"></a>

### Verify public input to behavior

Test omitted/default/explicit alternative and required invalid inputs through the configuration loader or command. Observe the constructed runtime dependency or resulting behavior. Compile applicable interfaces and run representative backend/platform checks. Helper or schema tests are useful but do not prove end-to-end wiring. If values are transported correctly but the consuming algorithm is wrong, use the mechanism skill for that algorithm instead of adding more configuration plumbing.

<a id="configuration-runtime-wiring-mechanism-details"></a>

<a id="configuration-runtime-wiring-wiring-patterns-defaults-and-whole-path-checks"></a>

### Wiring patterns, defaults, and whole-path checks

<a id="configuration-runtime-wiring-details-configuration-ledger"></a>

<a id="configuration-runtime-wiring-configuration-ledger"></a>

#### Configuration ledger

| Layer | Observation |
|---|---|
| Public format/schema | Accepted name, type, optionality, selector values |
| Decoder | Runtime value and whether presence survives |
| Validation/defaults | Effective value and which rule produced it |
| Options/constructor | Actual argument or injected dependency |
| Runtime consumer | Observable behavior, backend call, or selected implementation |

The first edge that breaks expected propagation is a better edit target than every layer that mentions the field. Still verify downstream consumers and alternative paths after fixing it.

<a id="configuration-runtime-wiring-details-worked-case-a-explicit-false-versus-inherited-default"></a>

<a id="configuration-runtime-wiring-worked-case-a-explicit-false-versus-inherited-default"></a>

#### Worked case A: explicit false versus inherited default

A service enables an option by default but permits explicit false. Code such as “if not enabled, apply default true” collapses omission and false. Preserve presence until the designated defaulting layer, using the repository's optional type, pointer, presence flag, or configuration representation. Resolve omission to the default there and pass an ordinary effective value downstream.

Test omitted, explicit false, and explicit true through the real loader. A constructor test passing `false` directly bypasses a decoder/defaulting defect.

<a id="configuration-runtime-wiring-details-worked-case-b-pluggable-resolver-backend"></a>

<a id="configuration-runtime-wiring-worked-case-b-pluggable-resolver-backend"></a>

#### Worked case B: pluggable resolver backend

A schema permits backend A and backend B. Validation recognizes B, but a constructor always creates A or neglects B's required client. Use a backend selection table and a fake client with distinguishable behavior. Load a configuration selecting B and invoke the public operation; assert B's client is used and its result/error is mapped correctly.

Exercise omitted selector compatibility and unsupported values. Do not initialize every external service just to make the selector test pass; inject dependencies at the existing seam.

<a id="configuration-runtime-wiring-details-counterexample-constructor-owned-defaults"></a>

<a id="configuration-runtime-wiring-counterexample-constructor-owned-defaults"></a>

#### Counterexample: constructor-owned defaults

A library explicitly promises that every constructor accepts raw options and applies defaults itself. Moving all defaulting into one configuration loader can break direct constructor users. The correct location follows the public API and established architecture. Where multiple entry points exist, centralize semantics without removing their guarantees.

<a id="configuration-runtime-wiring-details-change-checklist"></a>

<a id="configuration-runtime-wiring-change-checklist"></a>

#### Change checklist

Search the option's names and the changed interface's implementations. Inspect examples and embedded schemas that production actually loads. A generated file and its source can diverge if only the output is edited. Check build tags/platform-specific constructors without forcing unsupported targets to run locally.

For Go interfaces, method sets and parameter/result types must match; similar names are insufficient. For a Go/CUE boundary, distinguish schema acceptance from decoding a value and from invoking the consuming Go code. Confirm the checkout's version and actual API before choosing a decoding helper.

<a id="configuration-runtime-wiring-details-evidence-of-completion"></a>

<a id="configuration-runtime-wiring-evidence-of-completion"></a>

#### Evidence of completion

The complete chain is observed: a public input selects the intended runtime behavior; omission preserves compatibility; explicit alternatives survive; invalid input has its required error. Passing a schema check or adding a field declaration alone establishes only one edge.

<a id="configuration-runtime-wiring-concrete-runtime-boundaries"></a>

### Concrete runtime boundaries

Read [schema and types](#domain-schema-and-types) for real-default validation, optional subsections, tagged coercion, embedded assets, and error contracts. Read [service wiring](service-construction.md#domain-service-wiring) for exporters, sampling, propagation, credentials/address selection, and shutdown. Read identity and credentials for static/dynamic provider modes.

<a id="domain-schema-and-types"></a>

<a id="domain-presence-schemas-coercion-and-representation-boundaries"></a>

### Presence, schemas, coercion, and representation boundaries

Use when a setting validates but changes meaning, a decoder loses a union/tag, or schema validation disagrees with real configuration. The workflow is authored synthesis; linked language/library facts must be matched to the checkout's version.

<a id="domain-schema-and-types-model-presence-before-choosing-defaults"></a>

<a id="domain-model-presence-before-choosing-defaults"></a>

#### Model presence before choosing defaults

An optional scalar has at least two independent attributes: present and value. `present=false` is not the same as `present=true, value=false`. A language's default zero cannot necessarily recover that distinction after decoding. Keep a pointer, option wrapper, presence map, or existing decoder metadata until the authorized defaulting boundary.

Distinguish absent subsection, explicit null, and an empty subsection object. A value struct may emit `{}` for an unused backend and accidentally activate schema constraints requiring its fields. A nullable representation can preserve absence, but changing to pointers also requires updating callers, default initialization, and serialization. Do not add nullability solely to make a simplified test pass.

A constructor may accept only resolved selectors and reject empty input while the loader owns a backward-compatible default. Putting that default in both places can conceal missing wiring and violate direct-constructor behavior. Test the public loader and the constructor contracts separately.

<a id="domain-schema-and-types-validate-the-actual-representation"></a>

<a id="domain-validate-the-actual-representation"></a>

#### Validate the actual representation

Trace the real default factory, environment/file merge, decode-hook chain, field-name mapping, duration/enum adaptation, and schema input. A handcrafted map that resembles configuration omits exactly the tricky fields most likely to fail. Use the actual production factory and transformations.

Durations may be native values internally and strings at a schema boundary. The conversion must happen at that boundary without accidentally destroying the internal type or changing field names. Reflection over struct tags must parse tag options such as omission separately from the field name. Do not turn an entire tag string into the schema key.

Write a representation ledger: internal type, decoded type, encoded form, schema type, default, and presence semantics. For unions, list every accepted branch and its discriminator. A single-item collection may have a canonical serialized shorthand while a multi-item value uses a structured form; that does not imply every neighboring field adopts the same union.

<a id="domain-schema-and-types-cue-validation-pipeline"></a>

<a id="domain-cue-validation-pipeline"></a>

#### CUE validation pipeline

CUE's Go API provides values, unification, validation, and decoding. Unification combines constraints; an incomplete value and an inconsistent value are different states. Consult the pinned API's concrete-validation and context requirements. Source: [CUE value API](https://pkg.go.dev/cuelang.org/go/cue), with [Go integration concepts](https://cuelang.org/docs/concept/how-cue-works-with-go/).

Concrete API stages to identify in a version-compatible implementation include `Context.CompileBytes` or `CompileString` for schema text, `encoding/yaml.Extract` for YAML syntax to a CUE AST, `Context.BuildFile` for the input value, `LookupPath`/`ParsePath` when selecting a named definition, `Value.Unify` for constraints, and `Validate` with the required concreteness options. `Decode` into Go is a different operation. Source: [CUE YAML API](https://pkg.go.dev/cuelang.org/go/encoding/yaml#Extract). These names illustrate API roles, not a target wrapper signature; verify availability in the checkout's version.

Go can embed schema assets using `//go:embed` with the embed package, into supported variable forms. The pattern is relative to the package and the asset must participate in the build. Source: [Go embed](https://pkg.go.dev/embed). Test the built artifact outside the source directory rather than relying on a coincidental local file.

At a repair boundary, distinguish YAML syntax errors, schema compilation errors, type/constraint conflicts, and unresolved required values. A schema asset can compile in source tests but disappear from a packaged binary if runtime loading assumes an external path. Verify embedding/loading through the actual build mechanism and run from a directory that does not accidentally supply the source asset.

The validation error is itself part of some public APIs. Returning the original error, wrapping it with context, and substituting a sentinel preserve different things. Follow the specified contract. Do not prepend helpful text when byte-exact error identity or text is required. At another boundary, context may be explicitly required; there is no universal rule to wrap or never wrap.

<a id="schema-presence"></a>

## Schema lookup, presence and decoded defaults

If a public schema consumer looks up a named definition, expose that exact name/path and test lookup before validation. In CUE, optional field presence (`field?`) does not by itself allow a null value. Permit null only when the decoded configuration contract admits it. Similarly, a disjunction of named strings does not accept an empty string unless explicitly included; decide whether the loader supplies a named default or preserves empty.

Validate the actual decoded map rather than a hand-written approximation. Closed schema definitions may reject fields emitted by embedded structs, pointers, tags or zero-value serialization. Enumerate the real fields and required optionality, then test empty/default input and representative populated input. Avoid blanket `| null` or arbitrary open fields as a way to silence schema errors. Those changes can discard a validation promise.
