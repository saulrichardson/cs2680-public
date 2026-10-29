# Assignment 3: an expressive coding agent

This assignment builds a coding agent that can investigate a repository-repair
problem, make a source change, and revise its approach when the evidence disagrees
with its explanation. Its focus is the model's working understanding: what it sees
at each decision, what it remembers as an investigation grows, and how it can return
to original observations when a summary turns out to be misleading.

The solver runs inside the course execution environment. It combines ordinary
repository tools with retained history, model-written continuation summaries,
expert critique, and a fresh recovery attempt after an unsuccessful investigation.
The design guides explain how those pieces support sustained engineering work.

## How the investigation works

The owner begins with the repair task and engineering guidance, then chooses its
own reads, searches, commands, edits, and checks. The runtime records complete
exchanges and actual tool outcomes. It validates an action before execution and
tracks jobs that continue after a tool response.

When the working context needs to be reduced, the model writes a continuation
summary. Original evidence remains available for retrieval, so the next decision
can use a compact working account without making that account the sole record of
what happened. This separation matters when an early diagnosis is later contradicted
by a test or a previously overlooked caller.

A first failed grade can supply an expert critique for the owner to investigate.
If the attempt still ends unsuccessfully, a fresh owner can start from the original
source with a brief describing the public task and relevant findings. These are
distinct from Assignment 2's routine implementation-then-review flow. The exact
triggers, ownership, and shared spending policy are defined in
[the model-experience guide](MODEL-EXPERIENCE.md#current-policy-and-authority).

## Read the design

Start with the model-experience guide to understand the solver's environment, then
follow the architecture into the responsible source. The development guide explains how to assess changes through complete
request, execution, and observation paths.

| Question | Where to look |
| --- | --- |
| What instructions, tools, context, and feedback reach the model? | [MODEL-EXPERIENCE.md](MODEL-EXPERIENCE.md) |
| How do task coordination, execution, inference, and grading fit together? | [ARCHITECTURE.md](ARCHITECTURE.md) |
| How should changes be investigated and checked? | [DEVELOPMENT.md](DEVELOPMENT.md) |
| How should contributors work on the solver? | [AGENTS.md](AGENTS.md) |

## Read the implementation

| Responsibility | Source |
| --- | --- |
| Course entry point and task workers | [agentic_loop.py](src/agentic_loop.py) |
| Investigation loop and ownership | [agent.py](src/agent.py) |
| Working context and original history | [context.py](src/context.py) |
| Files, commands, and continuing jobs | [tools.py](src/tools.py), [jobs.py](src/jobs.py) |
| Course integration and exact source capture | [course.py](src/course.py), [capture.py](src/capture.py) |
| Expert critique and fresh recovery | [critique.py](src/critique.py), [recovery.py](src/recovery.py) |
| Solver instructions and practical guidance | [prompt.txt](src/prompt.txt), [investigation-skills.txt](src/investigation-skills.txt), [language guides](src/skills/) |

The [architecture source map](ARCHITECTURE.md#source-map) expands these entry points
and explains the execution boundaries.

## Source and environment

The course imports `src.agentic_loop.Agent`, constructs `Agent(dispatcher)`, and
calls `run()`. The solver implementation uses Python's standard library; the course
provides the dispatcher, task images, network gateway, and grading environment.
Only `src/` enters the solver submission package.

This checkout includes solver source and the five engineering guides. The course
starter bundle, original handout, private evidence, local test helpers, and evaluation
assets are absent. DEVELOPMENT.md describes focused checks and the external execution boundary.

To check source syntax without the course environment, run this from the repository
root:

```bash
python3 -m compileall -q assignment3/src
```

This checks syntax, not task execution or repair quality. For the checks supported
by this checkout, see the [repository contributing guide](../CONTRIBUTING.md).
