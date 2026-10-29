"""Readable derived views; original receipts and complete output stay addressable.

Like Harness.Presentation, unknown fields survive and strings remain literal.
Paging bounds delivery of a new observation, never masks older context.

Each tool view answers its call first: one process-status sentence with command
and output, numbered file text, ``path:line: text`` matches, changed files with
a diff, a grade sentence, or an attributed report. Identifiers and storage
paths follow under Details, and the evidence references end the result.
Shared formatting keeps the producer's field order and hierarchy, names empty
fields on one ``not recorded`` line and shows clocks in UTC. Display-only
additions (line-number prefixes, omitted-range markers) are labelled. Messages
the harness injects (notifications, agent messages, delegated context,
compaction evidence, recovered exchanges) are labelled text, not serialized
JSON; exact JSON remains at each evidence reference. Witnesses:
test_information_views, test_main_flow's CLI flow, test_capacity's slice
coverage and test_evidence_delivery's archive paging.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path


def clock(value):
    return datetime.fromtimestamp(value, timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')


def _label(key):
    text = str(key).lstrip('_').replace('_', ' ')
    return text[:-5] if text.endswith(' unix') else text


def _scalar(key, value):
    if isinstance(value, str):
        return value or '(empty)'
    if value is None or isinstance(value, bool):
        return json.dumps(value)
    name = str(key or '')
    if (name == 'at' or name.endswith('_at') or 'unix' in name or 'deadline' in name) and 1e9 <= value < 1e10:
        return clock(value)
    if isinstance(value, float) and 'seconds' in name:
        return f'{value:.2f}'.rstrip('0').rstrip('.')
    if name == 'mode' and type(value) is int:
        return oct(value)
    return json.dumps(value, ensure_ascii=False)


def _simple(value):
    return isinstance(value, (bool, int, float)) or isinstance(value, str) and '\n' not in value


def _block(lines):
    return ['  ' + line if line else line for line in lines]


def _lines(value, key=None):
    if isinstance(value, dict):
        lines, missing = [], []
        for k, v in value.items():
            label = _label(k)
            if v is None:
                missing.append(label)
                continue
            if isinstance(v, list) and v and all(_simple(item) for item in v):
                joined = ', '.join(_scalar(k, item) for item in v)
                if len(joined) <= 300:
                    lines.append(label + ': ' + joined)
                    continue
            if isinstance(v, (dict, list)):
                inner = _lines(v, k)
                lines.extend([label + ':', *_block(inner)] if inner else [label + ': (none)'])
            elif isinstance(v, str) and '\n' in v:
                lines.extend([label + ':', *_block(v.split('\n'))])
            else:
                lines.append(label + ': ' + _scalar(k, v))
        # Absence stays explicit without one line per empty field.
        if missing:
            lines.append('not recorded: ' + ', '.join(missing))
        return lines
    if isinstance(value, list):
        lines = []
        for item in value:
            inner = (_lines(item, key) if isinstance(item, (dict, list)) else
                     item.split('\n') if isinstance(item, str) and item else [_scalar(key, item)])
            inner = inner or ['(none)']
            lines.extend(['- ' + inner[0], *_block(inner[1:])])
        return lines
    return value.split('\n') if isinstance(value, str) else [_scalar(key, value)]


def facts(value):
    if isinstance(value, str):
        return value
    if value is None:
        return 'not recorded'
    if isinstance(value, (dict, list)):
        return '\n'.join(_lines(value)) or '(none)'
    return _scalar(None, value)


def _without(body, *keys):
    return {k: v for k, v in body.items() if k not in keys}


def _present(values):
    """A view's own optional entries; None there means not applicable, not unrecorded."""
    return {k: v for k, v in values.items() if v is not None}


def _relative(path, root):
    try:
        return str(Path(path).relative_to(root)) if root else str(path)
    except ValueError:
        return str(path)


def runtime_observation(snapshot, *, details=None):
    """A small decision view of recorded facts, followed by their complete details."""
    from .decision_context import candidate_comparison
    lines = []
    planning = snapshot.get('planning_boundary')
    if planning:
        lines.append(f"Planning boundary: {planning['phase']}; exploration responses used {planning['turns_used']}/{planning['max_turns']}.")
        if planning['phase'] == 'planning' and planning.get('policy_version') == 2:
            lines.append('Explore the repository. Batch independent reads/searches as parallel tool calls. A single planning decision response follows the exploration allowance.')
        elif planning['phase'] == 'decision':
            lines.append('Return the detailed repository-grounded plan with repair_plan action=continue, or action=skip with a reason. Only this decision tool is available.')
        elif planning['phase'] == 'planning':
            lines.append('Choose repair_plan action=continue with a plan, or action=skip with a reason, as a sole call before the exploration limit.')
    grade = snapshot.get('candidate_evidence') or {}
    if grade.get('counts_available'):
        lines.append(f"Latest session grade: {grade['tests_failed']}/{grade['tests_total']} failing on captured candidate {grade.get('candidate_sha256')}. Counts do not identify the cause.")
    else:
        lines.append('No completed count-bearing grade is recorded in this session; inherited evidence remains in its takeover brief when present.')
    source = snapshot.get('source_context') or {}
    captured = source.get('latest_capture', {}).get('candidate')
    if captured:
        comparison = candidate_comparison({'base': grade.get('candidate_base'), 'sha256': grade.get('candidate_sha256')}, captured)
        lines.append(f"Latest session capture: {captured.get('sha256')}. Comparison with graded bytes: {comparison}.")
    lines.append('Current live source has not been compared by this observation.')
    action = source.get('last_source_action')
    if action:
        meaning = {'no_source_writes': 'rejected before source writes; this call changed no files',
                   'patch_applied': 'patch applied; this does not establish tests passed',
                   'partial': 'partially applied; inspect per-file receipts before another edit',
                   'unknown': 'outcome uncertain; observe state before retrying',
                   'source_not_compared': 'process/tool result recorded; its effect on source has not been compared'}.get(action['outcome'], str(action['outcome']))
        lines.append(f"Latest recorded source-capable result: {action['tool']}: {meaning}.")
    plan = snapshot.get('repair_plan') or {}
    if plan.get('plan_id') and not plan.get('check_names'):
        lines.append('The registered plan contains zero executable checks; verification is incomplete.')
    verification = (plan.get('verification') or {}).get('candidate', {})
    if verification:
        lines.append(f"Latest plan verification: {verification.get('state')}; passed={verification.get('passed')}; comparison to latest capture={verification.get('comparison_to_latest_capture', 'not_compared')}.")
    for job in snapshot.get('job_observations', []):
        if job.get('state') != 'exited':
            lines.append(f"Existing job {job['id']}: {job.get('state')}; output: {job.get('output_path', 'not recorded')}.")
    if snapshot.get('uncertain_effects'):
        lines.append('Uncertain effects remain recorded below; they are not authorization to replay actions.')
    return 'Decision summary (session-local recorded evidence):\n' + '\n'.join(lines) + '\n\nObservation details:\n' + facts(snapshot if details is None else details)


def takeover_observation(brief):
    """Put the consequential source facts before identifiers and archive metadata."""
    return (brief['inherited_repair'] + '\nChanged paths: ' + (', '.join(brief['changed_files']) or '(none)') +
            '\n' + brief['head_relationship'] + '\n\nRecorded brief:\n' +
            facts(_without(brief, 'inherited_repair', 'changed_files', 'head_relationship')))


def job_line(job):
    """One process state sentence from supervisor receipts; no success is inferred."""
    ident, state, code = job.get('id'), job.get('state'), job.get('exit_code')
    elapsed = job.get('elapsed_seconds')
    after = f' after {_scalar("elapsed_seconds", elapsed)} s' if isinstance(elapsed, (int, float)) else ''
    if state == 'exited' and type(code) is int:
        status = f'exited with status {code}' if code >= 0 else f'terminated by signal {-code}'
        return f'Job {ident} {status}{after}.'
    if state == 'running':
        return f'Job {ident} is still running{after}.'
    if state == 'unknown':
        return f'Job {ident}: outcome unknown{after}; the process may have run.' + (f' {job["error"]}' if job.get('error') else '')
    return f'Job {ident}: {state}{after}.'


def _output_view(ident, output, preview, path):
    total = output.get('total_bytes', 0)
    retrieve = f'Full output: {path}; job(id="{ident}", offset=N) reads any byte range.'
    if preview:
        (head, tail), (gap_start, gap_end) = preview['ranges'], preview['omitted_range']
        return (f'Output ({total} bytes; showing bytes {head["offset"]}-{head["next_offset"]} and '
                f'{tail["offset"]}-{tail["next_offset"]}):\n' + head['content'] +
                ('' if head['content'].endswith('\n') else '\n') +
                f'[... bytes {gap_start}-{gap_end} omitted ...]\n' + tail['content'] + '\n' + retrieve)
    content, start, end = output.get('content', ''), output.get('offset', 0), output.get('next_offset', 0)
    if not total:
        return 'Output: (none)'
    if start == 0 and output.get('complete'):
        return f'Output ({total} bytes):\n' + content
    if start == end:
        if end >= total:
            return f'No new output after byte {end} ({total} bytes in total). ' + retrieve
        return f'{total} bytes of output are recorded; none are displayed in this observation. ' + retrieve
    more = '' if output.get('complete') else f'\nMore output follows: job(id="{ident}", offset={end}).'
    return f'Output bytes {start}-{end} of {total}:\n' + content + more + '\n' + retrieve


def _command_view(heading, body):
    output = body.pop('output')
    preview = body.pop('output_preview', None)
    lines = [heading, job_line(body)]
    if body.get('state') == 'running':
        lines.append(f'The response wait ended; observe this process with job(id="{body.get("id")}", action="poll"). '
                     'Repeating exec_command launches another process.')
    elif body.get('state') == 'unknown':
        lines.append('Do not replay automatically; observe files and processes first.')
    for key, label in (('command', 'Command'), ('cwd', 'Directory')):
        if body.get(key):
            lines.append(f'{label}: {body[key]}')
    lines.append(_output_view(body.get('id'), output, preview, body.get('output_path')))
    details = _without(body, 'id', 'command', 'cwd', 'state', 'exit_code', 'elapsed_seconds', 'output_path', 'error')
    # Absent cancellation fields mean none was requested; the tool contract says so.
    for key in ('cancel_at', 'kill_after_ms', 'cancellation', 'cancellation_remaining_seconds'):
        if details.get(key) is None:
            details.pop(key, None)
    return '\n'.join(lines) + '\nDetails:\n' + facts(details)


def _read_view(heading, body):
    content = body.pop('content')
    first, last = body.get('first_displayed_line'), body.get('last_displayed_line')
    lines = content.split('\n')
    if lines and lines[-1] == '' and content.endswith('\n'):
        lines.pop()
    if first is None:
        numbered = '(no content in this range)'
    else:
        width = len(str(first + max(len(lines) - 1, 0)))
        numbered = '\n'.join(f'{number:>{width}}\t{line}' for number, line in enumerate(lines, first))
    header = (f'{body.get("path")}: lines {first}-{last} of {body.get("total_lines")} '
              f'(bytes {body.get("offset")}-{body.get("next_offset")} of {body.get("total_bytes")}; '
              f'SHA256 {body.get("sha256")}).' if first is not None else
              f'{body.get("path")}: no lines at byte {body.get("offset")} of {body.get("total_bytes")}.')
    notes = ['Each line is prefixed with its 1-based number and a tab; the prefix is not file content.']
    if not body.get('range_complete', True):
        notes.append(f'max_output_bytes stopped this range at byte {body.get("next_offset")}; the last displayed line may be partial.')
    if not body.get('complete', True) and last is not None:
        notes.append(f'The file continues: read_file(path=..., start_line={last + 1}). offset always means bytes.')
    artifacts = body.get('_artifacts', {}).get('snapshot_ref', {})
    details = _present({'snapshot': artifacts.get('path'), 'display_encoding': body.get('display_encoding')})
    return heading + '\n' + header + '\n' + '\n'.join(notes) + '\n' + numbered + '\nDetails:\n' + facts(details)


def _search_view(heading, body, page):
    root = body.get('root')
    entries, hashes = [], {}
    for entry in page['entries']:
        path = entry.get('relative_path') or _relative(entry.get('path'), root)
        if 'line' in entry:
            entries.append(f"{path}:{entry['line']}: {entry['text'].rstrip(chr(10)).rstrip(chr(13))}")
            hashes[path] = entry.get('sha256')
        else:
            entries.append(path)
        extra = _without(entry, 'path', 'relative_path', 'line', 'byte_offset', 'sha256', 'text')
        if extra:
            entries.extend(_block(_lines(extra)))
    what = f'"{body["query"]}"' if body.get('query') else f'paths matching {body.get("glob")}'
    coverage = 'scan complete' if body.get('scan_complete') else 'scan INCOMPLETE; see errors below'
    lines = [heading, f'{body.get("matches")} matches for {what} under {root} ({coverage}):', *entries]
    if not page['complete']:
        lines.append('More results remain in the archived results file at next record offset '
                     f'{page["next_offset"]}; refine the search or read that artifact.')
    details = {**_present({'source_sha256': [f'{path} {digest}' for path, digest in hashes.items() if digest] or None,
               'errors': body.get('errors') or None, 'skipped_binary_files': body.get('skipped_binary_files') or None,
               'results_file': body.get('_artifacts', {}).get('results_ref', {}).get('path'),
               'next_record_offset': page['next_offset'] if not page['complete'] else None}),
               **_without(body, 'root', 'query', 'matches', 'scan_complete', 'errors', 'skipped_binary_files', 'content',
                          '_artifacts', 'next_offset', 'total_bytes', 'complete', 'format', 'search_page')}
    return '\n'.join(lines) + '\nDetails:\n' + facts(details)


PATCH_LETTERS = {'add': 'A', 'update': 'M', 'delete': 'D'}


def _patch_view(heading, body):
    files = body.get('files') or []
    rows = [f"{PATCH_LETTERS.get(row.get('operation'), '?')} {row.get('path')}" +
            ('' if row.get('status') == 'applied' else f" ({row.get('status')}" + (f": {row['error']}" if row.get('error') else '') + ')')
            for row in files]
    if body.get('applied'):
        changed = sum(1 for row in files if row.get('changed'))
        lead = f'Patch applied: {changed} file(s) changed.' if changed else 'Patch applied: no file content changed.'
    else:
        lead = f'Patch not applied (state {body.get("state")})' + (f': {body["error"]}' if body.get('error') else '.')
    lines = [heading, lead, *rows]
    if body.get('display_diff'):
        lines.append('Diff:\n' + body['display_diff'].rstrip('\n'))
        if body.get('display_diff_complete') is False:
            lines.append('Diff truncated for display; exact before/after bytes are in the archive below.')
    details = {k: body[k] for k in ('operation_id', 'patch_path', 'receipt_path', 'archive', 'atomicity',
                                    'source_writes_started', 'replay_safe', 'message', 'display_diff_error') if k in body}
    return '\n'.join(lines) + '\nDetails:\n' + facts(details)


def _grade_view(heading, body, authored):
    grade = body.pop('grade_facts')
    candidate = body.get('candidate') or {}
    failed, total = grade.get('tests_failed'), grade.get('tests_total')
    supplier = grade.get('supplier', 'grader')
    lead = (f'Grade from {supplier}: {failed} of {total} tests failed on candidate {grade.get("candidate_sha256")}.'
            if type(failed) is int and type(total) is int else
            f'Grade from {supplier}: counts unavailable (grading state {body.get("grading_state")}).')
    lines = [heading, lead]
    if grade.get('trend'):
        lines.append('Trend: ' + str(grade['trend']))
    if body.get('meaning'):
        lines.append(str(body.pop('meaning')))
    result = dict(body.pop('grade', None) or {})
    receipt = result.get('receipt')
    if isinstance(receipt, dict) and receipt.get('tests_failed') == failed and receipt.get('tests_total') == total:
        receipt = _without(receipt, 'tests_failed', 'tests_total')
        result = {**_without(result, 'receipt'), **({'receipt': receipt} if receipt else {})}
    if result.get('candidate_sha256') == grade.get('candidate_sha256'):
        result.pop('candidate_sha256')
    shown = _without(candidate, 'sha256') if candidate.get('sha256') == grade.get('candidate_sha256') else candidate
    details = {'candidate': shown, **_without(body, 'candidate'),
               **({'grade': result} if result else {}), 'previous_candidate': grade.get('previous_candidate')}
    return '\n'.join(lines) + authored + '\nDetails:\n' + facts(details)


def _jobs_view(heading, body):
    lines = [heading]
    for job in body.get('jobs', []):
        lines.append(job_line(job))
        size = (job.get('output') or {}).get('total_bytes')
        lines.extend(_block([f"Command: {job.get('command')}", f"Directory: {job.get('cwd')}",
                             f"Output: {size if size is not None else 'unknown'} bytes at {job.get('output_path')}; "
                             f"job(id=\"{job.get('id')}\", offset=0) reads it."]))
    return '\n'.join(lines) if len(lines) > 1 else heading + '\nNo jobs recorded in this session.'


def _status_view(heading, body):
    deadline = body.get('deadline') or {}
    lines = [heading]
    if deadline.get('deadline_unix') is not None:
        remaining = deadline.get('remaining_seconds')
        lines.append(f"Task deadline: {clock(deadline['deadline_unix'])}"
                     + (f"; {_scalar('remaining_seconds', remaining)} s remaining at observation" if isinstance(remaining, (int, float)) else '')
                     + (' (EXPIRED)' if deadline.get('expired') else '') + '.')
    lines.append('Active agents: ' + (', '.join(body.get('active_agent_ids') or []) or 'none'))
    jobs = body.get('running_or_uncertain_jobs') or []
    lines.append('Running or uncertain jobs: ' + (', '.join(f"{j.get('id')} ({j.get('state')})" for j in jobs) or 'none'))
    latest = (body.get('investigation') or {}).get('latest_evaluation') or {}
    receipt = (latest.get('result') or {}).get('receipt') or {}
    if type(receipt.get('tests_failed')) is int:
        lines.append(f"Latest evaluation: {receipt['tests_failed']} of {receipt.get('tests_total')} tests failed on candidate {latest.get('candidate_sha256')}.")
    if body.get('workflow_state'):
        lines.append('Workflow state: ' + str(body['workflow_state']))
    return '\n'.join(lines) + '\nDetails:\n' + facts(body)


def _parallel_view(heading, body):
    """Each member keeps its own tool view; group state and receipts follow."""
    lines = [heading, f"Parallel group {body.get('group_id')}: {body.get('state')}"
             + ('; cancellation requested' if body.get('cancel_requested') else '') + '.']
    for member in body.get('members', []):
        lines.append(f"Member {member.get('index')}: {member.get('name')}, {member.get('state')}")
        result = member.get('result')
        if isinstance(result, dict):
            view = observation(member.get('name'), result).split('\n', 1)
            lines.extend(_block(view[1].split('\n') if len(view) > 1 else []))
        if isinstance(member.get('job_observation'), dict):
            lines.append('  Current observation: ' + job_line(member['job_observation']))
        rest = _without(member, 'index', 'name', 'state', 'result', 'job_observation')
        if rest:
            lines.extend(_block(_lines(rest)))
    return '\n'.join(lines) + '\nDetails:\n' + facts(_without(body, 'group_id', 'state', 'members', 'cancel_requested'))


def observation(name, receipt):
    heading = 'Runtime result: ' + name
    if receipt.get('execution_state') == 'unknown' or receipt.get('state') == 'unknown':
        heading += '\nExecution outcome unknown; the action may have run.'
    # Keep the command, literal output and byte ranges together. Do not infer
    # semantic success from prose or exit zero.
    body = dict(receipt)
    advice = body.pop('authored_guidance', [])
    authored = ''.join('\n\nAuthored guidance (not grader facts):\n' + facts(item) for item in advice)
    if body.get('recovery'):
        recovery = dict(body.pop('recovery'))
        message = recovery.pop('message', None)
        if message:
            heading += '\n' + message
        heading += '\n' + facts(recovery)
        # The complete format manual remains in the original receipt. Lead with
        # this failure's correction instead of repeating the entire manual.
        if name == 'apply_patch':
            body.pop('patch_format', None)
            body.pop('example', None)
    if name == 'repair_plan' and body.get('registered'):
        # Prose and argv already appear in the model's original call. Keep the
        # immutable plan reference and newly selected guidance in this result.
        body.pop('plan', None)
        checks = body.pop('checks', [])
        body['registered_checks'] = [{**check, 'cwd': check.get('cwd', '.'),
            'expectation': check.get('expectation', 'pass'), 'result_format': check.get('result_format', 'exit')}
            for check in checks]
        decision = body.get('planning_decision')
        if isinstance(decision, dict) and decision.get('plan_id', body.get('plan_id')) == body.get('plan_id'):
            body['planning_decision'] = _without(decision, 'plan_id', 'id')
    page = body.get('search_page')
    if name == 'search_files' and isinstance(page, dict) and page.get('version') == 1:
        return _search_view(heading, body, page)
    if name in ('inspect_agent', 'wait_agent') and 'report' in body:
        report = body.pop('report')
        identity = {k: body.pop(k) for k in ('agent_id', 'role', 'workspace', 'workspace_mode', 'status') if k in body}
        return heading + '\n' + facts(identity) + '\nModel-authored report (interpretation, not verified runtime facts):\n' + facts(report) + '\nAgent details:\n' + facts(body)
    if name == 'edit_file' and 'diff' in body:
        diff = body.pop('diff')
        identity = {k: body.pop(k) for k in ('applied', 'path', 'error') if k in body}
        return heading + '\n' + facts(identity) + '\nDiff:\n' + diff + '\nEdit details:\n' + facts(body)
    if name == 'apply_patch' and 'files' in body:
        return _patch_view(heading, body)
    if 'grade_facts' in body:
        return _grade_view(heading, body, authored)
    if name in ('exec_command', 'job') and 'output_path' in body and isinstance(body.get('output'), dict):
        return _command_view(heading, body)
    if name == 'read_file' and 'content' in body:
        return _read_view(heading, body)
    if name == 'read_archive' and 'content' in body:
        # The cursor precedes a large page so continuation survives any clipping.
        content = body.pop('content')
        page = (f"Archive page: bytes {body.get('offset')}-{body.get('next_offset')} of {body.get('total_bytes')}; "
                f"next offset: {body.get('next_offset')}; " + ('complete.' if body.get('complete') else 'more follows.'))
        return (heading + '\n' + page + '\nArchived content:\n' + content + '\nArchive identity:\n' +
                facts(_without(body, 'offset', 'next_offset', 'total_bytes', 'complete')))
    if name == 'search_history' and 'matches' in body:
        return heading + '\n' + history_view(body)
    if name == 'list_jobs' and isinstance(body.get('jobs'), list):
        return _jobs_view(heading, body)
    if name == 'task_status' and 'deadline' in body:
        return _status_view(heading, body)
    if name in ('parallel', 'inspect_parallel', 'cancel_parallel') and isinstance(body.get('members'), list):
        return _parallel_view(heading, body)
    if name in ('exec_command', 'job') and 'output' in body:
        output = body.pop('output')
        return heading + '\nOutput:\n' + facts(output) + '\n' + facts(body)
    return heading + authored + '\n' + facts(body)


def history_view(body):
    query, matches = body.get('query'), body.get('matches', [])
    lines = [f'{len(matches)} archived matches for "{query}" (excerpts are discovery, not new observations):']
    for match in matches:
        where = ', '.join(f'{_label(k)} {match[k]}' for k in ('epoch', 'message_index', 'role') if k in match)
        lines.append(f"- agent {match.get('agent_id')}, {match.get('kind')} {match.get('ref')}" + (f' ({where})' if where else '') + ':')
        lines.extend(_block(_block(str(match.get('excerpt', '')).split('\n'))))
        exact = match.get('read_archive', {})
        lines.append(f"  Exact record: read_archive(path=\"{exact.get('path')}\", ref=\"{exact.get('ref')}\", json_pointer=\"{exact.get('json_pointer')}\")")
        if match.get('read_exchange'):
            lines.append(f"  Complete exchange: same arguments with include_exchange=true")
    if body.get('has_more'):
        lines.append(f"More matches: search_history again with offset={body.get('next_offset')}.")
    if body.get('coverage_errors'):
        lines.append('Coverage errors:\n' + facts(body['coverage_errors']))
    if body.get('scope'):
        lines.append('Scope: ' + body['scope'])
    return '\n'.join(lines)


def tool_call_text(call):
    """A native call with its arguments decoded; the original encoding stays archived."""
    function = call.get('function', {})
    raw = function.get('arguments')
    try:
        arguments = json.loads(raw) if isinstance(raw, str) else raw
    except ValueError:
        arguments = None
    if isinstance(arguments, dict):
        shown = facts(arguments) if arguments else '(no arguments)'
    else:
        shown = 'Arguments (original text): ' + str(raw)
    return f"Call {call.get('id')}: {function.get('name')}\n" + '\n'.join(_block(shown.split('\n')))


def message_text(message):
    """One archived chat message as labelled text; native fields stay in the archive."""
    role = message.get('role')
    content = message.get('content')
    if isinstance(content, list):
        content = facts(content)
    if role == 'assistant':
        parts = ['Assistant (model interpretation):' + ('\n' + content if content else ' (no text)')]
        parts.extend(tool_call_text(call) for call in message.get('tool_calls') or [])
        return '\n'.join(parts)
    if role == 'tool':
        return f"Tool result for {message.get('tool_call_id')}:\n" + str(content or '')
    return f'{role} message:\n' + str(content or '')


def exchange_text(exchange):
    header = (f"Original exchange from {exchange.get('ref')} (epoch {exchange.get('epoch')}, messages "
              f"{exchange.get('first_message_index')}-{exchange.get('next_message_index')}; SHA256 {exchange.get('source_sha256')}).\n"
              f"{exchange.get('meaning', '')}")
    return header + '\n\n' + '\n\n'.join(message_text(message) for message in exchange.get('messages', []))


def notification_text(value):
    """Harness notifications lead with the event and any model report they carry."""
    body = dict(value)
    event = body.pop('event', 'notification')
    if event in ('agent_returned', 'failure_review_returned') and isinstance(body.get('model_report'), dict):
        report = body.pop('model_report')
        kind = 'Failure review' if event == 'failure_review_returned' else 'Agent'
        lines = [f"{kind} {body.pop('agent_id', None)} returned: status {body.pop('status', None)}"
                 + (f", outcome {report['outcome']}" if report.get('outcome') else '') + '.']
        if body.get('candidate_sha256'):
            lines.append('Reviewed candidate: ' + str(body.pop('candidate_sha256')))
        if 'text' in report:
            lines.append(f"Report by agent {report.get('author')} (model interpretation, not verified runtime facts):\n"
                         + (report['text'] or '(empty)'))
        else:
            lines.append(f"Report by agent {report.get('author')}: available at " + facts(report.get('reference')).replace('\n', '; '))
        artifacts = body.pop('artifacts', {}) or {}
        patch = artifacts.get('patch')
        lines.append('Patch: ' + (f"{patch.get('patch_path')} ({patch.get('bytes')} bytes, SHA256 {patch.get('sha256')}, base {patch.get('base')})"
                                  if isinstance(patch, dict) else 'none captured'))
        source = body.pop('source', {}) or {}
        if source.get('workspace'):
            lines.append(f"Workspace: {source['workspace']}" + (f" (base {source['base']})" if source.get('base') else ''))
        assignment = body.pop('assignment', {}) or {}
        if 'text' in assignment:
            lines.append(f"Assignment by {assignment.get('author')}:\n" + '\n'.join(_block(str(assignment['text']).split('\n'))))
        elif assignment:
            lines.append(f"Assignment by {assignment.get('author')}: " + facts(assignment.get('reference')).replace('\n', '; '))
        receipt = body.pop('return_receipt', None)
        if isinstance(receipt, dict) and receipt.get('ended_at'):
            lines.append('Ended at: ' + _scalar('ended_at', receipt['ended_at']))
        meaning = body.pop('meaning', None)
        rest = facts(body) if body else ''
        return '\n'.join(lines) + (('\n' + meaning) if meaning else '') + (('\nDetails:\n' + rest) if rest else '')
    meaning = body.pop('meaning', None)
    return 'Event: ' + str(event).replace('_', ' ') + (('\n' + meaning) if meaning else '') + (('\n' + facts(body)) if body else '')


def human_inputs_text(context):
    """Literal human inputs with their archive identity, followed by other context facts."""
    inputs = context.get('human_inputs') or []
    lines = [f"Human input {index} ({item.get('author', 'human')}; {item.get('ref')} in {item.get('archive')}):\n{item.get('text')}"
             for index, item in enumerate(inputs, 1)] or ['(no further human inputs beyond the original public task)']
    rest = _without(context, 'human_inputs')
    return '\n\n'.join(lines) + (('\n' + facts(rest)) if any(v is not None for v in rest.values()) else '')


def session_facts(metadata):
    shown = dict(metadata)
    shown['parent'] = metadata.get('parent') or 'none (root of this team)'
    if 'max_agents' in shown and shown['max_agents'] is None:
        shown['max_agents'] = 'not configured'
    return facts(shown)


def render_result(root: Path, database: Path, ref: str, name: str, receipt: dict, max_bytes=16000):
    text = observation(name, receipt)
    path = root / 'presentations' / (ref.replace(':', '-') + '.txt')
    path.parent.mkdir(exist_ok=True)
    raw = text.encode('utf-8')
    with path.open('xb') as stream:
        stream.write(raw)
    # Cut only at UTF-8 boundaries; offsets address the exact saved view.
    page = raw[:max_bytes].decode('utf-8', errors='ignore')
    offset = len(page.encode('utf-8'))
    header = f'Result {ref}\nOriginal JSON: {database} ref={ref}\nFull readable result: {path}\n'
    footer = (f'\nPage bytes 0..{offset} of {len(raw)}. '
              f'Read more with read_archive(path={json.dumps(str(path))}, offset={offset}).') if offset < len(raw) else ''
    return page + footer + '\n\nEvidence references:\n' + header
