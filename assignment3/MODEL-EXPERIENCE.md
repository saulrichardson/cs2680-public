# The model's experience

The goal is a model that can direct a difficult repository investigation, learn
from real execution, preserve the understanding needed to continue, and deliver
a checked solution. This document specifies its environment at the moments that
matter. It explains what the model should understand, how the selected runtime
supports it, and what evidence shows whether the design is working.

## Current policy and authority

Latest explicit user instructions govern. This is the maintained runtime policy;
source and serialized requests establish what is implemented. Historical designs
and older compatibility fixtures explain previous behavior without authorizing
its restoration. If policy and implementation disagree, identify the specific
mismatch instead of silently treating an old example as current behavior.

The engineering foundation in `src/prompt.txt` is followed by the runtime
adapter. It explains how to move from evidence to a coherent edit, check and
finishing decision. Shared skills and fully inserted detected language guides
develop the practical techniques. Preserve that foundation unless the user
authorizes changing it. No separate orientation stage, diagnosis
ledger, grading prerequisite or additional harness-enforced planning/reflection ritual is
selected. The model can organize its work without the runtime requiring a second
administrative workflow.

The model-facing guidance distinguishes execution facts from explanations.
Aggregate grading outcomes never diagnose particular failures or validate every
edit in a passing candidate. Language guides supply locally observable diagnostics
rather than score-to-cause rules. Expert notes identify a proposed check and its
expected consequence; the owner tests an uncertain premise before adopting it.
Shared guidance encourages an early executable example of the task's central
promise through real producers and consumers. It teaches owners to inspect
returned values even when their own assertions pass, and reviewers to reconcile
historical observations with the exact graded patch before describing a defect
as current. These are resources for choosing useful actions, not additional
workflow gates or reporting requirements.

Related edits may be completed coherently before verification. The shared skills
teach persistence as developing an informative next question: make an unobserved
assumption executable, examine what existing checks leave open, and revise the
design when evidence supports it. Remaining funding supplies room for that work;
conversation length and local confidence do not establish completion. Ending an
attempt remains an engineering decision grounded in the feasible work and actual
obstacles, with no requirement to identify hidden assertions or consume the full
allowance. Recovery receives the same lesson and knows that ending closes the
task. These instructions add no runtime grading prerequisite, reviewer stage or
transition threshold.

The submission starts with five task workers and one shared allocator. Each
non-skipped task has a $1.10 baseline spending ceiling. This is permission to
spend, not a separate reservation or a guarantee that every task can spend $1.10.
New inference and task opening stop once settled charges plus unknown-charge
reservations reach the full $10 declared budget. No fixed reserve is withheld.
Already-admitted responses can finish above that ceiling, and their actions and
settlement grading proceed while the external course environment permits them.
The course can independently stop the whole submission at its budget ceiling.

For increases, subtract completed tasks' accounted costs and unattributed calls
from the $10 pool, then divide by the number of unfinished non-skipped tasks,
including tasks not yet opened. This is a total task ceiling, not additional
spending on top of an active task's charges. Pending calls owned by completed
tasks and unattributed calls count at their full quotes; unknown charges retain
their full reservations. An active task's own spending cannot raise its ceiling.
The fair share is rounded down to whole cents.
Give newly opened tasks the greater of the baseline and that fair share; raise
running tasks to that share when it exceeds their prior ceiling. Ceilings never
decrease, even after a late overrun, and completed tasks never reopen. The shared
spending check governs admission even when existing ceilings exceed the money
left. There is no first-ten phase, allowance earmarking or refund ledger.

Initial solving, automatic expert review, recovery, maintenance and memory all
spend the same task allowance. Recovery does not reset it or request an extension.

The harness imposes no task or submission time deadline. Admission and warnings
use monetary allowances only. Elapsed time and declared platform time limits
remain internal accounting metadata, not local stopping rules or model countdowns.
The course can still stop the submission under its external four-hour contract.
Admitted inference and grading complete without a local time cutoff. Budget
exhaustion prevents the next inference. The last admitted response finishes its
actions; after owned-process cleanup, the runtime settles current source through
the ordinary capture and evaluation path. New nonempty bytes receive one grade,
unchanged bytes reuse their receipt, and an empty base retains the selected
candidate. A secured pass or ended owner is not reopened. No further inference
or recovery starts. Memory finishing has no exemption from budget enforcement.

Charges are settled with Decimal arithmetic at course prices, including cached
input and billed retries. Maximum-output estimates are evidence of uncertain
exposure, not money already spent or a reason to shrink output. A call whose usage
is unknown is charged at its full reservation, the most it could cost, rather than
becoming invented savings; it counts against its task's allowance and the shared
limit. Unrelated tasks can continue while shared funding remains. Explicitly
unbudgeted development inference retains its provider-recovery behavior and
unresolved charge records.
Admitted responses may overrun task or shared limits; admission does not cap
their output to force the final total below the ceiling.
Development resource declarations use their full declared budget,
task count and platform timing metadata, rather than silently claiming the course defaults.
When the entire usable declared pool is below $1.10, clamp the baseline to that
usable total. An explicitly unbudgeted run has no monetary task ceiling.
There is no independent local time limit in development either.

Standard owns both the initial investigation and the possible fresh recovery
attempt. A failed `evaluate_candidate` leaves the owner active. The initial
owner's first actual failed grade triggers at most one tool-free expert critique;
its engineer notes are unverified advice, not a transfer of responsibility.
`end_attempt` settles current source and the owner writes its memory note; an
unsuccessful actual grade can then start one fresh standard recovery owner. There is
no discretionary consultation or model-selection action. Context maintenance is
model-authored replacement with immutable owned archives.

## Accounting and model-facing allocation

`Gateway.state()` retains `spent` as the legacy charged-or-reserved exposure for
recovery/accounting callers, and separately reports `charged` (settled receipts)
and `reserved` (quotes with no known charge). Current task status displays these
separately; shared-run status does likewise when the shared ledger exists. Unknown
charges remain unknown, not zero. A roughly $5.93 expert output reservation is
not evidence of $5.93 spent. This corrects a misleading decision surface without
treating a maximum-output reservation as a settled charge.

Current status also presents declared resources for the whole run: the spending
ceiling, headroom after known settled charges, the admission stop with the amount
remaining before it, task count and approximate time remaining. The shipped default identifies the course contract of $10, four hours
and 26 tasks. That ceiling is not a queried balance; a lower daily account balance
can stop the submission sooner. Development launches declare their actual settings
through `CS2680_RUN_RESOURCES`, including an unknown spending ceiling when none was
configured. In-flight and unknown charges remain separate from headroom.

One shared clock starts on first task delivery, slightly after the course opens
that task. It does not reset on new tasks, compaction or recovery. Opened-task and
exited-worker counts describe execution, not successful finalization or solves.
These observations use the existing current-state carrier, preserving tool
continuation and native reasoning rather than inserting a fresh user message.
The same continuation-safe carrier presents the current task allowance and its
upward-only policy. At 80% of that allowance spent it asks the owner to prioritize
verification and evaluation. This changes neither the native reasoning boundary
nor output allocation. Task limits survive compaction and recovery.

When the course endpoint explicitly refuses a request before forwarding inference
because its accounting is uncertain or its ledger cannot be persisted, the runtime
stops with an infrastructure outcome. Only that refused request has a known zero
charge; earlier unknown charges and their reservations remain unresolved. This
requires the complete structured admission-refusal response, without usage or
generation evidence. Generic service errors and incomplete or contradictory
responses retain their ordinary uncertainty. The owner does not retry a permanent
accounting refusal or enter recovery because of it. Its selected captured candidate
remains selected, and unfinished changes are retained separately without grading.

## The central distinction: an observation and its meaning

The runtime can establish that a command executed in a particular working
directory, produced certain bytes and exited with a particular status. It can
identify a saved source observation or the patch an official grade assessed.
Those are execution facts. Whether the output explains the bug, whether a check
covers the public requirement and whether the next edit is justified require
interpretation by the model.

This distinction should be visible without requiring a separate evidence-labeling
ritual. Results retain conditions and retrieval references. Summaries, notes and
expert advice remains an attributed interpretation. A path or hash locates an
observation; it does not certify the conclusion. Calling a summary “verified”
cannot make its unsupported assumptions true.

For example, a pass after changing test fixtures and a compile failure in an
original caller are different observations about different conditions. Preserve
both. The model should investigate the compatibility failure unless the public
task actually changes that caller's contract. The harness must not combine them
into an authoritative “verification passed” statement.

## Starting and continuing an investigation

An ordinary request contains the full system prompt, original public task/user
instructions, available tool schemas, current working history or continuation
summary, and essential runtime state. The model should be able to identify its
objective, its actual available actions and what work is already in progress.

The intended progression is evidence-driven rather than a fixed sequence of
runtime stages. The model grounds the requirement, locates real code, traces the
relevant path, chooses a useful check, edits and verifies. A failed experiment
can advance understanding. Repeating an operation can be appropriate after a
relevant change. The question is whether the next decision follows from what
was learned and improves the chance of resolving the original task.

A brief plan or visible note can preserve direction, as described by the supplied
prompt. It is useful when it connects findings to the next action. The runtime
does not require a new plan account, review form or reflection call on every turn.
Those mechanisms would consume attention without necessarily improving the
investigation. Preserve the prompt's actual workflow while avoiding extra rituals.

## Instructions in the complete request

`src/prompt.txt` supplies the system foundation and runtime adapter. The table
maps other information that can enter a request; it describes the current
implementation, not a requirement to preserve each surface. Read them together
when judging whether the model receives a coherent objective, clear actions and
useful feedback. A component's plausible purpose alone does not justify an
additional instruction layer.

| Surface | Source | Information supplied |
| --- | --- | --- |
| Engineering foundation and runtime adapter | `src/prompt.txt` | Establish task-solving behavior and how it fits this runtime |
| Tool and control schemas | `src/tools.py`, `src/context.py`, `src/agent.py` | Describe the actions actually callable in this request and their semantics |
| Maintenance instruction | `src/maintenance.py` | Ask the same owner to compact at the route's evidenced input threshold |
| Capacity-recovery rendering | `src/context.py` | Explain an actual provider rejection and make recovery actions usable |
| Investigation skills | `src/investigation-skills.txt` | Teach evidence-driven repair once in each owner or critique system request |
| Automatic critique guidance | `src/critique-prompt.txt` | Audit exact failed-attempt evidence and separate engineer notes from a narrow original-source brief |
| Fresh repair guidance | `src/validation-prompt.txt` | Teach recovery to derive and complete an independent repair from original source |
| Observations and current state | The modules owning execution, grading and rendering | Return facts and recovery guidance for what just happened |

Descriptions and validators must agree. An instruction to use a tool that is not
in the current schema, or a development override contradicted by the corresponding
control description, gives the model incompatible expectations. Inspect the full outbound
request when evaluating an instruction change. AGENTS.md instructs the engineer;
it does not itself become the solving model's system prompt.

The full system message survives maintenance and recovery. The immediate request
is appended with harness attribution through the provider-compatible message
carrier. This preserves the original obligations while making the current decision
clear. Replacing the foundation with a short summarization prompt can remove the
requirements the continuation most needs to remember.

## Tools: a clear path from intent to observation

The ordinary tools are `exec_command`, `job`, `list_jobs`, `edit_file`,
`read_file`. Repository discovery and search use shell commands. Control actions include
`evaluate_candidate` and `end_attempt`. There is no advisory or model-selection
control: the runtime owns the single automatic expert review. After a passing
candidate, or once an owner ends without one, `finish_task` replaces investigation controls. Context actions are
described below.
Use source schemas for exact arguments; legacy primitives retained internally or
in replay fixtures are not an additional ordinary tool menu.

Commands have explicit working directories, noninteractive execution and Bash
pipefail. Each invocation starts a fresh shell; changes to its directory or
environment do not carry into later calls. The advertised tool description explains
automatic stdout/stderr capture and paging with `read_file`: adding `head` or `tail`
merely to shorten a check can buffer or hide diagnostics, and `head` can stop the
producer early. When a shell exits, its remaining children are stopped. A persistent
service therefore runs in the foreground with a short yield and an owned job.
A shell lets the model express unfamiliar investigations without waiting
for a specialized wrapper. Structured reads and exact-text edits provide reliable
common operations and useful observations. Each tool should earn its place through
that benefit; overlapping names with subtly different semantics add confusion.

`read_file(path=..., search="...", context_lines=8)` searches an ordinary file or
an immutable captured output extent for a case-insensitive literal. Line ranges
bound matching lines; context may extend around them. A page selects up to twenty
matching lines, shows their original numbers and context, and provides the next
matching `start_line` when needed. Long lines receive a byte excerpt near the
match and an exact byte-read reference. The source hash and searched extent remain
explicit. A current owned job observation is separate from the captured prefix;
historical output does not acquire a current job verdict. No matches is not a
test pass. Command results show an actual search call so another view of output
does not require another execution. They label the `.extent.json` path as a
saved-output reference: `read_file` resolves its exact captured bytes, while shell
reads of that path return the JSON descriptor rather than stdout/stderr. Job
inspection also supplies a shell recovery command for the captured bytes. Full
output remains retained independently of the displayed page.

An oversized command observation shows bounded beginning and ending excerpts of
the captured output, with explicit source byte ranges and an omitted-middle
notice. This makes opening context and terminal diagnostics available together
without filtering the command or requiring another execution. The ending excerpt
belongs to that immutable capture, not to output appended afterward. A running
job still has no completed verdict. Small observations remain contiguous, and
`read_file` remains the way to retrieve an exact contiguous range or search the
saved output. Inspecting a job does not advance its wait cursor; waiting reports
the newly captured interval, including when its middle is omitted from display.

`edit_file` replaces exact UTF-8 text in an existing file. Zero matches or multiple
matches leave it unchanged; the result reports the count and offers a nearby
line or matching locations for repair. `replace_all=true` explicitly opts into
replacing every match. Successful edits show numbered changed regions and retain
exact pre-write bytes. Creation, renaming and deletion use `exec_command`; there
is no second patch grammar in the ordinary menu.

A result needs to support interpretation and the next useful action:

| Operation | Information the model needs | Reason |
| --- | --- | --- |
| Read source | Path, displayed range, relevant totals and recoverable exact evidence | An excerpt must not look like a complete file; a saved read may differ from later source |
| Edit exact text | Match count, changed regions, failure explanation and retained recovery evidence | The model must know which repository state to reason about next |
| Run a command | Working directory, completion state, exit status, output and job/retrieval references | Starting a check is different from obtaining its result |
| Inspect/wait for a job | Owned identity, current state and captured output | Quiet output does not establish a hang or authorize another launch |

Line reads disclose possible UTF-8 replacement characters, retaining exact source
bytes for recovery. Preview pagination counts consumed source bytes independently
of the replacement text's encoded size; invalid bytes are not silently dropped.

Yielding is an observation interval, not cancellation. The job can continue after
the tool returns. A cleanup failure retains ownership and any known shell exit,
without claiming the process group is stopped. Cleanup retries preserve the
original cancellation or natural-completion reason. Waiting, inspecting and
stopping are distinct operations. Output pages preserve the complete underlying evidence outside the task repository.
Pipelines or display filters must not turn a failed build into a seemingly
successful result or destroy the diagnostic the model needs.

Exact-text edits validate the old text before mutation. Missing or ambiguous
matches leave the file unchanged and identify how to refresh or disambiguate the
selection. Applied edits return numbered changed regions and retain pre-write
bytes for recovery. Shell commands create new files.

Every tool or context change needs an example of the delivered result and an
assertion on the next serialized request. Check the path that motivated the
change: long output, a mismatched edit, an unfinished command or a rejected read.
Internal metadata alone does not show that the model gained the missing information.

## Working understanding and exact history

A long investigation has two different needs. The working view should concentrate
on what matters now. Exact original observations should remain available when the
model needs to challenge its understanding. Keeping every detail in every request
burdens attention; destroying originals makes a mistaken summary hard to correct.

Current status identifies this owner's latest repair receipt and says explicitly
when there is no full pass. The prior owner's fallback remains private to the runtime. Current source remains
unverified against a grade until the boundary actually captures those bytes;
ordinary status does not manufacture equivalence after later edits. Ownership, task/shared accounting, owned jobs and this owner's exact receipt
paths remain available. Asynchronous course reporting and its spool diagnostics
stay in runtime evidence rather than becoming repeated solver obligations.

Requests retain full displayed results for the latest ten tool-bearing
exchanges in the active history. One exchange is a model's tool-call batch and
its results; multiple results in a batch count once, and prose or harness-only
events do not advance the window. Older result bodies become short receipts
only when the receipt is smaller. The receipt identifies the operation, its
recorded outcome and exact retrieval paths. Commands in receipts use bounded previews; complete arguments
and tool-call/result identities remain in the conversation. Shorter original
results stay intact. Assistant text, original instructions, continuation summaries
and current execution state are unaffected. Once a grading event is older than
the same recent-tool window, its explanatory guidance becomes a factual receipt:
candidate identity, counts, whether an evaluation actually started, and exact
retrieval paths. The complete event remains in its immutable exchange archive.

This is a presentation rule, not a freshness judgment or a model-authored summary.
The runtime neither tracks whether an old read is still applicable nor reruns an
operation. The model can read current source or execute a new check whenever it
needs current information. Historical evidence remains available when the question
concerns an earlier observation; retrieval is not a prerequisite to new work.
For example, an old test log can become a receipt saying `earlier exec_command
result; output omitted from this request`, with its recorded exit code, working
directory, job identity and saved-output path. A new invocation produces a fresh,
fully displayed observation even if its command is identical.

`compact_context({"summary":"..."})`, called alone, replaces completed investigation
history and all previous working summaries with one model-authored continuation
summary. The runtime chooses a complete exchange boundary. Prior discussion,
source reads, results, selected excerpts, summaries and startup observations leave
the working presentation. Original user instructions, the full system prompt and
essential current execution state remain. New exchanges accumulate afterward.

The original evidence stays immutable in the archive. `search_history` searches
decoded conversation text, tool arguments, result metadata and saved summaries
with case-insensitive literal matching. Quotes, backslashes and newlines are text,
not JSON escapes the model must reconstruct. Matches identify their original
record, field and role so a tool observation is distinguishable from a model claim
or prior query. Search pages show bounded command previews; complete command text
remains in the original exchange rather than being reinserted through metadata.
The search does not scan undisplayed bytes in complete saved
outputs; a zero result does not establish their absence. Locate the command or
result, then use `read_file` on its saved output. `read_file` also retrieves original
exchange records and saved summaries. Notes on disk remain ordinary files and are
not automatically appended to every request. Writing another note beside unchanged history does not
perform the intended replacement.

The only context-specific model actions are `compact_context` and `search_history`.
The rich summary guidance on `compact_context` in `src/context.py` owns the
continuation guidance for initial solving, fresh recovery and capacity maintenance.
It explains voluntary use when an investigation changes direction, preservation
of exact obligations and unapplied corrections, and a continuation that enables
the next useful action. An unexplained failed grade remains unexplained in the
continuation: a suspected cause must not erase changed behaviors that have yet
to be exercised. The runtime adapter and recovery prompt refer to that
shared guidance instead of maintaining separate summary checklists. The owner
retains the actual caller, input, source conditions and observed result for a
consequential check. It does not assign hidden entries to named passing or failing
behaviors. Continuation resumes the preserved next action, retrieving details
where needed rather than starting repository orientation again.

The summary remains unrestricted in length, free-form and model-authored. The
runtime neither certifies its interpretation nor requires a reporting form.
Original requirements, current working source, saved candidate identities and
historical observations have distinct roles; reducing request size does not
establish that the summary preserved those distinctions. Assess what the owner
does after compaction, including whether it must reconstruct an investigation it
had already completed.

For illustration, a useful continuation might say that an adapted test passes,
an unchanged caller still fails against a named signature, and the next action
is to trace that caller using a saved error reference. “Implementation complete;
tests pass” would remove the very clue needed for repair. This example describes
the desired information, not a literal captured tool result.

## Maintenance, capacity recovery and their limits

Capacity facts describe different limits. A provider's advertised total context
window and maximum output are not the input capacity available with its default
allocation. The course's default allocation remains unknown unless the interface
or an observed response establishes it. An explicit provider rejection can
establish a narrower input boundary for that route and allocation; retain its
provenance instead of treating it as a universal model limit or deriving a
default from two advertised maxima.

Ordinary and maintenance requests show the approximate input size, the qualified
maintenance threshold, current execution facts and archive retrieval references.
Exchange counts stay in the internal history state; they are not presented as
capacity or progress indicators.

The runtime uses evidenced route capacity to schedule maintenance at 80% of the
applicable input boundary. Learned allocation facts can refine that scheduling
estimate. This is advance notice to the owner, not a local input-admission gate:
the provider remains authoritative, and an estimate does not independently
reject a request. A route change uses the destination's evidence. The crossing
latch avoids repeated maintenance for the same unchanged crossing. Voluntary
compaction remains available before this threshold; capacity alone does not
measure whether an investigation's working account is useful.

Once maintenance opens, the same owner may inspect owned immutable history,
saved summaries and owned job state, then must call `compact_context`. There is
no retain-context action. Repository mutations are unavailable during maintenance,
and prose alone cannot finish it. Finite protocol-failure handling prevents an
invalid response from trapping the owner in an endless maintenance exchange;
after three invalid responses, the task stops with an infrastructure outcome
and retains its selected captured candidate. Failure to archive the required
continuation also stops truthfully. Neither failure resumes the uncompacted
investigation or evaluates ungraded edits. This bounds repair of a broken
maintenance protocol, not ordinary solving turns or summary length.

Actual provider rejection is a separate event. The runtime can temporarily
project archive pages or omit native reasoning from the rejected prefix to make
the compaction request deliverable, preserving the full system foundation and
exact originals. That temporary projection ends when the owner commits its new
working view. The rejection episode remains recorded until an ordinary request
using the new view is accepted. A changed view, an accepted request and a useful
continuation are separate observations; none should be inferred from the others.

Neither path adds a default generation ceiling or an economic output limit.
Omitted `max_tokens` remains omitted; a learned input boundary informs context
maintenance rather than silently changing how much output a response may use.
After a captured pass, the normal next action is the direct memory note.
Threshold maintenance does not delay it; an actual provider capacity rejection
can still require compaction before the note can be delivered.

Inspect the serialized maintenance request, model-authored summary, next ordinary
request and following action. Establish what remained, what left presentation
and where exact originals remain retrievable. A smaller temporary projection is
not evidence of model-authored compaction or improved engineering judgment.

## One automatic expert review

The first unsuccessful actual grade of the initial owner requests one tool-free
expert critique, including when that grade occurs at `end_attempt` settlement.
The trigger belongs to the task, not a conversation revision or repeated grade
receipt. Recovery grades, compaction, unchanged-patch receipts and later initial
grades cannot create additional reviews. Shared execution admission and honest
failure accounting still apply; unavailable review evidence does not become an
invented successful review.

The critique receives the complete public task, exact failed patch and its actual
grade. The exact patch appears once. A disposable Git index identifies original
source regions around its hunks, with 20 surrounding lines, separately from the
patch; mutable working-tree bytes cannot replace the graded candidate. Binary
changes retain their exact patch without invented textual source regions.
The record includes all recorded command identities with their latest
observed states and outputs for every completed nonzero command plus the last four
observed commands, once per job. Later successful listings or formatting checks
must not displace the diagnostics from an earlier failed build. Nonzero status
can also mean a search miss or an interrupted pipeline; it does not classify an
assertion failure or establish that a historical issue remains unresolved. The
reviewer interprets it alongside subsequent evidence and source conditions.
The packet identifies the triggering candidate as the capture boundary: all
selected observations precede that capture, and no further owner action occurs
before the synchronous review. This rules out treating the final local checks as
post-capture owner work. It does not establish each command's exact source version
or rule out background filesystem changes.
Recency follows the latest job observation, including a later completion of an
earlier background job. An inspection during context maintenance still resolves
the original captured output, separately from its bounded observation receipt.
The record includes the latest saved observation for each of the last eight distinct repository files
read with `read_file`, plus separately identified original-base content at those
same line coordinates where available, rather than appending whole original
files. Those coordinates are explicitly not an edit-aware mapping. Identical
command output appears once, with later jobs referencing that included content;
their commands, exit states and provenance remain distinct. These mechanical
selections are not a claim that the most relevant tests or callers were read.

Each selected output or source observation is complete when at most 16,000 bytes;
larger values carry the first and last portions, explicit omitted-byte counts and
an identity for the full selected original. Source read ranges remain ranges,
not whole files. A saved file observation's hash identifies those observed bytes,
not the whole candidate patch or necessarily current source. A command's observed
state does not invent its later completion. Missing originals and unavailable base
files are labeled rather than filled from current source.

The input contains no owner narrative, inferred test relevance, model-written
handoff, hidden diagnostics or reference patch. The expert cannot retrieve the
provenance paths or run tools; it must acknowledge gaps. The record is explicitly
attributed evidence with a following instruction to return the two routed sections,
so quoted task or command text does not request continuation of a transcript.

The continuing engineer receives **NOTES FOR THE ENGINEER** alongside the failed
grade. These notes may discuss its candidate, unsupported assumptions, unexercised
changes and concrete discriminating checks. The review leads with the most
consequential unsupported assumption, a credible alternative, and a small check
through the actual caller or producer that distinguishes them. It explains what
the outcomes imply, or identifies missing evidence needed to construct that check.
It does not create an exhaustive review checklist or an approval stage. The narrow
recovery brief pairs established original-source locations with useful first
checks and may explicitly say there is no additional supported orientation.
The notes are attributed, unverified
interpretations. A useful review should change an informative next action; the
expert's confidence cannot discharge a local counterexample. Failed grading
still leaves the initial owner free to repair. An ending initial owner needs no
extra inference to prepare a handoff.

The runtime retains **BRIEF FOR AN INDEPENDENT DESIGNER** for possible recovery.
It may name public contract distinctions and established original-source entry
points, producers, consumers and tests. It must not describe the failed design,
quote the old transcript, prescribe a replacement architecture or link into the
failed attempt. The distinction is about the source of a claim, not whether a
file happened to be edited previously. A neutral fact about the original version
can be useful; a candidate-derived diagnosis cannot be smuggled in as a task
requirement. Missing or unusable brief material leaves recovery with the complete
public task and original source.

Both owners remain standard. The expert review has no tools, cannot take ownership
and is not a parallel repairer. Its usage, including unsuccessful or uncertain
usage, is charged to the same task and shared accounting boundary. There is no
separate expert or recovery allowance. `investigation-skills.txt` is included
once in each assembled owner or critique system request; the review and recovery
prompts add only their distinct role and evidence rules. This avoids three copies
of the same skills in a recovery context. The shared caller guidance covers both
directions: a helper-only pass may miss the application path, and an application
pass may rely on setup that a separately promised public operation must not need.
Changing a probe's starting conditions does not repair the original failing
public call sequence.

## Verification, grading and finishing

The public task and established interfaces define the target. Local tests can
provide detailed feedback, but their meaning depends on what they exercised.
The official grader restores original test code, applies its own test patch and
checks the captured candidate in a pristine environment. That is an execution
fact, not a rule that old callers outrank an explicitly required API migration.
When the public task changes an interface, a local test may need adaptation to
exercise the required interface while retaining useful behavioral coverage. When
the task preserves the contract, original callers remain compatibility evidence.
Neither a changed test nor a blanket declaration that old tests are obsolete
establishes correctness. Preserve contrary observations with their conditions.
A dependency installed only in the live sandbox or a modified fixture does not
establish that submitted source works in the grading environment.

The adapter states the fixed test-code reset contract before work begins. On a
failed submission, the receipt identifies only candidate paths matching that
rule: test/spec directories and standard test filenames, with data/fixture
parents exempt. These paths come from the solver's captured patch, not hidden
tests or diagnostics. The verifier subsequently applies its own tests, so the
list does not identify what failed or promise that all original tests run. It
does not reset the live sandbox or alter the submitted patch. An unchanged-patch
receipt keeps the same candidate-specific information. The useful next action is
to check the required interface and real callers, distinguishing intentional
migrations from required compatibility. Modified tests supply local evidence only;
aggregate counts do not establish successful compilation.

`evaluate_candidate()` optionally requests feedback during investigation. It
evaluates the exact current captured patch and returns its identity and official
counts. Failure leaves the investigation open, while an unchanged patch
is not graded again. Passing selects the successful candidate and opens the direct
memory-note finishing path. Aggregate counts do not reveal hidden case names or distinguish all setup failures from assertion failures.
Preserve that limit without ignoring the failure's significance.
Feedback names the captured candidate and exact patch hash. Course evaluation
ordinals include private restoration operations and remain receipt provenance;
they are not presented as the number of different repairs.
The readable candidate JSON binds the original course result to its exact patch,
base, candidate ID and course attempt. Its `artifact_retention` describes storage;
the separate `evaluation` records completed execution, the unchanged course
result, purpose and elapsed boundary time through receipt arrival, before artifact
copying. It describes those captured bytes, not the
current working tree (`current_workspace_identity` is `not_checked`).
Reusing an unchanged candidate returns the same historical
receipt without another evaluation. Private fallback receipts remain outside the
recovery owner's evidence.

After a completed failed grade, the attributed grading event asks the owner to
question what its checks failed to establish. It encourages a check of the real
producer, caller or lifecycle with meaningful source conditions, without requiring
a fixed test ritual or inferring hidden assertions. Repeated same-idea failures
are evidence that the explanation is insufficient; equal counts do not establish
a shared defect or require automatic abandonment. Counts alone never trigger a
build-failure diagnosis or a mandatory compile-before-logic stage. The event
suggests a focused original-caller build or import check when an interface or
dependency changed, as a way to distinguish compatibility problems from incorrect
behavior. Shared and ecosystem guidance preserve the same uncertainty. At the
first actual initial failure, the automatic critique adds its engineer notes if the owner continues.
The ordinary coding menu remains available. Compaction stays optional when
accumulated narration obscures the next useful action.

`end_attempt(reason="...")` means that the model ends its investigation, whether
it considers the repair complete or cannot make useful progress. Candidate
settlement belongs to the harness: capture the current source, grade new nonempty
bytes once or reuse an exact prior receipt, then choose the next workflow state.
Every end opens the memory-note phase, passing or not. An unsuccessful initial
attempt with an actual grade preserves its captured candidate and enters fresh
recovery if shared execution still admits work. The owner need not request an
evaluation first, explain a hidden failure, or write an outgoing handoff.
Recovery ends through the same settlement path, finalizing its latest actually
graded candidate, whether fully passing or partial. Until recovery obtains a new
actual grade, the initial candidate remains selected. Recovery cannot start another owner.

An unchanged empty base without any actual grade ends with no candidate and no
recovery. Capture or evaluation-preparation errors before grading return actionable
feedback and leave the owner active. An uncertain grade stops as an infrastructure
outcome; it is not an unsuccessful candidate receipt or permission to replay the
operation. Recovery preparation failure is also distinct from a model give-up.
External shared stops, platform deadlines and cancellation preserve the selected
captured candidate without newly grading unsubmitted source. Local allowance
exhaustion and platform funding refusal instead settle current source after
process cleanup, as described in the budget contract above. A historical reused
receipt does not select older bytes. Neither stop path starts recovery. Ordinary
prose does not grade or terminate.
When an owner ends through a pass or `end_attempt`,
`finish_task(memory_note="...")` retains its memory note and finishes without
another evaluation. Memory inference uses the same task allowance and
shared budget. Exhaustion, including a platform budget refusal, does not open
or extend this phase: no additional note inference is admitted. A response
admitted before exhaustion remains uncapped, including an already admitted
finishing action. Its description asks a failed owner for what
holds regardless of the failed repair: how to run checks, where behavior lives,
environment quirks and ruled-out explanations, with remaining ideas labelled
unverified. Its restricted menu permits targeted
evidence reads, inspection or cleanup of owned jobs, and context maintenance when needed, while source
editing and new grading are unavailable. The owner writes the note
from its completed investigation instead of beginning a repository survey. The
passing captured candidate is already safe if a shared stop prevents the note.

Cross-task memory retains, for each task finished during the current run, passed
or not, the public task text, the complete note authored by its last owner and the
exact selected diff, marked passing or not passing, with runtime repository/task provenance and the captured passing
candidate's identity and counts. Each record's provenance carries an identifier
created when the submission process starts and hashes of all three parts; only
records written by the current process and unchanged since are presented, so
files left in the memory directory by any other run are ignored. New tasks
receive a compact index with same-repository entries first, each entry listing
the earlier task's title and the files its diff changed, so overlap with the
current repository is visible before anything is opened. Complete records are
copied into owned, versioned sandbox evidence and retrieved with `read_file`
when useful. The task text and diff are exact; the note is a historical
interpretation. None of them is a new requirement or knowledge of hidden
assertions. Success-finishing asks for a detailed summary of the public
requirement, relevant implementation and callers, evidence and correction,
actual checks, useful rejected explanations, and remaining limits. Because the
diff is saved automatically, the note explains what the diff cannot show rather
than transcribing it. There is no summary-length cap or mandatory form. A
passing candidate does not establish a universal architecture rule.

Transparent development can use known tests to understand how the model gets to
its answer. Any submission override must align instructions and tool schemas and
be recorded as an explicit experiment. Hidden developer diagnostics remain outside
autonomous inputs. Production continues to use the official grading contract.

## Provider reasoning and execution continuity

Native reasoning fields, visible attributed text and archived-only evidence are
different surfaces. Their replay depends on the verified profile and message
placement rules. Another model's reasoning is not native continuation. Inspect
serialization and actual provider support before asserting that reasoning carries
forward; a field in an archive or metric does not establish model access or use.

Validate a complete response before executing its action batch. Interrupted or
contradictory streams cannot authorize tools. Definite pre-generation rate limits
can wait and retry; uncertain inference, tools and grades require their own
receipt handling. Preserve raw evidence and valid usage rather than treating an
uncertain timeout as proof nothing happened.

These protections serve the model's ability to reason about a real environment.
The practical acceptance question remains: after an observation, compaction,
expert review or failure, does the model retain the right obligations and evidence,
choose a useful next action, and move the original task toward a checked solution?
Use DEVELOPMENT.md to investigate that question and compare other leading harnesses
at the same model-facing boundaries.

## One fresh recovery attempt

A difficult task receives at most one automatic fresh standard recovery owner.
The transition requires an explicit initial-owner `end_attempt(reason="...")`, an
unsuccessful actual grade after its candidate is settled, and continued admission
by the shared submission controls. The required grade may have occurred during
investigation or as part of ending the attempt. Failed evaluation while working
leaves the initial owner free to investigate and repair. Cancellation,
deadline/shared stop, provider failure, and environment
failure keep their own truthful outcomes; they do not impersonate a decision to
give up. Recovery has sole ownership and ordinary coding, grading, context and
completion tools. It is not a contributor waiting for an earlier owner to act.

Before transition, the runtime preserves exact graded patches and receipts,
the selected fallback identity, distinct ungraded outgoing state and original
archives. These remain private to runtime finalization and developer inspection.
Recovery starts in an isolated workspace at the exact original source base. Its
tools cannot reach the initial owner's source, Git state, patches, history,
receipts or outputs. There is no historical index or path remapping into that
attempt. Recovery can read original source and its own subsequent evidence; it
cannot adopt the failed patch by discovering it through another path.

Preparation restores the complete pre-owner workspace at its original path,
including Git state and legitimate ignored/dependency files. This preserves
editable imports and absolute links while removing outgoing source changes.
Initial runtime evidence is exported privately before its exposed files are
removed; recovery owns a fresh evidence root. Intake inventories remove newly
created scratch entries without deleting pre-existing scratch or dependencies.
This is owned lifecycle cleanup, not a new OS boundary or rollback of arbitrary
changes outside the owned environment. Preparation is an initial observation
before the first action, not a current-source claim repeated after later edits.
Its original remains retrievable from recovery's own archive after compaction.
Continuing state identifies the recovery investigation and its own latest
candidate; compaction resumes that owner and workspace, without preparing the
original base again or starting another owner.

The first request contains the public task, unchanged main foundation and runtime
adapter, shared investigation skills, recovery-specific guidance, accurate
workspace/base facts, and any usable original-source brief. No previous diagnosis
is placed in authoritative instructions. The brief is separately attributed as
unverified reviewer guidance beside the public task, once per request. It survives
voluntary and capacity compaction independently of the owner's replacement summary;
historical preparation facts still leave presentation after compaction. Later
observations and the owner's revised understanding can contradict the advice.
Actual provider-capacity recovery can temporarily page this reference, or leave
only its exact archive path after repeated rejection; the ordinary view restores
the complete brief. It is archived with intake metadata and remains retrievable
through the existing history/read tools. Recovery derives the required behavior independently,
follows real producers and callers, checks exact public interfaces, preserves
contradictions and turns concrete findings into edits. It receives no new expert
call, approval stage, mandatory checklist or formal review obligation. Its ordinary
source work, grading and context tools support a complete second investigation.

Recovery's own commands, original outputs, summaries and grading receipts remain
immutable and retrievable through `search_history` and `read_file`. Compaction
preserves its unresolved obligations and current execution facts without importing
the previous owner's account. The retained initial candidate identity stays in private runtime records rather
than being exposed as repair context.

Recovery takes responsibility for the final submission as well as the investigation.
The outgoing graded candidate remains selected until recovery obtains a new actual
grade. Each completed recovery evaluation selects its exact captured bytes, even
if the partial count is worse. There is no score ranking or candidate-selection
tool. Reusing an older candidate's receipt is not a new evaluation and does not
replace the latest actually graded candidate. The initial candidate remains private
unless recovery independently reconstructs those bytes.

Failed recovery grades leave that owner working and do not trigger fallback
restoration. The course's latest evaluated candidate is already the selected final
artifact. A full pass secures that candidate and opens memory-note finishing.
Shared stops retain the selected captured bytes without grading unfinished edits.
An explicit end_attempt settles new current bytes before finishing; ending at the
unchanged source base does not replace an existing graded candidate with an empty
patch. Preparation failures and uncertain grading never manufacture a selection.
An already admitted grade that completes during shutdown is reconciled before
finalization and becomes the selected actual candidate. A genuinely unknown grading
outcome is neither replayed nor claimed released; external deadlines provide no
atomic finalization guarantee.

Recovery does not acquire a new task, reset spending, extend the deadline or
create a separate allowance. Its main calls, maintenance and retries
use the existing shared boundary with usage uncertainty preserved. Fresh-owner
accounting may distinguish phases for analysis without granting new resources.

Development-only recovery replay and its separate experimental packaging are
described in DEVELOPMENT.md. That adapter is outside the submitted runtime.

### Repository language guidance

The shared investigation guidance is followed by guides selected from manifests
at the repository root, for both owners and the automatic expert review. Go uses
`go.mod`; Python uses its project/test manifests; `package.json` selects
TypeScript/JavaScript, and a React entry in a dependency, development-dependency
or peer-dependency mapping adds React. Monorepos often declare React only in
workspace packages, so manifests two directory levels down (`*/*/package.json`,
outside `node_modules`) also count. Unreadable, invalid or non-object package JSON supplies no React signal;
malformed dependency sections are ignored independently. Other valid ecosystem
signals still apply, and repository metadata cannot abort prompt assembly through
an unexpected JSON shape.

Both owners also receive a system-prompt catalog listing all four guides and
which are already included in full. Complete copies live at the owner's
`runtime/language-guides/`, outside the candidate repository. When detection
misses an ecosystem, the owner can use ordinary `read_file` paging to read its
guide. The catalog survives compaction with the system prompt; retrieved guide
text follows ordinary conversation retention and can be read again. Fresh
recovery installs its own copies and catalog. The tool-free expert receives
selected guides in full without a catalog promising unavailable retrieval.

The guides provide language-specific explanations, worked examples and commands
that make a useful investigation concrete. Their diagnostics start from observed
source, compiler output, exceptions, callers and event behavior. Aggregate scores
cannot supply those observations. The guides do not require a test-restoration
ritual before each evaluation or prescribe a fixed inventory of probes. The owner
chooses a check for the uncertainty it can resolve, preserves full output and
carries useful evidence forward as the repair changes. Examples are adaptable
engineering tools, not a compulsory task sequence. The shared opening explicitly
gives the owner responsibility for choosing its approach: make explanations
testable, question the probe as well as the patch, and revise assumptions when
observations contradict them. This does not require a reflection report or an
exploratory stage before an already justified edit. Supported behavior matters
more than preserving a particular design or following a guide's suggested route.

These guides teach concrete investigation techniques, not knowledge of hidden
assertions. Repository conventions suggest checks; public requirements and actual
callers establish the contract. Shared guidance owns grading and intentional
interface-migration policy so individual language guides do not redefine it.

Language guides describe ecosystem APIs and investigation techniques. Application
interfaces, supported configuration sources, dependency substitution and error
representations must be established from the current public task and repository.
Examples do not prescribe a particular application architecture or imply knowledge
of unseen test contents or grader retry policies.

### Coordinator skip policy

`src/agentic_loop.py` owns the explicit one-based leaderboard skip list, currently
2, 4, 9 and 10 (dispatcher IDs 1, 3, 8 and 9), leaving 22 tasks eligible for solving.
The coordinator registers each acquired
task, then checks its dispatcher identity before invoking any solving workflow.
Skipped tasks go directly to `dispatcher.done(..., reason="policy_skip",
iterations=0)` with no patch, inference, evaluate request, review, recovery or memory note.
The course still starts a sandbox during acquisition; successful finalization
releases it. Its finalizer records an empty-patch failure through its grading
bookkeeping, without running candidate tests or receiving an evaluate request.
Only a confirmed finalization completes the ledger entry. Known skipped IDs
receive zero allowance and are excluded from fair-share calculations even before
delivery; completing a skip does not manufacture savings or increase ceilings.
A failed or uncertain finalization stops coordinator admission through the
existing error path and is not recorded as successful cleanup. An empty skip
set disables the policy. Development manifests can renumber tasks; this policy
uses delivered dispatcher IDs, never benchmark instance IDs or worker order.
