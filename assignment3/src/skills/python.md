# Working in Python repositories

Python makes a small experiment easy to write, but an experiment is useful only
when it reaches the operation in question. A direct helper call, a framework
entry point and a test with replaced dependencies may run different behavior.
Use this guide to understand those differences. The shared investigation guidance
owns the public contract, grading boundary and continuation policy; the examples
here explain how to apply that reasoning in Python.

## Establish the interpreter, imports and caller

Read the relevant project configuration and an existing test before choosing an
invocation. A repository may use pytest, unittest, a custom runner or several of
them. Its conftest files, plugins, environment setup and fixtures can be necessary
to construct a valid caller. Use the installed interpreter and dependencies. If
imports disagree between a script and the test runner, inspect what each loaded:

```sh
python --version
python -c 'import package.module as m; print(m.__file__)'
python -m pytest --collect-only -q tests/test_component.py
python -m pytest -q tests/test_component.py::test_case
```

These are examples with placeholder paths. Use the project's actual command and
retain its complete output. Collection verifies that tests can be discovered and
loaded under those conditions; it does not exercise their assertions. A script
under `/tmp` and a test inside a package can have different import paths. An
installed package may also point into this checkout through an editable install,
so inspect the resolved file rather than diagnosing from a directory name alone.

When loading fails, distinguish a missing import from a package that was found
but failed while importing one of its dependencies. Read the full traceback and
check `sys.executable`, the resolved source and the project's declared versions.
For a missing import name, find the distribution that supplies it; distribution
and import names need not match. A separate `pip` executable may manage a different
interpreter. Where this project uses pip, `python -m pip --version` helps establish
that relationship before changing the installation.

An installed distribution can still lack a working native extension. An undefined
symbol, missing shared library or incompatible wheel calls for inspecting the
interpreter, platform and binary dependency, not replacing the Python API. A source
distribution may need build tools or build dependencies that a compatible prepared
wheel would not. Inspect available artifacts and the repository's setup before
attempting installation in an offline environment. Disabling build isolation does
not supply those requirements; it makes the current environment responsible for
them. If a supported local installation is necessary, retain the interpreter and
artifact identity so its result has clear conditions.

After correcting setup, return to the same real import and caller that failed.
Inserting a dummy module into `sys.modules`, omitting a failing plugin or replacing
the disputed dependency can make an isolated probe run while bypassing the original
problem. Such experiments may answer a narrower question, but leave that boundary
unverified. Changes to an environment or installed package do not travel with the
submitted patch. Where the repair needs a dependency declaration or build change,
use the project's maintained files and verify the affected caller; do not replace
them with a snapshot of every package installed in the sandbox.

Import executes module-level code when the module is first loaded. Later imports
normally reuse that module object. Configuration captured at import can therefore
behave differently from a getter evaluated on each call. Circular imports may
expose partially initialized modules, and test plugins can change import order.
Follow the traceback to the actual access and compare the normal entry point
before changing imports or adding a fallback. An exception during collection does
not establish which tests would pass once collection works.

Search names using tools available in the sandbox. `git grep -n -F 'symbol'` is
useful for tracked source; include untracked files or dependency source when they
matter. No textual match does not prove a method is absent: it may be inherited,
generated, dynamically installed or supplied by a different implementation.
Inspect the concrete object, its type and relevant base classes. A focused
`inspect.signature` or `inspect.getsource` call can clarify a callable, though
some extension and dynamically generated objects do not expose source that way.

## Preserve protocols and meaningful distinctions

Read a return annotation together with its documented semantics and callers.
An iterable, iterator, generator, sequence and concrete list offer different
operations. A generator protocol need not be implemented by a function containing
`yield` unless that implementation detail is explicitly required. Likewise, a
mapping contract does not automatically require `type(value) is dict`; a task can
require a concrete type, but that must come from the contract. A `None` return
annotation describes the result, not a prohibition on spelling `return None`.

For iteration, write the actual consumer expression. Given a two-field record,
`name, value = record`, `list(record)`, `for name, value in records`, and
`dict(records)` ask different things of `record` and `records`. Test the required
expression using distinguishable values. A representation that satisfies one
expression may fail another; changing the probe to a different expression does
not resolve the original question.

Subclasses and wrappers can preserve metadata while comparing equal to plain
values. When working with tagged strings, numeric subclasses or lazy records,
inspect the real producer and the required result: value, concrete type, metadata
and identity may be separate obligations. `is` checks identity and `==` checks
equality. Interning and constant folding make literal-based identity experiments
poor evidence about allocation. If identity matters, retain the exact input object
and compare the result to that object under the specified conversion.

Python's `bool` is an `int` subclass; bytes, text, mappings and sequences can also
reach branches that a loose type check did not intend. Choose examples that
separate those cases when the task changes coercion. Do not treat every unusual
input as invalid or invent normalization rules. Learn whether the public API
accepts it, rejects it with a particular exception, or delegates to another API.
Missing, `None`, zero, false and empty values can carry distinct meanings.

An unspecified parameter name cannot be recovered by guessing what a hidden test
might use. Search public callers and analogous interfaces, preserve names the task
actually supplies, and choose an idiomatic name for a genuinely new unspecified
parameter. Keep the choice consistent without promoting it into a quoted task
requirement.

## Follow dependency lookup and framework execution

A patch replaces a particular name on a particular object. Whether the code sees
it depends on when and where lookup occurs. If `consumer.py` does
`from service import fetch`, replacing `service.fetch` afterward does not normally
replace `consumer.fetch`. If a constructor captures a callable before a test
replaces it, the instance may retain the earlier reference. These are explanations
to investigate, not a reason to make every dependency lookup dynamic. Preserve
supported substitution seams while choosing lifetime and ownership deliberately.

Read how a real test constructs the component and what it substitutes. A sibling
can demonstrate a framework's manager, parameter, registry or client protocol,
but copying its inheritance tree does not establish your component's behavior.
Follow the public operation into the selected implementation. A new manager can
contain every expected method name yet still dispatch to an inherited abstract
method. Exercise construction and the real create/update/delete path, replacing
only the external device or service boundary when that is appropriate.

Mocks should expose mistakes in the interaction under investigation. A permissive
`MagicMock` can manufacture attributes and return more mocks, concealing a method
that the real provider lacks. A concrete fixture or a suitably constrained mock
can make that mismatch visible. Inspect the real provider before adding methods
to a fake merely to satisfy your implementation. Conversely, a test double may
be incomplete for an intentional interface migration; establish that from the
contract and real implementation rather than assuming either side is correct.

An unexpected call count can arise from retries, duplicate dispatch, constructor
work or cleanup. Inspect the actual sequence and arguments. A `side_effect` list
running out shows that the executed sequence exceeded that list, not why it did.
Avoid changing call counts solely to satisfy a mock unless they represent the
required interaction. Broad exception handlers also deserve a boundary check:
a framework's exit or failure method may be represented by an exception in tests,
and catching it can turn an intended result into a different error.

## Build observations you can reuse

When an experiment becomes central to the repair, save it as a small test or
script in an appropriate temporary location and retain its invocation. Use a
quoted heredoc or file when multiline shell quoting obscures the Python program.
Assertions should identify the input and expected consequence; printed comparisons
are useful during exploration but do not make a failing comparison exit nonzero.
For several independent cases, parametrization can report each case even if an
earlier one fails. A sequential script stops at its first uncaught exception.

Locate that exact exception before naming a cause. The last printed `PASS` line
usually describes completed work, not the next failing assertion. Stdout and
stderr may also appear in a different order because of buffering. Use the
traceback's file and line against the saved script, or rerun the identified case
with the relevant values displayed. If a smaller probe passes, compare its setup
and input with the failing one; that discrepancy is useful evidence, not grounds
to discard the original result.

For exact serialization, compare against the specified reference operation under
the same conditions. If the contract names a standard-library API and options,
call that API to produce expected bytes rather than writing a second serializer
from memory. Normalize only differences the contract permits. A boundary token or
timestamp may vary, while header order, line endings or encoding may be required.
Keep the asserted comparison and show the first differing region with `repr`:

```python
expected = reference_operation(input_value)
actual = repaired_operation(input_value)
assert actual == expected, (repr(expected), repr(actual))
```

Replace those placeholder operations with the actual APIs. If output is large,
report the first difference and nearby bytes without throwing away the full
outputs. A successful hand-built fragment does not establish whole-message
compatibility. Standard-library defaults matter: JSON separators and escaping,
CSV newline handling, text-mode newline translation and email policy can each
change bytes. Inspect the selected options instead of assuming one canonical
format applies to every caller.

A persistence check should reach storage and retrieval through the supported
provider. A passing in-memory helper does not show that a later read uses the
saved value. Use distinguishable records and a fresh read where relevant. If a
connection fails, follow whether this case was supposed to reach that service.
Inspect available setup and compare the base when useful; do not add fallback
storage or swallow the error merely to make a probe complete.

For asynchronous code, distinguish coroutine creation from execution and await
at the interface that owns completion. Reuse the project's async fixtures and
markers. For GUI bindings, distinguish removing a registry reference, closing a
resource and scheduling object destruction. Deferred deletion may require an
event-loop turn; the project's existing lifetime tests can show how to observe
it. A fake recording `deleteLater()` establishes that the call was made, while a
real-object lifetime check answers whether destruction happened.

## Finish a coherent Python repair

Check the implementation through its actual caller after related edits are in
place. A compile/import check is useful for syntax and loading, while a focused
behavioral test establishes what happened on its input. Broaden coverage where
shared state, callers or imports make interference plausible. If a test passes
alone but fails in a group, investigate state, ordering, caches and resources
under both conditions rather than declaring the group obsolete.

Keep supported behavior and required migrations distinct. An old expectation may
need adaptation, but the new expected value still needs to follow from the task.
Do not infer a source repair from a symptom alone: a read-only property does not
automatically need to become a method, and an absent configuration key does not
automatically need a new schema entry. Follow what the caller is entitled to do.
Once a concrete correction is established, apply it and rerun the retained check.
Use the result to decide the next edit, broader check or evaluation.
