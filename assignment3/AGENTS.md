# Working on Assignment 3

Read the README, MODEL-EXPERIENCE.md, and the relevant architecture section before
changing the solver. Source and serialized requests establish implemented behavior;
design proposals and historical results are not competing runtime policies.

Keep execution state separate from model-authored interpretations. Original
observations must remain available after a summary or context transition. Only a
complete validated response may authorize tool execution. Preserve candidate
identity through edits, capture, grading, and recovery.

The course imports `src.agentic_loop.Agent`, constructs `Agent(dispatcher)`, and
calls `run()`. Only `src/` enters the submission package. Runtime code uses the
standard library. The course runner and task/grading assets are external resources.
Keep credentials and runtime evidence outside candidate source.

Preserve the engineering foundation in `src/prompt.txt` unless a change to it is
explicitly requested. Runtime behavior belongs in MODEL-EXPERIENCE.md; component
responsibilities belong in ARCHITECTURE.md. DEVELOPMENT.md explains focused
verification and the limits of checks available in this checkout.

Use a reproduction that exercises the affected boundary. Do not infer successful
tool execution from an instruction string, or repair correctness from a summary.
Documentation edits need content and link review. Do not launch paid inference or
course campaigns without an explicit request.
