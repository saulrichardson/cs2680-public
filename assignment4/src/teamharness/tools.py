"""Explicit tool effects over the existing Investigator process/file primitives.

No command-output heuristics or inferred correctness. Original results are retained
outside the candidate tree; effect identity prevents accidental mutation replay.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, TYPE_CHECKING

from src import workspace as effects
from .patching import PATCH_EXAMPLE, PATCH_FORMAT, patch_format_help
from .contracts import validate, check_schema, describe, examples as contract_examples

if TYPE_CHECKING:
    from .agents import AgentManager


def tool(name: str, description: str, properties: dict, required: tuple = ()) -> dict:
    return {"type": "function", "function": {"name": name, "description": description,
        "parameters": describe(name, {"type": "object", "properties": properties,
                       "required": list(required), "additionalProperties": False})}}


STRING = {"type": "string"}
BOOL = {"type": "boolean"}
NONNEG = {"type": "integer", "minimum": 0}
POSITIVE = {"type": "integer", "minimum": 1}
TOOLS = [
    tool("exec_command", "Run a real command with Bash by default. Default response wait is 10 seconds; a wait is not a deadline. Calls in one response run concurrently: run tests that depend on an edit in a later response after its result. Optional kill_after_ms kills the process group after the model-chosen duration; omitted means no cancellation deadline. Long output shows beginning/end ranges with full output retrievable. Output includes actual exit status or a running job ID; nonzero status is not automatically a defect.",
         {"command": STRING, "cwd": STRING, "shell": STRING, "tty": BOOL,
          "yield_time_ms": {**NONNEG, "description": "Milliseconds to wait for this response; expiration leaves the job running. Poll its returned ID instead of launching again."}, "kill_after_ms": NONNEG, "max_output_bytes": {**POSITIVE, "maximum": 65536}}, ("command",)),
    tool("job", "Observe or control an existing job. Default response wait is 10 seconds. Poll never reruns the command. Default polling advances the output cursor. A long first observation also previews beginning/end; explicit offset selects a contiguous page. Finishing a role does not kill jobs.",
         {"id": STRING, "action": {"type": "string", "enum": ["poll", "input", "close_stdin", "interrupt", "terminate", "kill"]},
          "chars": STRING, "offset": NONNEG, "input_offset": NONNEG, "yield_time_ms": NONNEG,
          "max_output_bytes": {**POSITIVE, "maximum": 65536}}, ("id",)),
    tool("list_jobs", "Discover this session's persisted jobs and their current observed state.", {}),
    tool("read_file", "Read a file by 1-based lines (start_line/line_count), or by 0-based BYTE offset. offset is never a line number. Displayed lines carry a line-number-and-tab prefix that is not file content; omit it from patch context and replacement text. Absolute paths are allowed; omit Markdown #section anchors from the path. Originals are archived.",
         {"path": STRING, "start_line": {**POSITIVE, "description": "1-based starting LINE number, e.g. start_line=1328 to read line 1328. Omit offset."},
          "line_count": {**POSITIVE, "description": "Number of lines; default 200. Use with start_line, not offset."},
          "offset": {**NONNEG, "description": "0-based BYTE offset for byte pagination, not a line number. Omit start_line/line_count."},
          "max_output_bytes": {**POSITIVE, "maximum": 65536}}, ("path",)),
    tool("search_files", "Find paths or literal text in actual files; report coverage errors and retain full results. For regular expressions use exec_command with grep or another installed search program, then job to observe or control a long search.",
         {"path": STRING, "glob": STRING, "query": STRING, "ignore_case": BOOL,
          "exclude": {"type": "array", "items": STRING}, "max_output_bytes": {**POSITIVE, "maximum": 65536}}),
    tool("edit_file", "Replace exact UTF-8 text in one existing file. Each replacement defaults to exactly one match; mismatch rejects all replacements. Before/after bytes and a diff are archived. Use distinguishing old text. Calls in one response run concurrently: put dependent tests in a later response after the edit result.",
         {"path": STRING, "replacements": {"type": "array", "minItems": 1,
          "items": {"type": "object", "properties": {"old": {"type": "string", "minLength": 1}, "new": STRING, "count": POSITIVE},
                    "required": ["old", "new"], "additionalProperties": False}}, "expected_sha256": STRING}, ("path", "replacements")),
    tool("apply_patch", "Edit one or several files. Every hunk is validated before source writes; later write failures retain explicit per-file results and original/after bytes. No semantic correctness is inferred. Calls in one response run concurrently: run dependent tests in a later response after this edit completes.\n\n" + PATCH_FORMAT + "\n\n" + PATCH_EXAMPLE,
         {"patch": {**STRING, "description": "Complete patch text using the exact grammar and example above, with actual newlines. This is not a Git unified diff."}}, ("patch",)),
    tool("parallel", "Start an explicit group of independent tool calls concurrently. Pass calls as an actual array, never a JSON-encoded string. Each member has sibling name and arguments fields; name selects an existing tool such as exec_command, not a label such as test1. Consecutive ordinary calls in one response also run concurrently; use this tool for an explicit group handle. Returns a group ID and separate member receipts; failure does not roll back or silently cancel siblings. Shell members remain active until their tracked jobs exit. Do not put dependent edits or session/group control tools in the group.",
         {"calls": {"type": "array", "description": 'Array of tool calls, for example [{"name":"exec_command","arguments":{"command":"pwd"}},{"name":"exec_command","arguments":{"command":"ls"}}]. Do not quote or JSON-encode this array. Put name beside arguments, not inside it.',
                    "minItems": 1, "items": {"type": "object", "properties": {"name": {**STRING, "description": "Existing tool name, for example exec_command; not an arbitrary member label."}, "arguments": {"type": "object", "description": "Arguments for the named tool, as an object."}}, "required": ["name", "arguments"], "additionalProperties": False}}, "yield_time_ms": NONNEG}, ("calls",)),
    tool("inspect_parallel", "Inspect original per-member results and currently observed state of a parallel group without restarting any work.", {"group_id": STRING}, ("group_id",)),
    tool("cancel_parallel", "Request cancellation of pending members and termination of group-launched shell jobs. Completed results remain. A request is not proof that running work terminated; inspect the separate receipts. No rollback is implied.",
         {"group_id": STRING, "yield_time_ms": NONNEG}, ("group_id",)),
    tool("read_archive", "Read original evidence. For session.sqlite3, supply ref='obj:N' for an object, or kind/after to discover records; text files use zero-based BYTE pagination, never line numbers. For line selection use read_file(start_line=..., line_count=...). include_exchange with a session-state message pointer retrieves its complete tool call/result exchange. Never edits history.",
         {"include_exchange": BOOL, "path": STRING, "ref": STRING, "json_pointer": STRING, "kind": STRING, "after": NONNEG, "offset": NONNEG,
          "max_output_bytes": {**POSITIVE, "maximum": 65536}}, ("path",)),
    tool("search_history", "Search archived team messages, tool arguments/results, original responses and handoffs by literal text, including earlier compacted epochs. Returns bounded excerpts and exact read_archive references; excerpts are discovery, not new observations.",
         {"query": STRING, "agent_ids": {"type": "array", "items": STRING}, "ignore_case": BOOL,
          "limit": {**POSITIVE, "maximum": 100}, "offset": NONNEG,
          "excerpt_chars": {"type": "integer", "minimum": 80, "maximum": 1000}}, ("query",)),
    tool("spawn_agent", "Start a fresh child with its own context. Supply a concrete assignment; private copies isolate edits, shared workspaces require deliberate coordination. source defaults to current; starting uses preserved starting code, checkpoint uses your checkpoint_id. Starting/checkpoint require private. Slot exhaustion is reported.",
         {"role": {"type": "string", "enum": ["planner", "implementer", "test_author", "reviewer", "integrator", "investigator"]},
          "task": STRING, "workspace": {"type": "string", "enum": ["private", "shared"]},
          "source": {"type": "string", "enum": ["current", "starting", "checkpoint"]}, "checkpoint_id": STRING}, ("role", "task", "workspace")),
    tool("inspect_agent", "Read status, model report, workspace and captured patch. Reports are claims, not verified conclusions.", {"agent_id": STRING}, ("agent_id",)),
    tool("list_agents", "Compact team roster with IDs, roles, parents, observed status, workspaces, and available report/patch/checkpoint references.", {}),
    tool("wait_agent", "Wait for an agent without claiming a pending agent failed. timeout_seconds only limits this wait.",
         {"agent_id": STRING, "timeout_seconds": {"type": "number", "minimum": 0}}, ("agent_id",)),
    tool("wait_agents", "Wait until any or all specified agents finish or need attention. Idle agents have not completed. Timeout ends only this wait and never cancels agents.",
         {"agent_ids": {"type": "array", "minItems": 1, "items": STRING}, "mode": {"type": "string", "enum": ["any", "all"]}, "timeout_seconds": {"type": "number", "minimum": 0}}, ("agent_ids", "mode")),
    tool("send_message", "Append information or a follow-up to an agent. Starts a new turn if the agent is idle or completed.",
         {"agent_id": STRING, "message": STRING}, ("agent_id", "message")),
    tool("stop_agent", "Request the agent stop at the next model/action boundary. Already executed effects and running jobs remain; use job tools to stop processes explicitly.",
         {"agent_id": STRING}, ("agent_id",)),
    tool("apply_agent_patch", "Apply an explicitly selected agent's captured patch after verifying hash and git apply --check. Conflicts are returned for investigation; no reset or silent overwrite.",
         {"agent_id": STRING}, ("agent_id",)),
    tool("checkpoint", "Capture current source as an exact immutable patch without finishing your role. Returns base/hash/artifact identity and double-capture stability observation. It does not mean tests passed or review accepted the source.", {"label": STRING, "review": {**BOOL, "description": "Main only: start independent adversarial review of this exact checkpoint in a private copy. Returns reviewer ID; wait/inspect its evidence before finishing."}}),
    tool("repair_plan", "Record or revise your prose investigation plan and executable checks, loading only selected frozen guidance. action=continue records the initial repository-grounded plan; action=set revises it and adds executable checks after continuation; action=skip ends planning with reason and no completion takeover. continue/skip must be sole calls. action=run executes saved checks against starting source or an exact candidate. Baseline failures are observations. no_new_failures needs cases_json. Old plans, decisions and receipts remain archived.",
         {"action": {"type": "string", "enum": ["set", "run", "continue", "skip"]}, "plan": STRING, "reason": STRING,
          "guidance": {"type": "array", "items": {"type": "object", "properties": {"path": STRING, "heading": STRING}, "required": ["path"], "additionalProperties": False}},
          "checks": {"type": "array", "items": {"type": "object", "properties": {
              "name": STRING, "argv": {"type": "array", "minItems": 1, "items": STRING}, "cwd": STRING,
              "expectation": {"type": "string", "enum": ["pass", "no_new_failures"]},
              "result_format": {"type": "string", "enum": ["exit", "cases_json"]}}, "required": ["name", "argv"], "additionalProperties": False}},
          "assessment": {"type": "object", "properties": {
              "readiness": {"type": "string", "enum": ["repair", "experiment", "search", "blocked"]},
              "effort": {"type": "string", "enum": ["small", "medium", "large"]}},
              "required": ["readiness", "effort"], "additionalProperties": False},
          "target": {"type": "string", "enum": ["baseline", "candidate"]}, "checkpoint_id": STRING}, ("action",)),
    tool("task_status", "Observe shared deadline and remaining time, active agents, tracked jobs, latest captured candidate, workflow stage and this session's context usage/recovery state. Runtime facts only; no repair-quality judgment.", {}),
]

EVALUATION_TOOL = tool('evaluate_candidate',
    'Capture and evaluate your current patch, or evaluate a selected immutable checkpoint. '
    'The configured grader returns its actual receipt; failed, passing, missing and uncertain grades remain distinct. '
    'Grades leave you active to choose the next action; independent attempts are optional. '
    'Repeated identical candidates reuse the existing receipt and do not count again.',
    {'checkpoint_id': STRING})


def parallel_validation_error(args: Any, error: Exception) -> dict:
    """Explain rejected input without parsing, repairing, or starting it."""
    if not isinstance(args, dict):
        field, value = "arguments", args
    else:
        field, value = "calls", args.get("calls")
    kind = ("null" if value is None else "boolean" if type(value) is bool else
            "object" if isinstance(value, dict) else "array" if isinstance(value, list) else
            "string" if isinstance(value, str) else "number" if type(value) in (int, float) else
            type(value).__name__)
    if isinstance(args, dict) and "calls" not in args:
        kind = "missing"
    return {"executed": False, "started": False, "error": str(error),
            "received_field": field, "received_type": kind,
            "message": "No parallel group or member was started. Submit calls as an actual array, not a JSON string. Each member needs name beside arguments. name is an existing tool name such as exec_command, not a label such as test1. Correct and submit explicitly; the rejected input was not repaired.",
            "example": {"calls": [
                {"name": "exec_command", "arguments": {"command": "pwd"}},
                {"name": "exec_command", "arguments": {"command": "ls"}}]}}


def validation_rejection(name, args, error, schemas):
    """Known pre-execution rejection, with corrections limited to actual capabilities."""
    available = set(schemas)
    recovery = {'code': 'invalid_arguments', 'message': 'This call did not execute. Correct the arguments and submit explicitly.'}
    if name not in available:
        recovery.update(code='unknown_tool', available_tools=sorted(available),
                        message='This tool is not available in this conversation. No action executed. Use an advertised tool.')
        examples = {'apply_patch': {'patch': '*** Begin Patch\n*** Update File: example.txt\n@@\n-old value\n+new value\n*** End Patch\n'},
                    'exec_command': {'command': 'pwd'}, 'read_file': {'path': 'example.txt', 'start_line': 1}}
        recovery['available_examples'] = []
        for n, a in examples.items():
            if n not in available:
                continue
            try:
                validate(a, schemas[n])
            except (ValueError, TypeError):
                continue
            recovery['available_examples'].append({'name': n, 'arguments': a})
        recovery['example_scope'] = 'Examples only; adapt paths/content. No example was executed.'
    else:
        recovery['expected_parameters'] = schemas[name]
        recovery['examples'] = contract_examples(schemas[name])
        recovery['example_scope'] = 'Argument-shape examples only; adapt paths and use observed IDs. Nothing was executed.'
        if hasattr(error, 'path'):
            recovery['issues'] = [{'path': error.path, 'rule': error.rule, 'message': str(error)}]
        if isinstance(args, dict):
            recovery['missing_fields'] = sorted(set(schemas[name].get('required', [])) - args.keys())
            recovery['unknown_fields'] = sorted(args.keys() - schemas[name].get('properties', {}).keys())
    return {'state': 'not_executed', 'executed': False, 'error': str(error),
            'error_type': type(error).__name__, 'recovery': recovery}


class ToolRuntime:
    def __init__(self, manager: AgentManager, agent_id: str, descriptor: dict):
        self.manager, self.agent_id = manager, agent_id
        self.root = Path(descriptor['root'])
        self.working_directory = descriptor.get('working_directory', descriptor['workspace'])
        self.schemas = {t['function']['name']: t['function']['parameters'] for t in descriptor['tools']}
        self.workspace = {"root": descriptor['workspace'], "state": str(self.root / 'workstate'),
                          "base": descriptor.get('base'), "capture_patch": bool(descriptor.get('base'))}
        Path(self.workspace['state']).mkdir(parents=True, exist_ok=True)
        (self.root / 'effects').mkdir(exist_ok=True)

    def validate_tools(self, tools: list[dict]) -> None:
        """Descriptors may evolve only for effects this runtime actually supplies."""
        from .session import CONTROL_TOOLS
        available = {t['function']['name'] for t in TOOLS + CONTROL_TOOLS}
        workflow = getattr(self.manager, 'workflow', None)
        if workflow is not None and workflow.grading_available and self.agent_id in (workflow.report['agents'].get('main'), workflow.report['agents'].get('original_main')):
            available.add('evaluate_candidate')
        for descriptor in tools:
            name = descriptor['function']['name']
            if name not in available:
                raise ValueError('Unsupported tool configuration: runtime does not provide ' + name)
            check_schema(descriptor['function'].get('parameters'))

    def configure_tools(self, tools: list[dict]) -> None:
        self.validate_tools(tools)
        self.schemas = {t['function']['name']: t['function']['parameters'] for t in tools}

    def checkpoint_candidate(self, identity: str) -> dict:
        if not isinstance(identity, str) or not identity or any(c not in '0123456789abcdef' for c in identity):
            raise ValueError('Expected an existing hexadecimal checkpoint ID')
        receipt = json.loads((self.root / 'checkpoints' / (identity + '.json')).read_text())
        candidate = receipt['candidate']
        if (not receipt.get('captured') or receipt.get('agent_id') != self.agent_id
                or candidate.get('base') != self.workspace['base']
                or hashlib.sha256(Path(candidate['patch_path']).read_bytes()).hexdigest() != candidate['sha256']):
            raise ValueError('Checkpoint is not an intact captured candidate of this agent')
        return candidate

    def runtime_facts(self):
        import time
        from .decision_context import grade_context, job_context, observed_jobs, job_observations, uncertain_effects, source_context
        now = time.time()
        roster = self.manager.list_agents()['agents']
        created = self.manager.started_at
        run = {'available': False, 'meaning': 'No run-wide clock, GPU inventory or task count supplied by the external owner.'}
        if self.manager.run_facts_provider is not None:
            try:
                run = self.manager.run_facts_provider()
            except Exception as error:
                run = {'available': False, 'error': str(error)}
        jobs = observed_jobs(self.workspace)
        return {'observed_at': now, 'task_elapsed_seconds': max(0, now-created) if created is not None else None,
                'task_deadline': self.manager.deadline_status(),
                'active_agents_in_this_task': sum(row['status'] == 'running' for row in roster),
                'run': run, 'candidate_evidence': grade_context(self.root), 'source_context': source_context(self.root),
                'existing_jobs': job_context(self.workspace, jobs), 'job_observations': job_observations(self.workspace, jobs),
                'uncertain_effects': uncertain_effects(self.root), 'repair_plan': self.plan_facts()}

    def plan_facts(self):
        if self.manager.descriptor(self.agent_id)['role'] != 'main':
            return {'stage': 'bounded_assignment', 'meaning': 'Follow your role and concrete assignment; no main planning ceremony is required.'}
        from .repair import current
        from .decision_context import verification_context
        plan = current(self.root)
        from .planning import current as planning_decision
        decision = planning_decision(self.root)
        boundary = self.manager.descriptor(self.agent_id).get('planning_boundary', {}).get('enabled')
        if plan is None:
            return {'stage': 'planning' if boundary else 'investigation', 'planning_decision': decision,
                    'meaning': 'Inspect source and choose continue with a plan or skip with a reason.' if boundary else
                    'Choose useful inspection and guidance. A repair_plan records executable verification when helpful; bounded lookups need no plan.'}
        return {'stage': ('implementation' if decision and decision['decision'] == 'continue' else 'planning') if boundary else 'investigation_and_implementation',
                'planning_decision': decision, 'plan_id': plan['id'],
                'author': 'model', 'check_names': [check['name'] for check in plan['checks']],
                'guidance_references': [{k: p[k] for k in ('source_path', 'source_sha256', 'section')} for p in plan['guidance']],
                'archive': str(self.root / 'repair-plans' / (plan['id'] + '.json')),
                'verification': verification_context(self.root, plan)}

    def before_request(self):
        workflow = getattr(self.manager, 'workflow', None)
        if workflow is not None and self.agent_id in (workflow.report['agents'].get('main'), workflow.report['agents'].get('original_main')):
            workflow.dispatch_reviews()

    def recover_effect(self, name: str, args: dict, effect_id: str) -> dict | None:
        """Read the original intent/result without dispatching any action."""
        digest = hashlib.sha256(effect_id.encode()).hexdigest()
        receipt = self.root / 'effects' / (digest + '.json')
        intent = self.root / 'effects' / (digest + '.intent.json')
        if intent.exists():
            original = json.loads(intent.read_text())
            if original != {'name': name, 'arguments': args, 'effect_id': effect_id}:
                return {'executed': False, 'error': 'Effect identity reused with different tool or arguments'}
        if receipt.exists():
            if not intent.exists():
                return {'execution_state': 'unknown', 'replay_safe': False, 'effect_id': effect_id,
                        'receipt': str(receipt), 'message': 'Saved result has no matching intent; inspect original evidence.'}
            return json.loads(receipt.read_text())
        if intent.exists():
            return {"execution_state": "unknown", "replay_safe": False, "effect_id": effect_id,
                    "intent": str(intent), "message": "Prior effect has no receipt. Observe files/jobs/agents; do not replay blindly."}
        return None

    def __call__(self, name: str, args: dict, effect_id: str) -> dict:
        previous = self.recover_effect(name, args, effect_id)
        if previous is not None:
            return previous
        digest = hashlib.sha256(effect_id.encode()).hexdigest()
        receipt = self.root / 'effects' / (digest + '.json')
        intent = self.root / 'effects' / (digest + '.intent.json')
        schema = self.schemas.get(name)
        if schema is None:
            return validation_rejection(name, args, ValueError('Unknown tool ' + name), self.schemas)
        try:
            validate(args, schema)
            if name in ('wait_agent', 'stop_agent', 'send_message') and args.get('agent_id') == self.agent_id:
                raise ValueError('Use the current session controls instead of messaging/waiting/stopping yourself')
            if name == 'wait_agents' and self.agent_id in args['agent_ids']:
                raise ValueError('A session cannot wait for itself to finish')
            if name == 'parallel':
                from .parallel import FORBIDDEN
                if any(c['name'] in FORBIDDEN for c in args['calls']):
                    raise ValueError('Parallel groups cannot contain nested groups or session/group controls')
        except (ValueError, TypeError) as e:
            if name == 'parallel':
                return parallel_validation_error(args, e)
            if name == 'apply_patch':
                return {**validation_rejection(name, args, e, self.schemas),
                        "source_writes_started": False, **patch_format_help()}
            return validation_rejection(name, args, e, self.schemas)
        effects.dump(intent, {"name": name, "arguments": args, "effect_id": effect_id})
        from .repair import PlanValidationError
        try:
            result = self.execute(name, args, digest)
        except PlanValidationError as error:
            result = validation_rejection(name, args, error, self.schemas)
            result['recovery'] = error.recovery
        except Exception as e:
            # Read failures have no uncertain source/process effect. Mutations
            # still retain uncertainty after entry, even on a familiar error.
            if name in ('read_file', 'search_files', 'read_archive', 'search_history'):
                result = {'execution_state': 'failed', 'replay_safe': True, 'error': str(e), 'error_type': type(e).__name__}
            else:
                result = {"execution_state": "unknown", "replay_safe": False, "error": str(e), "error_type": type(e).__name__}
        effects.dump(receipt, result)
        return result

    def execute(self, name: str, args: dict, digest: str) -> dict:
        if name == 'repair_plan':
            from . import repair
            workflow = self.manager.workflow
            if workflow is None or self.agent_id != workflow.report['agents']['main']:
                return {'executed': False, 'error': 'The current main owns the executable repair plan.'}
            if args['action'] in ('continue', 'skip'):
                from . import planning
                boundary = self.manager.descriptor(self.agent_id).get('planning_boundary', {})
                if not boundary.get('enabled'):
                    raise repair.PlanValidationError('This recorded investigation has no planning boundary; use action=set or action=run.', code='planning_not_enabled')
                planning.ensure_open(self.root)
                decision_options = {'paused': boundary.get('pause_after_planning', False),
                                    'allocation_assessment': args.get('assessment')}
                if args.get('assessment') is not None:
                    planning.assessment(args['assessment'])
                elif decision_options['paused']:
                    raise repair.PlanValidationError('Include readiness and effort for task allocation.', code='missing_planning_assessment')
                if args['action'] == 'skip':
                    if set(args) - {'action', 'reason', 'assessment'}:
                        raise repair.PlanValidationError('Skip accepts action and reason only; no plan or checks were changed.', code='invalid_skip')
                    return planning.decide(self.root, digest, 'skip', reason=args.get('reason'), **decision_options)
                if 'plan' not in args:
                    if set(args) - {'action', 'assessment'}:
                        raise repair.PlanValidationError('Supply plan with its checks/guidance, or use action=continue alone for the saved draft.', code='invalid_continue')
                    return planning.decide(self.root, digest, 'continue', plan=repair.current(self.root), **decision_options)
            if args['action'] in ('set', 'continue'):
                result = repair.register(self.root, self.manager.guide_root, digest, args.get('plan'),
                                         args.get('checks', []), args.get('guidance', []))
                if args['action'] == 'continue':
                    result.update(planning.decide(self.root, digest, 'continue', plan=repair.current(self.root), **decision_options))
                if not result['checks'] and workflow.report.get('completion_gate', {}).get('enabled'):
                    result['completion_implication'] = ('No model-defined executable checks are registered. This is a verification gap, not a failed execution; configured grader and required command checks still apply.'
                        if workflow.report['completion_gate'].get('version') == 2 else
                        'With this plan registered, completion verification will fail because it contains zero executable checks. You can revise the plan; editing remains available.')
                return result
            plan = repair.current(self.root)
            if plan is None:
                return {'executed': False, 'error': 'Record a repair_plan before running its checks.'}
            baseline = args.get('target', 'candidate') == 'baseline'
            if baseline and args.get('checkpoint_id'):
                return {'executed': False, 'error': 'Baseline cannot select a candidate checkpoint.'}
            if baseline:
                from .candidates import capture
                candidate = capture(workflow.starting_workspace(), self.root / 'effects' / (digest + '.patch'), workflow.report['frozen']['revision'])
            else:
                if args.get('checkpoint_id'):
                    candidate = self.checkpoint_candidate(args['checkpoint_id'])
                else:
                    captured = self.manager.checkpoint(self.agent_id, digest)
                    if not captured.get('captured'):
                        return {'executed': False, 'error': 'Candidate source did not establish a stable capture.', 'capture': captured}
                    candidate = captured['candidate']
            return repair.run(workflow, self.agent_id, plan, candidate, self.root / 'test-plan-runs' / digest, baseline=baseline)
        if name == 'evaluate_candidate':
            if self.manager.workflow is None:
                return {'executed': False, 'error': 'The original investigation/grader must be attached before continuation.'}
            result = self.manager.workflow.evaluate(self.agent_id, digest, args.get('checkpoint_id'))
            counts = result.get('grade_facts', {})
            if type(counts.get('tests_failed')) is int and counts['tests_failed'] > 0 and not result.get('reused'):
                from .guidance import passage
                advice = passage(self.manager.guide_root, 'skills/investigate-with-evidence/SKILL.md',
                                 'Make evaluation feedback useful without inventing its meaning')
                if advice:
                    result = {**result, 'authored_guidance': [advice]}
            return result
        if name in ('exec_command', 'job', 'read_file', 'search_files', 'edit_file'):
            params = dict(args, workspace=self.workspace)
            if name == 'exec_command' and not params.get('cwd'):
                params['cwd'] = self.working_directory
            if name == 'exec_command':
                params.setdefault('shell', '/bin/bash')
                params['_job_id'] = digest[:32]
            if name == 'edit_file':
                params['operation'] = 'replace'
            related = []
            if name == 'exec_command':
                from .decision_context import running_jobs
                related = [row['id'] for row in running_jobs(self.workspace)
                           if row.get('command') == args['command'] and row.get('cwd') == str((Path(self.workspace['root']) / params['cwd']).resolve())
                           and row.get('shell') == params['shell'] and row.get('state') == 'running']
            result = effects.dispatch('shell' if name == 'exec_command' else name, params)
            if related:
                result['other_running_jobs_with_same_command'] = related
                result['launch_meaning'] = 'This invocation started a separate process; those earlier jobs remain independently observable.'
            return result
        if name == 'apply_patch':
            from .patching import apply_patch, display_diff
            result = apply_patch(Path(self.workspace['root']), self.root / 'patches', args['patch'], digest)
            try:
                shown = display_diff(result)
            except OSError as error:
                result = {**result, 'display_diff_error': str(error)}
            else:
                if shown:
                    result = {**result, 'display_diff': shown[0], 'display_diff_complete': shown[1]}
            recovery = result.get('recovery', {})
            example = recovery.get('example_call')
            if example:
                try:
                    validate(example['arguments'], self.schemas[example['name']])
                except (KeyError, ValueError, TypeError):
                    # The patch archive keeps its generic help; this conversation
                    # must not recommend a capability removed by configuration.
                    recovery = {k: v for k, v in recovery.items() if k != 'example_call'}
                    recovery['available_tools'] = sorted(self.schemas)
                    result = {**result, 'recovery': recovery}
            return result
        if name in ('parallel', 'inspect_parallel', 'cancel_parallel'):
            groups = self.parallel_groups()
            if name == 'parallel':
                return groups.start(args['calls'], digest[:32], yield_ms=args.get('yield_time_ms', 1000))
            if name == 'cancel_parallel':
                return groups.cancel(args['group_id'], yield_ms=args.get('yield_time_ms', 1000))
            return groups.inspect(args['group_id'])
        if name == 'search_history':
            from .history import search_history
            return search_history(self.manager.root, **args)
        if name == 'list_agents':
            return self.manager.list_agents()
        if name == 'wait_agents':
            return self.manager.wait_agents(args['agent_ids'], args['mode'], args.get('timeout_seconds', 30))
        if name == 'checkpoint':
            result = self.manager.checkpoint(self.agent_id, digest, args.get('label', ''))
            from .decision_context import grade_context
            evidence = grade_context(self.root)
            if evidence:
                result['previous_grade'] = evidence
                if result.get('captured') and result['candidate']['sha256'] != evidence.get('candidate_sha256'):
                    from .guidance import passage
                    advice = passage(self.manager.guide_root, 'skills/verify-contracts-and-tests/SKILL.md',
                                     'Verify after integration, not only during construction')
                    if advice:
                        result['authored_guidance'] = [advice]
            if args.get('review') and result.get('captured'):
                workflow = self.manager.workflow
                if workflow is None or self.agent_id != workflow.report['agents']['main']:
                    result['review_error'] = 'Only the current main can request checkpoint review.'
                else:
                    result['adversarial_review'] = workflow.review_checkpoint(self.agent_id, result['candidate'])
            return result
        if name == 'task_status':
            from .decision_context import grade_context
            return {**self.manager.task_status(self.agent_id), 'candidate_evidence': grade_context(self.root)}
        if name == 'list_jobs':
            jobs = Path(self.workspace['state']) / 'jobs'
            results = []
            for p in sorted(jobs.glob('*')):
                if p.is_dir():
                    try:
                        results.append(effects.observe(self.workspace, p.name, count=0))
                    except Exception as e:
                        results.append({"id": p.name, "state": "unknown", "error": str(e)})
            return {"jobs": results}
        if name == 'read_archive':
            path = Path(args['path'])
            if not path.is_absolute():
                path = self.root / path
            if path.suffix == '.sqlite3':
                from src.store import Store
                store = Store(path, readonly=True)
                try:
                    value = store.get(args['ref']) if 'ref' in args else store.query(
                        kind=args.get('kind'), after=args.get('after', 0), limit=25)
                finally:
                    store.db.close()
                if args.get('include_exchange'):
                    from .history import archived_exchange
                    value = archived_exchange(value, args.get('json_pointer'))
                elif 'json_pointer' in args:
                    if 'ref' not in args:
                        raise ValueError('json_pointer requires an exact archived object ref')
                    pointer = args['json_pointer']
                    if pointer and not pointer.startswith('/'):
                        raise ValueError('JSON pointer must be empty or begin with /')
                    for token in pointer.split('/')[1:] if pointer else []:
                        import re
                        if re.search(r'~(?![01])', token):
                            raise ValueError('Invalid JSON pointer escape')
                        token = token.replace('~1', '/').replace('~0', '~')
                        if isinstance(value, list):
                            if re.fullmatch(r'0|[1-9][0-9]*', token) is None:
                                raise ValueError('JSON pointer array index must be canonical')
                            value = value[int(token)]
                        elif isinstance(value, dict):
                            value = value[token]
                        else:
                            raise ValueError('JSON pointer cannot traverse a scalar')
                if args.get('include_exchange'):
                    # Decoded calls and readable results; exact JSON remains at
                    # the same ref and json_pointer without include_exchange.
                    from .presentation import exchange_text
                    raw = exchange_text(value).encode()
                    shape = 'Readable rendering of the original exchange; read the same ref/json_pointer without include_exchange for exact JSON'
                else:
                    raw = json.dumps(value, ensure_ascii=False).encode()
                    shape = 'Original archived record or discovery page'
                start = args.get('offset', 0)
                count = min(args.get('max_output_bytes', 16000), 12000)
                part = effects.utf8_prefix(raw[start:start + count + 3], count)
                return {'path': str(path), 'content': part.decode(errors='replace'),
                        'ref': args.get('ref'), 'json_pointer': args.get('json_pointer'),
                        **({'include_exchange': True} if args.get('include_exchange') else {}),
                        'offset': start, 'next_offset': start + len(part), 'total_bytes': len(raw),
                        'complete': start + len(part) >= len(raw), 'format': shape}
            if args.get('include_exchange'):
                raise ValueError('include_exchange requires an exact session-state object in a SQLite archive')
            return {"path": str(path), **effects.page(path, args.get('offset', 0), min(args.get('max_output_bytes', 16000), 12000))}
        if name == 'spawn_agent':
            source_choice = args.get('source', 'current')
            source = Path(self.workspace['root'])
            cwd_relative = Path(self.working_directory).relative_to(source)
            base = None
            if source_choice != 'checkpoint' and 'checkpoint_id' in args:
                raise ValueError('checkpoint_id requires source=checkpoint')
            if source_choice != 'current':
                if args['workspace'] != 'private':
                    raise ValueError('Starting/checkpoint source requires a private workspace')
                if source_choice == 'starting':
                    workflow = self.manager.workflow
                    if workflow is None:
                        raise ValueError('Preserved starting source requires an attached investigation')
                    source = workflow.starting_workspace()
                    base = workflow.report['frozen']['revision']
                else:
                    candidate = self.checkpoint_candidate(args.get('checkpoint_id'))
                    base = candidate['base']
                    import subprocess
                    from .agents import snapshot
                    from .workspaces import bind_capture_base
                    source = self.root / 'spawn-sources' / digest / 'source'
                    # Reset only this new private materialization. Snapshot captures
                    # all nonignored files so later untracked edits cannot leak in.
                    snapshot(Path(self.workspace['root']), source)
                    bind_capture_base(source, Path(self.workspace['root']), base)
                    subprocess.run(['git', '-C', str(source), 'reset', '--hard', base],
                                   check=True, capture_output=True)
                    raw = Path(candidate['patch_path']).read_bytes()
                    if hashlib.sha256(raw).hexdigest() != candidate['sha256']:
                        raise ValueError('Checkpoint bytes changed before replay')
                    if raw:
                        subprocess.run(['git', '-C', str(source), 'apply', '--binary', '-'],
                                       input=raw, check=True, capture_output=True)
                    from .candidates import capture
                    observed = capture(source, source.parent / 'checkpoint.patch', base)
                    if observed['sha256'] != candidate['sha256']:
                        raise ValueError('Replayed checkpoint differs from selected bytes')
            child = self.manager.create(args['role'], args['task'], source,
                                        private=args['workspace'] == 'private', parent=self.agent_id,
                                        identity=digest[:32], capture_base=base,
                                        working_directory=source / cwd_relative,
                                        inherit_context=source_choice == 'current')
            started = self.manager.start(child)
            return {'agent_id': child, 'source': source_choice,
                    'checkpoint_id': args.get('checkpoint_id'), **started}
        if name == 'inspect_agent':
            return self.manager.inspect(args['agent_id'])
        if name == 'wait_agent':
            return self.manager.wait(args['agent_id'], args.get('timeout_seconds', 30))
        if name == 'send_message':
            self.manager.send(args['agent_id'], args['message'], identity=digest, sender=self.agent_id)
            return self.manager.start(args['agent_id'])
        if name == 'stop_agent':
            return self.manager.stop(args['agent_id'], identity=digest, sender=self.agent_id)
        if name == 'apply_agent_patch':
            child = self.manager.inspect(args['agent_id'])
            if child['status'] in ('running', 'idle'):
                return {"applied": False, "error": "Agent has no settled candidate yet; wait or inspect its work"}
            patch = child.get('patch')
            if not patch:
                return {"applied": False, "error": "Agent has no captured patch"}
            path = Path(patch['patch_path'])
            raw = path.read_bytes()
            if hashlib.sha256(raw).hexdigest() != patch['sha256']:
                return {"applied": False, "error": "Captured patch hash mismatch"}
            if not raw:
                return {"applied": True, "changed": False, "patch": patch}
            import subprocess
            argv = ['git', '-C', self.workspace['root'], 'apply', '--binary']
            checked = subprocess.run([*argv, '--check', str(path)], capture_output=True, text=True)
            if checked.returncode:
                return {"applied": False, "error": checked.stderr, "patch": patch}
            applied = subprocess.run([*argv, str(path)], capture_output=True, text=True)
            return {"applied": applied.returncode == 0, "exit_code": applied.returncode,
                    "output": applied.stdout + applied.stderr, "patch": patch}
        raise ValueError('Unknown tool ' + name)

    def parallel_groups(self):
        return self.manager.parallel_groups(self.agent_id)
