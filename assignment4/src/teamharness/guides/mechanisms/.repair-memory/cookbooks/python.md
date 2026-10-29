# Python repair cookbook

This is optional third-layer memory. The main skills contain the required repair procedures and domain knowledge. Use a recipe when its trigger matches an unresolved implementation or testing problem. Examples declare narrow policies and are independent of any target repository. Check the project's Python version, existing abstractions, and public contract before adapting them.

## Additional focused recipes

- [P7 — Cache APIs and structural contracts](#p7-cache-and-shapes)
- [P8 — Preserve identity and validate decoded shape](#p8-identity-and-json)
- [P9 — Exact grammar and subprocess text](#p9-grammar-and-text)
- [P10 — Available test scope and chain effects](#p10-test-scope-and-chains)
- [P11 — Import scope and domain punctuation](#p11-imports-and-values)
- [P12 — Qt version types and deferred cleanup](#p12-qt-compatibility)


## Recipe map

| Trigger | Recipe |
|---|---|
| A mock is installed but the real dependency still runs | [P1: binding and patch location](#p1-binding) |
| A JSON parser silently loses duplicate fields or accepts non-finite numbers | [P2: explicit decoding policy](#p2-json) |
| Acquisition fails halfway, or cleanup runs after successful transfer | [P3: rollback ownership](#p3-rollback) |
| A file must become visible as one snapshot rather than a partial write | [P4: staged publication](#p4-publication) |
| A regex accepts unexpected Unicode digits/space or a valid substring | [P5: discriminating grammar checks](#p5-grammar) |
| Cleanup masks the original failure, or a delayed handler loses its exception | [P6: exception lifetime](#p6-exceptions) |

<a id="p1-binding"></a>

## P1 — Diagnose the binding that is actually called

**Use when:** a patched dependency is not observed, a test unexpectedly performs I/O, or a runtime-overridable option appears frozen.

Python's mocking guidance distinguishes defining a value from looking it up: patch the name used by the consumer. A `from provider import operation` statement binds a name in the importing module; later replacing `provider.operation` does not necessarily replace that consumer binding. Source: [where to patch](https://docs.python.org/3/library/unittest.mock.html#where-to-patch).

Inspect the call expression and import, not just the dependency's original definition. There can also be a value captured in a default argument, closure, bound method, singleton, or module-level constant. Find the binding time. Re-importing an entire module to refresh it can repeat registrations or discard unrelated state; prefer the established injection seam.

An independent diagnostic can construct a provider and consumer with harmless functions. Show that changing the provider leaves an eagerly imported alias unchanged, then patch the consumer alias and assert the fake's arguments. If production deliberately promises module-attribute lookup at call time, test that promise directly instead of making a test-only alias patch conceal the defect.

**Stop condition:** the final consumer observes the intended fake/value and the public path behaves correctly. A mock's existence in a namespace is insufficient. Do not rewrite all imports because one test patches the wrong name.

<a id="p2-json"></a>

## P2 — Make duplicate-field and non-finite-number policy explicit

**Use when:** a structured message is syntactically decoded but loses distinctions before validation.

Python's JSON decoder exposes `object_pairs_hook` and `parse_constant`; these allow policy at the decoding boundary. Source: [JSON decoder](https://docs.python.org/3/library/json.html). The invented model below rejects duplicate object fields and non-finite constants. Some applications instead preserve pairs or deliberately choose a winner; do not impose rejection without a contract.

```python recipe=strict-json
import json

def strict_json(text):
    def unique_pairs(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate object field")
            result[key] = value
        return result

    def reject_constant(value):
        raise ValueError("non-finite JSON number")

    return json.loads(text, object_pairs_hook=unique_pairs,
                      parse_constant=reject_constant)
```

This rejects named non-finite constants, not every exponent whose conversion might overflow. Magnitude, nesting, and input-size limits are separate policies. Test duplicates in nested objects as well as the outer object. Valid repeated values in an array are not duplicate object fields. Keep malformed syntax distinct from a policy rejection if callers require that distinction. Preserve original bytes separately when signatures or byte compatibility depend on them; parsing and dumping cannot prove byte preservation.

**Counterexample:** a lossless record adapter that needs repeated fields should keep an ordered pair representation instead of rejecting them. This helper's policy is not that adapter's specification.

<a id="p3-rollback"></a>

## P3 — Acquire several resources and transfer the cleanup capability

**Use when:** a later acquisition or validation can fail, and success must transfer ownership rather than run rollback.

`ExitStack` registers cleanup callbacks and `pop_all()` transfers the registered callbacks into a separate stack. Source: [ExitStack](https://docs.python.org/3/library/contextlib.html#contextlib.ExitStack). This example's factories return resources with `close()`. Validation does not transfer ownership or publish side effects.

```python recipe=resource-transfer
from contextlib import ExitStack

def acquire_pair(open_left, open_right, validate):
    with ExitStack() as rollback:
        left = open_left()
        rollback.callback(left.close)
        right = open_right()
        rollback.callback(right.close)
        validate(left, right)
        ownership = rollback.pop_all()
        return left, right, ownership
```

If the second acquisition fails, the first is closed. If validation fails, both close in reverse registration order. On success the returned ownership stack belongs to the caller, which must close or transfer it. Losing that handle can leak resources even though the local function used a context manager.

For a real publication API, determine whether it can transfer only part of the ownership before raising. This simple model does not handle that automatically. Model dependencies and transfer points before registering callbacks. A callback failure can also affect which error becomes visible; preserve the application's error policy rather than silently suppressing every cleanup failure.

**Checks:** fail at each factory and validation; record release order; assert no release before successful transfer; close the returned owner; then verify repeated close behavior under the stack's semantics.

<a id="p4-publication"></a>

## P4 — Stage a single-file snapshot without claiming a database transaction

**Use when:** readers must not observe a partially written replacement and the contract permits replacing one file.

Use a temporary file on the destination filesystem and close it before replacement. Python documents named temporary-file behavior, including platform differences, and `os.replace` replacement semantics. Sources: [temporary files](https://docs.python.org/3/library/tempfile.html), [OS file operations](https://docs.python.org/3/library/os.html#os.replace).

```python recipe=publish-snapshot
import os
import tempfile
from pathlib import Path

def publish_snapshot(destination, payload):
    destination = Path(destination)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=destination.parent,
                                         delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, destination)
        temporary = None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
```

This model expects a pre-existing parent and byte payload, uses the temporary file's metadata, and does not provide multi-file atomicity or a complete power-loss durability guarantee. `missing_ok` needs a supported Python version; adapt using the existing project's missing-file handling on older versions. Decide permissions, ownership, directory durability, symlinks, and cleanup-error mapping under the actual contract. Do not apply this helper when a valid existing file must remain untouched or no filesystem side effects are permitted.

**Checks:** a normal publication, failure before replacement, failure during replacement, preservation of old content before the commit point, and absence of owned temporary artifacts. A successful read after a normal run does not test failure atomicity.

<a id="p5-grammar"></a>

## P5 — Test the character class and consumption boundary

**Use when:** a parser's regex looks correct but unexpected spellings pass.

Make accepted character sets and whole-input consumption explicit. Source: [Python regular expressions](https://docs.python.org/3/library/re.html). Do not assume broad digit/space classes mean ASCII. The ordinary parsing skill contains the policy model; this small illustration shows an independent testable boundary, not a size/unit parser.

```python recipe=ascii-identifier
import re

def ascii_identifier(text):
    return re.fullmatch(r"[A-Z]{2}[0-9]+", text) is not None
```

Compare an ASCII identifier with a non-ASCII digit spelling, leading/trailing whitespace, an extra suffix, and a newline. Test each difference separately so a failing case identifies the boundary. A production parser may permit some of those forms; derive its exact matrix before tightening validation.

When minimising a surprising input, remove characters while preserving the mismatch between expected and observed behavior. Keep the smallest distinguishing pair as a regression case. This is more informative than adding many random strings that all fail for the same reason.

<a id="p6-exceptions"></a>

## P6 — Preserve the primary failure and capture delayed diagnostics deliberately

**Use when:** an operation appears to succeed after failure, or deferred logging/cleanup sees a missing exception variable.

A `return` in `finally` can replace the pending return or exception. Python also clears an exception target after its handler. Source: [try-statement semantics](https://docs.python.org/3/reference/compound_stmts.html#the-try-statement).

Inspect the full exit path: original exception, cleanup outcome, wrapping, return value, and deferred callback. Keep the primary operation failure and cleanup failure distinct under the established API. Avoid a catch-all that converts a programmer error into an empty successful result.

For delayed work, capture the specific harmless information needed at scheduling time rather than depending on the handler variable later. A default-argument capture retains the object; extracting a message retains less state, but may lose error type/identity. Choose based on the contract and avoid keeping sensitive payloads in long-lived closures.

**Checks:** the original failure remains observable, a cleanup failure follows the specified mapping, successful cleanup does not create a failure, and delayed diagnostics reference the intended operation. Do not add suppression merely to make a cleanup test green.


<a id="p7-cache-and-shapes"></a>

## P7 — Cache APIs and structural contracts

**Use when:** a callable promises cache invalidation or a record must satisfy a structural protocol.

For a pure function with hashable arguments, `functools.lru_cache` already supplies `cache_clear()`; no self-assignment is needed:

```python
from functools import lru_cache
@lru_cache(maxsize=32)
def normalized_pair(left: str, right: str) -> tuple[str, str]:
    return left.casefold(), right.casefold()
normalized_pair.cache_clear()
```

Mutable context/module objects may be unsuitable keys, and cached values may outlive configuration changes. Choose explicit key/lifetime/invalidation semantics rather than introducing a generic dictionary cache over arbitrary arguments. Test the callable's public invalidation operation when it is required.

For dict records, examine real input/output keys and nested shape, not variable names. For a promised dataclass, use `dataclasses.is_dataclass`/`astuple` as appropriate; a slotted class with the same attributes is not automatically a dataclass. Preserve aliases at input boundaries deliberately rather than silently renaming external fields to internal spellings.

<a id="p8-identity-and-json"></a>

## P8 — Preserve identity and validate decoded shape

**Use when:** an unchanged tagged value must remain the same object, or a JSON cache expects a particular container.

`str()` and library conversion helpers do not universally allocate a new object. Identity must be tested rather than inferred from equality or from the function name. If a contract promises unchanged input identity, avoid re-tagging/copying on the no-op branch:

```python
def normalize_with_identity(value, normalize):
    result = normalize(value)
    return value if result == value else result
```

This pattern is valid only if equal values are semantically interchangeable and preserving the original type/tags is required. Explicit conversion to a canonical type may instead require the new result.

JSON can decode to a dict, list, string, number, boolean or None. Before using dict methods on an externally supplied/cache value, validate the expected shape and apply the explicit invalid-cache policy. For example `isinstance(data, dict) and data.get('version') == 2` tests only a versioned mapping—not the whole schema. Include valid wrong-shaped JSON and distinguish invalidation from silently fabricating defaults.

<a id="p9-grammar-and-text"></a>

## P9 — Exact grammar and subprocess text

**Use when:** Unicode acceptance or decoded subprocess output is part of the public contract.

For an illustrative ASCII integer-with-optional-unit grammar, `re.fullmatch(r'[0-9]+(?:[ ]+[A-Za-z]+)?', text)` rejects Arabic-Indic digits, tabs and trailing newlines. Change the grammar to match the actual interface; do not use `.strip()` to make invalid input valid. Python `\d` covers Unicode decimal digits, not all digit-like symbols; `\s` has a defined whitespace set, not every invisible character.

For subprocess output, determine whether it is bytes or already text and select the repository's decoder/error policy explicitly. `to_text`, `to_native` and `.decode(..., errors=...)` are not universally equivalent. Strip whitespace only if it is not meaningful to the output/error contract. Redact secrets after decoding consistently, and keep stdout/stderr and process status distinguishable. A friendly displayed message is not a reason to discard diagnostic bytes needed to explain failure.

<a id="p10-test-scope-and-chains"></a>

## P10 — Available test scope and chain effects

**Use when:** focused tests pass while a refactor changes chain traversal or side effects.

A selected suite establishes only the cases it executed. Use available related tests or a discriminating example when another boundary is at risk; a hidden grader is not an interactive tool unless actually exposed. Preserve a test command's exit status and full output before viewing head/tail excerpts. A broad suite failure can be unrelated, environment-limited or caused by the patch; compare the unchanged baseline only if you actually restore/run that baseline in a separate workspace.

For redirects, linked lists or recursive traversals, decide which state and effects accumulate across the chain. Returning the child's result can lose a required deletion or record for the parent. Retain original and resolved identities when both have observable roles. Test a missing intermediate node, a redirect chain, a cycle and an ordinary terminal node; choose the contract's cycle policy. Do not always append everything: deduplication, ordering and side effects are domain choices.

<a id="p11-imports-and-values"></a>

## P11 — Import scope and domain punctuation

**Use when:** a branch-local import shadows a name, or normalization removes meaningful punctuation.

An import binds a name locally throughout a function if the function contains that binding. A branch can leave it unbound on another path:

```python
import json

def serialize(value):
    # Both success and error paths use the same binding.
    return json.dumps(value)
```

If a lazy import is necessary, keep every use within a path that establishes it. Exercise both sides of the relevant branch.

Bracketed abbreviations can be domain values rather than surrounding syntax. Do not indiscriminately strip brackets/periods with a broad `.strip('[] .')`. Define the normalization rule using representative real values and preserve known lexical tokens, punctuation and script distinctions where required. Fixtures should include similar-looking values with different meanings so a shared normalization assumption cannot hide data loss.

<a id="p12-qt-compatibility"></a>

## P12 — Qt version types and deferred cleanup

**Use when:** one supported binding/version lacks a runtime type used in annotations, or native-object destruction is observable.

Do not alias an unavailable Qt type to `int` or `None` merely to make annotations import: that changes runtime/type semantics and can make feature checks lie. Prefer the project's compatibility wrapper, guarded imports and a separate capability flag. `TYPE_CHECKING`/postponed annotations can avoid importing a type only needed statically, but code evaluating annotations later still needs resolvable names. Test import and the selected runtime branch for each supported version when possible.

For owned QObject cleanup, stop or await owned work according to the contract, arrange deletion through the parent or `deleteLater`, and allow deferred events to be processed. Observe destruction with the binding's supported check or signal, not merely removal from a Python collection. An illustrative lifecycle is timer-stop -> work-finished -> schedule owned-object deletion; process cancellation is a separate policy decision. Do not promise immediate deletion or rely on an arbitrary sleep; wait on the relevant event with a bounded test timeout.
