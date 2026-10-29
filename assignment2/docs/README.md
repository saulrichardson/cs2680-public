# MADS documentation

The [assignment overview](../README.md) explains the repair flow and gives the
shortest path to a local run. Use this guide to go from that overview into commands,
runtime behavior, and the evidence behind a model decision.

## Run a task and understand the lifecycle

Begin with the [CLI reference](cli.md) for configuration, provider selection, and
the files a run produces. Then read the [implementation and review flow](solve-review.md)
to understand why a fresh session takes over after implementation and how it
delivers the final workspace. The [runtime reference](runtime-reference.md) connects
those stages to tool execution and retained observations.

## Follow a decision through its evidence

The [context lifecycle](context-lifecycle.md) explains how the model's working view
changes while original records remain available. The [evidence workflow](evidence-workflow.md)
shows how to locate those records. [Decision inspection](decision-inspection.md)
then connects a particular decision to the request, account, and observations that
preceded it. Use [live activity inspection](live-work-interfaces.md) when you need to
understand work that is still in progress.

For a fresh investigation seeded with selected findings, read the
[handoff guide](handoff.md). It explains which evidence can be carried forward and
why a saved bundle does not restore a running process or native conversation.

## Check an implementation change

The [offline check guide](offline-checks.md) describes the reviewed commands that
exercise the harness without model calls. The [check catalog](check-catalog.md)
maps those commands to the behavior they establish. Use the catalog to select a
check for a specific uncertainty rather than importing every script in `checks/`.

## Integration and specialized topics

| Topic | Guide |
| --- | --- |
| Course wrapper, containers, and log compatibility | [Course integration](course-compatibility.md) |
| Runtime import paths during task launch | [Launcher import contract](launcher-boot-pythonpath-contract.md) |
| Experimental presentation of plain reasoning | [Reasoning replay](reasoning-replay.md) |

Course integration needs external task data and grading assets. The normal local
agent loop, journal inspection, and documented offline checks are separate from
that environment.
