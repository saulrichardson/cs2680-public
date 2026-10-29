---
name: investigate-with-evidence
description: Choose observations that distinguish competing explanations and revise an investigation after contradictory results. Use when a repair seems plausible but failures persist, a grade is unexplained, or repeated checks no longer change the decision.
---

# Make the next observation consequential

Use this skill when the obstacle is uncertainty about the cause, scope, or adequacy of a repair. A familiar pattern can suggest where to look, but it can also organize every later observation around a mistaken explanation. The purpose of an experiment is to create an opportunity for that explanation to lose. This is different from finding another example that the explanation can describe after the fact.

## Turn a symptom into alternatives

Start with the last observation you can state without relying on your theory. A request produced no stored record; a view did not update after a mutation; a converted value lost information; a grader rejected a candidate whose local checks pass. Each symptom admits several causes. The input might not reach the relevant code, the transformation might be wrong, a later layer might overwrite the result, or the check might be observing a different path.

Choose alternatives that imply different observations. If a value may be lost either at a wrapper or inside the consumer, inspect what the consumer receives rather than rewriting both layers. If an operation may have failed or merely completed late, observe its receipt and result rather than launching another copy. If the task concerns a change in behavior, a before/after comparison using the same meaningful input can distinguish the repair from an unrelated environment change.

An explicit table can help when there are several independent dimensions, but the useful content is the distinction, not the format. Write only enough to keep the alternatives separable. A large speculative checklist can make a simple uncertainty harder to see.

## Choose the boundary that contains the disagreement

A good probe has a controlled input, an execution path that includes the disputed mechanism, and an observable result whose interpretation is clear. Consider a handler wrapped by authentication and serialization. Calling an inner business function may establish its arithmetic while saying nothing about the authenticated caller's response. If the disagreement concerns the wrapper, exercise the wrapped handler and inspect the returned type, payload, and effect.

Avoid creating a substitute that contains the desired answer. Copying a few lines of the new implementation into a standalone script can help understand those lines, but it excludes failures in imports, decorators, configuration, callers, and state ownership. The probe should retain the boundary being questioned and simplify surrounding conditions. A fake backend is useful when it preserves the actual query or callback contract and makes the relevant effect distinguishable.

The related [contract skill](../verify-contracts-and-tests/SKILL.md) develops independent expectations. The [representation skill](../preserve-representations/SKILL.md) explains why a round-trip through real APIs answers a different question from testing an isolated string transformation.

## Preserve contradictions long enough to learn from them

When execution contradicts the theory, preserve the input, expectation, output, and candidate identity. Then reconsider both the theory and the expectation. A failing assertion may be stale, but that conclusion needs a reason independent of the implementation under test. A source comment, a reviewer explanation, or a green narrower check can suggest an alternative interpretation; it cannot erase the original contradiction.

For example, a converted object may preserve metadata on its members while losing metadata on the object itself. If the task requires both, observing member preservation does not justify dropping the container assertion. If the task only promises member preservation, the broader assertion may indeed be wrong. The decisive step is recovering the promised contract and checking the distinctions separately.

Keep the same counterexample when changing the candidate so the next execution can answer whether that problem was resolved. If a setup correction reveals a new failure, distinguish the two states rather than calling the later failure a regression automatically. The original source, installed dependencies, and command conditions can help establish which interpretation fits.

## Make evaluation feedback useful without inventing its meaning

An external evaluator, when the host provides one, reports a result for a particular candidate under particular conditions. Aggregate failing counts do not reveal the failed test, and a small count does not establish a small defect. One missing method or compilation failure can prevent many checks from executing. A zero count can establish success for that evaluator without proving every possible property of the program.

After a failed grade, ask which unverified assumption is most capable of explaining the result. Revisit the exact public interface, a changed nullable result, a wrapper path, or an unexercised event sequence. Inspect the existing output before repeating a broad suite. Reusing an unchanged receipt is useful for retrieving evidence but does not constitute a fresh experiment. Follow the host's actual continuation policy; this skill supplies no failure threshold, role transition, or grading tool name.

A reviewer can help by investigating a concrete disagreement with the original relevant requirements and source. A reviewer can also be wrong. Check arithmetic, quoted source, and executable evidence before acting on a finding. Rejecting an incorrect finding leaves the candidate's other evidence unchanged. Independent assistance is most valuable when it creates a new opportunity to discover error rather than supplying another confident summary.

## Keep the investigation available to a continuation

A useful continuation preserves the current uncertainty, the observations that constrained it, the exact candidate involved, and the next discriminating question. It should distinguish completed effects from intentions and leave unfinished processes identifiable through the facilities the host provides. An interpretation such as “the remaining failure is unrelated” should retain its supporting evidence or remain a hypothesis.

When progress stalls, change the question or the level of observation. Repeating a passing helper check is unlikely to explain a failing public operation. A broader suite may be appropriate when interactions are unknown; a narrower direct caller is often better when the disagreement is already localized. The skill succeeds when an observation changes the repair or justifies a conclusion, even if that conclusion is that the available environment cannot settle the question.
