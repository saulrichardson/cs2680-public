# Working in Go repositories

Go makes many interface mistakes visible at compilation, while behavior still
depends on the values, state and callers that reach the compiled code. Use both
kinds of evidence. This guide develops Go-specific ways to investigate a repair;
the main owns its engineering decisions, while the public task and runtime receipts define requirements and grading facts.
The examples are tools for a question, not a sequence to repeat after each grade.

## Find the package and execution conditions

Read `go.mod`, relevant build configuration and nearby tests. Module versions,
build tags, cgo, generated source and workspace or vendor mode determine what
will compile. Tests in the same package can use unexported members; external test
packages exercise an exported surface. Neither arrangement tells you which hidden
assertions exist. Existing callers show compatibility to preserve unless the task
changes that interface.

Use the actual package path and retain complete command output. For example:

```sh
go env GOMOD GOWORK GOFLAGS CGO_ENABLED
go test -count=1 -v ./internal/store > /tmp/store-test.log 2>&1
```

Read the command's completion state and the saved log. `-count=1` requests a fresh
test execution; a cached result is a result reused under Go's cache rules, not
proof that arbitrary changed source was ignored. A fresh run is useful when
external state or the experiment's execution matters. The build cache can still
save compilation work.

Go test binaries normally run with the package directory as their working
directory. If a test in `internal/widget` opens `fixtures/sample.yaml`, the file
must be available at `internal/widget/fixtures/sample.yaml`; a repository-root
`fixtures/` directory is a different path. Inspect the real file consumer and
existing layout, and run the actual package test. A replacement local test that
searches parent directories can hide a missing package-relative fixture. Keep
required data in the captured candidate and verify its location after replay.

`go build` checks production packages. `go test -run '^$' ./internal/store`
compiles the selected test package without selecting ordinary tests; package
initialization and a custom `TestMain` can still run. It is a useful build probe,
not a behavioral pass. When selecting subtests with `-run`, inspect verbose output
to confirm the intended cases ran. Suite frameworks may select cases through
separate options; inspect the installed version and existing invocation rather
than assuming a Go test-name filter selects an inner spec.

A long cold compile may be doing useful work. Inspect the owned job and output,
and continue independent investigation if possible. Quiet output alone does not
establish a hang. Equally, waiting longer does not fix a completed compiler error.
Read whether the operation is running, completed, cancelled or failed before
choosing another command.

## Resolve dependencies under the conditions that matter

When compilation stops at dependency resolution, start with the exact diagnostic
and the artifact it requests. An extracted package directory, module metadata and
checksum records serve different purposes. For example, a request for
`example.org/library/@v/v1.2.3.mod` concerns the module's dependency metadata;
finding its Go source locally does not establish that resolution can complete.
Likewise, a checksum entry for a module's content and one ending in `/go.mod`
authenticate different material. A missing-checksum message is not evidence that
the package source is absent, and adding a checksum cannot supply missing source
or metadata. An undefined method after dependencies resolve is a different problem:
inspect the selected version's actual API.

Use the installed environment to distinguish these explanations. Alongside the
package's complete build output, useful facts include:

```sh
go env GOMOD GOWORK GOFLAGS GOMODCACHE GOPROXY GOSUMDB
```

Read the selected module and workspace files, including replacements, and inspect
the relevant version in the reported cache or prepared vendor tree. Cached source
can answer API questions through direct inspection or `go doc`; those answers
must come from the version the build actually selects. Commands that resolve the
module graph can themselves need unavailable metadata, so their failure is an
observation to interpret rather than a reason to assume the cache is empty.

Choose a bounded experiment for the suspected difference. If a parent workspace
may be supplying dependencies, compare the same package command with
`GOWORK=off` to investigate single-module behavior. This is a comparison, not an
instruction to disable a workspace the project requires. `GOPROXY=off` can expose
whether a command needs uncached module downloads; it does not prepare those
dependencies or guarantee that no other network operation occurs. Keep overrides
scoped to the diagnostic command so later checks have understandable conditions.

A local pass and a failed official grade do not by themselves identify an
environment defect. Ask what the local pass depended on: tracked dependency files,
workspace replacements, flags, or preparation outside the repository. Only the
captured patch travels to the pristine grading environment. A local cache repair
can enable investigation without repairing submission. When the actual grader
diagnostics are unavailable, preserve that distinction as an unresolved hypothesis;
aggregate counts cannot establish that compilation failed there.

Repair the smallest established dependency problem using legitimate available
material and the repository's policy. If metadata and authenticated dependencies
are available, use Go tooling to update required module records and inspect the
diff. If a version is unavailable, investigate compatible prepared versions only
when the public interface and dependency constraints permit that choice. Existing
vendoring may provide a supported build path; creating a new vendor tree is a
substantial dependency change, not an automatic fallback. Never invent checksums,
substitute stub dependencies to obtain a green build, or treat disabling checksum
verification as a repair for missing packages. Repeated downloads cannot overcome
an observed network prohibition.

After a repair, compile the actual affected package and its tests again, then
exercise the requested behavior. If compilation remains blocked, retain the exact
unresolved requirement and distinguish checks you could complete from those you
could not. This leaves a useful next question instead of promoting a plausible
implementation into a verified one.

## Follow types through their consumers

A named type, alias, pointer, value and interface can express different contracts.
Compile a real consumer expression when changing one. Small compile probes can
establish a method set or signature without pretending to exercise its behavior:

```go
var _ Backend = (*Store)(nil)
var _ func(*Store, context.Context, string) (*Item, error) = (*Store).GetItem
```

Adapt these placeholders to the actual declarations. A value receiver and pointer
receiver have different method sets, and a pointer stored in an interface can be
nil while the interface itself is non-nil. When nil behavior matters, inspect both
the interface value and the concrete value it carries. For a nil slice, empty
slice and omitted serialized field, equality and encoding may differ; choose the
representation required by the caller rather than initializing everything by
habit.

A small probe is informative only about the declarations it actually uses. A
standalone program that recreates an interface can compile while the repository's
real interface remains incompatible. Prefer a probe importing the real package,
or a temporary test in that package when unexported declarations matter. If a
dependency blocker prevents that compilation, a self-contained language experiment
can still clarify a narrow question, but leaves the real package unchecked.
Similarly, `gofmt` can expose syntax problems; successful formatting does not
establish type correctness or that a mock implements its actual consumer's API.

An explicit interface migration can require coordinated edits to implementers,
consumers and local test invocations. A compiler diagnostic is a concrete list of
remaining incompatible uses, not a command to undo the migration. Conversely,
changing an existing signature for convenience can break a caller the task leaves
intact. Internal refactoring is allowed when the relevant behavior and required
interfaces survive; every private field is not automatically an immutable API.

Names and literals supplied by the task are exact. For unspecified names, inspect
existing interfaces and choose consistent Go naming, including initialisms where
appropriate. Do not infer a hidden identifier from a phrase such as "buffer size".
A requirement to return an error needs to be reconciled with the actual named
operation and signature; it does not automatically dictate the signature of every
option, constructor or helper involved.

Inspect promoted methods and embedded types before concluding that a missing text
match means an interface has no implementation. Generated mocks or protobuf code
may have a separate source of truth. Use the repository's generation path when
available and inspect resulting changes. Editing generated output alone can leave
its schema or generator inconsistent; an unavailable generator is a limitation to
resolve explicitly, not a reason to invent an unrelated interface.

## Make configuration observable end to end

Configuration travels through input spelling, decoding, defaults, validation and
use. JSON, YAML and mapstructure tags can legitimately differ. Trace the public
key through the actual loader rather than testing an internal struct assignment
or a different decoder. For a new `retry_limit`, a useful check loads a small file
through the repository's loader and observes the effective retry behavior or
field. Check environment overrides too if the task promises them; inspect how
this loader registers and resolves those keys.

Absence and zero require special care. Comparing `Limits` to `Limits{}` cannot
distinguish an omitted section from an explicitly supplied all-zero section.
If that distinction is required, preserve presence information through the loader
using an appropriate representation, such as a pointer, an existing presence API
or an explicit flag. Choose in light of the requested interface and callers.
Do not silently change public types or assume that a pointer is always required.

Defaults belong at the point where the contract makes them observable. A
constructor may establish them before options run; a loader may apply them to
missing keys; a lazy component may establish them on first use. Inspect the
required lifecycle. Applying a default to every zero value can erase an explicit
zero. An error guard can similarly erase a meaningful empty configuration or
change which error reaches the caller.

For a schema task, compare the values actually produced with the schema actually
used. A reusable probe can obtain canonical defaults, run the named decode hooks,
and validate the resulting map through the project's validator. When validation
fails, inspect the precise path, value and type at that point. A duration may be
represented as `time.Duration`, an integer or text at different stages. Printing
an independently constructed map or merely parsing the schema does not establish
that the public pipeline accepts the real defaults.

A schema accepting `string` broadly can make a valid example pass while also
accepting inputs the contract rejects. Once a concrete mismatch is understood,
check a meaningful accepted and rejected example at that boundary. Do not weaken
the schema wholesale or adjust default values simply to satisfy an unrelated
historical fixture. Preserve the contract's intended distinction.

## Trace data, state and resource ownership

Follow the real operation through each relevant implementation and wrapper. A new
stored field may need insert, update, select, scan, migration and cache handling;
changing the response struct alone cannot persist it. Conversely, a list of
similarly named backends is only a discovery aid. Determine which implement the
changed contract before editing all of them. A save followed by a fresh retrieval
can reveal a missing write hidden by the original in-memory object.

For derived state, find every writer that can make it stale. A merge or later
normalization can drop a field that the first helper computed correctly. Use
inputs with distinguishable identities and values through the actual producer
and consumer. Do not reverse traversal, deduplicate records or key a map by one
field until you understand which order and multiplicity the consumer requires.

Objects constructed by struct literals may omit collaborators a constructor
normally sets. Establish which construction forms the contract supports. If a
logger or clock is optional, a default or nil guard may be appropriate; if a
required dependency is absent, silently ignoring it may conceal invalid setup.
A test using a partial literal is evidence about that caller, not a universal
requirement that every zero-valued instance perform every operation.

For streams, check how far reads and writes actually progressed. A reader can
return data and an error together, and one `Read` need not fill the buffer.
`io.ReadFull` is useful when the protocol requires a fixed amount; reaching EOF
may be normal for a different protocol. Process the returned count according to
the chosen operation instead of assuming one call consumes an entire message.
Observe delivered items as well as metadata and totals.

Concurrency requires a causal check. Identify who starts each goroutine, owns
shared state, closes channels and waits for completion. Context cancellation is a
signal; code must observe it or pass it to an operation that does. A successful
send, a queued send and an incremented counter can occur at different moments.
A controllable channel or test dependency can expose the order without relying on
an arbitrary sleep. Use the race detector where the environment supports it and
the change warrants it, while retaining behavioral checks for ordering and loss
that race detection does not establish.

## Interpret diagnostics without inventing a diagnosis

An undefined symbol or type mismatch can block a package before its tests run.
A panic can interrupt later work in that test process. Neither event reveals how
the official aggregate entries are grouped. Follow the actual compiler location,
stack and process result. An untouched consumer can fail because your change sent
it different data; a test-source compile error may instead expose a deliberate
migration that needs a local test adaptation. Decide from the public contract.

Errors also have identity and structure. `errors.New` with the same message does
not recreate a sentinel. `errors.Is` and `errors.As` follow supported wrapping;
`%w` preserves an error relationship while changing its displayed message. Use the
repository's error contract and the task's exact text when specified. A new prefix
or catch-and-continue path can change public behavior even if it seems helpful.

If a check needs a service, inspect the existing test setup and available binary
before treating it as unavailable. A base comparison under the same unavailable
service establishes a shared local limitation; it does not explain an official
grade. If the case is promised to use only cached or in-memory data, a network
attempt is evidence to trace through that case's actual setup. Do not add a
fallback store just to suppress the diagnostic.

Once an experiment establishes a correction, keep its invocation and make the
source change. Reuse the same check to establish its effect, then broaden where
interfaces or shared state warrant it. A full source survey after each partial
grade can displace this work. Preserve the current concrete question and exact
observation so the next action advances the repair.
