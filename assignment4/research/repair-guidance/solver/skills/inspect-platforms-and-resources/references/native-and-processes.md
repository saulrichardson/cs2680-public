# Native interfaces and process outcomes

These are conditional technical examples. Apply the installed library, public contract, and actual environment to choose the relevant behavior. A worked case illustrates a mechanism; it is not evidence about the repository being repaired.

<a id="domain-process-and-qt"></a>

<a id="domain-process-native-protocol-and-qt-compatibility-boundaries"></a>

### Process, native protocol, and Qt compatibility boundaries

Use when behavior varies by platform/dependency, process outcomes are misreported, or typed Qt APIs replace integer conventions. Facts from the linked APIs are followed by authored repair reasoning; use the versions pinned by the checkout.

<a id="domain-process-and-qt-separate-process-outcomes"></a>

<a id="domain-separate-process-outcomes"></a>

#### Separate process outcomes

Model spawn failure, running, ordinary exit, signal/crash, timeout, and explicit cancellation. Exit code and exit mechanism are separate. Preserve outcome data before cleanup destroys the process object. Presentation policy—verbosity, warning/error level, and whether success is silent—belongs in an explicit outcome-by-mode table. Do not turn every nonzero code into the same user message or treat a crash as an ordinary successful exit because a stale code field is zero.

Track PID, pipes, callbacks, timers, temporary credentials, and lifecycle metadata with separate owners. An object that is safe to release may still have a required registry tombstone. Test commands, status pages, completion providers, and lookups that consume that state, not only the exit callback.

<a id="domain-process-and-qt-python-subprocess-communication"></a>

<a id="domain-python-subprocess-communication"></a>

#### Python subprocess communication

Python provides pipe-aware communication and distinct process-creation/timeout errors. Waiting while undrained pipes fill can deadlock. Multiplexing support differs by platform; the high-level selectors interface does not make Windows pipes behave like Unix descriptors. Sources: [subprocess](https://docs.python.org/3/library/subprocess.html), [selectors](https://docs.python.org/3/library/selectors.html).

For a bounded one-shot exchange, understand whether the current API can own input delivery, output collection, and waiting as one operation. For streaming interaction, choose the repository's platform-capable I/O mechanism. A workaround based on `select` may fail for high descriptor numbers or unsupported file kinds. Installing an optional library is not a repair when the contract requires consistent behavior without it.

Separate argv tokenization from shell execution. A shell-like option string can be parsed into argument tokens without launching a shell. Preserve executable selection, argument ordering, and any caller-required invocation seam. Environment inheritance is a policy: sometimes only a narrow environment is permitted, elsewhere preserving inherited variables is required. Do not choose one universally.

For password or token input, use harmless synthetic values in tests. Redact the actual secret from stderr before composing public diagnostics, while preserving required decoding/whitespace/error behavior. Avoid treating a failed spawn as a child exit; it has no ordinary child status. Clean temporary resources along every ownership path.

<a id="domain-process-and-qt-qt-object-and-event-loop-lifetimes"></a>

<a id="domain-qt-object-and-event-loop-lifetimes"></a>

#### Qt object and event-loop lifetimes

QObject parent ownership and deferred deletion are different mechanisms. `deleteLater()` schedules destruction through event-loop/lifecycle behavior; calling it does not prove immediate destruction. Qt documents special cases for stopped or absent event loops. Source: [QObject](https://doc.qt.io/qt-6/qobject.html), with [object ownership](https://doc.qt.io/qt-6/objecttrees.html).

Associate callbacks and timers with the correct owner/context. Inspect whether releasing a registry entry actually releases parented objects, whether a live timer can recreate work, and whether a callback dereferences an object after its lifetime ends. Do not add a second owner or unconditional destructor merely because a raw pointer exists.

Qt's QProcess distinguishes process error signals, exit status, and exit code; its documentation defines when outcome values are meaningful. Source: [QProcess](https://doc.qt.io/qt-6/qprocess.html).

Use those separate axes to construct the reporting table. Preserve the outcome to downstream consumers before scheduling cleanup. Exercise failed start, nonzero ordinary exit, crash/signal, successful exit, and configured verbosity as relevant. Timer-driven cleanup should act on the original process identity, not whichever process later reused a display slot.

<a id="domain-process-and-qt-typed-keys-across-qt-versions"></a>

<a id="domain-typed-keys-across-qt-versions"></a>

#### Typed keys across Qt versions

Qt 6 introduces QKeyCombination, representing a key and modifiers, with `key()`, `keyboardModifiers()`, `toCombined()` and `fromCombined()`. It is not simply interchangeable with every integer operation. Source: [QKeyCombination](https://doc.qt.io/qt-6/qkeycombination.html).

Choose a semantic internal representation and adapt at version boundaries. Inventory key parsing, modifier changes, equality, hashing, iteration, serialization, and event conversion. Replacing one cast can leave arithmetic/bitwise assumptions elsewhere. Preserve explicit default modifiers and ensure comparisons operate on equivalent semantic types. Use supported conversions instead of reaching into undocumented bit layouts.

Construct semantic round trips: parse a key/modifier combination, inspect its parts, serialize, read it again, and compare meaning. Test a modifier-free key and multiple modifiers. Gate version-specific APIs at the boundary; do not scatter inconsistent conversions through every caller or upgrade Qt just to make one API available.

<a id="domain-process-and-qt-native-binary-protocol-adapters"></a>

<a id="domain-native-binary-protocol-adapters"></a>

#### Native binary protocol adapters

For a native status/event interface, separate portable event contracts from platform transport. Fixed binary layouts need fixed-width fields and the protocol's byte order. Kernel/library-owned length, sequence, and process fields should not be invented by an application. The number and ordering of replies, acknowledgements, and events follows the actual transport API; waiting for an extra acknowledgement that will never arrive can hang an otherwise valid exchange.

Source for Netlink exchange distinctions: [Linux Netlink introduction](https://www.kernel.org/doc/html/latest/userspace-api/netlink/intro.html); classic and generic families can differ, so match the actual family/library contract.

Put transport behind the existing injectable interface. Test status-disabled, connect failure, malformed status, send failure, and success with recorded messages. Observe whether a disabled subsystem emits zero events and whether enabled operation emits the required number. Keep unsupported-platform behavior explicit and run available compile checks for alternate files. Never replace the real dependency with a locally invented lookalike merely to make tests compile.

<a id="qt-lifecycle-and-signals"></a>

## Observe native resource lifetime and exit provenance

Qt timers, child processes, QObject ownership, Python references and deferred deletion are different lifetimes. `deleteLater()` schedules deletion when Qt processes the relevant deferred event; it does not guarantee destruction at the next arbitrary event-loop iteration. Stop or await owned work according to the lifecycle contract, then schedule destruction when safe. Do not blindly kill processes or delete parent-owned objects merely because a collection removed a reference. `del` only changes a Python reference; binding ownership rules determine whether that also destroys a native object.

A focused check observes both the required stop/finished condition and actual object destruction while allowing the appropriate event loop to progress. A parent can own deletion; duplicate cleanup should not access an already deleted object. Test required idempotence and failure-before-start separately.

For exit reporting, retain QProcess error, exit status and exit code separately. A normal exit code is not a signal number. Only use `signal.Signals(code).name` when the platform/API contract actually establishes that code is a signal; catch unknown values. Qt's cross-platform crash status does not itself guarantee Unix signal-number encoding. Preserve raw values and a neutral fallback when provenance is unclear.
