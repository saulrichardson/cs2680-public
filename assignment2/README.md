# Assignment 2: MADS repository-repair harness

MADS runs a coding model against a repository-repair task and records enough of the
work to inspect how a change was produced. The model can read files, run commands,
edit source, and check its work. The harness manages execution, model requests,
working context, and the evidence left behind by each action.

The assignment explores a practical problem: a useful repair needs more than a
plausible final explanation. A reader should be able to recover the original task,
see what the model received, inspect the commands that actually ran, and identify
the code that was exported. MADS keeps those records alongside the model's evolving
account of the investigation.

## How a repair proceeds

Every ordinary run begins with an implementation session. When that session calls
`finish`, the harness starts a fresh review-and-repair session using the same model.
The reviewer can inspect and change the resulting workspace; its finish ends the
run. Review is part of the execution flow rather than a comment appended to the
implementer's final response.

Both sessions use the same core tools and engineering guidance. Requests, responses,
tool output, and context changes are saved in a journal. The final records distinguish
the model's completion claim, harness execution, workspace cleanup, patch export,
and any external grading result. The [review flow](docs/solve-review.md) describes
ownership and handoff in detail.

## Start a local run

Use Python 3.10 or newer. From the repository root:

```bash
cd assignment2
python3 -m pip install -r requirements.txt
python3 -m mads --help
```

Choose a provider and set its credential in your shell. The default `course` route
uses `CS2680_API_KEY`; `openrouter` uses `OPENROUTER_API_KEY`; `openai` uses
`OPENAI_API_KEY`. The [CLI reference](docs/cli.md) covers provider selection, model
overrides, and transport settings.

With the course route configured, run a task against a repository:

```bash
python3 -m mads run /absolute/repository \
  --task /absolute/task.txt \
  --run-dir /absolute/new-run

python3 -m mads status /absolute/new-run
python3 -m mads logs /absolute/new-run --follow
```

The task file contains the repair request. The run directory must be new and outside
the repository being edited. It holds the journal, captured output, working account,
and outcome records. Use `--provider openrouter` or `--provider openai` to select an
alternative route. Add `--dry-run` to validate local configuration without creating
a run or calling a model; it still checks that the selected credential is present.

## Understand a result

Begin with the timeline, then inspect a decision and the request that preceded it:

```bash
python3 -m mads inspect /absolute/new-run timeline --last 20
python3 -m mads inspect /absolute/new-run decision --record latest --limit 5
python3 -m mads inspect /absolute/new-run request --view wire --readable
python3 -m mads audit /absolute/new-run --strict
```

The `wire` view selects the recorded outgoing HTTP body. The `compiled` view selects
the request assembled before provider serialization. Original output remains
available when a shortened presentation or working summary needs to be checked.
See [decision inspection](docs/decision-inspection.md) and the
[evidence workflow](docs/evidence-workflow.md) for the connection between decisions,
execution, and retained records.

## Read the implementation

| Area | Guide or source |
| --- | --- |
| Commands, provider settings, and run layout | [CLI reference](docs/cli.md) |
| Runtime responsibilities and execution guarantees | [Runtime reference](docs/runtime-reference.md) |
| Implementation and review ownership | [Review flow](docs/solve-review.md), [workflow.py](mads/workflow.py) |
| Working context and retained observations | [Context lifecycle](docs/context-lifecycle.md), [context.py](mads/context.py), [store.py](mads/store.py) |
| Model transport and provider configuration | [provider.py](mads/provider.py), [routes.py](mads/routes.py) |
| Tool execution and job handling | [tools.py](mads/tools.py), [execution.py](mads/execution.py), [jobs.py](mads/jobs.py) |
| Further topics and offline checks | [Documentation guide](docs/README.md) |

## Course evaluation

The course wrapper adds container setup, task acquisition, patch bundling, and
external grading. It needs Docker, course credentials, task/test JSON, and the
grading environment. The datasets are not included in this checkout. Supply them
through `TASKS_JSON` and `TESTS_JSON` before following the
[course integration guide](docs/course-compatibility.md).

For checks that do not call a model, start with `python3 -m mads check --list` and
the [offline check guide](docs/offline-checks.md). The
[repository contributing guide](../CONTRIBUTING.md) describes the narrower checks
used for this curated edition.

Claude Code and Codex were used in developing this assignment.
