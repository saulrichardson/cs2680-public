# Native and platform repair cookbook

This optional memory layer contains specialised lifecycle, representation, and testing techniques. Required process, inventory, ownership, and Qt knowledge lives in the platforms skill. The recipes do not prescribe target code locations or platform constants. Match the actual protocol, toolkit version, build target, and operation contract.

## Recipe map

| Trigger | Recipe |
|---|---|
| A callback can outlive a QObject or a raw pointer becomes stale | [N1: guarded observation and connection lifetime](#n1-qpointer) |
| Key sequences work locally but serialize differently across platforms | [N2: portable text versus native display](#n2-key-text) |
| A binary status looks plausible on one host and wrong/truncated elsewhere | [N3: explicit layout and byte order](#n3-binary) |
| Cleanup loses exit data or a reused identifier is affected by an old timer | [N4: immutable outcome and operation generation](#n4-outcomes) |
| Symlink aliases or source labels change precedence unexpectedly | [N5: test identity without a live host inventory](#n5-source-identity) |

<a id="n1-qpointer"></a>

## N1 — A guarded pointer observes destruction; it does not own or lock the object

**Use when:** an observer stores a QObject pointer whose lifetime belongs elsewhere.

QPointer is a guarded QObject pointer that becomes null when its object is destroyed. It is not an ownership mechanism. Source: [QPointer](https://doc.qt.io/qt-6/qpointer.html).

Identify the real owner and the thread/event loop in which the callback may run. Where supported, a context-bound signal/functor connection can tie callback delivery to a QObject lifecycle; inspect the exact overload in the checkout's Qt version. A null check is not a general cross-thread synchronization strategy. Another thread changing lifetime between observation and use requires the application's actual ownership/threading model.

Treat parent destruction, `deleteLater()`, signal disconnection, queued delivery, and guarded-pointer clearing as different transitions. A test that only drops a registry reference may not destroy a parented object. A test that calls deferred deletion but never processes the relevant lifecycle may not observe destruction. Use a destruction signal or guarded pointer in the correct event-loop harness to establish what actually happened.

**Counterexample:** replacing an owning lifetime with only a guarded pointer can turn a leak into premature disappearance. Use the guard for observation, not as a substitute for deciding who releases the resource.

**Validation boundary:** this is version-scoped API guidance. It needs a Qt-native test/build to establish the application's behavior; Python/Node model checks do not verify it.

<a id="n2-key-text"></a>

## N2 — Store a portable representation and render native display separately

**Use when:** parsed key sequences compare correctly on one platform but round trips or user-visible text differ elsewhere.

QKeySequence distinguishes `NativeText` and `PortableText` formats. Source: [QKeySequence](https://doc.qt.io/qt-6/qkeysequence.html). Decide which representation is a persistence/protocol value and which is presentation.

Use the same declared format for parsing and serializing stored text. A native display string is not necessarily a portable configuration representation. Keep modifier/key semantics separate from an integer encoding, and apply Qt-version conversion at a narrow boundary as described in the main skill.

Construct a test matrix for a modifier-free key, several modifiers, a multi-key sequence where supported, locale/platform display differences, and unknown input. Compare semantic sequences as well as exact text where the contract promises a canonical spelling. Do not rewrite user-visible native labels to portable text unless requested.

A round trip can be self-consistently wrong if both parser and serializer choose the wrong format. Keep at least one independently defined configuration fixture and compile applicable version adapters. These recipes do not claim that a Qt version installed elsewhere matches the target.

<a id="n3-binary"></a>

## N3 — Verify layout, width, and framing independently

**Use when:** a status decoder depends on host ABI, reads the wrong message, or mistakes truncated bytes for disabled/default state.

Python's struct formats distinguish native byte order/alignment from explicit standard layouts. Source: [struct layout](https://docs.python.org/3/library/struct.html). A C structure's layout, a Go binary-serialization sequence, and a protocol's wire layout are not automatically identical.

The independent model below reads one unsigned 32-bit prefix and returns the remaining bytes as payload. It deliberately does not implement a kernel or application protocol. The caller chooses byte order, and a complete protocol decoder must additionally enforce its own framing and payload rules.

```python recipe=binary-prefix
import struct

def binary_prefix(data, order):
    if order not in {"<", ">", "="}:
        raise ValueError("choose explicit byte-order policy")
    word = struct.Struct(order + "I")
    if len(data) < word.size:
        raise ValueError("truncated prefix")
    return word.unpack_from(data)[0], data[word.size:]
```

Use independently constructed bytes for little-endian and big-endian cases, truncated data, and extra payload. Check field widths and offsets against the protocol, not a matching encoder that may share the same wrong assumptions. Do not use `@` as shorthand for a packed wire layout; native padding is a separate choice.

For message exchanges, identify who owns header length/sequence/process fields and whether a library already consumes acknowledgements. One receive can hold several messages; a multipart dump and a one-action reply can end differently. Inspect the actual API contract before filtering a response type or waiting for another acknowledgement. An unexpected empty reply is not proof that a subsystem is disabled.

<a id="n4-outcomes"></a>

## N4 — Capture outcome values before releasing native objects

**Use when:** cleanup destroys data needed by status/reporting consumers, or an old callback acts on a new process using a recycled identifier.

Record an immutable outcome at the established completion boundary: operation identity/generation, spawn-versus-exit mechanism, meaningful exit code/status, required diagnostic text, and timestamp if part of the API. Keep this distinct from the owning process object and from a displayed PID/name.

Then apply the public retention policy. Live pipes, timers, and child objects can be released while a tombstone/outcome record remains readable. A stale timer must compare the original operation identity before cleaning a registry slot later reused by another operation. Numeric PID equality alone may be insufficient for long-lived bookkeeping.

Test a new operation under a reused logical identifier, then deliver the old operation's timer/completion. Inspect both the surviving new operation and the retained old outcome under the contract. Also test failed start, nonzero normal exit, crash/signal, timeout, and successful completion independently of verbosity. A wrapper that reports success while retaining a failed native status is inconsistent even if the message text looks right.

Do not add a permanent event log merely because this model names an immutable outcome. Reuse the application's actual record and retention requirements.

<a id="n5-source-identity"></a>

## N5 — Reproduce source-alias behavior in an isolated filesystem

**Use when:** source precedence, deduplication, or provenance changes when two paths identify the same physical object.

Create a temporary directory with a real fixture file and, where supported, an alias/symlink to it. Feed both logical labels through the production source-selection path. Assert read count, chosen precedence, reported source label, and original source text separately. A canonical path can be appropriate for read-once identity while the supplied path remains the required label.

Add two distinct source files that report the same resource. That is record-level duplication, not source duplication. Test the requested first-wins/aggregate/merge policy and warning ownership independently. An enrichment helper returning no optional metadata should not erase a valid record unless required.

Keep platform prerequisites explicit. Symlink creation can be unavailable or restricted; an unsupported test is not a pass. Do not modify real system mounts, accounts, processes, or package state just to exercise parsing. Substitute collectors and fixed fixtures, then use the real parser/normalizer/consumer path.
