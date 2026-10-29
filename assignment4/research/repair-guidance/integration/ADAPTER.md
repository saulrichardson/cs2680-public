# Adapt the guidance to a host without inventing its behavior

This package supplies authored investigation guidance. The host owns the actual tools, permissions, context assembly, workspace, artifact capture, evaluation, persistence, and role transitions. Integration should make those conditions truthful and easy to understand while preserving the developed prose. No library paragraph should silently become a new execution gate or an assertion that an unavailable capability exists.

## Keep the useful operating contract

Start from the host's current delivered request, including the system prompt, capability descriptions, runtime facts, tool schemas, and any role-specific instructions. Identify which passages explain actual behavior and which are optional advice. Preserve the instructions needed to use the current environment, including useful evidence distinctions that a replacement prompt could otherwise remove.

The portable system prompt can be reconciled into the host's foundation or included as an attributed investigation layer. Remove genuine duplication by retaining the fuller and more accurate explanation at one location. Do not replace the host's whole operating prompt merely to make a clean copy of this file fit. The earlier experiment changed both operating guidance and domain advice that way, so it could not isolate the contribution of the new lessons.

A host can adapt terminology while retaining meaning. If one tool both launches and waits for work, explain its real receipts. If another separates job creation and observation, explain how to continue the existing job. Do not infer an execution deadline from a response-wait parameter or a cancellation from a lost reply.

## Supply actual capability facts

Workspace facts should identify the assigned source, how paths resolve, what changes are captured, and whether dependencies, ignored files, or external caches accompany the artifact. A local conversation without Git should not receive a fictional patch base. A course task that replays a diff in a fresh environment should make that boundary explicit. The guidance's discussion of portability depends on those actual conditions.

Process facts should explain what starts a process, how its identity is retained, how output is retrieved, what a completion receipt means, and which lifecycle may end the process. Preserve uncertainty when an interrupted request may have executed. An active process should not become a hidden prohibition on source changes or finishing merely because accurate state is harder to present.

Evaluation facts should state whether a grader exists, how to invoke it, which candidate it evaluates, what its receipt establishes, and whether finishing itself evaluates or releases the environment. State any actual feedback, retry, continuation, or ownership policy. This package does not choose a failure threshold or decide when a fresh investigator takes over. An independent reviewer may have a different capability set from an owner; describe only what the supplied role can do.

Evidence and context facts should identify how original commands, results, source snapshots, user instructions, and model summaries can be revisited. Preserve their different authors and meanings. A model's interpretation of a failed grade should not be rendered as a runtime diagnosis. Context reduction should not claim an old effect did not execute or that a summary is equivalent to the original observation.

Permissions and external limits come from the host and user authorization. Registering a skill does not expand them. Present real constraints clearly instead of adding arbitrary tool bans, mandatory role sequences, reading quotas, or new approval stages as a substitute for understanding the environment.

## Make discovery visible

Copy the `solver/` contents as one read-only reference tree or expose equivalent files through the host's existing retrieval mechanism. Provide its actual root in the delivered context. Its internal links use relative paths and require no machine-specific directory. Preserve that relationship when packaging references outside the candidate repository so they cannot accidentally enter a submitted source diff.

If the host supports skill discovery, register each `SKILL.md` name and description and retain access to its body and supporting files. If it does not, deliver `SKILLS.md` with the system prompt or an appropriate authored-guidance message. The system prompt also contains concise file-level routing so a solver need not discover the catalog by guessing directories. The catalog's fuller paragraphs help select among overlapping subjects.

A registered name or a readable file is not proof that its body was loaded. Inspect an actual model request and a following execution that reads a relevant file. Where the host provides bounded reads, use its existing search or range tools to retrieve the named section. The reference chapters retain explicit anchors and headings for navigation; a Markdown anchor in a path is not automatically a supported file-tool argument.

Do not require every chapter to be loaded on every task. The investigator should be able to choose the material that helps resolve a consequential uncertainty. For a controlled evaluation of a particular skill, the experimental condition can deliberately include its body; record that choice rather than treating it as spontaneous retrieval.

## Package solver input separately from research evidence

Ship `SYSTEM_PROMPT.md`, `SKILLS.md`, and `skills/` from `solver/`. The publication manifest identifies these files and their hashes. Keep `evidence/`, this integration directory, the authoring analyses, raw traces, task identities, and golden patches outside the solver input. Human coverage records explain where a lesson came from; they are not context to give the solver an answer to an evaluation instance.

The solver tree is self-contained. It does not import a source module, call an adapter, or require the original repository or experiment archives. Ordinary file access is sufficient. Technical examples can require the relevant project runtime when executed, and the prose explicitly asks the investigator to inspect the installed version. The host should not represent those examples as pre-executed facts about a fresh task.

## Check the complete projection before claiming integration

Validate file presence, link targets, skill metadata, and the bytes actually packaged. Then reconstruct or inspect the real serialized request without executing effects. Confirm that operating instructions remain, runtime facts are accurate, the catalog and reference root are visible, and unavailable capabilities are not promised. Check the first request for a continued or fresh role as well as an initial owner where the host has those transitions.

A small scripted-provider exercise can verify delivery: obtain a real request, have the model read one selected reference through the actual tool, make a small source change, run a meaningful check, and return through the host's normal mechanism. Inspect the following receipt and artifact. This establishes integration mechanics, not that the guidance improves a live model's decisions. Behavioral evaluation is a separate claim described in [EVALUATION.md](EVALUATION.md).
