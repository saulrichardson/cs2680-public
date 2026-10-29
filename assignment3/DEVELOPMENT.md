# Developing the expressive coding agent

Start with [the architecture](ARCHITECTURE.md) and the
[model-experience guide](MODEL-EXPERIENCE.md). They explain where execution happens
and what information reaches the solver. This guide describes how to assess a
change without relying on private development history or unavailable test helpers.

## Trace one complete interaction

Follow an input through request construction, the provider response, tool argument
validation, execution, and the observation supplied to the next request. Check the
actual serialized request when changing presentation or context. A value stored in
an archive does not necessarily appear in the model's working view.

For repository changes, establish which workspace a tool modified and which bytes
candidate capture read. For continuing jobs, distinguish a returned tool response
from process completion. For grading, identify the captured candidate to which the
result applies. These relationships matter more than matching a particular log
layout or summary phrase.

## Check the relevant boundary

Select a focused reproduction for the behavior being changed. Context changes need
the request before and after the transition. Execution changes need the actual
filesystem or process outcome. Recovery changes need the interrupted or unsuccessful
path, including the identity of the source given to the next owner.

Retain original observations when a working summary is replaced. A summary can
organize findings, but it should not become the only record that supports them.
When a check disagrees with an explanation, inspect that disagreement before adding
another instruction or control stage.

## What can be checked from this checkout

From the repository root, `python3 -m compileall -q assignment3/src` checks source
syntax. The [repository checks](../CONTRIBUTING.md) also check documentation links
and the selected file inventory. The course dispatcher, task images, private traces,
and original evaluation helpers are not included here.

Execution within the course environment requires its runner and grading assets.
When reporting results from that environment, identify the source version, inputs,
actual execution, and limits of the check. A source check or scripted tool exchange
does not establish repair quality on other tasks.

## Keep explanations with the implementation

The README introduces the project. ARCHITECTURE.md owns component boundaries;
MODEL-EXPERIENCE.md owns current solver behavior. Update the responsible guide when
a change alters what the model receives or can do. Put consequential rationale next
to that explanation rather than copying campaign logs into the repository.
