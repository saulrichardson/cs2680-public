# Constructors, services, and changed interfaces

These are conditional technical examples. Apply the installed library, public contract, and actual environment to choose the relevant behavior. A worked case illustrates a mechanism; it is not evidence about the repository being repaired.

<a id="domain-service-wiring"></a>

<a id="domain-service-construction-telemetry-endpoint-selection-and-shutdown"></a>

### Service construction, telemetry, endpoint selection, and shutdown

Use when configuration changes span a provider factory, global service setup, clients, or shutdown. The procedures are authored synthesis; use the actual dependency APIs and public contracts.

<a id="domain-service-wiring-factory-versus-loader-responsibilities"></a>

<a id="domain-factory-versus-loader-responsibilities"></a>

#### Factory versus loader responsibilities

Write separate contracts for the public loader and dependency factory. If the loader resolves an omitted selector, the factory can legitimately reject an empty selector. Putting the default inside the factory can hide a caller that failed to load configuration and can violate a direct-factory API. Track enabled/disabled separately from selected implementation and from transport propagation.

For each backend record inputs, injectable client, construction side effects, output capability, shutdown function, and errors. A factory that returns a working object but a nil cleanup callback can fail the service's shutdown path. An exporter package that registers itself at import time can conflict with later configured construction. Prefer the existing lifecycle boundary and test initialization more than once only under the documented reset/reuse contract.

Distinguish process-wide one-time initialization from configuration-specific state. A resettable test seam does not imply live production reconfiguration is supported. If singleton state is part of the API, scope tests so they do not accidentally succeed only because another test initialized the desired backend first.

<a id="domain-service-wiring-endpoint-parsing-and-dependency-options"></a>

<a id="domain-endpoint-parsing-and-dependency-options"></a>

#### Endpoint parsing and dependency options

A plain host/port and a URL with a scheme can be parsed differently by standard URL libraries. Derive accepted forms, transport security, path handling, and default ports from the client contract. Do not turn a supported scheme into an unsupported transport by blindly passing the full input to a host-only option.

Verify headers and backend-specific options reach the actual constructed client. A schema accepting an endpoint and headers does not establish that either is used. Use distinguishable fake clients or transport spies and assert the public operation, not only the config object's fields.

<a id="domain-service-wiring-trace-sampling-and-propagation"></a>

<a id="domain-trace-sampling-and-propagation"></a>

#### Trace sampling and propagation

Sampling decides recording/export behavior; propagation carries context across boundaries. OpenTelemetry describes injection and extraction through carriers to preserve causal context. Source: [context propagation](https://opentelemetry.io/docs/concepts/context-propagation/).

Therefore disabling an exporter is not automatically permission to discard inbound/outbound propagation. Determine where server/client setup installs the propagator and whether that is required independently of tracing export. Compose named propagators using real implementations, not a custom object that merely advertises the expected field names. Names with similar labels can represent different wire formats.

A ratio in an inclusive range includes both endpoints. Explicit zero must not be mistaken for omission. Understand whether the chosen sampler is root ratio-based, parent-based, or another policy; test the appropriate root and parent cases rather than claiming that every incoming trace obeys the same decision. Validate out-of-range values at the specified boundary and preserve required public errors.

Keep file, environment, schema, and runtime representations aligned. If a list is ordered, preserve that ordering through decode hooks and composition. A generated schema or example configuration must reflect the actual loader rather than an obsolete field list.

<a id="domain-service-wiring-endpoint-selection-is-a-policy-tree"></a>

<a id="domain-endpoint-selection-is-a-policy-tree"></a>

#### Endpoint selection is a policy tree

Distinguish direct local credentials, a remote-cluster route, and discovered service endpoints. Each branch can choose both an address source and a credential/CA source. Keep those paired; a reachable address with credentials for a different branch can fail or select the wrong identity.

Build a table before coding: requested target, known local material, remote/local state, discovery results, credentials requested, candidates produced, and required error. Check a missing target before requesting credentials if the contract requires that ordering. Preserve discovery order and duplicates according to the public API rather than silently sorting or collapsing them.

During dialing, keep candidate address separate from committed active address. Update active state only when a connection succeeds. Check empty candidates, an early failed candidate followed by success, and all failures. Propagate context cancellation and the documented retry policy; do not turn authentication or certificate errors into an unbounded network retry.

<a id="domain-service-wiring-whole-path-tests"></a>

<a id="domain-whole-path-tests"></a>

#### Whole-path tests

Load public input, construct the provider, perform an observable operation, and execute shutdown. Cover disabled state, each backend, omitted versus explicit selector, malformed endpoint, boundary ratios, propagation with exporting disabled, and the endpoint/credential branch table as applicable. Inspect error type/text at the boundary where it is promised. A factory test and a server wiring test catch different omissions.

<a id="integration-seams"></a>

## Respect the actual dependency and constructor seam

For a manager/client refactor, inspect how callers construct it, how it obtains its client, and which binding actually performs I/O. A fake at the provider definition may not intercept a consumer's imported alias or preconstructed client. Check a real public call with the intended fake and inspect the outgoing arguments/side effects; mocks that replace the whole manager can hide the integration failure. Preserve public constructor signatures and fixture shapes unless the task changes them. A connection refusal during a nominal unit test can indicate a missed injection seam, not unavailable fleet infrastructure.

Verify enum members and SDK symbols against the pinned dependency's source or type declarations. Similar spelling or a member from a newer release is not evidence. A missing property error alone does not prove that an enum member is absent: inspect whether the containing value is undefined too. Type checking helps when declarations are accurate; actual binding checks are still useful at dynamic boundaries.

<a id="runtime-defaults"></a>

## Separate lookup time and canonical defaults

A module-level value may be captured at import, but some libraries expose lazy proxies. Inspect the actual implementation instead of assuming every constant-looking access is static. When configuration must honor construction-time or call-time overrides, use the established lookup at that boundary and test an override after import without reloading the module. Re-import can repeat registration and conceal the real timing contract.

DefaultConfig, normal Load(empty input), schema defaults and serialized output should agree where the public contract says they represent the same defaults. Compare them at that boundary rather than hand-authoring parallel tables. Explicit false, empty, null and absent values may have different meanings; a fixture conflict is not permission to erase a required default.

<a id="go-decode-and-types"></a>

## Go decoding, pinned types and interface migration

For mapstructure or another decoder, check which conversions the installed version already supplies. A hook for a typed slice should be gated on both source and destination types and leave unrelated values unchanged. Determine the environment-string separator from the interface; whitespace, comma and JSON arrays are different formats. Exercise the actual loader with YAML/JSON/environment input, not only the hook in isolation.

<a id="go-typed-slice-hook"></a>

### Worked example: one string field becomes a named-string slice

This complete Go package targets `github.com/mitchellh/mapstructure v1.5.0` and its [type-based hook API](https://pkg.go.dev/github.com/mitchellh/mapstructure@v1.5.0#DecodeHookFuncType). It models an invented configuration contract: `channels` accepts a whitespace-separated string or an ordinary list, while `label` remains one string and `tags` remains a normal string slice. The hook changes only the exact `string → []Channel` pair. `strings.Fields` deliberately collapses whitespace and returns no fields for an empty string; choose a different parser if empty items, quoting or commas have meaning.

```go recipe=typed-slice-decode
package recipe

import (
    "reflect"
    "strings"

    "github.com/mitchellh/mapstructure"
)

type Channel string

type channelConfig struct {
    Channels []Channel `mapstructure:"channels"`
    Label    string    `mapstructure:"label"`
    Tags     []string  `mapstructure:"tags"`
}

func channelListHook() mapstructure.DecodeHookFuncType {
    source := reflect.TypeOf("")
    target := reflect.TypeOf([]Channel{})
    return func(from, to reflect.Type, data interface{}) (interface{}, error) {
        if from != source || to != target {
            return data, nil
        }
        words := strings.Fields(data.(string))
        result := make([]Channel, len(words))
        for i, word := range words {
            result[i] = Channel(word)
        }
        return result, nil
    }
}

func decodeChannels(input map[string]interface{}) (channelConfig, error) {
    var result channelConfig
    decoder, err := mapstructure.NewDecoder(&mapstructure.DecoderConfig{
        Result:  &result,
        TagName: "mapstructure",
        DecodeHook: mapstructure.ComposeDecodeHookFunc(channelListHook()),
    })
    if err != nil {
        return result, err
    }
    err = decoder.Decode(input)
    return result, err
}
```

For `{"channels": "alpha beta", "label": "keep spaces", "tags": []string{"x y"}}`, expect `[]Channel{"alpha", "beta"}`, the unchanged label, and one unsplit tag. Also check an ordinary `[]interface{}{"alpha", "beta"}` list, a named-string slice, empty text, and invalid numeric input. The hook leaves nonmatching types alone; the decoder still decides whether those values are valid. In a real loader, compose with its existing hooks in the established order instead of replacing them. Confirm the actual environment/config path reaches this decoder: a direct call to the example proves neither Viper environment binding nor the repository's loading behavior. Enum validation, if required, belongs at the specified boundary; this illustration does not invent allowed channel names.

### Package-qualified types and caller migration

Go types with similar printed names or fields are not automatically assignable. Inspect package-qualified definitions, aliases, pointer/value forms, method sets, mocks and generated adapters before reusing or introducing a type. For a signature or struct-field change, enumerate all callers including tests and platform files; a new return value needs a meaningful use or intentional discard at each consumer. Compile the affected test packages when possible as well as production code. Go formatting cannot detect assignment arity or interface conformance.

<a id="callers-and-accounting"></a>

## Propagate changed interfaces and accounting

When parameters, return values or result fields change, search production callers, wrappers, mocks, tests, platform files and generated interfaces. A successful production build does not compile all test callers. Update the producer and each affected consumer; for a new result such as usage, distinguish per-operation data from cumulative totals and propagate it only along the paths that own it. Observe empty/short-circuit, success, retry, cancellation and partial-result paths separately.

Check the exact requested type/function names as well as structural compatibility. Prefer the existing result object and interface unless a new abstraction has a concrete role. Record missing compilation coverage honestly if dependencies or target platforms are unavailable. Formatting and grepping are useful observations, but neither demonstrates signature compatibility.
