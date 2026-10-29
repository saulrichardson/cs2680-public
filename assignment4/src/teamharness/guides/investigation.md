# Investigation skills for repository repair

You own the investigation, including the decision about how to investigate. These
techniques and the language examples are resources for your judgment, not a route you
must follow. Choose, adapt or leave aside a technique according to the question in front
of you. A familiar pattern, an expert suggestion and your own first explanation can all
be useful starting points without being correct. The public requirements and runtime
execution facts remain constraints; the engineering approach is yours to develop from
the evidence.

Treat your explanation as something that makes predictions. Ask what observation would
make you change it, especially when a result is surprising or work stops producing new
information. For example, a missing value at a consumer could mean that the producer
never set it, a conversion dropped it, or a later writer replaced it. Inspecting the
value at the relevant boundary can distinguish those accounts; adding another default
may conceal all three. Use an alternative explanation when it changes the next useful
observation, not to generate a catalog of possibilities. Question your test's
expectation and setup as well as the implementation.

Let the result change your decision. If it contradicts your explanation, identify which
assumption no longer holds and revise that part while retaining supported work. You may
need a different representation, a different entry point or a more coherent change
across callers rather than another variation of the same patch. If the source and
contract already establish the correction, make it and check its effect; you do not need
to manufacture doubt or run a preliminary experiment to earn permission to edit.
Reading, a small executable probe and a broader test are all useful when they answer the
actual question.

Self-questioning should make the next action clearer, not become another loop. There is
no required reflection report, number of hypotheses or ritual before each tool call. The
useful result is a checked repair and an honest understanding of what remains
unresolved, not evidence that you followed every example here.

These techniques describe the engineer's work. A reviewer without tools applies the same
distinctions to the supplied record and proposes useful observations; it must not claim
to have executed checks or inspected material it was not given.

The public task defines the change. Preserve the exact names, signatures, literal
values, formats, roles, error behavior and ordering it specifies. A small or degenerate
example can expose a boundary that a typical example misses. Do not quietly substitute
an easier value, weaken "only" into "usually", or replace an exact comparison with a
prefix rule. At the same time, distinguish requirements from the reporter's explanation
and illustrative examples. A suspected cause in the issue does not outweigh contrary
execution evidence. When clauses appear to conflict, follow their actual conditions and
seek an implementation that satisfies both before deciding that one must be discarded.

Let the central promise of the task guide an early behavioral example. When the
requirement connects several operations, exercise that connection using the repository's
actual implementation. A configuration may load successfully yet fail when its decoded
representation reaches a validator; a stream may produce correct items yet fail to
deliver them to its consumer. Checks of the individual components are useful, but leave
the connection unexplained. The ecosystem guides offer concrete examples of these
boundaries; adapt them to the real interfaces.

You can build this example while the repair is still incomplete. Its first failure can
tell you where to work, and the same invocation can show what each correction
accomplished. Keep the input and expected consequence grounded in the public
requirement, including its exact literals and relevant boundary cases. A nearby existing
test can supply useful setup without defining the new behavior. If setup prevents the
example from reaching the relevant operation, investigate that prerequisite or exercise
a smaller real boundary while keeping the original question open. This is a way to
organize useful investigation, not a mandatory first action or a prerequisite for making
a correction already established by source inspection. Distinguish what you executed
from what you only read about.

If the task prescribes how something must be done (a particular mechanism, API, option
or sequence), implement it that way even when another route reaches the same output;
tests may observe the route. A requirement can also forbid an operation rather than
describe a final state: "do not modify X" is violated by an operation that writes X,
even when it writes the same value. Observe which operations run, not only the end
result.

An unspecified value is not automatically zero, empty, absent or a plausible invented
default. Determine whether the task leaves the existing behavior in place, delegates the
decision to an established interface, or leaves a real ambiguity. Inspect producers,
consumers, analogous behavior and the actual type contract. Preserve existing behavior
where the task preserves it, and explain an unresolved interpretation honestly. Adding a
convenient identifier or output field can change the public structure just as surely as
removing one. If exact output preservation matters, compare actual outputs under the
relevant conditions. Missing, null, false, zero, empty and invalid may represent
different states. Converting an unspecified preference to false can turn inheritance
into an explicit choice; treating zero as absent can discard a valid value. A default,
coercion or fallback must preserve the distinctions the contract requires.

A new guard or exception handler can hide a defect without repairing it. Check whether
the public operation now works or merely returns an empty result instead of reporting
failure. Defensive behavior should implement an established case, not make an
unexplained value disappear.

A task can deliberately replace an interface. Implement the new signature, name, type or
format; an old test calling the previous interface does not authorize undoing the
migration. Find what behavior that test protected and retain useful coverage through a
check of the required interface. Conversely, an inconvenient old caller is not obsolete
merely because your implementation does not support it. Determine what the task changes
and preserves. Changing tests never establishes source correctness by itself. Where a
verifier restores tests or uses a pristine environment, its recorded contract matters.
Do not assume that behavior without evidence; it does not reveal hidden assertions or
override the public task.

Compare your test setup with the arguments and state actual callers provide. A new test
may supply a property, constructor argument, initialization step or dependency that the
original caller never supplies. Its pass establishes only behavior under that expanded
setup. Render a changed UI component with the original caller's props, or invoke a
library or service through its existing entry point. If that path fails, determine
whether the task requires migrating the interface. Update affected callers coherently
when it does; otherwise repair the compatibility boundary. Adding the missing argument
to a test does not repair it. Do not assume the verifier's test patch supplies your new
arguments, initializes your new state or updates a broken caller. Its existence reveals
none of those details.

Understand a required interface by its role as well as its spelling. A dispatcher
selects an implementation; a factory constructs one; a parser converts an input; a
handler participates in a lifecycle. Find existing components with that role, not merely
a similar name. Read their callers and tests together to understand construction,
selection and dependency injection. A similarly named component with a different role
can be a misleading template. A required class or method may matter because callers
instantiate or substitute it, even when your preferred internal design could eliminate
it.

Follow the real operation from its entry point through producers, transformations, state
changes, storage and consumers. A helper that produces the right value when called
directly is insufficient if the application never calls it, supplies different
arguments, or ignores its result. The converse matters too: a passing higher-level
caller does not validate every separately promised public operation. A public
operation's permitted starting state is part of its contract. If the task requires using
an object immediately after construction, a caller that first initializes it exercises a
different case. Adding that initialization to a failing probe bypasses the requirement;
it does not show that the source now satisfies it. Preserve the original call sequence
and check it again after the source correction. Keep the prepared caller as separate
coverage, unless you establish that the original call was outside the public contract or
the probe itself was invalid. For persistence, distinguish updating an in-memory object
from saving a value and reading it through the public retrieval path. When relevant,
retrieve through a new object or the normal storage boundary so a cache does not conceal
a missing write. A pass through one prepared path does not validate another separately
promised operation.

Trace every relevant writer that can overwrite a value later. An untouched
initialization, merge, serialization or cleanup path can undo a correct edit elsewhere.
Use the repository's constants, enums, error representations and fixtures where they
define the interface; an invented equivalent can have a different identity or meaning. A
value correct immediately after your helper runs may be overwritten by a later effect or
merge. Exercise the relevant journey through loading, the action, intended asynchronous
updates and the final public consequence. Investigate the writer that changes the value
instead of repeatedly correcting an earlier calculation.

A failure in an untouched function can still be caused by your change. You may alter the
data reaching it, bypass normalization, remove a filter or change when it runs. File
ownership is not causal ownership. Inspect the actual input and state at the failing
boundary; where useful, compare equivalent original and changed paths to locate the
earliest consequential divergence. Follow that through to the failure rather than
dismissing the consumer or patching it blindly.

Search is an operation with a result, not proof of absence. Read its exit state and
diagnostics. An unsupported option, wrong working directory or excluded directory can
produce no useful matches while the code exists. If the result drives a consequential
conclusion, verify the scope and try a known nearby symbol as a control. Inspect the
library version actually installed before relying on remembered behavior. For generated
code, identify the generator and normal generation path; hand edits can leave an
interface inconsistent with its source.

Obtain data shape, type, ordering and missing-value behavior from the actual producer.
Where execution is feasible and consequential, observe representative values inside the
real path using the repository's fixtures or construction mechanisms. A plain dictionary
can omit attribute behavior, lazy loading or normalization that the real record
supplies. A hand-built list in a toy script can prove that your loop works on the order
you imagined while saying nothing about the order the producer emits. When the task
concerns first, next, previous, latest, oldest or before, establish producer order and
consumer interpretation before changing traversal direction. One observed order is not
automatically a guarantee; read the code that establishes it.

Establish identity and multiplicity before indexing by one field, converting a sequence
to a set, choosing a first match or combining records. Entries with the same category
may represent different occurrences; differently spelled values may identify the same
object after normalization. Use distinguishable examples such as repeated categories
with different identities and a linked item that must match only one occurrence. Do not
remove duplicates or reverse traversal until you understand which distinctions the
consumer needs.

Interpret values in the consumer's frame. Units and offsets travel with values: seconds
versus milliseconds, absolute versus relative positions, zero-based versus one-based
indexes. Follow each through every conversion to the consumer. Encodings compose too: a
value already percent-encoded, escaped or serialized can be encoded again by a setter or
helper; print the exact bytes the consumer receives. A relative path may resolve from
the package directory rather than the repository root. A key may already contain the
prefix another layer adds. Identity can depend on multiple fields, and normalization can
make differently spelled inputs equivalent. Observe where these conversions occur. Do
not repair a failing call that exercises the public requirement by silently adding a
prefix, changing its working directory, removing an input or patching an internal value
unless the public contract requires that change. It may remove exactly the evidence that
exposed the source defect. A probe can also be wrong; establish why rather than
dismissing an inconvenient result as a test bug.

Configuration is a complete path. Public spelling must survive decoding, schema
validation, default selection and consumption. Assigning an internal field directly does
not test that path. A decoder and schema can agree on an incorrect spelling, and a
default can conceal the missing value. Load the task's actual example through the public
mechanism and inspect the effective value or behavior. Omitted, explicit zero, empty and
invalid inputs can have distinct meanings; choose examples that distinguish the contract
instead of normalizing them all into a convenient representation.

For a new component, inspect how existing components with the same responsibility are
driven. Their tests can reveal important seams: an instance method replaced by a fake, a
module-level name patched before construction, or a manager obtained through a getter
when needed. Capturing a dependency in a constructor, rebinding an imported name or
bypassing a factory can defeat the caller's supported substitution. Adapt a useful
sibling test while preserving what it establishes, and check the new component's own
required behavior. Copying names into an unrelated test is not independent validation.
Do not reproduce a sibling's incidental structure when the task requires different
semantics.

Refactoring deserves the same behavioral care as adding code. Understand the
preconditions, defaults, placeholders and special cases in the code being moved. Review
removed lines and follow their consumers; a clean rewrite can silently lose behavior
outside the example you considered. Keep changes connected to the repair's explanation.
This does not forbid coordinated edits across an interface: the producer, representation
and consumers sometimes must change together. Understand their relationship and verify
the required result.

Streaming and asynchronous behavior require a causal sequence. Follow when data is
produced, queued and consumed, when a resource becomes complete or closed, and what
operations remain possible afterward. Counting, inspecting or finalizing a stream can
consume it or prevent later updates. A correct final scalar or metadata object does not
show that the consumer received the intended items. Use distinguishable values and
observe the actual producer-to-consumer path. Cancelling a wait differs from cancelling
underlying work. Registration differs from invocation, and a callback's position in a
file does not establish ordering between concurrent events. When concurrency is part of
the change, use the project's relevant concurrency checks and inspect what they actually
exercise.

Attempted, accepted, delivered and completed work are distinct. Updating a counter
before an awaited send succeeds can report work the consumer never received; marking a
stream complete too early can prevent its final item from arriving. Check the
transitions that determine the requirement, such as no items yet, the first item, later
items, completion or failure. Observe the order of effects as well as the final value.
Use actual completion signals or controllable dependencies where available; a sleep that
happens to be long enough is weaker evidence of ordering.

A useful check could expose the plausible defect you are trying to rule out. Start from
the public input and observable consequence when possible, retaining the real code at
the disputed boundary. Mocking an unrelated external service may be necessary; mocking
the data shape, ordering or lifecycle under investigation can make the check incapable
of failing for the actual reason. Place probes where they can use the real package and
fixtures instead of reimplementing the algorithm in a detached script. Syntax, expected
names and small helper probes are legitimate intermediate evidence with limited scope.
They do not establish that a real caller can construct and use the repaired component.

The expected result can repeat your implementation's assumption just as the test input
can. Derive it from the public contract, an established caller or an independently
understood example. Reimplementing the same transformation in the test makes agreement
weak evidence. Choose an example on which your approach and a plausible incorrect
approach produce different public consequences. If both would pass, the check may
protect compatibility without resolving that question. A round trip can conceal a shared
mistake too: an encoder and decoder can agree while emitting a format an external
consumer cannot use. Inspect that boundary.

A check's verdict is a conclusion produced by its assertions. Read the relevant returned
values with the same care you give the verdict. A serialization check may confirm that a
header contains a boundary parameter while missing that its value differs from the
delimiters in the body. The command can pass even though its output exposes the defect.
When you notice such a contradiction, strengthen the example to express the relationship
the caller needs, then use it to check the repair. You can improve your test's
understanding as well as your implementation. A passing result is useful evidence under
the conditions it exercised; it need not end an investigation that the actual output
gives you reason to continue.

Comparing a new behavioral check on original and changed source can be informative: a
failure on the original and a pass on the candidate helps connect the edit to the
required behavior. Inspect both actual results and source versions. Running only an old
test that passes on both versions does not establish new behavior. For a completely new
interface, an original-source import failure is a trivial difference, not evidence that
the new semantics are right; test the required contract and real caller instead. Some
valid checks protect unchanged behavior and should pass on both versions. Do not require
a baseline failure where it answers the wrong question or claim its absence reveals what
the grader checks.

Make an important experiment reusable once it can decide the repair. A small saved test
or script with explicit assertions preserves the input and expectation while you change
source; repeatedly rebuilding it from memory can quietly change the question. If setup
prevents it from reaching the disputed operation, repair that setup while retaining the
intended assertion. After a source correction, run the same check and inspect what
changed. This need not become a permanent test file or another reporting artifact; its
value is a stable comparison you can use.

Locate the exact failed operation before explaining it. A traceback's file and line, an
assertion's expected and actual values, or a compiler's referenced expression are better
anchors than the last familiar word in the output. A line saying one case passed does
not identify the following failure. A sequential probe may stop before later cases
execute, and stdout/stderr buffering can change the apparent order. If a reduced example
passes, compare its input and setup with the original failure instead of declaring the
original resolved. Give independent cases meaningful assertions so a failure identifies
what was actually exercised.

Understand the state an experiment depends on. Environment variables, temporary files,
caches, module-level state, a running server or a database left by one probe can change
the next one. Isolate cases that should be independent; retain shared state when its
lifecycle is the behavior under investigation. If a result changes between runs, compare
their setup and state rather than assuming either a source defect or contamination
before locating the difference.

Keep an environment explanation tied to the source and command that established it. Once
a setup blocker is repaired, the next run may expose a compiler error or an assertion
that requires a different correction. Read the new diagnostic rather than carrying the
earlier explanation forward unchanged. A local workaround may enable that investigation
without becoming part of the submitted repair. Compare what the captured patch
reproduces with what the sandbox supplied; when official diagnostics are unavailable, a
local setup failure remains local evidence rather than a proven explanation of the
grade.

Editing a test or expected fixture until it agrees with your output proves only that
they now agree. Do not assume submitted test or fixture edits control an external
verifier; inspect its actual documented behavior. A changed local test can supply
evidence under its new conditions, but its pass does not settle the original failure.
Determine which expectation the public contract supports and exercise the required
behavior through the real boundary. Updating a snapshot alone does not show that this
behavior is correct.

When an original test's expected value differs from your output, decide each differing
value separately against the requirement. If a requirement sentence changes that value,
the old expectation is stale: work out the new value by hand from the requirement on
that test's own input, and do not reproduce the old one through tuning, fallbacks or
output kept "for compatibility". Where the task preserves behavior, investigate the
difference as a possible regression, including the test's actual setup and caller. A
test of behavior the task changes should distinguish old and new outcomes under the
relevant input. If it does not, inspect whether that input reaches the changed path or
whether the assertion is too broad.

If an external verifier applies a private test patch or restores tests, that fact does
not reveal its assertions or explain a failed grade. A local pass under adapted
expectations establishes only what those expectations check. Existing tests can protect
compatibility without exercising the new behavior. Choose a check of that behavior
through its real entry point; a meaningful original/source comparison can connect the
change to its effect, while a new interface still needs its semantics checked beyond
proving that it was absent on the base.

Know what ran. Confirm selected tests, imported source, arguments, environment, process
completion and full output. A stale build artifact or narrower filter can test something
other than the edited code. "No tests to run" is no pass. Select focused checks
deliberately, then broaden coverage where changed interfaces and callers warrant it.
Keep complete output in saved runtime evidence and retrieve another slice with read_file
instead of rerunning the command through different head, tail or grep pipelines. Output
discarded inside the shell cannot be recovered afterward. A running or cancelled command
has no completed verdict.

Concrete contrary evidence remains an obligation until explained or repaired. Keep the
failed invocation, expected consequence, observed result, source version and environment
clear. If you change the probe, establish why the original was invalid; otherwise rerun
the original invocation after the source correction. A later passing probe that omits
the failing operation does not close it. This applies to a failure discovered at any
point, including after compaction.

Read the diagnostic before attributing a failure to the environment. A missing
executable, failed dependency resolution, import error, collection error or unfinished
process differs from an assertion about public behavior. When the same operation exists
on original source, compare equivalent conditions to see whether your change introduced
the failure. If the comparison is unavailable or exercises a different interface, use
the direct evidence you have and state its limits. One environmental failure does not
explain every nearby failure.

A workaround in the live sandbox establishes behavior under that workaround. It does not
establish that the patch works in the pristine verifier. Distinguish source, dependency
declarations, assets and configuration carried by the patch from preparation that exists
only in the live filesystem or process. Investigate that difference before repeatedly
varying source in response to a failure it cannot repair.

The saved candidate patch contains captured source changes, not private grading
diagnostics. Its receipt locates the assessed bytes and outcome; rereading it cannot
reveal diagnostics the verifier did not supply. Retrieve it to resolve a concrete
source-version or capture question, then return to public behavior and local evidence
for the repair.

An official grade establishes an outcome for exact captured bytes. Aggregate counts
cannot identify hidden assertions, divide entries into known passing and failing groups,
or prove compilation succeeded. Equal counts on different patches do not establish
identical failures; an unchanged-candidate receipt is not a new experiment. If the cause
is not established locally, preserve that uncertainty. A higher passing count does not
establish that every edit helped, and a lower count does not identify a regression or an
original test. Repeated unsuccessful grades leave the repair unresolved and are reason
to examine what the current explanation and checks have not established. Renaming,
reordering or adding guards around the same idea and grading again rarely teaches
anything. Before another speculative variation, identify an important assumption those
candidates share and obtain evidence that could overturn it, using the required input,
real producer, caller or repository fixture. Let that evidence decide whether to repair
the approach or replace it, retaining corrections it supports. Before another
evaluation, know what changed and why the next result would be informative. A source
correction supported by inspection can justify evaluation; rerunning an unchanged
candidate is not another experiment. Prefer a local check that could expose the
suspected mistake, rather than treating grading as the only way to investigate it. A
probe built from plain dictionaries, permissive mocks or classes you wrote accepts
whatever access pattern your code uses, so it cannot expose a wrong one; objects created
through the project's own fixtures or real producer can.

When every grading entry fails, or a whole group fails at once, the counts still do not
distinguish incorrect behavior from tests unable to import, compile, construct or locate
something. Use your actual changes and local observations to choose a discriminating
check. If an interface, import or fixture path changed, compare it with the public
contract and exercise the touched packages through a real caller or test runner.
Successful loading leaves the behavior to investigate; a loading error supplies a
concrete correction to check. If the original source also fails locally, preserve that
comparison without assuming it explains the official result.

Keep a candidate understandable as a repair. Related changes may need to move together
across a producer, representation and consumer; unrelated speculative changes obscure
what a result establishes. Know what changed and which local observations support it
before evaluating. An improved count does not prove every edit was correct, and an
unchanged count does not prove nothing improved. Preserve locally established
corrections while investigating what remains. Where changes must be separated to explain
a regression, use focused local checks rather than treating official grading as the only
experiment.

Advice and summaries are interpretations. A reviewer can identify a real unsupported
assumption, but confidence does not establish its favored explanation. Preserve a
concrete contradiction even when an expert answer sounds persuasive. Examine the source
reference and whether the proposed experiment distinguishes the competing accounts. Do
not abandon a lead supported by execution for a more fluent unsupported story. Your own
earlier conclusions deserve the same treatment. A cause, value, name, field or type
supplied by a review is a claim to compare with the task and current source. If the
advice depends on a language or framework rule, examine the exact operation at issue and
execute a small example when that can settle it. Tuple unpacking, iteration over pairs
and converting to a mapping, for example, are different callers; agreement under one
does not establish the others. Choose an expectation from the contract and caller, not
from the proposed fix. Adopt the correction when its premise is supported, and keep
contrary evidence open when it is not. A passing bundle of changes does not establish
every claim made in the advice.

Turn a discovered correction into an edit and a check. Repeatedly describing a missing
propagation, wrong condition or unhandled caller does not advance the repair. Once the
consequential uncertainty is resolved sufficiently to support a justified reversible
change, implement it and inspect the consequence. Further reading is useful when it
answers a remaining question, not because it delays an already supported repair. If the
approach remains uncertain, execute a smaller discriminating probe rather than
repeatedly simulating the same cases in reasoning.

When a supported correction exposes another problem, investigate their causal
relationship instead of automatically reverting it or defending it. The new result may
reveal a separate defect, a regression, or an assumption the earlier check missed. A
temporary reversal can be a useful comparison, and a different design can supersede the
edit. Preserve the established behavior and evidence, not a particular patch merely
because it once passed a check.

Keep working understanding useful as the investigation changes. The compact tool can
replace accumulated narration while preserving original evidence. A useful continuation
carries exact unfinished obligations, contrary evidence, unknown grade causes and
changed behavior not yet exercised. Retain references that recover original
observations. A concise account that drops a known unapplied correction is worse than a
detailed account that lets the next action perform it. Completed work and attractive
theories should not crowd out what is still wrong. A new observation can correct your
summary; the summary does not overrule the observation.

Before evaluating or ending, inspect relevant pending checks and read the results you
rely on. Relevant existing tests provide evidence that new probes can miss; investigate
how to run them and preserve any setup limits honestly. Wait for a check whose completed
verdict matters; if you cancel an uninformative check, retain its unfinished status.
Exercise required behavior through real entry points and callers, select relevant
existing tests deliberately, and broaden coverage where changed interfaces warrant it.
Read the selected cases and their outcomes rather than treating a narrow filter or a
broad test command as sufficient by itself. Review the whole diff and remove scratch
files, build outputs, debugging prints and unrelated edits; non-ignored repository files
enter the candidate. Make remaining gaps clear to yourself. Saying "implementation
complete" does not resolve a failure or execute a check you meant to run. A difficult
investigation often reaches a point where the implementation looks complete and the
familiar checks all pass, yet the required outcome is still unresolved. This is an
opportunity to improve the explanation you are working from. Your job includes
discovering a useful next question; it need not already appear in a diagnostic or
reviewer note. The public contract, real callers and current source remain available
even when official diagnostics are private. You can make progress without identifying a
hidden assertion.

Start with a consequential claim that currently rests on reasoning rather than an
observation. Perhaps two paths are said to produce the same value, a conversion is
assumed to preserve a distinction, or a consumer is expected to accept what a producer
emits. Make that claim observable in the actual code. For example, invoke the two paths
with the same public input and compare their outputs, or feed the emitted representation
to its real consumer. A check that succeeds can still narrow the investigation by
establishing something the earlier checks did not. If it fails, retain the example,
repair the source and repeat the check. These are ways to choose a question, not a
required sequence or list to complete.

Use the scope of your evidence to find the gap. When familiar checks pass but the task
remains unresolved, ask which part of its central promise those checks actually reached.
An important unexercised connection is often a better next experiment than another
variation of a check that already passes. Revisit the expectation and setup with the
same curiosity as the implementation. When the approach itself depends on an unsupported
assumption, you have permission to replace that part of the design. Keep the corrections
supported by evidence and let new observations change the rest. More variations of a
familiar patch are not the only option.

End or continue according to the work and evidence you judge useful. Actual configured
limits remain runtime facts; this reference adds no recovery owner, spending policy,
reading quota or finishing gate. Returning an investigation does not certify correctness
or terminate jobs.
