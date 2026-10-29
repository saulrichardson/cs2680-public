# Current harness architecture

The canonical design for this branch is [src/teamharness/DESIGN.md](src/teamharness/DESIGN.md).
It describes the maintained Python runtime. This entry point deliberately avoids
maintaining a second copy of its architecture.

The separation of concerns is:

1. Model instructions and selectively retrieved skills guide investigation and
   engineering judgment; they do not supply runtime facts.
2. The main workflow owns completion and fresh diagnosis-and-repair takeover.
3. Session, tool, source and process components execute actions and retain original
   observations with exact identities.
4. The course/AWS adapter owns deployment and external grading effects.
5. Tests establish mechanics; experiments measure solver outcomes. Neither model
   confidence nor a local scripted test is a leaderboard-quality claim.
