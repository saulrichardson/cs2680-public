"""One investigation; the model chooses tools and the loop owns continuation."""
from __future__ import annotations
from .run_control import AllocationExceeded

from dataclasses import dataclass, field, asdict
from decimal import Decimal
import json
import hashlib
import http.client
import math
from pathlib import Path
import re
import sys
import time
import urllib.error
import urllib.request
import uuid

from .context import Context, context_schemas, validate_context_call
from .capture import preserve_ungraded, retain_json
from .models import BudgetExceeded, CapacityError, ProviderCapacityRejected, ProviderRejected, ResponseRejected, InfrastructureError, AccountingError, UncertainInference, DEFAULT_OUTPUT_CEILING, encoded, validate_response, TraceDelivery
from .tools import Tools, ToolResult, tool_schemas, validate_call
from .maintenance import Maintenance, MAX_FAILURES


def resource_observation(shared):
    """Present declared run resources without introducing an admission policy."""
    facts = shared.get('resource_facts')
    if not facts:
        return ''
    course = facts['basis'] == 'course_contract'
    lines = [
        'Shared resources across this run: all tasks, initial owners, expert reviews and recovery owners use the same pool.',
        ('Declared course submission contract' if course else 'Declared development run settings')
        + f": {facts['task_count']} tasks; {facts['tasks_opened']} opened, {facts['tasks_completed']} task workers exited (any outcome).",
    ]
    if facts['budget_usd'] is None:
        lines.append('No spending ceiling was declared by this launcher; remaining platform funding is unknown.')
    else:
        ceiling = Decimal(facts['budget_usd'])
        headroom = Decimal(facts['headroom_usd'])
        lines.append(f'Declared course-priced inference ceiling: ${ceiling:.6f}; '
                     f'headroom after known settled charges: ${headroom:.6f}.')
        lines.append(f"New inference stops once settled and unknown charges reach ${Decimal(facts['admission_limit_usd']):.6f}; "
                     f"${Decimal(facts['admission_headroom_usd']):.6f} remains before that stop. "
                     'Already-admitted responses may finish above that ceiling.')
        if shared.get('inflight') or shared.get('unknown'):
            lines.append(f"Headroom excludes unsettled charges: {shared.get('inflight', 0)} calls in flight, "
                         f"{shared.get('unknown', 0)} unknown-charge attempts; reservations are not final charges.")
        if course:
            lines.append('This is the submission maximum, not a queried balance; the daily account balance may stop the run sooner.')
    lines.append('These are shared execution facts, not a separate allowance for this task or owner. Recovery does not reset them; platform admission remains authoritative.')
    return '\n'.join(lines)


def control_schemas(*, note=None):
    specs = [
        ("evaluate_candidate", "Capture and grade the exact current code, including your latest edits. A failed grade leaves you working. An unchanged candidate returns its existing receipt without another grading experiment. During recovery, your latest actually graded candidate becomes the final submission even if it does not fully pass. A pass secures that candidate and opens memory-note finishing. Use relevant existing tests and focused checks to establish the repair, and read their actual results. Investigate setup failures where feasible and preserve any limits honestly. The receipt reports an aggregate outcome for these captured bytes; it does not identify hidden failures. Call alone.", {}, []),
        ("end_attempt", "Finish this owner's investigation. The harness captures your current repair and grades new candidate bytes, or reuses their exact existing receipt. Every end, passing or not, opens finish_task for one memory note. An unsuccessful initial attempt starts one fresh standard recovery owner if shared admission remains open; recovery finalizes its latest actually graded candidate, retaining the initial candidate only if recovery has no new grade. An unchanged source base with no graded candidate ends without recovery. Use the shared investigation guidance to develop and pursue informative next actions within available resources. Choose this action when ready to settle the repair or when the remaining evidence and feasible work justify ending. An unsuccessful grade remains unresolved regardless of your explanation. Explain why you are ending; no outgoing handoff is required. Call alone.",
         {"reason": {"type": "string", "minLength": 1}}, ["reason"]),
    ]
    if note == 'pass':
        specs = [("finish_task", "Save a detailed cross-task memory summary of the requirement, relevant implementation, evidence, repair, actual checks and remaining limits, then finish the already passing candidate. The public task and the exact passing diff are saved beside your note for later tasks, so explain what the diff cannot show: why the change is right, which callers and checks mattered, and which explanations turned out wrong. The passing artifact is secured; this action neither captures source nor grades again. Write from the completed investigation, retrieving exact evidence only when needed. Call alone.",
                  {"memory_note": {"type": "string", "minLength": 1}}, ["memory_note"])]
    elif note:
        specs = [("finish_task", "Save a detailed cross-task memory summary, then finish. This investigation has ended without a passing candidate; later tasks in the same repository can still learn from it. The public task and the exact selected diff are saved beside your note, marked as not passing. Record what holds regardless of the failed repair: how to build and run checks here, where the relevant behavior lives, which callers and producers matter, environment quirks, and which explanations you tested and ruled out. Then state what the remaining failures might still involve and what you would check next, labelled as unverified. This action neither captures source nor grades again. Write from the completed investigation, retrieving exact evidence only when needed. Call alone.",
                  {"memory_note": {"type": "string", "minLength": 1}}, ["memory_note"])]
    return [{"type": "function", "function": {"name": n, "description": d, "parameters": {"type": "object", "properties": p, "required": r, "additionalProperties": False}}} for n, d, p, r in specs]


def validate_batch(message, prior_ids=(), controls=None):
    if not isinstance(message, dict) or message.get("role") != "assistant" or (message.get("content") is not None and not isinstance(message["content"], str)):
        raise ValueError("Provider message must have assistant role and text content before any action")
    calls = message.get("tool_calls", [])
    if not isinstance(calls, list):
        raise ValueError("tool_calls must be a list of objects, never null")
    seen, parsed = set(prior_ids), []
    controls = {x["function"]["name"]: x["function"]["parameters"] for x in (control_schemas() if controls is None else controls)}
    for call in calls:
        if not isinstance(call, dict) or call.get("type") != "function" or not isinstance(call.get("id"), str) or not call["id"] or call["id"] in seen:
            raise ValueError("Each tool call needs a nonempty string id unique across this investigation and function type")
        seen.add(call["id"])
        f = call.get("function")
        if not isinstance(f, dict) or not isinstance(f.get("name"), str) or not isinstance(f.get("arguments"), str):
            raise ValueError("Each tool call needs a function object with string name and complete JSON arguments")
        args = json.loads(f["arguments"])
        name = f["name"]
        if name in ("compact_context", "search_history"):
            validate_context_call(name, args)
        elif name not in controls:
            validate_call(name, args)
        else:
            schema = controls[name]
            if not isinstance(args, dict) or set(args) - set(schema["properties"]) or set(schema["required"]) - set(args):
                raise ValueError("Invalid control arguments")
            if name in ('end_attempt', 'finish_task'):
                key = 'reason' if name == 'end_attempt' else 'memory_note'
                if not isinstance(args[key], str) or not args[key].strip():
                    raise ValueError(f'{name}.{key} must contain nonempty text; no action ran')
        parsed.append((call, name, args))
    # A terminal/control action cannot hide trailing mutations or advice calls.
    if len(parsed) > 1 and any(n in controls for _, n, _ in parsed):
        raise ValueError("Call control tools alone; no actions in this batch ran")
    if len(parsed) > 1 and any(n == 'compact_context' for _, n, _ in parsed):
        raise ValueError('Call compact_context alone; no actions ran')
    return parsed


@dataclass
class Session:
    context: Context | None
    model: str = "standard"
    iterations: int = 0
    last_grade: dict | None = None
    previous_grade: dict | None = None
    reason: str | None = None
    model_uses: dict = field(default_factory=dict)
    critique: dict | None = None
    # The outcome waiting for this owner's memory note: 'pass', 'give_up' or 'cap'.
    note_pending: str | None = None
    ended_after_failed_grade: bool = False
    attempt_end_reason: str | None = None
    selected_candidate: dict | None = None
    termination_detail: str | None = None
    maintenance: Maintenance = field(default_factory=Maintenance)


def public_task(task):
    if isinstance(task, str):
        # The boundary already selected only public fields. Their Markdown may
        # contain arbitrary nested headings; parsing headings would drop content.
        return task
    return "\n\n".join(f"## {title}\n{task[key]}" for key, title in (("problem_statement", "Problem statement"), ("requirements", "Requirements"), ("interface", "Interface")) if task.get(key))


def grade_reference(result):
    candidate = result.get("candidate_id")
    name = f"Captured candidate ID {candidate}" if type(candidate) is int and candidate > 0 else "Captured candidate"
    identity = result.get("patch_sha256")
    if not isinstance(identity, str) or not re.fullmatch(r"[0-9a-f]{64}", identity):
        identity = None
    return name, identity


def grade_counts(result):
    """Present official grading units without inventing assertion/execution scope."""
    failed, total = result['tests_failed'], result['tests_total']
    return (f"{total - failed} of {total} required grading entries reported passing; "
            f"{failed} of {total} required grading entries not reported passing")


GRADE_SCOPE = ("A grading entry may aggregate multiple cases. These counts alone do not show "
               "which assertions ran, failed, or were blocked before execution.")


def apply_grade(session, result):
    if result.get('selected_candidate'):
        session.selected_candidate = result['selected_candidate']
    if type(result.get("tests_failed")) is int and type(result.get("tests_total")) is int:
        if result.get("unchanged") is False and not result.get("preparation_error"):
            session.previous_grade = session.last_grade
        session.last_grade = result
    if result.get("terminal"):
        session.reason = result.get("reason", "pass" if result.get("tests_failed") == 0 else "give_up")


def recoverable_inference(state, model):
    """Only a completed gateway failure with retained valid accounting can continue."""
    attempt = state.get('last_attempt', {})
    reservation = attempt.get('reservation')
    charge = attempt.get('course_price', reservation)
    shared = state.get('run_budget', {})
    return (attempt.get('gateway_completed') is True and attempt.get('model') == model
            and str(attempt.get('error', '')).startswith('UncertainInference:')
            and type(reservation) in (int, float) and math.isfinite(reservation) and reservation >= 0
            and type(charge) in (int, float) and math.isfinite(charge) and charge >= 0
            and not shared.get('stop_reason') and not state.get('snapshot_while_operations_pending'))


def wait_after_failure(seconds, gateway, runtime):
    """Pace fresh operations; never replay a failed RPC or defer explicit shutdown."""
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if (runtime / 'stop-request').exists():
            raise InfrastructureError('External stop received during provider-failure backoff')
        state = gateway.state()
        if state.get('run_budget', {}).get('stop_reason'):
            raise InfrastructureError('Shared shutdown during provider-failure backoff')
        time.sleep(min(.5, max(0., deadline - time.monotonic())))


def failed_grade_event(result, previous=None):
    """A completed failed evaluation is attributed runtime evidence."""
    grade = result.metadata
    if (grade.get("kind") != "grade" or grade.get("reason") != "continue"
            or grade.get("unchanged") is not False or grade.get("terminal") is not False
            or type(grade.get("tests_failed")) is not int or grade["tests_failed"] <= 0
            or type(grade.get("tests_total")) is not int or grade["tests_total"] < grade["tests_failed"]):
        return None
    name, identity = grade_reference(grade)
    comparison = ""
    if (previous and previous.get('tests_total') == grade['tests_total']
            and type(previous.get('tests_failed')) is int):
        comparison = (f"Previous candidate: {grade_counts(previous)}. "
                      f"Reported-passing count: {grade['tests_total'] - previous['tests_failed']}/{grade['tests_total']} "
                      f"-> {grade['tests_total'] - grade['tests_failed']}/{grade['tests_total']} "
                      f"(change: {previous['tests_failed'] - grade['tests_failed']:+d}). "
                      "Individual outcomes were not supplied; this does not identify which cases changed.\n")
    return {"content":
        "[Harness grading event — supplied by the evaluation boundary, not a human message.]\n"
        "Official evaluation failed.\n"
        f"{name} completed: {grade_counts(grade)}.\n"
        + (f"Graded patch SHA256: {identity}.\n" if identity else "Graded patch identity was not verified.\n")
        + comparison + "This is the completed official evaluation for that captured patch; no separate grading stage is pending. "
        "The task remains in this same investigation. No additional grader diagnostics are supplied with these counts.\n"
        "\n"
        "[Continuing the investigation]\n"
        "This candidate has not satisfied the evaluation. The counts do not identify the failed behavior. "
        "If you changed an interface or dependency, a focused build or import check through an original caller "
        "can help distinguish compatibility problems from incorrect behavior. "
        "Keep corrections supported by concrete checks, and examine an important assumption your local validation "
        "has not established. A plausible explanation does not settle a contrary observation.\n\n"
        "This candidate has not fully passed, regardless of your explanation. If a local failure also appears "
        "on original source, preserve that comparison and its conditions; it does not identify the cause of this grade.\n\n"
        "If a public invocation or original caller is already failing, address that contradiction directly. "
        "Otherwise choose a small check through the actual producer and consumer that could expose a mistake "
        "in your explanation. Changing the test setup, predicting what hidden tests contain, or grading another "
        "cosmetic variation does not supply that observation. Use what the check shows to make the next source "
        "correction. Related edits may need to move together across an interface; preserve supported corrections "
        "when another failure remains.\n\n"
        "If accumulated history obscures the next action, use compact_context to preserve the unresolved "
        "invocation, observed result and next useful check while replacing obsolete narration. "
        "Continue from what you have learned. If no useful next action remains, end_attempt can finish "
        "this investigation without claiming that the unresolved behavior is correct."}


def finish_investigation(session, root, runtime, gateway, tools, grade, started,
                         last_state, logging_errors, *, cap_pending=False, trace_delivery=None):
    """One terminal path: cleanup, optional cap grade, recovery, retained outcome."""
    cleanup_error = None
    if tools is not None:
        try:
            tools.close()
        except Exception as error:
            cleanup_error = f"process cleanup failed: {type(error).__name__}: {error}"
            session.reason = "infrastructure_error"
            session.termination_detail = (session.termination_detail or "") + "; " + cleanup_error
    if cap_pending and cleanup_error is None:
        try:
            apply_grade(session, grade({"cap": True}))
            session.reason = session.reason or "cap"
        except Exception as error:
            session.reason = "infrastructure_error"
            session.termination_detail = "Budget finalization failed; grading was not repeated: " + str(error)
    session.reason = session.reason or "infrastructure_error"
    try:
        budget = gateway.state()
    except Exception as error:
        budget = {**(getattr(gateway, "last_state", None) or last_state), "unavailable": True,
                  "last_known_only": True, "error": type(error).__name__ + ": " + str(error)}
    outcome = {"reason": session.reason, "iterations": session.iterations, "grade": session.last_grade,
               "budget": budget, "reductions": [],
               "context_edits": session.context.edits if session.context else [],
               "working_context": ({"revision": session.context.view.revision,
                    "has_continuation": session.context.view.continuation is not None,
                    "compacted_through": session.context.view.continuation.through if session.context.view.continuation else 0,
                    "exact_view_record": session.context.view_source,
                    "capacity_recovery": session.context.recovering} if session.context else None),
               "model_uses": session.model_uses, "critique": session.critique,
               "expert_reviews": int(bool(session.critique and session.critique.get('inference_requested'))),
               "selected_candidate": session.selected_candidate, "ended_after_failed_grade": session.ended_after_failed_grade, "attempt_end_reason": session.attempt_end_reason, "memory_note_pending": session.note_pending,
               "maintenance": session.maintenance.records,
               "final_model": session.model,
               "seconds": time.time() - started, "logging_errors": logging_errors,
               "termination_detail": session.termination_detail,
               "owned_work_cleanup": {"confirmed": cleanup_error is None, "error": cleanup_error,
                   "note": "Registered owned-job cleanup remains unconfirmed." if cleanup_error else
                           "Registered owned jobs were reaped; unrelated writers are not covered." if tools is not None else "No tool owner was created."}}
    if trace_delivery is not None:
        outcome["trace_delivery"] = trace_delivery
    if tools is not None:
        outcome["output_capture"] = tools.output_capture_status()
    if session.reason in ('give_up', 'cap') and (runtime / 'capture-base.txt').exists():
        retained = preserve_ungraded(root, runtime,
            'Final working tree retained separately; selected captured candidate is unchanged.',
            known_grade=session.last_grade)
        key = 'ungraded_final_candidate' if retained['ungraded'] else 'final_working_candidate'
        outcome[key] = retained
    if session.reason not in ("pass", "give_up", "cap", "development_complete"):
        try:
            if cleanup_error is not None:
                raise InfrastructureError("Owned-job cleanup was not confirmed; ungraded recovery capture was not attempted")
            outcome["ungraded_final_candidate"] = preserve_ungraded(root, runtime,
                session.termination_detail or "Abnormal investigation exit")
        except Exception as error:
            outcome["ungraded_final_candidate"] = {"status": "unknown" if cleanup_error else "error", "ungraded": True,
                "error": type(error).__name__ + ": " + str(error)}
        try:
            (runtime / "error.txt").write_text(session.termination_detail or "Abnormal investigation exit")
        except OSError as error:
            logging_errors.append({"event": "finalization", "sink": "error.txt", "error": str(error)})
    failure = retain_json(runtime / "outcome.json", outcome)
    if failure:
        logging_errors.append({"event": "finalization", "sink": "outcome.json", "error": failure})
    return outcome


def run(task, root, runtime, gateway, grade, log, profiles, context_factory=Context, *, control_descriptions=None, recovery=None, require_memory_note=False, memory=None, task_started_at=None):
    root, runtime = Path(root).resolve(), Path(runtime).resolve()
    started, logging_errors = time.time(), []
    task_started_at = started if task_started_at is None else task_started_at
    session, state, tools = Session(None), {}, None
    cap_pending = False
    last_generation_allocation = None
    maintenance_record = None
    trace = TraceDelivery(runtime, log)

    def saved(text, kind, extra=None):
        path = runtime / "out" / f"control-{time.time_ns()}.txt"
        path.parent.mkdir(exist_ok=True)
        path.write_text(text)
        return ToolResult(text + f"\nSaved result: {path}", str(path), {"kind": kind, **(extra or {})})

    def begin_note(outcome):
        """This owner has ended; one memory note follows before it stops."""
        if not require_memory_note:
            return False
        grade({'begin_note': True})
        session.note_pending, session.reason = outcome, None
        return True

    def candidate_action(action, args):
        if action == 'finish_task' and not session.note_pending:
            return saved('finish_task is available after this investigation ends. No note or grade was created.', 'finish', {'error': True})
        if session.note_pending and action != 'finish_task':
            return saved('This investigation has ended. Use finish_task(memory_note="your useful observation"); no candidate change or grading started.', 'finish', {'error': True})
        result = grade({'action': action, **args})
        if action == 'finish_task':
            outcome, session.note_pending = session.note_pending, None
            if not result.get('passed'):
                session.reason = outcome
                return saved(result.get('message', 'Memory note saved.') + f"\nMemory record: {result.get('memory_note_path')}", 'finish')
            apply_grade(session, result)
            session.reason = outcome
            return saved(result['message'] + f"\nSelected final candidate: {json.dumps(session.selected_candidate)}"
                         + f"\nMemory record: {result.get('memory_note_path')}", 'finish')
        if action == 'end_attempt':
            session.ended_after_failed_grade = (result.get('terminal') is True
                and result.get('reason') == 'give_up'
                and type(result.get('tests_failed')) is int
                and type(result.get('tests_total')) is int
                and not (result['tests_total'] > 0 and result['tests_failed'] == 0))
            session.attempt_end_reason = args['reason']

        apply_grade(session, result)
        if recovery is None and session.critique is None:
            from .critique import eligible, review
            if eligible(result):
                try:
                    session.critique = review(public_task(task), root, runtime, gateway,
                                              result, ctx, profiles)
                finally:
                    review_record = runtime / 'critique' / 'record.json'
                    if review_record.exists():
                        session.critique = json.loads(review_record.read_text())
        if result.get('terminal') and session.reason in ('pass', 'give_up'):
            begin_note(session.reason)
        if result.get("tests_failed") is None:
            text = result.get("message", "No valid grading counts are available. No pass is inferred.")
            if result.get('preparation_error'):
                text += f"\nLocal candidate ID: {result['candidate_id']}; no confirmed course attempt was consumed."
            if result.get("capture_error"):
                details = result["capture_error"]
                text += f"\nCapture error: {details['type']}: {details['message']}"
                if details.get("git_stderr"):
                    text += "\nGit diagnostic:\n" + details["git_stderr"]
                if details.get("git_stderr_truncated"):
                    text += f"\nGit diagnostic is a first page of {details['git_stderr_characters']} characters ({details['git_stderr_bytes']} bytes)."
                if details.get("git_stderr_path"):
                    text += "\nComplete Git diagnostic: " + details["git_stderr_path"]
                if details.get("receipt_path"):
                    text += "\nCapture diagnostic receipt: " + details["receipt_path"]
                for key in ("git_stderr_retention_error", "receipt_error"):
                    if details.get(key):
                        text += f"\nDiagnostic retention failed ({key}): {details[key]}"
                text += ("\nThe requested termination remains in effect; no evaluation was replayed." if result.get("terminal") else
                         "\nInspect and repair the repository, then call evaluate_candidate() again. No evaluation was replayed.")
        else:
            name, identity = grade_reference(result)
            text = f"{name}: {grade_counts(result)}.\n"
            text += f"Graded patch SHA256: {identity}.\n" if identity else "Graded patch identity not verified: receipt has no valid SHA256.\n"
            text += "These counts apply only to the patch captured at this submission. Hidden diagnostics are unavailable.\n"
            if result.get('tests_failed', 0) and result.get('restored_test_code_files'):
                text += ("The official verifier restores original test code and applies its own tests, so submitted "
                         "test-code edits do not control grading. These changed files look like test code by common "
                         "naming conventions:\n"
                         + ''.join(f"- {path}\n" for path in result['restored_test_code_files'])
                         + "This list is a naming heuristic, not a diagnosis of the failing tests. "
                         + GRADE_SCOPE + "\n"
                         "Original callers are compatibility evidence for preserved behavior; explicitly requested interface changes may require adapted tests. Changing a test alone does not establish the required behavior; "
                         "the verifier does not change your live sandbox.\n")
            if result.get("elapsed_seconds") is not None:
                if result.get("unchanged"):
                    text += f"\nSubmission check and receipt retrieval took {result['elapsed_seconds']:.1f} seconds."
                else:
                    text += f"\nThis grading operation took {result['elapsed_seconds']:.1f} seconds."
            if result.get("unchanged") and result.get("grading_seconds") is not None:
                text += f"\nOriginal grading operation took {result['grading_seconds']:.1f} seconds."
            if result.get("capture_stability", {}).get("note"):
                text += "\nCapture: " + result["capture_stability"]["note"]
            if result.get("unchanged"):
                if action == 'end_attempt':
                    text += ("\nCurrent source is the unchanged original base; it was not graded. The previously captured candidate remains selected."
                             if result.get('current_candidate_empty') else
                             "\nThe harness settled this attempt using the exact existing candidate receipt; no duplicate candidate evaluation was started.")
                else:
                    text += ("\nThis exact patch was already evaluated; no new grading was started. The existing grade still applies. "
                             "Investigate the unresolved public behavior and use your findings to repair the candidate before resubmitting. "
                             "Changing comments or formatting solely to obtain a different patch identity does not repair behavior.")
                    text += " You may continue repair or use end_attempt(reason='...') to have the harness settle this attempt's candidate; an unsuccessful initial attempt can admit one fresh standard recovery."
        if session.selected_candidate:
            text += "\nSelected final candidate: " + json.dumps(session.selected_candidate)
        if session.note_pending == 'pass':
            text += '\nThe captured candidate passed.'
            text += ' Finish through finish_task(memory_note="..."). Save the detailed cross-task summary described in its description; targeted evidence retrieval is available if needed. This action saves the note and finishes without another grade.'
        elif session.note_pending:
            text += ('\nThis investigation has ended without a passing candidate. Finish through finish_task(memory_note="...") '
                     'with the cross-task summary described in its description; targeted evidence retrieval is available if needed.')
        if result.get("retention_error"):
            text += "\n" + result["retention_error"]
        patch = result.get('graded_patch')
        if isinstance(patch, dict):
            if patch.get('status') in ('retained', 'partial') and patch.get('path'):
                text += (f"\nExact captured graded patch: {patch['path']}\n"
                         f"Patch bytes: {patch['bytes']}; SHA256: {patch['sha256']}. "
                         f"Mutable candidate index (ordinary engineering access only): {patch['index_path']}. "
                         'Use search_history() or read the exact captured patch above. Ungraded edits do not replace the selected submission. During recovery, the latest actual grade selects the final candidate, including a partial result.')
                if patch.get('retention_error'):
                    text += '\n' + patch['retention_error']
            else:
                text += f"\nGraded patch runtime copy: {patch.get('status', 'unavailable')}; {patch.get('retention_error', 'see retained grade receipt')}. The known grade remains valid."
        if (session.critique and not result.get('terminal')
                and session.critique.get('patch_sha256') == result.get('patch_sha256')
                and not result.get('unchanged')):
            from .critique import observation
            text += observation(session.critique)
        return saved(text, "grade", {**result, "review_evidence": [session.critique["record_path"]] if session.critique else []})

    def current_status(state):
        if 'charged' in state and 'reserved' in state:
            usage = f"Course-priced task inference: ${state['charged']:.6f} settled charges"
            if state['reserved']:
                usage += f"; ${state['reserved']:.6f} reserved, not a known charge"
        else:
            usage = f"${state['spent']:.6f} course-priced charged/reserved exposure; settled charge unavailable"
        status = f"Current investigation owner: {session.model}\n"
        if recovery:
            status += "Phase: recovery investigation.\n"
        if session.selected_candidate:
            selected = session.selected_candidate
            status += (f"Selected final candidate: {selected.get('candidate_id', 'unavailable')}; "
                       f"{grade_counts(selected)}. SHA256: {selected.get('patch_sha256', 'unverified')}.\n")
        if session.note_pending == 'pass':
            status += "PASS CAPTURED; "
        if session.note_pending:
            status += "Investigation ended; finish with finish_task(memory_note=...).\n"
        status += "Current patch identity not reverified; receipts describe captured bytes, not later source.\n"
        timing = (f"\nTask solver elapsed: {time.time() - task_started_at:.1f} seconds; this owner: {time.time() - started:.1f} seconds.\n{tools.job_status()}"
                  f"\n{usage}")
        if last_generation_allocation is not None and (
                last_generation_allocation['requested_ceiling'] is not None
                or last_generation_allocation['admitted_maximum'] is not None
                or last_generation_allocation.get('incomplete')):
            timing += (f'\nPrevious main inference allocation — requested ceiling '
                       f'{last_generation_allocation["requested_ceiling"] if last_generation_allocation["requested_ceiling"] is not None else "provider-controlled allocation (default maximum unverified)"}; actual admitted maximum '
                       f'{str(last_generation_allocation["admitted_maximum"]) + " output tokens" if last_generation_allocation["admitted_maximum"] is not None else "provider-selected, maximum unverified"}.')
        allocation = state.get("task_allocation")
        if allocation:
            timing += (f"\nCurrent task allowance: {('$' + allocation['allowance_usd']) if allocation['allowance_usd'] is not None else 'no monetary ceiling declared'}; "
                       f"accounted ${allocation['spent_usd']}; remaining {('$' + allocation['remaining_usd']) if allocation['remaining_usd'] is not None else 'unknown'}. "
                       "Initial solving, expert review, recovery and memory share this allowance. "
                       "It may increase as other tasks finish; it never decreases. "
                       "The shared submission limit still applies; this allowance does not reserve funding.")
            allowance = allocation['allowance_usd']
            if allowance is not None and float(allocation['spent_usd']) >= .8 * float(allowance):
                timing += (" Write the memory note and finish now." if session.note_pending else
                           " Finish your repair, prioritize verification and evaluate the candidate now.")
                timing += " No further inference is admitted after exhaustion."
        shared = state.get("run_budget")
        if shared is not None:
            timing += f"\nShared-run inference: ${shared['spent']:.6f} settled charges"
            if shared['reserved']:
                timing += f"; ${shared['reserved']:.6f} reserved, not a known charge"
            if shared['unknown']:
                timing += f"; {shared['unknown']} unknown-charge attempts"
            timing += '.'
            resources = resource_observation(shared)
            if resources:
                timing += '\n' + resources
        last = session.last_grade
        if last is None:
            return status + "Latest repair receipt: none.\n" + timing
        name, identity = grade_reference(last)
        passed = last['tests_failed'] == 0 and last['tests_total'] > 0
        status += (f"Latest repair receipt: {name}; "
                   f"{'FULL PASS' if passed else 'NOT A FULL PASS'}; {grade_counts(last)}.\n"
                   "This receipt describes candidate bytes captured from your own investigation's workspace.\n")
        status += f"Graded patch SHA256: {identity}.\n" if identity else "Graded patch identity not verified: receipt has no valid SHA256.\n"
        patch = last.get('graded_patch')
        if isinstance(patch, dict) and patch.get('status') in ('retained', 'partial') and patch.get('path'):
            status += f"Exact captured graded patch (readable): {patch['path']}.\n"
        if isinstance(patch, dict) and patch.get('receipt_path'):
            status += f"Exact grading receipt: {patch['receipt_path']}.\n"
        if last.get("retention_error"):
            status += "\n" + last["retention_error"]
        return status + timing

    def active_schemas():
        if session.note_pending:
            return [item for item in schemas if item['function']['name'] in
                    ('read_file', 'search_history', 'compact_context', 'job', 'list_jobs')] + control_schemas(note=session.note_pending)
        return schemas

    def current_profile(state):
        # Admission observations are route-scoped scheduling evidence, not limits
        # or generation settings. Keep the discovered profile immutable.
        return {**profiles[session.model],
                'input_capacity_observation': state.get('capacity_observations', {}).get(session.model),
                'requested_output_ceiling': DEFAULT_OUTPUT_CEILING}

    def main_request(state, *, force=False, maintenance=None):
        profile = current_profile(state)
        ceiling = DEFAULT_OUTPUT_CEILING
        notes = ''
        selected_schemas = ctx.inspection_schemas(active_schemas()) if maintenance else active_schemas()
        status = current_status(state)
        if maintenance:
            measurement = maintenance['input_measure']
            status += '\nMaintenance scheduling observation: ' + json.dumps({
                **{key: maintenance[key] for key in ('reason', 'usable_context', 'threshold', 'enforcement')},
                'measured_input_tokens': measurement['estimate'],
                'presentation': 'temporary capacity recovery' if ctx.recovering else 'ordinary working context',
                'exact': measurement.get('exact', False),
                'method': measurement.get('estimate_method', measurement.get('method'))})
        request = ctx.build(session.model, profile, selected_schemas, notes, status,
                            force=force, maintenance=maintenance is not None)
        if maintenance:
            request['tool_choice'] = maintenance['tool_choice']
            ctx.last_meter = ctx._measure(request, profile)
        if ceiling is not None:
            request['max_tokens'] = ceiling
        return request

    def maintenance_failure(kind, detail):
        """A bounded repair of the auxiliary phase, never a solver turn quota."""
        maintenance_record['failed_attempts'] += 1
        exhausted = maintenance_record['failed_attempts'] >= MAX_FAILURES
        maintenance_record.update(status='failure_exhausted' if exhausted else 'retrying',
                                  failure_kind=kind, error=str(detail))
        if exhausted:
            session.reason = 'infrastructure_error'
            session.termination_detail = (
                f'Required context maintenance could not complete after {MAX_FAILURES} invalid responses: {kind}: {detail}')
        return (f" Required maintenance attempt did not complete ({kind}); "
                f"failure {maintenance_record['failed_attempts']}/{MAX_FAILURES}. "
                + ('Maintenance failed; the last accepted view and selected captured candidate are preserved. '
                   'No ungraded changes will be evaluated by this failure.' if exhausted else
                   'Maintenance remains pending. Inspect permitted evidence if needed, then call compact_context(summary=...). '
                   'Prose alone does not complete maintenance.'))

    def finish_maintenance():
        nonlocal maintenance_record
        if maintenance_record is None:
            return
        if '_turn_started' in maintenance_record:
            turn = {key: maintenance_record[key] for key in
                    ('request', 'response', 'usage', 'attempt', 'status', 'error', 'failure_kind', 'input_estimate', 'gateway_calls_before')
                    if key in maintenance_record}
            turn.update(seconds=time.monotonic() - maintenance_record.pop('_turn_started'),
                        before_view=maintenance_record.pop('_turn_before_view'), after_view=ctx.view_record(ctx.view))
            last_call = turn.get('attempt', {}).get('call')
            turn['gateway_call_range'] = ([turn['gateway_calls_before'] + 1, last_call]
                if type(last_call) is int and last_call > turn['gateway_calls_before'] else None)
            turn['before_view_sha256'] = hashlib.sha256(encoded(turn['before_view'])).hexdigest()
            turn['after_view_sha256'] = hashlib.sha256(encoded(turn['after_view'])).hexdigest()
            maintenance_record['attempts'].append(turn)
        if maintenance_record['status'] in ('inspecting', 'retrying') and not session.reason:
            return
        maintenance_record['after_view'] = ctx.view_record(ctx.view)
        maintenance_record['before_view_sha256'] = hashlib.sha256(encoded(maintenance_record['before_view'])).hexdigest()
        maintenance_record['after_view_sha256'] = hashlib.sha256(encoded(maintenance_record['after_view'])).hexdigest()
        maintenance_record['seconds'] = time.monotonic() - maintenance_record.pop('_started')
        if session.reason:
            maintenance_record['termination'] = session.reason
        session.maintenance.finish()
        maintenance_record = None

    def save_maintenance():
        if not session.maintenance.records:
            return
        try:
            (runtime / 'maintenance.json').write_bytes(encoded([
                {key: value for key, value in record.items() if not key.startswith('_')}
                for record in session.maintenance.records]))
        except OSError as error:
            session.maintenance.records[-1]['retention_error'] = str(error)

    try:
        runtime.mkdir(parents=True, exist_ok=True)
        state = gateway.state()
        if recovery:
            (runtime / 'capture-base.txt').write_text(recovery['source_base'])
        tools = Tools(root, runtime)
        startup = tools.execute("bash", {"command": (
            'pwd; git status --short --branch; '
            'for p in python3 python node npm go cargo make git rg; do command -v "$p" || true; done\n'
            'printf "\\nRuntime resource observations (platform facts, not harness limits):\\n"\n'
            'uname -sm\n'
            'printf "Cgroup quota/memory files, raw values; unreadable or absent files omitted:\\n"\n'
            'for p in /sys/fs/cgroup/cpu.max /sys/fs/cgroup/memory.max '
            '/sys/fs/cgroup/cpu/cpu.cfs_quota_us /sys/fs/cgroup/cpu/cpu.cfs_period_us '
            '/sys/fs/cgroup/memory/memory.limit_in_bytes; do '
            'if test -r "$p"; then printf "%s: " "$p"; cat "$p"; fi; done'
        ), "yield_time_ms": 1000})
        system = Path(__file__).with_name("prompt.txt").read_text().format(
            root=root, runtime=runtime, budget=state["limit"])
        if recovery is not None:
            from .recovery import brief_reference, instructions, observation
            session.model = "standard"
            if session.model not in profiles:
                raise ValueError("Configured recovery model has no verified route")
            system += "\n\n" + instructions()
            startup = ToolResult(startup.display + observation(recovery), startup.full_path,
                                 {**startup.metadata, 'persistent_reference': brief_reference(recovery)})
        if memory:
            from .memory import evidence_paths
            paths = evidence_paths(runtime.parent if recovery else runtime)
            startup = ToolResult(startup.display + "\n\n[Prior tasks' memory index — historical interpretations, not new requirements]\n" + memory,
                                 startup.full_path, {**startup.metadata, 'memory_evidence': paths})
        from .critique import investigation_skills
        system += "\n\n" + investigation_skills(root, runtime)
        ctx = context_factory(system, public_task(task), runtime)
        ctx.set_startup(asdict(startup))
        session.context = ctx
        controls = control_schemas()
        for name, description in (control_descriptions or {}).items():
            next(item['function'] for item in controls if item['function']['name'] == name)['description'] = description

        schemas = tool_schemas() + controls + context_schemas()
        requests = runtime / "requests"
        requests.mkdir(exist_ok=True)
        consecutive_failures = 0
        while True:
            try:
                while not session.reason:
                    finish_maintenance()
                    save_maintenance()
                    state = gateway.state()
                    try:
                        try:
                            if maintenance_record is None:
                                # Measure exactly the ordinary presentation about to be sent,
                                # including newly completed results; no previous-size baseline.
                                request = main_request(state)
                                # A secured pass should proceed directly to its note. Only
                                # an actual provider capacity refusal needs maintenance here.
                                if not session.note_pending or ctx.recovering:
                                    profile = current_profile(state)
                                    measurement = ctx.maintenance_measure(request, profile)
                                    maintenance_record = (session.maintenance.recover(session.model, profile, measurement,
                                        ctx._provider_capacity_detail) if ctx.recovering else
                                        session.maintenance.offer(session.model, profile, measurement))
                                if maintenance_record is not None:
                                    maintenance_record.update(before_view=ctx.view_record(ctx.view), _started=time.monotonic(),
                                                              attempts=[], inspections=[], inspection_exchanges=[])
                            if maintenance_record is not None:
                                for key in ('request', 'response', 'usage', 'attempt', 'error', 'failure_kind', 'input_estimate'):
                                    maintenance_record.pop(key, None)
                                maintenance_record.update(status='offered', gateway_calls_before=state['calls'], _turn_started=time.monotonic(),
                                                          _turn_before_view=ctx.view_record(ctx.view))
                                request = main_request(state, maintenance=maintenance_record)
                            request_path = requests / f"{session.iterations + 1:04d}.json"
                            request_path.write_bytes(encoded(request))
                            if maintenance_record is not None:
                                maintenance_record.update(request=str(request_path), input_estimate=dict(ctx.last_meter))
                            else:
                                for record in session.maintenance.records:
                                    if 'next_request' not in record:
                                        record['next_request'] = str(request_path)
                            answer = gateway.complete(request)
                            if maintenance_record is None:
                                ctx.accept_provider_request()
                        except ProviderCapacityRejected as error:
                            observed = gateway.state()
                            attempt = observed.get('last_attempt', {})
                            if maintenance_record is not None:
                                maintenance_record.update(status='provider_rejected', attempt=attempt, error=str(error))
                            output_only = error.kind == 'output' or (error.kind == 'combined' and error.supported_output_maximum)
                            if output_only:
                                raise InfrastructureError(
                                    f"Provider output allocation could not be corrected from its explicit feedback. "
                                    f"Selected input was preserved; no context reduction requested. {error}") from error
                            new_continuation = any(ex.messages for number, ex in enumerate(ctx.exchanges, 1)
                                if number > ctx._provider_capacity_exchange and number not in ctx.view.hidden)
                            if ctx.recovering and getattr(ctx, 'recovery_fallback', False) and not new_continuation:
                                raise CapacityError(f"Provider also rejected the minimal recovery request; selected view and exact evidence preserved. {error}") from error
                            session.iterations += 1
                            session.model_uses[session.model] = session.model_uses.get(session.model, 0) + 1
                            failure = {'kind': 'provider_capacity_rejection', 'attempt': attempt,
                                       'capacity_kind': error.kind, 'response_path': error.response_path,
                                       'operation': getattr(gateway, 'last_operation', None),
                                       'error': str(error), 'actions_executed': False}
                            with (requests / f'{session.iterations:04d}-response.json').open('xb') as output:
                                output.write(encoded(failure))
                            feedback = ''
                            if maintenance_record is not None:
                                # No model response was generated. Capacity recovery has
                                # its own unchanged-minimal-request stop above; it does
                                # not consume the model's protocol-repair attempts.
                                maintenance_record.update(status='retrying', failure_kind='provider_capacity_rejection')
                                feedback = (' Maintenance remains pending. This pre-generation capacity refusal did not consume a '
                                            'maintenance response-repair attempt. Inspect permitted evidence if needed, then apply '
                                            'compact_context(summary=...).')
                            ctx.append_harness(
                                f"The provider definitively rejected attempt {attempt.get('call')} before generation: {error}. "
                                "No tool action or grading executed. The same model still owns this investigation. "
                                + ("The provider did not establish that input alone is excessive. " if error.kind in ("ambiguous", "combined") else "") +
                                "The selected working view is unchanged. A temporary recovery request will let you inspect "
                                "exact evidence and completed jobs, then call compact_context. This is actual provider feedback, not a byte-estimate rejection." + feedback,
                                model=session.model, metadata=failure)
                            session.maintenance.bind_route(session.model, current_profile(observed))
                            ctx.mark_provider_capacity(str(error))
                            continue
                    except (ProviderRejected, ResponseRejected) as error:
                        if maintenance_record is None:
                            raise
                        state = gateway.state()
                        if state.get('run_budget', {}).get('stop_reason'):
                            raise
                        session.iterations += 1
                        session.model_uses[session.model] = session.model_uses.get(session.model, 0) + 1
                        maintenance_record.update(status='provider_rejected', attempt=state.get('last_attempt'), error=str(error))
                        if (isinstance(error, ProviderRejected) and maintenance_record['tool_choice'] == 'required'
                                and 'tool_choice' in str(error).lower()):
                            maintenance_record.update(tool_choice='auto', enforcement='loop', status='retrying',
                                                      capability_rejection=str(error))
                            continuation = 'Native required tool selection was rejected on this route. The loop still requires a decision; the next request uses auto with application-level enforcement and unchanged reasoning settings.'
                        elif isinstance(error, ResponseRejected):
                            continuation = maintenance_failure('malformed_response', error)
                        else:
                            session.reason, session.termination_detail = 'infrastructure_error', str(error)
                            continuation = 'Required maintenance inference failed. The investigation stops with its previous working view and selected candidate preserved.'
                        ctx.append_harness(f'Maintenance inference rejected: {error}. No tool actions executed; '
                            'the working view is unchanged. ' + continuation,
                            model=session.model, metadata={'kind': 'maintenance_rejection', 'actions_executed': False,
                                                           'attempt': state.get('last_attempt')})
                        continue
                    except UncertainInference as error:
                        # The RPC has a terminal failure receipt. Its provider attempt is
                        # never resent; a new loop turn requires a fresh admission.
                        state = gateway.state()
                        if not recoverable_inference(state, session.model):
                            raise
                        session.iterations += 1
                        session.model_uses[session.model] = session.model_uses.get(session.model, 0) + 1
                        attempt = state['last_attempt']
                        feedback = ''
                        if maintenance_record is not None:
                            maintenance_record.update(attempt=attempt)
                            feedback = maintenance_failure('interrupted', error)
                        last_generation_allocation = {'requested_ceiling': attempt['requested_output_ceiling'],
                                                      'admitted_maximum': attempt['admitted_output_maximum'], 'incomplete': True}
                        operation = getattr(gateway, 'last_operation', None)
                        failure_path = requests / f'{session.iterations:04d}-response.json'
                        failure = {'kind': 'uncertain_inference', 'error': str(error), 'attempt': attempt,
                                   'operation': operation, 'budget': state, 'actions_executed': False}
                        with failure_path.open('xb') as output:
                            output.write(encoded(failure))
                        ctx.append_harness(
                            f"Inference attempt {attempt['call']} ended with a completed gateway failure: {error}. "
                            + (f"Gateway operation {operation['operation_id']} is terminal ({operation['state']}). " if operation else '') +
                            "No tool actions from that response executed and no grading was requested. "
                            "Earlier completed tool operations and repository changes remain valid; do not repeat them. "
                            f"The interrupted request will not be reissued. Its charged or reserved course exposure is ${attempt.get('course_price', attempt['reservation']):.9f}; "
                            f"reservation retained: {attempt['reservation_retained']}. Exact partial response remains in the gateway archive: {attempt['response_path']}. "
                            f"Readable failure/accounting receipt: {failure_path}. Continue the same investigation with a new complete action; "
                            "the next inference receives a new operation identity. Fresh attempts are paced during consecutive transport failures." + feedback,
                            model=session.model, metadata={**failure, 'response_path': str(failure_path)})
                        consecutive_failures += 1
                        delay = max(min(30., 2. ** min(consecutive_failures - 1, 5)), attempt.get('retry_after_seconds', 0))
                        wait_after_failure(delay, gateway, runtime)
                        continue
                    consecutive_failures = 0
                    if isinstance(answer, dict) and isinstance(answer.get('budget'), dict):
                        state = answer['budget']
                    # The logical main inference has settled. Validation rejection is
                    # still a consumed generation; physical retries happened inside the
                    # gateway with this same request and allocation.
                    if isinstance(answer, dict) and isinstance(answer.get('generation_allocation'), dict):
                        last_generation_allocation = dict(answer['generation_allocation'])
                        last_generation_allocation['incomplete'] = answer.get('finish_reason') not in ('stop', 'tool_calls')
                    session.iterations += 1
                    session.model_uses[session.model] = session.model_uses.get(session.model, 0) + 1
                    (requests / f"{session.iterations:04d}-response.json").write_bytes(encoded(answer))
                    usage = answer.get("usage", {}) if isinstance(answer, dict) else {}
                    if isinstance(usage, dict):
                        ctx.observed_usage(request, usage, answer.get('input_receipt'),
                                           input_receipt_invalidated=answer.get('input_receipt_invalidated', False))
                        if maintenance_record is not None:
                            maintenance_record.update(usage=usage, attempt=state.get('last_attempt'),
                                                      response=str(requests / f'{session.iterations:04d}-response.json'))
                        else:
                            for record in session.maintenance.records:
                                if record.get('next_request') == str(request_path):
                                    record['next_request_usage'] = usage
                    try:
                        message = validate_response(answer)
                    except ValueError as error:
                        if maintenance_record is not None:
                            maintenance_record.update(status='response_rejected', error=str(error))
                        finish = answer.get("finish_reason") if isinstance(answer, dict) else None
                        response_recovery = "The response was truncated; continue with a complete smaller action." if finish == "length" else "Continue with a complete valid assistant action."
                        if maintenance_record is not None:
                            response_recovery += maintenance_failure('truncated' if finish == 'length' else 'malformed_response', error)
                        ctx.append_harness(f"Provider response rejected before any action: {error}. No tools ran and no grading was requested. {response_recovery} Exact response: {requests / f'{session.iterations:04d}-response.json'}.",
                            model=session.model, metadata={'kind': 'response_rejection', 'response_path': str(requests / f'{session.iterations:04d}-response.json'),
                                                          'finish_reason': finish, 'usage': usage, 'actions_executed': False})
                        continue
                    if not message.get("tool_calls"):
                        if maintenance_record is not None:
                            feedback = maintenance_failure('no_decision', 'Completed response supplied no maintenance action')
                            ctx.append_harness('No tool actions executed.' + feedback, model=session.model, messages=[message],
                                metadata={'kind': 'maintenance_protocol', 'actions_executed': False,
                                          'response_path': str(requests / f'{session.iterations:04d}-response.json'), 'usage': usage})
                            continue
                        if not (message.get("content") or "").strip():
                            next_action = (
                                "Capacity recovery permits search_history, compact_context, bounded read_file of owned immutable evidence or saved memory, and bounded read-only job inspect of an existing owned job. "
                                "Choose a fitting working view with compact_context before repository tools or submission become available."
                                if ctx.recovering else
                                "This investigation has ended; use finish_task(memory_note=...) to save your observation and finish."
                                if session.note_pending else
                                "Continue the investigation with a complete response or tool action; use evaluate_candidate() to grade a prepared repair.")
                            ctx.append_harness(f"Provider response ended with finish_reason={answer['finish_reason']!r} but no visible message or tool action. No tools ran and no grading was requested. {next_action}",
                                model=session.model, messages=[message], metadata={'kind': 'no_action', 'finish_reason': answer['finish_reason'],
                                                                                 'usage': usage, 'actions_executed': False})
                        else:
                            ctx.append([message], model=session.model)
                        continue
                    try:
                        batch = validate_batch(message, ctx.seen_call_ids,
                            control_schemas(note=session.note_pending) if session.note_pending else controls)
                        available = {t['function']['name'] for t in request['tools']}
                        if any(name not in available for _, name, _ in batch):
                            raise ValueError('Tool is not available in this request')
                        if maintenance_record is not None or ctx.recovering:
                            ctx.validate_recovery_batch(batch, message, owned_job=tools._registry.get,
                                purpose='Maintenance' if maintenance_record is not None else 'Capacity recovery')
                    except (ValueError, KeyError, TypeError) as error:
                        feedback = ''
                        if maintenance_record is not None:
                            feedback = maintenance_failure('invalid_batch', error)
                        # Malformed arguments cannot be serialized as a valid tool exchange.
                        # Preserve complete original arguments in the response archive;
                        # no rejected executable group is fabricated in the continuation.
                        visible = {key: value for key, value in message.items() if key != 'tool_calls'}
                        ctx.append_harness(f"Entire tool batch rejected before execution: {error}. Executable tools in this request: {', '.join(x['function']['name'] for x in request['tools'])}. Original response: {requests / f'{session.iterations:04d}-response.json'}" + feedback,
                            model=session.model, messages=[visible], metadata={'kind': 'batch_rejection', 'response_path': str(requests / f'{session.iterations:04d}-response.json'),
                                                                             'finish_reason': answer['finish_reason'], 'usage': usage, 'actions_executed': False})
                        continue
                    results, messages = [], [message]
                    pending_context = None
                    called_model = session.model
                    for call, name, args in batch:
                        trace.record("tool_call", {"iteration": session.iterations, "tool_name": name, "arguments": args})
                        try:
                            if name == "search_history":
                                result = saved(ctx.search(**args), "search_history")
                            elif name == "compact_context":
                                try:
                                    proposed = ctx.compact(args['summary'])
                                except ValueError as error:
                                    feedback = maintenance_failure('invalid_summary', error) if maintenance_record is not None else ''
                                    result = saved(
                                        f"Compaction rejected before commit: {error}. The working view is unchanged. "
                                        f"Your exact proposal remains at {requests / f'{session.iterations:04d}-response.json'} "
                                        f"(tool call {call['id']}). Correct the argument and call compact_context again." + feedback,
                                        "compact_context", {"error": True, "effect_status": "not_applied"})
                                else:
                                    pending_context = (proposed, len(results))
                                    result = ToolResult("Compaction prepared; the replacement will commit with its archived exchange.", "", {"kind": "compact_context"})
                            elif name in ("evaluate_candidate", "end_attempt", "finish_task"):
                                result = candidate_action(name, args)
                            elif name == "job" and (ctx.recovering or maintenance_record is not None):
                                result = tools.recovery_job(args, purpose='maintenance' if maintenance_record is not None else 'recovery')
                            else:
                                result = tools.execute(name, args)
                        except (BudgetExceeded, InfrastructureError):
                            raise
                        except Exception as error:
                            result = saved(f"Tool failed: {type(error).__name__}: {error}. Inspect current state before repeating any uncertain mutation.", name, {"error": True})
                        results.append(asdict(result))
                        messages.append({"role": "tool", "tool_call_id": call["id"], "content": result.display})
                        if name != 'compact_context' or pending_context is None:
                            trace.record("tool_result", {"iteration": session.iterations, "tool_name": name, "result": result.display,
                                                "is_error": bool(result.metadata.get("error"))})
                    # Validate the selected presentation with actual results and authoritative state.
                    # A rejected view never rolls back or replays earlier coding effects.
                    if pending_context is not None:
                        proposed, position = pending_context
                        committed_view = None
                        state = gateway.state()
                        try:
                            notice = ctx.prepare_edit(proposed, pending_messages=messages, pending_results=results,
                                source_model=called_model, status=current_status(state), notes='',
                                next_schemas=active_schemas(),
                                maintenance=maintenance_record is not None)
                            committed_view = proposed
                        except (ValueError, OSError, CapacityError) as error:
                            notice = f"Compaction rejected; previous view unchanged: {error}. Correct the reported error and call compact_context again."
                            if maintenance_record is not None:
                                if isinstance(error, ValueError):
                                    notice += maintenance_failure('invalid_edit', error)
                                else:
                                    maintenance_record.update(status='archive_failed', error=str(error))
                                    session.reason, session.termination_detail = 'infrastructure_error', f'Context maintenance archive failed: {error}'
                        # The applied view cannot depend on optional receipt retention.
                        try:
                            final_result = saved(notice, "compact_context", {"error": notice.startswith("Compaction rejected")})
                        except OSError as error:
                            final_result = ToolResult(notice + f"\nEdit receipt retention failed: {error}.", "", {"kind": "compact_context", "retention_error": str(error)})
                        results[position] = asdict(final_result)
                        messages[position + 1]['content'] = final_result.display
                        trace.record("tool_result", {"iteration": session.iterations, "tool_name": "compact_context",
                            "result": final_result.display, "is_error": bool(final_result.metadata.get('error'))})
                    archived_pair = True
                    if pending_context is not None and committed_view is not None:
                        # One immutable exchange write retains the result and selected
                        # view together, then updates the live view in memory.
                        try:
                            ctx.append(messages, results, called_model, view=committed_view)
                        except (OSError, ValueError) as error:
                            if maintenance_record is None:
                                raise
                            archived_pair = False
                            maintenance_record.update(status='archive_failed', error=str(error))
                            session.reason, session.termination_detail = 'infrastructure_error', f'Context maintenance archive failed: {error}'
                            ctx.append_harness(f'Maintenance context retention failed before view commit: {error}. '
                                'The previous working view remains selected; no repository actions executed. The investigation stops with its selected captured candidate preserved. '
                                f'Exact response: {requests / f"{session.iterations:04d}-response.json"}.',
                                model=called_model, metadata={'kind': 'maintenance_rejection', 'actions_executed': False})
                        else:
                            ctx.edits.append({'operation': 'compact_context', 'revision': committed_view.revision})
                            if maintenance_record is not None:
                                maintenance_record.update(status='accepted')
                    else:
                        try:
                            ctx.append(messages, results, called_model)
                        except (OSError, ValueError) as error:
                            if maintenance_record is None:
                                raise
                            archived_pair = False
                            maintenance_record.update(status='archive_failed', error=str(error))
                            session.reason, session.termination_detail = 'infrastructure_error', f'Context maintenance archive failed: {error}'
                            ctx.append_harness(f'Maintenance observation retention failed: {error}. The chosen view is unchanged; '
                                'no repository actions executed. The investigation stops with its selected captured candidate preserved. Exact response and saved read results remain available.',
                                model=called_model, metadata={'kind': 'maintenance_rejection', 'results': results,
                                    'response_path': str(requests / f'{session.iterations:04d}-response.json')})
                    if maintenance_record is not None:
                        inspected = [(call, name, args, result) for (call, name, args), result in zip(batch, results) if name != 'compact_context']
                        if inspected:
                            maintenance_record['inspections'].append({'request': str(request_path),
                                'exchange': len(ctx.exchanges) if archived_pair else None,
                                'paired_exchange': archived_pair,
                                'retention_receipt_exchange': None if archived_pair else len(ctx.exchanges),
                                'calls': [call['id'] for call, _, _, _ in inspected],
                                'operations': [{'name': name, 'arguments': args} for _, name, args, _ in inspected],
                                'results': [result for _, _, _, result in inspected]})
                            if archived_pair:
                                maintenance_record['inspection_exchanges'].append(f'E{len(ctx.exchanges)}')
                        if maintenance_record['status'] == 'offered':
                            if any(r.get('metadata', {}).get('error') for r in results):
                                feedback = maintenance_failure('inspection_failed', 'A requested read-only inspection failed')
                                ctx.append_harness(feedback, model=called_model, metadata={'kind': 'maintenance_protocol'})
                            else:
                                maintenance_record['status'] = 'inspecting'
                    if maintenance_record is None:
                        for record in session.maintenance.records:
                            if 'next_action' not in record:
                                record['next_action'] = {'request': str(request_path), 'exchange': len(ctx.exchanges),
                                                         'calls': [call['id'] for call, _, _ in batch], 'results': results}
                    event = failed_grade_event(result, session.previous_grade)
                    if event is not None:
                        event['content'] += (f"\nOwner: {session.model}, unchanged. If no useful repair remains, end_attempt(reason='...') settles your "
                            "current candidate and ends this investigation. An unsuccessful initial end can admit one fresh standard recovery; ending recovery finalizes its latest actually graded candidate, or retains the initial candidate if recovery has no new grade. The repository and working context remain open after this failed grade.")
                        ctx.append_harness(event['content'], model=called_model, metadata={'kind': 'grade', 'grade': session.last_grade})
            except BudgetExceeded:
                # Exhaustion ends inference, including memory-note inference.
                # Local allocation and platform refusals share final settlement.
                raise
            break
    except BudgetExceeded as error:
        session.termination_detail, cap_pending = str(error), True
    except (CapacityError, InfrastructureError) as error:
        session.reason, session.termination_detail = "infrastructure_error", str(error)
    except Exception as error:
        session.reason = "infrastructure_error"
        session.termination_detail = type(error).__name__ + ": " + str(error)
    finally:
        if maintenance_record is not None:
            finish_maintenance()
        save_maintenance()
        delivery = trace.close()
        logging_errors.extend(delivery["errors"])
        outcome = finish_investigation(session, root, runtime, gateway, tools, grade,
                                       started, state, logging_errors, cap_pending=cap_pending, trace_delivery=delivery)
    return outcome


def run_recovery_owner(task, root, runtime, gateway, grade, log, profiles, *, boundary, brief,
                       prepared_source, outcomes, started, task_started_at):
    """Prepare the original-source workspace and run the single fresh recovery owner."""
    import os
    from .recovery import prepare
    ws, receipt = prepare(root, runtime, boundary, preserved_source=prepared_source,
                          recovery_brief=brief)
    owner_runtime = runtime / 'recovery-owner'
    owner_runtime.mkdir(exist_ok=False)
    if isinstance(gateway, RemoteGateway):
        gateway.runtime = owner_runtime
    grade({'recovery_workspace': str(ws.path)})
    prior_environment = {key: os.environ.get(key) for key in ws.env}
    try:
        os.environ.update(ws.env)
        final = run(task, ws.path, runtime / 'recovery-owner', gateway, grade, log,
                    profiles, recovery=receipt, require_memory_note=True, task_started_at=task_started_at)
    finally:
        for key, value in prior_environment.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
    outcomes.append(final)
    return {**final, 'owners': outcomes, 'recovery_started': True,
            'iterations': sum(item['iterations'] for item in outcomes),
            'critique': outcomes[0].get('critique') if len(outcomes) > 1 else None,
            'expert_reviews': sum(item.get('expert_reviews', 0) for item in outcomes),
            'seconds': time.monotonic() - started, 'recovery_preparation': receipt,
            'model_uses': {model: sum(item.get('model_uses', {}).get(model, 0) for item in outcomes)
                           for model in profiles}}


def transition_failure(outcomes, started, error):
    """A failed transition is infrastructure failure, never a model give_up."""
    return {**(outcomes[-1] if outcomes else {}), 'reason': 'infrastructure_error',
            'termination_detail': f'Recovery transition failed: {type(error).__name__}: {error}',
            'owners': outcomes, 'seconds': time.monotonic() - started,
            'owned_work_cleanup': {'confirmed': True, 'note': 'No new owner started before transition completed.'}}


def run_task(task, root, runtime, gateway, grade, log, profiles, *, memory=None):
    """Production lifecycle: initial owner, at most one fresh recovery, same ledger."""
    from .recovery import preserve
    runtime, root = Path(runtime).resolve(), Path(root).resolve()
    outcomes = []
    started = time.monotonic()
    task_started_at = time.time()
    preparation = None

    def completed(value):
        value = {**value, 'seconds': time.monotonic() - started,
                 'task_preparation': preparation}
        runtime.mkdir(parents=True, exist_ok=True)
        retain_json(runtime / 'outcome.json', value)
        return value

    try:
        try:
            prepared_source = preserve(root, runtime)
            preparation = {'recovery_available': True, **{key: prepared_source[key] for key in
                           ('snapshot_seconds', 'files', 'file_bytes', 'archive_bytes')}}
        except Exception as error:
            prepared_source = None
            preparation = {'recovery_available': False,
                           'error': f'Intake snapshot failed: {type(error).__name__}: {error}'}
        initial = run(task, root, runtime, gateway, grade, log, profiles,
                      require_memory_note=True, memory=memory, task_started_at=task_started_at)
        initial['initial_owner_seconds'] = initial['seconds']
        outcomes.append(initial)
        last = initial.get('grade') or {}
        if (initial['reason'] != 'give_up' or not initial.get('ended_after_failed_grade') or not isinstance(last.get('tests_failed'), int)
                or (last.get('tests_total', 0) > 0 and last['tests_failed'] == 0) or not initial['owned_work_cleanup']['confirmed']):
            return completed(initial)
        if prepared_source is None:
            return completed({**initial, 'recovery_started': False,
                              'recovery_unavailable': preparation['error']})
        state = gateway.state()
        shared = state.get('run_budget') or {}
        if shared.get('admission_closed') or shared.get('stop_reason') or (state.get('remaining') is not None and state['remaining'] <= 0):
            return completed(initial)
        try:
            boundary = grade({'begin_recovery': True})
        except AllocationExceeded:
            # The last admitted note may consume the remaining allowance.
            # Its owner already settled; refusal to reopen is normal completion.
            return completed(initial)
        brief = (initial.get('critique') or {}).get('recovery_brief', '')
        outcome = run_recovery_owner(task, root, runtime, gateway, grade, log, profiles,
                                     boundary=boundary, brief=brief, prepared_source=prepared_source,
                                     outcomes=outcomes, started=started, task_started_at=task_started_at)
    except Exception as error:
        outcome = transition_failure(outcomes, started, error)
    return completed(outcome)


class RemoteGateway:
    def __init__(self, url, token, runtime=None):
        self.url, self.token = url.rstrip("/"), token
        self.runtime = Path(runtime) if runtime else None
        self.last_state = None
        self.last_operation = None

    def _post(self, path, payload):
        req = urllib.request.Request(self.url + path, data=encoded(payload) if payload is not None else None,
                                     headers={"Authorization": "Bearer " + self.token, "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=3700) as response:
                result = json.loads(response.read())
        except urllib.error.HTTPError as error:
            if path == "/state" and error.code in (408, 429, 502, 503, 504):
                error.close()
                raise
            result = json.loads(error.read())
        return result

    @staticmethod
    def _value(result):
        if not isinstance(result, dict):
            raise InfrastructureError("Gateway receipt must be an object")
        if result.get("state") == "completed":
            return result["result"]
        if result.get("state") == "failed":
            error = result["error"]
            result = {"error": error["message"], "type": error["type"], "details": error.get("details", {})}
        if result.get("ok") is False or ("error" in result and "message" not in result):
            kind = result.get("type", result.get("error_type", "InfrastructureError"))
            if kind == 'ProviderCapacityRejected':
                details = result.get('details', {})
                raise ProviderCapacityRejected(details.get('detail', result.get('error', 'Provider capacity rejection')),
                    kind=details.get('kind', 'unknown'), response_path=details.get('response_path', ''),
                    supported_output_maximum=details.get('supported_output_maximum'))
            raise {"AllocationExceeded": AllocationExceeded, "BudgetExceeded": BudgetExceeded, "CapacityError": CapacityError,
                   "ProviderRejected": ProviderRejected, "ResponseRejected": ResponseRejected,
                   "AccountingError": AccountingError, "UncertainInference": UncertainInference}.get(kind, InfrastructureError)(result.get("error", "Gateway failed"))
        return result.get("value", result)

    def call(self, path, payload=None):
        if path not in ("/complete", "/grade"):
            return self._value(self._post(path, payload))
        operation = uuid.uuid4().hex
        payload = {**(payload or {}), "operation_id": operation}
        self.last_operation = {'operation_id': operation, 'state': 'pending', 'route': path}
        if self.runtime:
            try:
                self.runtime.mkdir(parents=True, exist_ok=True)
                with (self.runtime / "operation-receipts.jsonl").open("ab") as stream:
                    stream.write(encoded(self.last_operation) + b"\n")
            except OSError as error:
                self.last_operation['local_receipt_error'] = str(error)
        transport_errors = (OSError, ValueError, http.client.HTTPException)
        try:
            receipt = self._post(path, payload)
        except transport_errors:
            receipt = {"state": "pending"}
        if not isinstance(receipt, dict):
            raise InfrastructureError("Gateway operation receipt must be an object; no duplicate operation requested")
        # A delivery failure never resubmits an effect. Look up its retained
        # receipt using the same identity; pending work is owned by the server.
        lookup_failures = 0
        while receipt.get("state") == "pending":
            try:
                receipt = self._post("/operation", {"operation_id": operation, "wait_seconds": 30})
                if not isinstance(receipt, dict):
                    raise InfrastructureError("Gateway operation receipt must be an object; no duplicate operation requested")
                lookup_failures = 0
            except transport_errors as error:
                lookup_failures += 1
                # A lost observation is not a failed remote operation. Keep
                # querying the same identity; platform/human shutdown owns stopping.
                self.last_operation = {'operation_id': operation, 'state': 'pending', 'route': path,
                                       'observation_error': str(error)}
                time.sleep(min(2 ** min(lookup_failures - 1, 5), 30))
        if receipt.get("state") in ("missing", "unknown"):
            raise InfrastructureError(f"Operation {operation} is {receipt['state']}; outcome cannot be verified and no duplicate execution was requested")
        self.last_operation = {'operation_id': operation, 'state': receipt.get('state'), 'route': path}
        if self.runtime:
            try:
                self.runtime.mkdir(parents=True, exist_ok=True)
                with (self.runtime / "operation-receipts.jsonl").open("ab") as stream:
                    stream.write(encoded({"route": path, **receipt}) + b"\n")
            except OSError:
                pass  # The server's durable receipt remains authoritative.
        return self._value(receipt)

    def state(self):
        delay = 1
        while True:
            if self.runtime and (self.runtime / "stop-request").exists():
                raise AccountingError("External stop received while awaiting accounting state")
            try:
                result = self.call("/state", {})
                if not isinstance(result, dict) or not all(k in result for k in ("spent", "remaining", "limit", "calls")):
                    raise ValueError("Malformed accounting state")
                self.last_state = result
                return result
            except (ConnectionError, TimeoutError, urllib.error.URLError, http.client.HTTPException) as error:
                if isinstance(error, urllib.error.HTTPError) and error.code not in (408, 429, 502, 503, 504):
                    raise AccountingError("Accounting state request was rejected") from error
                # This read has no effects to replay. Keep inference paused until
                # authoritative state returns; explicit shutdown still interrupts.
                deadline = time.monotonic() + delay
                while time.monotonic() < deadline:
                    if self.runtime and (self.runtime / "stop-request").exists():
                        raise AccountingError("External stop received while awaiting accounting state") from error
                    time.sleep(min(.5, max(0., deadline - time.monotonic())))
                delay = min(delay * 2, 5)
            except Exception as error:
                raise AccountingError("Authoritative accounting state is unavailable; inference cannot continue") from error

    def complete(self, request):
        result = self.call("/complete", {"request": request})
        if isinstance(result, dict) and isinstance(result.get("budget"), dict):
            self.last_state = result["budget"]
        return result


if __name__ == "__main__":
    config = json.loads(Path(sys.argv[1]).read_text())
    gateway = RemoteGateway(config["gateway_url"], config["token"], config["runtime"])
    run_task(config["task"], Path(config["root"]), Path(config["runtime"]), gateway,
        lambda args: gateway.call("/grade", args),
        lambda method, fields: gateway.call("/event", {"method": method, "fields": fields}), config["profiles"], memory=config.get("memory"))
