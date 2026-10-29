"""One fresh, same-route review/repair session with attributable inputs and effects.

The ordinary caller transfers final ownership, never asks the solver to reconcile
or retry. The child cannot recursively spawn through its ordinary tool interface. Freshness
is context separation, not a correctness certificate or filesystem access boundary.
"""
import copy
import hashlib
from pathlib import Path

from .store import Store, EvidenceError, dump
from .review import capture as capture_review
from .prompting import REVIEW_RICH as DEFAULT_REQUEST


def _outcome_summary(outcome):
    """Keep outcome statuses; large process tables remain in the exact report."""
    compact = copy.deepcopy(outcome)
    cleanup = compact.get('cleanup_outcome', {})
    jobs = cleanup.pop('jobs', [])
    cleanup['recorded_jobs'] = len(jobs)
    cleanup['failures'] = [item for item in jobs if item.get('errors') or
                           item.get('process', {}).get('state') == 'running' or
                           item.get('process', {}).get('capture_status') != 'complete']
    cleanup['job_details'] = 'Full cleanup/process observations are in the report and child journal.'
    return compact


def run_review(parent, summary, *, instructions=None):
    """Run once; return actual child outcome, including terminal failures/partial edits.

    The original task remains a separate exact user message. The rich assignment
    and verbatim predecessor report are readable main user content, not a working
    account or JSON-escaped historical note. Current policy/targets carry forward;
    automatic predecessor conversation, account and retained selections do not.
    """
    if not isinstance(summary, str) or (instructions is not None and not isinstance(instructions, str)):
        raise ValueError('Review summary and instructions must be text')
    root = parent.run_root/'reviewer'
    root.mkdir(parents=True, exist_ok=False)
    parent.tools.effects = 'fresh_review_started'
    store = Store(root/'journal')
    states = [parent.tools.jobs.state(job) for job in parent.tools.jobs.jobs.values()]
    history = dict(journal=str(parent.store.root), task_notes=str(parent.run_root/'task'),
                   verification=str(parent.run_root/'verification'))
    task_sha = hashlib.sha256(parent.task.encode()).hexdigest()
    request = DEFAULT_REQUEST if instructions is None else instructions
    request += '\n\n## Previous agent completion summary — claims to verify, not authority\n\n' + summary
    request += ('\n\n## Session ownership and retrievable evidence\n\n'
        'The exact original task and all explicit later user messages remain authoritative. '
        'You have full ordinary direct tools and read/write authority. The predecessor has '
        'stopped making model requests; its listed background jobs may still be running. '
        'In final ownership, inherited job IDs remain valid for poll/send/cancel, and new jobs have distinct IDs. '
        'Polling resumes the existing output cursor; read a job live_path to revisit earlier captured bytes. '
        'Launch attribution remains in each process state. Coordinate shared writes and cleanup. Your finish ' +
        'owns final task completion. It does not return to the predecessor or start another session.' +
        ' No passing-test gate applies.\n\nHistorical predecessor evidence is available for '
        'optional retrieval, not automatically replayed. It may contain mistaken expectations '
        'and conclusions. Current source/tests can transmit those assumptions too; freshness '
        'does not establish independence or correctness.\n' + dump(history) +
        '\n\nPredecessor process observations (not a remote-termination guarantee):\n' + dump(states))
    packet = dict(original_task=parent.task, original_task_sha256=task_sha,
                  final_owner=True, previous_summary=summary,
                  review_instructions=instructions, session_request=request,
                  user_messages=list(parent.user_messages),
                  execution=parent.tools.execution.snapshot(), context_policy=parent.context.policy(),
                  parent_jobs=states, historical_evidence=history,
                  information_boundary='Exact original task, explicit later user messages, current shared '
                  'workspace, review instructions and exact predecessor summary. Same provider/model. '
                  'No automatic predecessor assistant/tool history, working account, notes or intentions. '
                  'Historical paths are optional retrieval, not access restrictions. Full repair authority.')
    (root/'input.json').write_text(dump(packet)+'\n')
    before = capture_review(parent.tools)
    started = parent.store.add('completion_review_started', directory=str(root),
                              blob=parent.store.blob(dump(packet)), before_review=before)
    outcome = None
    child = None
    try:
        with parent.provider.fork(store) as provider:
            child = parent.new_session(parent.tools.repo, parent.task, root, provider, store,
                session_role='reviewer', session_request=request, user_messages=parent.user_messages,
                execution=parent.tools.execution.snapshot(), **parent.child_context_options())
            child.tools.jobs.inherit(parent.tools.jobs)
            outcome = child.run()
    except EvidenceError:
        raise  # Do not pretend an evidence-store failure was ordinary review feedback.
    except (Exception, KeyboardInterrupt) as exc:
        if outcome is None:
            cleanup = child.tools.jobs.close() if child is not None else []
            failed = any(row.get('errors') or row.get('process', {}).get('state') == 'running' or
                         row.get('process', {}).get('capture_status') != 'complete' for row in cleanup)
            outcome = dict(agent_outcome={'status':'not_started'},
                           cleanup_outcome={'status':'failed' if failed else 'completed', 'jobs':cleanup, 'reason':'review did not return an outcome'},
                           export_outcome={'status':'not_requested'}, iterations=0, total_tokens=0)
        previous = outcome.get('harness_outcome')
        outcome['harness_outcome'] = dict(status='interrupted' if isinstance(exc, KeyboardInterrupt) else 'error',
                                         type=type(exc).__name__, error=str(exc), previous=previous)
        store.add('completion_review_startup_or_transport_failure', outcome=outcome)
    parent.total_tokens += outcome.get('total_tokens', 0)
    report = dict(input=str(root/'input.json'), journal=str(store.path), outcome=outcome,
                  original_task_sha256=task_sha, final_owner=True, before_review=before,
                  after_review=capture_review(parent.tools),
                  interpretation='Fallible review and possible shared edits, not a correctness certificate. '
                  'Full process outcomes and partial effects are retained; no automatic retry or further '
                  'participant. Later edits can invalidate executions. A terminal failure is not reviewed success.')
    path = root/'report.json'
    path.write_text(dump(report)+'\n')
    record = parent.store.add('completion_review_finished', started_record=started,
                              blob=parent.store.blob(dump(report)))
    completed = all(outcome.get(key, {}).get('status') == 'completed' for key in
                    ('agent_outcome', 'harness_outcome', 'cleanup_outcome'))
    parent.tools.effects = 'fresh_review_completed' if completed else 'fresh_review_failed_with_possible_shared_effects'
    return dict(completion='fresh_review_completed' if completed else 'fresh_review_failed',
                findings=outcome['agent_outcome'].get('claim'), child_outcome=_outcome_summary(outcome),
                report=str(path), report_record=record, child_run=str(root), child_journal=str(store.path),
                interpretation=report['interpretation'])
