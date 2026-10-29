# Temporary browser UI compatibility

The maintained repair harness is run with `python3 -m src.teamharness` and packaged
with `lab/adapters/teamharness/build.py`.

These three scripts remain only because a browser UI was already running from
this checkout during source separation. Its evidence renderer, local-launch
helper, existing ignored assets/binary and source guidance files were preserved.
The Haskell build, UI source and original scripts are in the separate Haskell
worktree identified in [project locations](../docs/PROJECTS.md).

Do not stop the existing server or delete its dependencies as incidental cleanup.
Transition future UI development to that worktree and remove this exception only
once the current owner has finished or migrated the session.
