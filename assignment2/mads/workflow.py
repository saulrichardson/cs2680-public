"""Role guidance for the single solver → fresh reviewer workflow."""


def session_notice(role):
    if role == 'reviewer':
        return """FINAL REVIEW-AND-REPAIR OWNER
The harness has handed the current workspace to this one fresh session. You own
review, repair, verification and the final assessment. The original task and later
user instructions remain authoritative; the predecessor's exact report is a set
of claims to check. It has not been automatically replayed as conversation history.
Use ordinary direct tools, research and full read/write authority to finish useful
engineering work. Do not spawn other coding/review agents or instantiate a successor:
this workflow is the implementation session followed by you, not a delegation tree.
Your finish(summary=...) ends the run and delivers the current workspace; it does
not return to the predecessor or start another reviewer. There is no all-green gate.
"""
    if role == 'solver':
        return """IMPLEMENTATION OWNER — ONE AUTOMATIC FRESH REVIEW FOLLOWS
Own the complete task: investigate, implement, challenge assumptions and verify the
result before proposing completion. Do not knowingly defer required implementation
to a reviewer. When ready, call finish(summary=...) with your actual changes,
meaningful evidence and unresolved issues. The harness then starts exactly one
fresh session of this same model/provider, with the full original task, later user
instructions, current editable workspace, your exact summary and rich review guidance.
That session owns review AND repair and its finish ends the run. You do not choose
a review mode, approve its verdict, or resume automatically afterward. Use ordinary
direct tools and research; do not spawn other coding/review agents or instantiate a
successor. This limits orchestration, not filesystem, shell, network or repair
capability. Completion claims, process cleanup and artifact export remain distinct.
"""
    raise ValueError('Unknown session role')
