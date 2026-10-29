# Product intent

## Mission Control

Mission Control makes Claude Code's work understandable from one local interface. An operator chooses a folder, gives the agent a task, follows its actions, inspects its delegates and files, and continues the conversation.

The application is general-purpose. Prompts and project files define the task; the console provides execution, observation, navigation, and access to results.

## Interaction model

- A project groups related sessions by their selected folder.
- A session is an independent conversation. Its follow-up runs retain Claude's context.
- A project workspace shows up to three chosen sessions side by side, with a single-session switcher on narrow screens.
- Bring together creates a new session from captured reports and selected files. Its brief defines the outcome; original sessions remain independently usable.
- A subagent belongs to the run that spawned it. Its assignment, activity, and report remain connected to that parent.
- Sessions share their chosen folder unless the operator requests an isolated Git checkout. Files and conversation context have separate ownership.
- Live execution and recorded playback are visibly distinct.

## Design criteria

Keep the conversation readable, fold lengthy detail, and provide an outline for navigation. Make pending work, errors, outcomes, and reported usage explicit. Make agents visibly selectable and preserve orientation while switching between a parent and its delegates or between related sessions. Agent navigation complements the tool-call outline; it does not replace it.

Ordinary work should require few controls: create or select a folder, submit a prompt, inspect the result, and continue. Request Chrome access automatically and report its actual availability. Stopping execution, archiving a conversation, and deleting files are separate operations.
