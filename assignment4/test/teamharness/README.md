# Tests for dependable investigations

## Default development workflow

Start with the implementation: trace the relevant entry point, callers, state
owners and effects; inspect original requests/receipts; reason about the concrete
success, failure and interruption paths. Use existing evidence before launching
new processes. The goal is a dependable implementation and a clear explanation
of its behavior, not a large number of passing tests.

Do not run full test discovery, broad module groups, duplicate baseline suites,
mutation screens or extended generated tests as routine development or review.
Do not launch them in the background or alongside another chat's test run. Those
campaigns require an explicit user request; a request to verify a change or look
for submission blockers is not a request for a broad test campaign.

If execution is needed to answer a specific unresolved question, choose one small
reproduction, public-path check or named test case. Identify the question first,
read the actual result and stop when it is answered. Do not automatically broaden
the run for reassurance. Documentation-only edits need a diff review; do not run
tests or install dependencies for them. Do not add tests merely to mirror a simple
implementation change.

The existing suite is retained for targeted diagnosis and explicitly requested
test work. Its presence does not create an obligation to run or maintain unrelated
tests for every change. Linux environment qualification and course launch checks
remain deployment work, with their own purpose and evidence scope.

The stable contract is model freedom with truthful, retrievable evidence. Tests
should establish what happened to actual files, processes, source artifacts and
provider requests. They should not require a particular investigation sequence,
prose formulation, model judgment, sampling default or internal module layout.
Scripted models establish runtime mechanics, not repair quality or model competence.

Keep distinct contracts distinct:

- Candidate identity includes original source, captured bytes and exact replay.
  Initial human edits, private copies and unrelated live drift must be preserved.
- An effect identity cannot authorize different input. Reopening observes original
  receipts and uncertainty without automatically executing an old effect again.
  A new explicit action is distinguishable from replay of an interrupted action.
- Model reports, summaries and guidance cannot become runtime observations or
  erase failed checks. Evidence states its candidate, check and environment scope.
- Continuation preserves human inputs, session identity, original observations and
  actual external limits. Complete tool exchanges remain reconstructible.
- Independent effects retain independent outcomes. Human stop prevents new work;
  an agent return or a response wait does not imply that its shell jobs exited.
- Public CLI and packaged course paths execute the maintained runtime, preserve
  exact submission identity and reject incompatible records before effects.

## Maintaining the suite

Start with the consequential behavior each test protects. A useful test explains
what observable mistake it would catch: accepting different source bytes, losing
a human requirement, replaying an uncertain command, hiding a failed execution,
or applying a configured policy incorrectly. A fixed value is appropriate when
it is an explicit fixture input or the intended public contract. It needs a reason
when it comes from today's default, prompt wording or private storage layout.

When a test fails, inspect the responsible implementation and original request or
receipt before editing the expectation. Decide whether the product regressed,
the policy intentionally changed, or the fixture assumed an incidental detail.
A scripted provider that loops on a new observation heading is a fixture defect;
a receipt claiming an unexecuted command passed is a product defect. Do not treat
every failure during a refactor as evidence that the test is stale.

Use these maintenance choices:

| Situation | Preferred change |
| --- | --- |
| A new planning or completion policy supersedes an old one | Test each supported recorded policy explicitly; put the new public default in a focused entry-point witness. Unrelated tests select the policy they need. |
| Guidance text or presentation changes | Assert delivered fixture content, authorship, scope, provenance and retrievability. Reserve exact wording checks for text that is itself the required output. |
| A sampling default or implementation directory changes | Preserve explicit inputs and compare recorded settings or artifact identity across execution and continuation. Keep a separate default test only if that value is an intentional contract. |
| A long scenario repeats focused checks | Identify its unique interaction. Keep a short composition witness if needed; otherwise remove the duplicate and name the retained tests covering its contracts. |
| Concurrency makes a fixture unreliable | Coordinate the relevant boundary with events or observable effects. Avoid inferring ordering from a sleep or formatted status message; retain executor and effect-count assertions. |
| An uncertain effect or corrupted artifact makes progress fail | Keep the safety assertion and a separate reachable recovery/success witness. Never relabel missing evidence as success to make the flow finish. |

Generalization must still discriminate correct behavior from a regression. Do
not replace a specific policy expectation with "either old or new is fine,"
remove a failing field from the comparison, or derive the expected result by
calling the same production decision function. Prefer independent inputs and
observable files/process outcomes. Generated properties help explore variation;
small concrete witnesses establish that important successful paths are reached.

For a pruning change, leave a concise explanation of what was removed, why its
assumption was incidental or redundant, and where the consequential coverage
remains. The existing guide or change description is sufficient; no new registry,
fixed report format or target test count is required. Select a focused check only
when it resolves a concrete uncertainty. Broad runs and mutation campaigns require
an explicit user request; a pruning task does not authorize them automatically.
Report missing fixtures, warnings and source scope honestly. A check against an isolated snapshot does not validate
other contributors' later or uncommitted changes.

## Locate evidence for a concrete question

Use the responsible production code and archived observations first. Existing
tests can help locate small fixtures or reproductions when execution is needed:

| Question | Implementation and evidence to inspect |
| --- | --- |
| Which source bytes were captured or replayed? | `candidates.py`, `workspaces.py`, candidate hashes and replay receipts |
| Did a tool execute, partially write or remain uncertain? | `tools.py`, `parallel.py`, `patching.py`, original intent/result receipts and actual files/processes |
| What reached the model? | `session.py`, `context.py`, `presentation.py`, saved requests and read-only request reconstruction |
| Who owns the investigation after feedback or interruption? | `main.py`, `agents.py`, persisted workflow and return/control records |
| Which checks or grades apply to a candidate? | `repair.py`, `completion.py`, candidate/plan identities and original execution/grader receipts |
| What will the course package execute or submit? | Builder, course adapter, package manifest, worker configuration and submission identities |

When a named test is needed, read its fixture and invocation before running it.
Do not substitute a whole module or discovery run for identifying the relevant
behavior. Use an already-qualified test environment; do not spend routine
implementation time installing or repairing test infrastructure just to run a
suite. Missing test infrastructure is a verification limit to report, not a
reason to launch more work.

Planning, allocation and completion policies may change. Their tests should name
or supply the policy they exercise, distinguish historical continuation from new
run defaults, and verify consequences through actual requests/effects. Do not
turn an old automatic takeover, a ten-response default, a draft-plan ritual or
missing optional checks into a general integrity invariant. Keep current policy
witnesses alongside the responsible implementation rather than weakening them to
accept either outcome. Historical experiment reports retain their original scope.

The real CLI/HTTP repair, review, compaction and checkpoint flow remains in
`test_main_flow`. Focused HTTP tests also exercise concurrent calls, source edits,
guidance retrieval and exact-source checking. `audit_requests` independently
reconstructs complete archived requests and compares them to saved originals.
Retain these public-path witnesses: a green parser or prompt assertion does not
establish end-to-end behavior.

## Generated laws and sensitivity checks

Repair properties vary case identities, omitted executions, new failures/skips,
source changes, plan revisions and interrupted commands. The state machine uses
real Git, ToolRuntime, SQLite and processes. Explicit successful-path witnesses
prevent a system that rejects everything from satisfying safety properties.
`REPAIR_PROPERTY_PROFILE=extended` increases only this state machine's budget;
importing its module must not change unrelated suites' Hypothesis settings.
`REPAIR_PROPERTY_SEED` replays its generation, and `CONTEXT_PROPERTY_SEED` replays
the context state machine. Original evidence stays available after compaction.

Mutation screens and extended generated campaigns are retained for explicit
research/test assignments. They are not routine implementation verification.
Their programs are `check_repair_mutations.py` and `check_context_mutations.py`;
read their scope before any user-requested campaign. Import errors, timeouts and
syntax errors do not count as caught behavioral violations.

Full-suite discovery is deliberately not a recommended development command.
Prefer inspection, reasoning and the smallest execution that answers the actual
question. Report what was inspected or executed and its limits; avoid presenting
large test counts as a substitute for understanding the implementation. A focused
scripted-provider result does not establish live-model quality or GPU readiness.

## Pruning and handoff

The October 28 pruning pass removes three redundant test methods and their large
scripted providers. Coverage remains in the following real behavioral witnesses:

| Removed coupling or duplicate | Retained contract and location |
| --- | --- |
| Fifteen-stage `test_feature_flow` tour requiring helper/waits/groups/cancellation/compaction/history/checkpoint in one script | Actual HTTP multi-call execution and sibling failures in `test_native_parallel`; cancellation and persistent callbacks in `test_parallel`; immutable checkpoints, any/all waits and original archive retrieval in `test_coordination`; full CLI composition in `test_main_flow` |
| Fourteen-step fresh-owner error-recovery tour in `test_takeover_recovery` | Real patch rejection/correction in `test_patching`; generated capability/plan/partial-write recovery laws and interrupted owner creation remain in `test_takeover_recovery`; actual HTTP plan execution in `test_planned_repair`; misleading-summary/failing-evidence HTTP coverage in `test_context_properties` and `test_decision_guidance`; handoff identity in `test_completion_gate` |
| Permanent bans on two formerly removed CLI options | Public package import/help checks and incompatible frozen-workflow rejection before effects remain in `test_main_entrypoints`; read-only CLI behavior remains in `test_coordination` |
| Exact advice sentences, section names, chapter line 256 and publication file count | Frozen bytes/hashes, actual selected text, valid catalog links, dynamic line ranges, advertised capabilities and immutable resumed prompts |
| Hard-coded sampling temperature across unrelated tests | Explicit supplied settings, recorded settings on the actual course wire, and preservation of deliberately omitted backend defaults across reopening |
| Completion's private `test-plan/` directory layout | Original versus new execution receipt identity, scoped to the actual plan and candidate |

The busy-main fixture previously ignored only one runtime-message heading. New
job observations made it repeatedly launch a command instead of returning. It
now selects the latest non-system conversation message; executor exclusivity,
queued helper delivery and actual resulting files are still asserted. The repair
property module no longer loads a global Hypothesis profile during import.

When tests change, state the behavior they establish and retain the scope of any
observed failure. Historical test counts do not establish current correctness.
