# Go repair cookbook

This optional memory layer supplies focused implementation/debugging recipes. The main skills already contain the required mechanisms, schemas, identity rules, and verification. Match the module/toolchain versions and the current public contract. Examples use standard-library facilities and invented data; they are not target-project implementations.

## Additional focused recipes

- [G7 — Compare loader defaults and schema behavior](#g7-defaults-and-schema)
- [G8 — Pinned dependencies and clean replay](#g8-dependencies)
- [G9 — Parse the right version and use named native constants](#g9-release-and-kernel)
- [G10 — Shared state and sentinel identity](#g10-races-and-errors)
- [G11 — Key decoding and nullable fields](#g11-key-and-nullable)
  - [PEM/PKCS#8 to a checked crypto.Signer](#g11-pkcs8-signer)


## Recipe map

| Trigger | Recipe |
|---|---|
| Omitted, null, false, and stale decoded fields collapse together | [G1: inspect presence before decoding](#g1-presence) |
| Wrapping changes an exact error contract or breaks sentinel checks | [G2: error text versus identity](#g2-errors) |
| An error interface looks nil internally but is not equal to nil | [G3: typed nil](#g3-typed-nil) |
| Derived work ignores cancellation, deadlines, or request values | [G4: derive from the caller](#g4-context) |
| One platform compiles while another implementation is missing | [G5: compile the relevant surface](#g5-builds) |
| Deferred rollback sees the wrong error | [G6: argument capture and shadowing](#g6-defer) |

<a id="g1-presence"></a>

## G1 — Inspect field presence before choosing a scalar representation

**Use when:** the public contract distinguishes omission, explicit null, and a zero/false value, or a reused destination retains a previous request's data.

`json.RawMessage` permits delayed decoding. Map membership can preserve presence before decoding a field's type. Go's decoder also has rules for reused maps/structs and untyped numbers; do not assume a fresh zeroed destination on every call. Source: [encoding/json](https://pkg.go.dev/encoding/json).

This narrow model classifies one optional boolean while rejecting wrong-typed values. It is not a proposal that every API should accept null.

```go recipe=json-presence
package main

import (
    "bytes"
    "encoding/json"
    "fmt"
)

func classifyFlag(data []byte) (string, error) {
    var fields map[string]json.RawMessage
    if err := json.Unmarshal(data, &fields); err != nil { return "", err }
    raw, present := fields["enabled"]
    if !present { return "omitted", nil }
    if bytes.Equal(bytes.TrimSpace(raw), []byte("null")) { return "null", nil }
    var flag bool
    if err := json.Unmarshal(raw, &flag); err != nil { return "", err }
    return fmt.Sprintf("value:%t", flag), nil
}

func main() {
    cases := []struct{ input, want string }{
        {`{}`, "omitted"}, {`{"enabled":null}`, "null"},
        {`{"enabled":false}`, "value:false"}, {`{"enabled":true}`, "value:true"},
    }
    for _, c := range cases {
        got, err := classifyFlag([]byte(c.input))
        if err != nil || got != c.want { panic("presence policy failed") }
    }
    if _, err := classifyFlag([]byte(`{"enabled":"false"}`)); err == nil {
        panic("wrong type accepted")
    }
    fmt.Println("json-presence: ok")
}
```

Check the outer object's shape separately when required. Duplicate keys, unknown fields, trailing documents, and numeric precision are additional policies; this example does not validate them all. A pointer can preserve some distinctions but cannot by itself recover every distinction already discarded by decoding.

If a sequence of requests decodes into one existing struct/map, test a populated request followed by an omitted field. Prefer a fresh destination or an explicit reset when that is the production contract. A single fresh-instance test will miss stale state. Likewise, use typed values or the appropriate decoder options when arbitrary numeric identifiers must not pass through lossy untyped floating-point conversion.

<a id="g2-errors"></a>

## G2 — Test error text and machine identity independently

**Use when:** a helpful prefix changes a required public error, or a caller's sentinel check stops matching.

Go supports matching wrapped errors with `errors.Is` and extracting compatible error types with `errors.As`. Wrapping and retaining the original object are distinct. Source: [errors package](https://pkg.go.dev/errors).

```go recipe=error-contract
package main

import (
    "errors"
    "fmt"
)

func main() {
    original := errors.New("sample failure")
    wrapped := fmt.Errorf("read operation: %w", original)
    if wrapped == original { panic("wrapper unexpectedly identical") }
    if !errors.Is(wrapped, original) { panic("identity chain lost") }
    if wrapped.Error() == original.Error() { panic("text unexpectedly unchanged") }
    rendered := fmt.Errorf("read operation: %v", original)
    if errors.Is(rendered, original) { panic("formatting unexpectedly wrapped") }
    fmt.Println("error-contract: ok")
}
```

Choose the public rule first. A contract can require unchanged error identity/text; return the original there. Another boundary can require context and an unwrap chain. Do not add or remove wrapping universally. An `errors.Is` assertion cannot establish byte-exact `Error()` compatibility, and a text assertion cannot establish typed/sentinel behavior.

<a id="g3-typed-nil"></a>

## G3 — Inspect both dynamic type and value at an interface boundary

**Use when:** an operation returns an error that appears to contain a nil pointer, but the caller takes the failure branch.

An interface with a dynamic type and nil dynamic pointer is different from a nil interface. The official Go FAQ explains this common error-return pitfall. Source: [nil errors](https://go.dev/doc/faq#nil_error).

```go recipe=typed-nil
package main

import "fmt"

type sampleError struct{}
func (*sampleError) Error() string { return "sample" }

func wrongSuccess() error {
    var problem *sampleError
    return problem
}

func explicitSuccess() error { return nil }

func main() {
    if wrongSuccess() == nil { panic("typed nil collapsed unexpectedly") }
    if explicitSuccess() != nil { panic("success returned an error") }
    fmt.Println("typed-nil: ok")
}
```

Inspect the function that converts the concrete pointer into an interface. Fixing a later consumer with reflection often hides the broken return contract. A typed nil can be intentional data in another API; do not rewrite every interface value globally.

<a id="g4-context"></a>

## G4 — Derive work from the caller and release owned cancellation

**Use when:** a wrapper loses request values/cancellation or work outlives its owner.

Contexts carry cancellation, deadlines, and scoped values through derived work. Cancel functions release resources associated with derived contexts and should be called by the owner. Source: [context package](https://pkg.go.dev/context).

```go recipe=context-lineage
package main

import (
    "context"
    "fmt"
)

type requestKey struct{}

func main() {
    parent := context.WithValue(context.Background(), requestKey{}, "marker")
    child, cancel := context.WithCancel(parent)
    if child.Value(requestKey{}) != "marker" { panic("request value lost") }
    cancel()
    <-child.Done()
    if child.Err() != context.Canceled { panic("cancellation lost") }
    fmt.Println("context-lineage: ok")
}
```

The example uses no timer or external service. Test the actual wrapper similarly: observe which context reaches the downstream interface, cancel upstream, and confirm downstream settlement. Creating a background context inside a request wrapper can discard lineage. Conversely, continuing deliberately detached work requires an explicit product/lifecycle policy; do not preserve cancellation if the actual operation is intentionally owned elsewhere.

<a id="g5-builds"></a>

## G5 — Compile implementations that the host build did not select

**Use when:** a changed interface passes local tests but fails on another operating system, build tag, generated adapter, or optional backend.

Inventory implementations and build constraints before choosing checks. Targeted tests exercise the selected host implementation. Cross-compilation can check a different surface, but trying to run that binary on the host is a separate operation. The Go command distinguishes building, testing, and generating a test binary. Source: [Go command](https://pkg.go.dev/cmd/go).

Use the repository's supported targets. A compile-only check can reveal a missing method without proving runtime/native behavior. Cgo and platform libraries may require a real target toolchain; report an unavailable check instead of fabricating stubs. Keep generated interface source and generated outputs consistent through the project's generator. Do not use a dependency replacement to make a made-up SDK signature appear valid.

**Checks:** ordinary concrete calls, interface assignment, mocks/adapters, and each applicable platform file. Preserve a concise list of executed versus unavailable checks. A successful build with one disabled feature does not establish the enabled branch.

<a id="g6-defer"></a>

## G6 — Distinguish deferred argument evaluation from closure observation

**Use when:** rollback is skipped or executed based on a stale error value.

Deferred call arguments are evaluated when the defer statement runs; the call executes later. A deferred closure can instead observe variables at execution time. Source: [Go defer semantics](https://go.dev/ref/spec#Defer_statements).

Inspect whether `defer cleanup(err)` captured an initial nil value, whether a closure reads the intended final value, and whether an inner `err :=` shadows the named result. Do not mechanically convert all defers to closures: capturing a resource identity immediately can be exactly what is needed, while consulting a later mutated variable can close the wrong resource.

Write a lifecycle trace with acquisition, registration, reassignment, return, and cleanup. Test success, failure after acquisition, failure in an inner scope, and resource replacement. Preserve the existing primary/cleanup error policy and ownership transfer; syntax that runs later is not proof that it acts on the right object or error.


<a id="g7-defaults-and-schema"></a>

## G7 — Compare loader defaults and schema behavior

**Use when:** adding configuration defaults or CUE validation.

Compare empty-input loading, the canonical default constructor and serialized output where they promise the same values. An explicit true/default value can conflict with an old zero-value fixture; reproduce the exact discrepancy and consult the written contract before changing implementation or expectation. Do not automatically treat the fixture as stronger evidence than an explicit requirement.

Check named CUE lookup paths, field presence versus null, accepted empty defaults, and the actual decoded struct/map. CUE error text can depend on the pinned version, constraint expression and error rendering API; inspect exact output rather than claiming two equivalent constraints have identical diagnostics. Preserve an original validator error when the interface requires its text, and avoid wrapping it with an extra prefix. Place fixtures relative to the actual package working directory. A schema-only test and a full loader/schema test establish different things.

<a id="g8-dependencies"></a>

## G8 — Pinned dependencies and clean replay

**Use when:** adding a module or using an API that may be absent from the pinned version.

Inspect `go.mod`, `go.sum`, `go.work` and the selected module cache/source. A named API in the latest documentation may not exist in the installed release. A successful workspace/cache build does not prove a fresh module build can resolve the same dependency. Use `go list -m`, package documentation/source and an affected-package compile to establish the selected version and available symbols.

Network failure is different from API incompatibility. Do not fabricate checksums, add an unreviewed replacement, or assume the grader can download what this environment cannot. Commit legitimate `go.sum` changes produced by dependency tooling unless the task explicitly forbids/replaces that requirement; neither "always edit go.sum" nor "never edit go.sum" is universal. Retain unresolved dependency errors in the final account. Prefer an already supported dependency when it fulfills the contract, but do not silently change required semantics to avoid a missing module.

Mocks also need the pinned interface: compare package-qualified types, pointer/value returns and variadic options. A mock failure is not automatically production failure, and a mock tailored to the new implementation can conceal a caller mismatch. Compile affected tests as well as ordinary source when feasible.

<a id="g9-release-and-kernel"></a>

## G9 — Parse the right version and use named native constants

**Use when:** release identifiers or native protocol families determine behavior.

Identify which version is needed: product release, kernel release, package version and ABI are separate quantities. `strconv.Atoi` only accepts an integer string. A documented leading-major grammar may use a bounded scan/regex followed by Atoi, but extracting the first digits from a kernel release does not reveal the operating-system release. Test each actual family/format and include unparseable input with an explicit fallback/error policy.

For netlink and other OS protocols, prefer named constants from the pinned platform package or authoritative installed headers. Numeric values that look plausible are not interchangeable across families; do not copy a guessed table. Inspect the target platform's definitions and compile the selected platform file. Keep unsupported-platform behavior explicit; a working host-platform build does not establish cross-platform availability.

<a id="g10-races-and-errors"></a>

## G10 — Shared state and sentinel identity

**Use when:** a stream goroutine writes data that another path reads, or an error contract requires stable identity.

Builders, buffers, maps and structs are not made safe merely by returning them through a channel elsewhere. Establish ownership of every read/write and the lifetime of any lock. A `strings.Builder.String()` snapshot can race with concurrent writes. Either keep access in one owner goroutine, transfer immutable snapshots, or synchronize the complete access according to the design. A focused `go test -race` with controlled overlapping operations can expose a defect that `go build` cannot; a timed-out race test is not a pass.

For a sentinel, one package-level value is usually enough:

```go
var ErrUnavailable = errors.New("unavailable")
// Return ErrUnavailable or wrap it with fmt.Errorf("context: %w", ErrUnavailable)
// when the public error-text contract permits that wrapper.
```

`fmt.Errorf` assigned once at package scope can also create a stable sentinel; the issue is creating a new value on each call, not the constructor's spelling. Do not implement `Is` by equating every error of one private type unless that equivalence is intended. Check exact text separately from `errors.Is`/`errors.As` behavior.

<a id="g11-key-and-nullable"></a>

## G11 — Key decoding and nullable fields

**Use when:** parsing PEM/PKCS material or changing SQL zero values to nullable values.

`pem.Decode` can return a nil block; inspect block type and handle the remainder according to the input contract. `x509.ParsePKCS8PrivateKey` returns an interface value: assert the required capability such as `crypto.Signer` and report unsupported key types. Choose PKCS#1 or other parsers only when the encoded format and promised key family justify them. Trusted recorded metadata can avoid redundant parsing, but validate its provenance.

<a id="g11-pkcs8-signer"></a>

### Worked example: one unencrypted PKCS#8 signing key

This standalone package deliberately accepts exactly one unencrypted `PRIVATE KEY` PEM block, with optional outer whitespace and no headers or trailing material. Those are illustrative input-policy choices, not universal PEM requirements. [pem.Decode](https://pkg.go.dev/encoding/pem#Decode) can skip leading non-PEM data, so checking only its returned remainder would not establish this strict contract. [ParsePKCS8PrivateKey](https://pkg.go.dev/crypto/x509#ParsePKCS8PrivateKey) parses a key; the capability assertion separately establishes that it can sign. Some valid private keys are for key agreement instead. Error text/wrapping must follow the receiving application's contract.

```go recipe=pkcs8-signer
package recipe

import (
    "bytes"
    "crypto"
    "crypto/x509"
    "encoding/pem"
    "fmt"
)

func parsePKCS8Signer(input []byte) (crypto.Signer, error) {
    input = bytes.TrimSpace(input)
    if !bytes.HasPrefix(input, []byte("-----BEGIN PRIVATE KEY-----")) {
        return nil, fmt.Errorf("expected a PKCS#8 PRIVATE KEY PEM block")
    }
    if bytes.Count(input, []byte("-----BEGIN ")) != 1 {
        return nil, fmt.Errorf("expected exactly one PEM block")
    }
    block, rest := pem.Decode(input)
    if block == nil {
        return nil, fmt.Errorf("invalid PEM encoding")
    }
    if block.Type != "PRIVATE KEY" || len(block.Headers) != 0 {
        return nil, fmt.Errorf("unsupported PEM type or headers")
    }
    if len(bytes.TrimSpace(rest)) != 0 {
        return nil, fmt.Errorf("unexpected trailing PEM data")
    }
    key, err := x509.ParsePKCS8PrivateKey(block.Bytes)
    if err != nil {
        return nil, fmt.Errorf("parse PKCS#8: %w", err)
    }
    signer, ok := key.(crypto.Signer)
    if !ok {
        return nil, fmt.Errorf("PKCS#8 key cannot sign")
    }
    return signer, nil
}
```

The one-BEGIN-marker check prevents `pem.Decode` from skipping a malformed first block and accepting a later one; hyphens are outside the base64 alphabet, and this example rejects PEM headers. Exercise this with generated Ed25519, ECDSA and RSA keys, then sign and independently verify a message through the returned object. Reject absent/malformed PEM, malformed DER, a wrong PEM label, unsupported headers, a second block, leading/trailing junk, and a valid PKCS#8 X25519 key that does not implement `crypto.Signer`. Outer whitespace should remain accepted. Adapt the parser when the actual interface permits other formats or bundles; do not silently drop trailing certificates or treat an encrypted key as ordinary PKCS#8. Never print private bytes in diagnostics. This example uses standard-library APIs and does not implement application-specific key discovery, trust or storage.

### Carry nullable values through consumers

For SQL nullability, choose a pointer or `sql.Null*` representation that fits neighboring code. Migrate scan destinations, struct literals, accessors, copies, serialization and comparisons. Test NULL, present zero and present nonzero through storage and the public consumer; do not let a new pointer's address replace semantic equality. For a pointer field example, nil and `&zero` must remain distinguishable when the contract says so.
