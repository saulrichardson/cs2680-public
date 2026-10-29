# Modern AI Systems: Agents and System Optimizations

Four projects for COMPSCI 2680 explore how to make coding agents useful, observable,
and dependable. They cover both sides of the problem: an interface through which a
person can follow an agent's work, and the systems that let a model investigate a
repository, make changes, check them, and continue when its first approach fails.

The recurring question is how to connect an agent's understanding to what actually
happened. A conversation can describe a successful edit while a process is still
running or a test result belongs to an earlier version of the code. These projects
make those relationships explicit through event streams, retained observations,
working context, and checks tied to specific source changes.

## The assignments

| Project | What it builds | Main design question |
| --- | --- | --- |
| [1. Mission Control](assignment1/README.md) | A local web application for Claude Code | How can a person follow conversations, tool calls, delegated work, and files across several sessions? |
| [2. MADS](assignment2/README.md) | A repository-repair harness with implementation and fresh review sessions | How can tool execution, context, and saved evidence remain inspectable throughout a repair? |
| [3. Expressive coding agent](assignment3/README.md) | A course-integrated solver with context continuation, expert critique, and recovery | How can a model preserve useful understanding and revise a failed approach during a long investigation? |
| [4. Investigator repair harness](assignment4/README.md) | A Python runtime for a main investigator and model-selected specialists | How can multiple conversations, concurrent tools, and completion checks share dependable source and evidence? |

Here, a **harness** is the software around a model: it provides instructions and
tools, executes requested actions, records observations, and manages the work's
lifecycle. **Context** is the information supplied to the model for its next
decision. Each assignment makes different choices about those responsibilities;
they are separate projects with their own entry points and dependencies.

## Where to begin

For a hands-on introduction, start with Mission Control. Its README takes you from
installation to a conversation in the browser, then into the event-processing and
server architecture. To study an autonomous repair loop, start with MADS and follow
a run from the task file through implementation, review, and saved evidence.

Assignment 3 focuses on the relationship between instructions, working context,
and recovery inside the course execution environment. Assignment 4 extends the
investigation into a runtime with specialists, explicit planning, retained history,
and verification of the exact code submitted at completion. Their READMEs explain
the behavior first and then point to the responsible design documents and source.

| To explore | Start here |
| --- | --- |
| The user interface and live event flow | [Mission Control architecture](assignment1/docs/approach.md) |
| Implementation, review, and evidence inspection | [MADS documentation guide](assignment2/docs/README.md) |
| What an autonomous solver sees and can do | [Assignment 3 model experience](assignment3/MODEL-EXPERIENCE.md) |
| Investigation, delegation, and verified completion | [Assignment 4 runtime design](assignment4/src/teamharness/DESIGN.md) |
| Local checks and CI | [Contributing guide](CONTRIBUTING.md) |

## Running the projects

There is no shared application to start at the repository root. Follow the setup
instructions in the assignment you want to use. Mission Control needs Node.js,
pnpm, and a signed-in Claude Code CLI. MADS and the Investigator harness use Python
and a configured model provider. Assignment 3 is integrated with the course runner;
this checkout includes its solver source and design guides rather than a standalone
course environment.

The checkout includes the source, design guides, tests, and replay examples used
by these projects. Course task datasets, the dispatcher environment, and live model
credentials must be supplied separately. The [contributing guide](CONTRIBUTING.md)
explains the available checks and CI workflow.
