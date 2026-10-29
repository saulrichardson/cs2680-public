# Assignment 4: Investigator repair harness

Investigator is a Python runtime for a coding agent that can investigate directly,
ask specialists for help, and continue an existing conversation as the work evolves.
It records model exchanges, tool execution, source changes, and checks in an
immutable archive, while presenting the model with a manageable working context.

The assignment asks how to preserve a dependable account of engineering work when
several conversations and processes can be active at once. A specialist's report
may be useful advice, but the source it examined and the commands it ran still
matter. A passing check must refer to the version of the code that was checked.
The runtime keeps those relationships explicit through source snapshots and records
of each operation's execution and outcome.

## How the investigation works

One main investigator owns the repair. It reads and edits the repository using
shell and coding tools, and chooses whether specialists would help. Specialists
can work with private source copies or deliberately shared files according to their
assignment. Their findings return to the main as attributed observations and advice.

The default flow starts with an exploration phase and an explicit continue-or-skip
decision. Continuing keeps the conversation, workspace, and jobs. During the repair,
the model can select guidance, retain findings, register checks, and ask for further
investigation. Context can change without rewriting the original archive.

At completion, the harness captures a **candidate**: a specific version of the source
and patch. Completion checks and grading refer to those captured bytes. A confirmed
failed completion grade can transfer that candidate to a fresh diagnosis-and-repair
owner. The [runtime design](src/teamharness/DESIGN.md) explains planning, delegation,
interruption, and completion in detail.

## Start locally

Use Python 3.12 or newer on macOS or Linux. From the repository root:

```bash
cd assignment4
python3 -m pip install -r src/teamharness/requirements.txt
python3 -m src.teamharness --help
```

Configure `A4_BASE_URL`, `A4_MODEL`, and `A4_API_KEY` for your model provider, and
set `A4_API_PROTOCOL=chat_completions`. The maintained team runtime uses this
protocol explicitly. Provider configuration is described in the
[context contract](src/teamharness/CONTEXT.md#configuration-and-provider-compatibility).

```bash
python3 -m src.teamharness run \
  --task /absolute/task.txt \
  --workspace /absolute/repository \
  --output /absolute/new-evidence-directory

python3 -m src.teamharness status --output /absolute/new-evidence-directory
python3 -m src.teamharness resume --output /absolute/new-evidence-directory
```

The workspace must be a Git repository. The evidence directory must be new and
outside that workspace. A local run executes real commands and edits files using
the host's permissions; choose the workspace and provider accordingly. The
[operating guide](src/teamharness/README.md#local-use-and-continuation) covers session
controls, registered completion checks, and attaching a grader.

## Read the technical guides

| Question | Where to look |
| --- | --- |
| How do main, specialists, planning, and completion work? | [Runtime design](src/teamharness/DESIGN.md) |
| What information reaches the model, and how does context change? | [Context contract](src/teamharness/CONTEXT.md) |
| How do I use tools, continue sessions, and inspect evidence? | [Operating guide](src/teamharness/README.md) |
| How is the runtime packaged and connected to course or AWS infrastructure? | [Course/AWS adapter](lab/adapters/teamharness/README.md) |
| How are implementation changes checked? | [Focused verification guide](test/teamharness/README.md) |
| Which practical repair guidance can the model retrieve? | [Guidance library](src/teamharness/guides/README.md) |

## Follow the source

The investigation lifecycle begins in [main.py](src/teamharness/main.py).
[agents.py](src/teamharness/agents.py) manages conversations and specialists;
[session.py](src/teamharness/session.py) and [context.py](src/teamharness/context.py)
assemble exchanges and the working view. [tools.py](src/teamharness/tools.py) handles
model-requested operations, while [candidates.py](src/teamharness/candidates.py) and
[completion.py](src/teamharness/completion.py) connect source capture to verification.

Shared modules directly under `src/` provide HTTP access, SQLite storage, transport,
and process/file effects. The [adapter](lab/adapters/teamharness/) owns package
construction and external course/AWS integration. This separates the model's
investigation from the infrastructure used to run and assess it.

## Packaging and research

From `assignment4/`, freeze a course package with:

```bash
python3 lab/adapters/teamharness/build.py \
  --output /absolute/new-package --solve-seconds 1800
```

The builder packages the runtime, required shared modules, selected guidance, and
the course adapter. It does not launch AWS resources. Deployment and grading need
their own credentials and external environment; see the adapter guide.

The [portable guidance library](research/repair-guidance/README.md) documents the
material packaged for selective retrieval. This checkout includes the runtime,
reusable infrastructure utilities, and focused tests. Campaign reports and private
operational records are kept outside it.

For contributor practice, start with [AGENTS.md](AGENTS.md). Documentation changes
need content and link review; runtime changes use focused verification appropriate
to the affected behavior.
