"""Model-owned working context over an immutable, task-local investigation archive."""
from __future__ import annotations

import copy
from dataclasses import dataclass, field
import hashlib
import json
import os
from pathlib import Path
import re
import time
import uuid

from .models import (InputReceipt, encoded, input_measure, input_fingerprint, input_receipt_key,
                     normalize_message, present_messages, provider_policy)
from .maintenance import THRESHOLD, INSTRUCTION as MAINTENANCE_INSTRUCTION, maintenance_capacity


RECENT_TOOL_EXCHANGES = 10


def immutable_bytes(path, raw):
    """Publish complete archive bytes without replacing an existing identity."""
    pending = path.with_name('.' + path.name + '-' + uuid.uuid4().hex + '.pending')
    try:
        with pending.open('xb') as output:
            output.write(raw)
        os.link(pending, path)
    finally:
        # The exact inference response is retained separately. A failed archive
        # write must not occupy the next exchange/memory identity with partial bytes.
        pending.unlink(missing_ok=True)


@dataclass(frozen=True)
class Exchange:
    messages: tuple[dict, ...]
    originals: str
    results: tuple[dict, ...] = ()
    model: str = "standard"
    recorded_at: float | None = None
    harness: tuple[dict, ...] = ()


@dataclass(frozen=True)
class Continuation:
    """The owner's complete replacement understanding and its archive boundary."""
    text: str
    through: int
    source_model: str


@dataclass(frozen=True)
class View:
    continuation: Continuation | None = None
    revision: int = 0

    @property
    def hidden(self):
        return range(1, self.continuation.through + 1) if self.continuation else range(0)

    @property
    def startup(self):
        return self.continuation is None


def context_schemas():
    summary_guidance = """You are preparing the working understanding from which you will continue this same investigation. Write an account that lets you resume useful engineering work without reconstructing the entire conversation.

Use this replacement when the investigation changes direction, you reject a major theory, you complete a substantial phase, or accumulated narration makes the next action harder to see. You need not wait for the runtime's capacity request. The purpose is to carry useful understanding forward while letting superseded explanations and repetitive activity leave the working conversation. Write as much detail as the investigation needs; there is no required summary length or compression ratio.

Preserve the requested behavior and the constraints that determine whether a solution is acceptable, including exact interface obligations. Keep those obligations distinct from your chosen approach and unresolved assumptions. Explain what you changed, why you changed it, what the observations support, and what remains uncertain. Where evidence contradicts your current explanation, retain the contradiction and the conditions under which it appeared. A summary should help you reconsider a mistaken conclusion, rather than make that conclusion harder to question.

Preserve what each consequential check established: the actual caller, input, source conditions and observed result, with a reference for retrieving the original evidence. When a failed grade has no locally established cause, keep that cause unknown. Do not assign hidden entries to named passing or failing behaviors from the count. Keep your proposed explanation separate from the observed result, and retain consequential changed behaviors your checks have not exercised. A suspected explanation does not discharge those obligations.

Preserve a concrete correction you have discovered but not yet applied. Completed changes can be easy to summarize while an unfinished source change disappears. This includes a correction from a review or from your own reasoning ("this should be X", "the production function is untouched") that you accepted but have not acted on: carry it with its exact names and values, not paraphrased into a theme. Name the unapplied correction, why it is needed, the evidence supporting it, and the next edit or check. Preserve unresolved failures as reproducible obligations: the invocation, expected consequence, observed result, source conditions and retrieval reference. Do not reduce them to a topic name or replace them with a preferred explanation. A concrete observation can correct your summary; the summary does not overrule the observation. Keep exact interface spellings, input shapes, failing caller conditions and relevant source or evidence references when the continuation depends on them. A persuasive explanation does not discharge an unresolved counterexample.

Preserve the scope of what an observation actually establishes. If the distinction depends on an exact literal, equality comparison, ordering, or state transition, retain it exactly rather than replacing it with a broader category. An equality condition is not a prefix rule, and a counterexample under one input and caller does not establish failure for every related case. Keep the observed condition and the proposed general explanation separate so the next action can test that explanation.

Retain failed approaches when their failure changes what you should try next. Replace superseded explanations and repetitive activity with what you learned, rather than copying the public task or a diary of completed reads. Include the next useful action and enough source or evidence references to perform it. After compaction, resume that action using the understanding already gained; retrieve evidence or reread mutable source where a particular detail matters, rather than restarting repository orientation by default. Historical observations describe the state in which they were recorded. Distinguish a graded candidate from later ungraded changes. Do not invent missing results or promote an interpretation into an observed fact.

The result should leave you oriented toward the unresolved engineering problem, with the evidence and freedom needed to solve it."""
    specs = [
        ("compact_context", "Replace completed investigation history and the previous working summary with one complete continuation summary. Original user instructions and current execution state remain; earlier observations stay archived. Call alone.",
         {"summary": {"type": "string", "minLength": 1, "description": summary_guidance}}, ["summary"]),
        ("search_history", "Search decoded archived conversation text, tool arguments, result metadata and saved summaries for a case-insensitive literal substring. Returns attributed excerpts and exact original/result paths. Does not search undisplayed bytes of full saved outputs: locate the command or result, then read_file its saved output, optionally with search and context_lines. Omit query to list exchanges. start defaults to 1 and limit to 20.",
         {"query": {"type": "string"}, "start": {"type": "integer", "minimum": 1}, "limit": {"type": "integer", "minimum": 1, "maximum": 50}}, []),
    ]
    return [{"type": "function", "function": {"name": n, "description": d, "parameters": {
        "type": "object", "properties": props, "required": required, "additionalProperties": False}}}
        for n, d, props, required in specs]


def validate_context_call(name, args):
    schemas = {s['function']['name']: s['function']['parameters']['properties'] for s in context_schemas()}
    if name not in schemas or not isinstance(args, dict) or set(args) - set(schemas[name]):
        raise ValueError("Unknown or malformed context arguments")
    if name == 'compact_context':
        if set(args) != {'summary'} or not isinstance(args['summary'], str) or not args['summary'].strip():
            raise ValueError('compact_context requires nonempty summary text')
        return
    for key in ('start', 'limit'):
        if key in args and (type(args[key]) is not int or args[key] < 1 or (key == 'limit' and args[key] > 50)):
            raise ValueError("Context paging requires positive integers; limit <= 50")
    if 'query' in args and not isinstance(args['query'], str):
        raise ValueError("Context query must be text")


def validate_request(request):
    names = set()
    for tool in request.get("tools", []):
        f = tool["function"]
        if tool["type"] != "function" or f["parameters"].get("type") != "object" or f["name"] in names:
            raise ValueError("Invalid or duplicate tool schema")
        names.add(f["name"])
    pending, seen = set(), set()
    for m in request["messages"]:
        if m["role"] not in ("system", "user", "assistant", "tool"):
            raise ValueError("Unsupported role")
        if pending and m["role"] != "tool":
            raise ValueError("Interrupted tool result group")
        if m["role"] == "tool":
            if m.get("tool_call_id") not in pending:
                raise ValueError("Orphan or duplicate tool result")
            pending.remove(m["tool_call_id"])
        for call in m.get("tool_calls", []):
            if m["role"] != "assistant" or not isinstance(call.get("id"), str) or not call["id"] or call["id"] in seen or call.get("type") != "function":
                raise ValueError("Invalid tool call identity")
            if not isinstance(call.get('function'), dict) or not isinstance(call['function'].get('name'), str) or not call['function']['name']:
                raise ValueError('Invalid historical tool function name')
            args = json.loads(call["function"]["arguments"])
            if not isinstance(args, dict):
                raise ValueError("Tool arguments must be a complete object")
            seen.add(call["id"])
            pending.add(call["id"])
    if pending:
        raise ValueError("Missing tool results")


@dataclass
class Context:
    system: str
    task: str
    runtime: Path
    exchanges: list[Exchange] = field(default_factory=list)
    view: View = field(default_factory=View)
    edits: list[dict] = field(default_factory=list)
    construction: list[dict] = field(default_factory=list)
    recovering: bool = False
    _provider_capacity_revision: int | None = None
    _provider_capacity_detail: str = ""
    _provider_capacity_rejections: int = 0
    _provider_capacity_exchange: int = 0
    last_meter: dict = field(default_factory=dict)
    _call_ids: set[str] = field(default_factory=set)
    _startup: Exchange | None = None
    _prior: dict | None = None
    _prior_usage: dict = field(default_factory=dict)
    _input_receipts: dict[tuple, InputReceipt] = field(default_factory=dict)
    _input_receipt: InputReceipt | None = None
    _build_args: tuple | None = None
    index_error: str = ''
    view_source: str = ''
    _memory_artifacts: set[str] = field(default_factory=set)

    @property
    def recovery_fallback(self):
        return self._provider_capacity_rejections > 1

    @property
    def seen_call_ids(self):
        return frozenset(self._call_ids)


    def set_startup(self, result):
        if self._startup is not None or self.exchanges:
            raise ValueError("Startup is immutable and must precede the conversation")
        folder = self.runtime / 'history'; folder.mkdir(parents=True, exist_ok=True)
        path = folder / 'startup.json'
        immutable_bytes(path, encoded(result))
        self._startup = Exchange((), str(path), (copy.deepcopy(result),))

    def append(self, messages, results=(), model='standard', *, view=None, harness=()):
        folder = self.runtime / 'history'; folder.mkdir(parents=True, exist_ok=True)
        if view is not None and view.continuation is not None:
            entry = view.continuation
            path = self._memory_path(entry)
            path.parent.mkdir(parents=True, exist_ok=True)
            raw = entry.text.encode('utf-8')
            try:
                immutable_bytes(path, raw)
            except FileExistsError:
                if path.read_bytes() != raw:
                    raise ValueError('Saved continuation does not match its immutable content identity')
            self._memory_artifacts.add(str(path.resolve()))
        number = len(self.exchanges) + 1; path = folder / f'{number}.json'
        recorded_at = time.time()
        immutable_bytes(path, encoded({'messages': messages, 'results': list(results), 'model': model,
                                  'recorded_at': recorded_at,
                                  'harness': list(harness),
                                  **({'working_view': self.view_record(view)} if view is not None else {})}))
        self.exchanges.append(Exchange(tuple(copy.deepcopy(messages)), str(path), tuple(copy.deepcopy(results)), model, recorded_at, tuple(copy.deepcopy(harness))))
        if view is not None:
            # Try the newly selected view ordinarily. Keep the rejection facts
            # until an ordinary request actually reaches the provider; committing
            # a replacement does not prove that replacement fits.
            self.recovering = False
            self.view = view
            self.view_source = str(path)
        operations = []
        calls = [c for m in messages for c in m.get('tool_calls', [])]
        self._call_ids.update(c['id'] for c in calls)
        for j, call in enumerate(calls):
            result = results[j] if j < len(results) else {}; meta = result.get('metadata', {})
            args = json.loads(call['function']['arguments'])
            operations.append({'ref': f'E{number}.R{j + 1}', 'operation': call['function']['name'],
                'subject': str(meta.get('path') or meta.get('command') or args.get('path') or args.get('query') or '')[:240],
                'state': meta.get('state', 'failed' if meta.get('error') else 'observed'),
                'exit_code': meta.get('exit_code'), 'output': result.get('full_path'),
                'edit_outcome': meta.get('edit_outcome'), 'patch_sha256': meta.get('patch_sha256')})
        try:
            with (folder / 'index.jsonl').open('ab') as output:
                output.write(encoded({'exchange': number, 'original': str(path), 'model': model, 'operations': operations}) + b'\n')
        except OSError as error:
            self.index_error = f'History index incomplete: {error}; exact numbered exchange files remain available.'

    def append_harness(self, text, model='standard', *, messages=(), metadata=None):
        """Archive an attributed runtime observation without inventing a user turn."""
        self.append(messages, model=model, harness=({'text': text, **(metadata or {})},))

    def accept_provider_request(self):
        """Close capacity recovery only after an ordinary request is accepted."""
        if not self.recovering:
            self._provider_capacity_revision = None
            self._provider_capacity_detail = ""
            self._provider_capacity_rejections = 0
            self._provider_capacity_exchange = 0

    @property
    def input_receipt(self):
        return self._input_receipt

    def receipt_for(self, request, profile):
        fingerprint = input_fingerprint(request, profile)
        return next((value for value in reversed(self._input_receipts.values())
                     if value.fingerprint == fingerprint), None)

    def _measure(self, request, profile):
        return input_measure(request, self._prior, self._prior_usage, profile=profile,
                             receipt=self.receipt_for(request, profile))

    def maintenance_measure(self, request, profile):
        """Current consumed input, estimated when the route has no exact counter."""
        return self._measure(request, profile)

    def observed_usage(self, request, usage, receipt=None, input_receipt_invalidated=False):
        self._prior, self._prior_usage = request, dict(usage)
        if input_receipt_invalidated:
            self._input_receipts.clear()
            self._input_receipt = None
        if self._build_args is not None:
            profile = self._build_args[1]
            retained = InputReceipt.from_public(request, profile, receipt, usage)
            if retained is not None:
                key = input_receipt_key(request, profile, retained.source['endpoint'])
                self._input_receipts.pop(key, None)
                self._input_receipts[key] = retained
                self._input_receipt = retained


    def _memory_path(self, entry):
        return self.runtime / 'history' / 'memory' / (hashlib.sha256(entry.text.encode('utf-8')).hexdigest() + '.txt')

    def validate_recovery_read(self, args, *, purpose="Capacity recovery"):
        """Permit a bounded new observation of already-owned immutable evidence."""
        from .tools import _regular_file, validate_call
        from .job_output import open_extent
        validate_call('read_file', args)
        path = Path(args['path'])
        if not path.is_absolute():
            raise ValueError(f'{purpose} read_file needs an exact absolute evidence or saved-memory path from search_history')
        path = path.resolve()
        allowed = set(self._memory_artifacts)
        for exchange in ([self._startup] if self._startup else []) + self.exchanges:
            allowed.add(str(Path(exchange.originals).resolve()))
            for observation in exchange.harness:
                if observation.get('response_path'):
                    allowed.add(str(Path(observation['response_path']).resolve()))
            for result in exchange.results:
                allowed.update(result.get('metadata', {}).get('memory_evidence', []))
                allowed.update(result.get('metadata', {}).get('review_evidence', []))
                graded = result.get('metadata', {}).get('graded_patch') or {}
                if graded.get('status') in ('retained', 'partial'):
                    for key in ('path', 'receipt_path'):
                        if graded.get(key):
                            allowed.add(str(Path(graded[key]).resolve()))
                for value in (result.get('full_path'), result.get('metadata', {}).get('before_image_path'), result.get('metadata', {}).get('submitted_path'), result.get('metadata', {}).get('diff_path'), result.get('metadata', {}).get('output_extent_path')):
                    if value:
                        allowed.add(str(Path(value).resolve()))
        if str(path) not in allowed:
            raise ValueError(f'{purpose} read_file rejected path {str(path)!r}: only owned immutable observations, exact exchange records and saved model memory are available. Mutable indexes, repository files, current notes and arbitrary runtime files require ordinary engineering tools. Use search_history() for evidence references; read an exact immutable evidence path from those results or an earlier receipt')
        # Validate provenance and availability, not a guessed token/byte budget.
        # The ordinary read tool owns pagination; accepted pages reach the next
        # request in full. Only the provider can reject their context capacity.
        try:
            with (open_extent(path) if path.name.endswith('.extent.json') else _regular_file(path)):
                pass
        except OSError as error:
            raise ValueError(f'{purpose} evidence page could not be inspected: {error}. No tool ran and the chosen view is unchanged; inspect another retained reference or choose from the evidence available. Keeping the same view may repeat a provider-capacity rejection') from error
        return 0

    def validate_recovery_batch(self, batch, message=None, owned_job=None, *, purpose="Capacity recovery"):
        """Admit complete fresh inspection pages together before any tool runs."""
        for _, name, args in batch:
            if name == 'read_file':
                self.validate_recovery_read(args, purpose=purpose)
            elif name == 'job':
                if args.get('action') != 'inspect' or set(args) != {'action', 'id'}:
                    raise ValueError(f'{purpose} permits only read-only job inspection with action=inspect and id; no process was started, waited for or stopped')
                if owned_job is None:
                    raise ValueError('Owned job inspection is unavailable; no operation ran')
                try:
                    owned_job(args['id'])
                except ValueError:
                    raise ValueError(f"Unknown owned job {args['id']!r}; inspect prior job references with search_history. No operation ran") from None
            elif name not in ('search_history', 'compact_context'):
                raise ValueError(f'{purpose} permits search_history/compact_context, read_file pages of owned immutable evidence or saved memory, and read-only job(action=inspect,id=...) for owned jobs; no repository action ran')


    def compact(self, summary):
        """Replace at a complete-response boundary, including this control exchange.

        The caller executes this tool alone. Pending command jobs live in Tools,
        independently of conversational selection. Original user messages are
        rendered separately even when their exchange is hidden.
        """
        validate_context_call('compact_context', {'summary': summary})
        boundary = len(self.exchanges) + 1
        entry = Continuation(summary, boundary,
                             self._build_args[0] if self._build_args else 'standard')
        return View(continuation=entry, revision=self.view.revision + 1)


    @staticmethod
    def _facts(meta, *, command_limit=None):
        keys = ('kind', 'command', 'cwd', 'job_id', 'state', 'exit_code', 'cleanup_error', 'error', 'edit_outcome', 'match_count', 'changed', 'effect_status',
                'observation_retained', 'retention_error', 'partial', 'final_output_captured', 'capture_error',
                'tests_failed', 'tests_total', 'patch_sha256', 'candidate_id', 'attempt', 'evaluation_started', 'unchanged', 'evaluation_purpose', 'before_image_path', 'before_image_sha256',
                'before_image_bytes', 'prior_exists', 'submitted_path', 'diff_path', 'diff_sha256', 'diff_bytes', 'diff_lines',
                'query', 'matching_lines', 'selected_match_lines', 'next_start_line', 'path', 'range_mode', 'start_line', 'end_line', 'start_byte', 'end_byte', 'selected_bytes', 'selected_sha256', 'total_lines', 'total_bytes', 'version', 'observed_at', 'recorded_at')
        facts = {key: meta[key] for key in keys if key in meta}
        if command_limit is not None and len(facts.get('command', '')) > command_limit:
            facts['command'] = facts['command'][:command_limit] + '… [complete command in original exchange]'
        guidance = {'ambiguous': ' Supply more surrounding text or explicitly choose replace_all.',
                    'no_matches': ' Read the current file and verify exact whitespace before retrying.',
                    'identical': ' Old and new text were identical; no edit was applied.'}.get(meta.get('edit_outcome'), '')
        return 'Observed outcome: ' + json.dumps(facts, ensure_ascii=False) + guidance

    def _page(self, text, limit, ref, original):
        if limit is None or len(text.encode('utf-8')) <= limit:
            return text
        page = text.encode('utf-8')[:limit].decode('utf-8', errors='ignore')
        return page + (f'\n[Mechanical first page for {ref}; full {len(text.encode())} bytes archived at {original}. '
                       'Retrieve exact lines or bytes with read_file; no summary was generated.]')

    @staticmethod
    def _latest_user_boundary(exchanges, view):
        return max(((number, index) for number, ex in enumerate(exchanges, 1) if number not in view.hidden
                    for index, message in enumerate(ex.messages) if message['role'] == 'user'), default=(0, 1))

    def _harness_text(self, number, ex, limit, *, old=False):
        parts = []
        for observation in ex.harness:
            text = observation['text']
            if old and observation.get('kind') == 'grade' and isinstance(observation.get('grade'), dict):
                grade = observation['grade']
                text = 'Historical grading receipt; explanatory guidance omitted. ' + self._facts(grade)
                artifact = grade.get('graded_patch') or {}
                text += '\nExact captured patch: ' + str(artifact.get('path', 'unavailable'))
                text += '\nExact receipt: ' + str(artifact.get('receipt_path', 'unavailable'))
            parts.append(f'\n[Immutable harness observation E{number}; recorded_at={ex.recorded_at}; '
                         f'exact archive: {ex.originals}; runtime feedback, not a human message]\n'
                         + self._page(text, limit, f'E{number}', ex.originals) + '\n')
        return ''.join(parts)

    @staticmethod
    def _no_action(message):
        return message['role'] == 'assistant' and not message.get('tool_calls')

    @staticmethod
    def _response_projection(exchanges, view, policy):
        """Only the trailing no-action chain needs an attributed source carrier.

        A later completed tool group makes earlier original assistant/native
        messages eligible again under the verified provider presentation rule.
        """
        if not provider_policy(policy).native_requires_tool_tail:
            return frozenset()
        active = [(number, ex) for number, ex in enumerate(exchanges, 1) if number not in view.hidden]
        completed = []
        for number, ex in active:
            calls = {call['id'] for message in ex.messages for call in message.get('tool_calls', [])}
            results = {message.get('tool_call_id') for message in ex.messages if message['role'] == 'tool'}
            if calls and calls <= results:
                completed.append((number, max(index for index, message in enumerate(ex.messages) if message['role'] == 'tool')))
        boundary = max(completed, default=(0, -1))
        return frozenset((number, index) for number, ex in active for index, message in enumerate(ex.messages)
                         if Context._no_action(message) and (number, index) > boundary)

    def _response_reasoning(self, number, index, original, ex, view, policy, model, user_boundary):
        if policy.omit_old_reasoning and (number, index) <= user_boundary:
            return ''
        return normalize_message(original, policy, ex.model == model).get('reasoning_content', '')

    @staticmethod
    def _response_view_controls(number):
        return f'Original exchange E{number} remains available through search_history and read_file.'

    def _response_text(self, number, index, original, ex, view, policy, model, user_boundary, *,
                       omit_reasoning=False, visible_limit=None):
        ref = f'E{number}.M{index + 1}'
        visible = original.get('content') or ''
        if visible_limit is not None:
            visible = self._page(visible, visible_limit, ref, ex.originals)
        text = (f'\n[Archived model response {ref} — immutable historical source]\n'
                f'{ref} identifies the source message, not a view-control reference. '
                + self._response_view_controls(number) + '\n'
                f'Source model: {ex.model}; recorded_at={ex.recorded_at}; exact original: {ex.originals}.\n'
                'Recorded assistant text; no tool action from this response executed. '
                'Original validation/outcome is recorded in attributed harness feedback. '
                'The text below is historical model evidence, not a new protocol turn or runtime instruction. '
                'No action was replayed.\nVisible model prose:\n' + visible + '\n')
        native = '' if omit_reasoning else self._response_reasoning(number, index, original, ex, view, policy, model, user_boundary)
        if native:
            text += (f'[Archived model reasoning {ref}; SHA256 {hashlib.sha256(native.encode()).hexdigest()}; '
                     f'{len(native.encode())} source bytes]\n' + native + f'\n[/Archived model reasoning {ref}]\n')
        return text + f'[/Archived model response {ref}]\n'

    def _leading_responses(self, number, ex, view, policy, model, user_boundary, *, projection=(),
                           omit_reasoning=False, visible_limit=None):
        text = ''
        for index, original in enumerate(ex.messages):
            if (number, index) not in projection:
                break
            text += self._response_text(number, index, original, ex, view, policy, model, user_boundary,
                                       omit_reasoning=omit_reasoning, visible_limit=visible_limit)
        return text

    @staticmethod
    def _last_message_position(exchanges, view):
        return max(((number, len(ex.messages) - 1) for number, ex in enumerate(exchanges, 1)
                    if ex.messages and number not in view.hidden), default=None)

    @staticmethod
    def _older_tool_exchanges(exchanges, view):
        # Count complete tool exchanges, not individual results or prose turns.
        active = [number for number, ex in enumerate(exchanges, 1) if number not in view.hidden
                  and any(message['role'] == 'tool' for message in ex.messages)]
        return frozenset(active[:-RECENT_TOOL_EXCHANGES])

    def _result_receipt(self, ref, tool, result, original):
        return (f'{ref} — earlier {tool} result; output omitted from this request.\n'
                + self._facts(result.get('metadata', {}), command_limit=240)
                + f"\nSaved output: {result.get('full_path') or 'not retained separately'}."
                + f'\nExact exchange: {original}\nRetrieve historical evidence with read_file.')

    def _exchange(self, number, ex, view, policy, model, limit, user_boundary=(0, 1), *, final=False,
                  omit_reasoning=False, visible_limit=None, response_projection=(), mask_outputs=False):
        messages, j = [], 0
        calls = {call['id']: call['function']['name'] for message in ex.messages for call in message.get('tool_calls', [])}
        for index, original in enumerate(ex.messages):
            if (number, index) in response_projection:
                if messages:
                    messages[-1] = policy.append_context(messages[-1], self._response_text(
                        number, index, original, ex, view, policy, model, user_boundary,
                        omit_reasoning=omit_reasoning, visible_limit=visible_limit))
                continue
            message = normalize_message(original, policy, ex.model == model)
            if omit_reasoning or (policy.omit_old_reasoning and (number, index) <= user_boundary):
                message.pop('reasoning_content', None)
            if message['role'] == 'tool':
                ref = f'E{number}.R{j + 1}'; result = ex.results[j] if j < len(ex.results) else {}
                meta = result.get('metadata', {})
                message['content'] = f'{ref}\n' + self._page(message.get('content') or '', limit, ref, ex.originals)
                if limit is not None and len((original.get('content') or '').encode()) > limit:
                    message['content'] += '\n' + self._facts(meta) + f"\nExact retained output: {result.get('full_path', 'unavailable')}."
                if mask_outputs and ex.originals:
                    receipt = self._result_receipt(ref, calls.get(message.get('tool_call_id'), 'tool'), result, ex.originals)
                    if len(receipt.encode('utf-8')) < len(message['content'].encode('utf-8')):
                        message['content'] = receipt
                j += 1
            elif message['role'] != 'user' and limit is not None and isinstance(message.get('content'), str) and len(message['content'].encode()) > limit:
                message['content'] = self._page(message['content'], limit, f'E{number}', ex.originals)
            messages.append(message)
        if messages and ex.harness:
            messages[-1] = policy.append_context(messages[-1], self._harness_text(number, ex, limit, old=mask_outputs))
        return present_messages(messages, policy) if final else messages


    def _finish_request(self, model, profile, schemas, notes, status, exchanges, view, messages, header, *, recovery=False, maintenance=False):
        messages = copy.deepcopy(messages)
        schemas = copy.deepcopy(schemas)
        tail = ('\n\n[Current harness state — authoritative owner, grading, accounting and execution facts]\n'
                + status + '\n' + self.index_error)
        # One current model-owned view follows runtime facts. Preserve its text,
        # attribution and provider-compatible carrier; do not synthesize a goal.
        working = '\n\n' + header
        # Preserve the full ordinary system contract. Phase requests use the
        # same provider-supported context carrier as current runtime state and
        # are explicitly attributed to the harness, not to the observed tool.
        phase_instruction = ''
        if recovery:
            phase_instruction = ('[Provider capacity recovery]\nThe provider rejected the request size. '
                                 + MAINTENANCE_INSTRUCTION)
        elif maintenance:
            phase_instruction = MAINTENANCE_INSTRUCTION
        if phase_instruction:
            working += '\n[Harness request — immediate context action, not tool output]\n' + phase_instruction + '\n[End harness request]\n'
        policy = provider_policy(profile)
        messages[-1] = policy.append_context(messages[-1], tail)
        request = {'model': model, 'messages': present_messages(messages, profile), 'tools': schemas,
                   'tool_choice': 'auto'}
        # Measure the substantive request once, including the chosen memory and
        # evidence. The short approximate notice need not count its own digits.
        original_last = request['messages'][-1]
        request['messages'][-1] = policy.append_context(original_last, working)
        meter = self._measure(request, profile)
        capacity = maintenance_capacity(profile)
        label = 'Temporary recovery request' if recovery else 'Working context'
        notice = f"\n{label}: approximately {meter['estimate']:,} input tokens before this status line."
        if recovery:
            notice += ' This is not the size of your selected ordinary view; temporary omissions have not changed your selections.'
            ordinary = next((row for row in reversed(self.construction) if not row['recovery']), None)
            if ordinary is not None:
                notice += (f" Last ordinary request estimate: {ordinary['estimate']:,} input tokens, before subsequent inspections or edits."
                           ' This historical estimate is not a count of the next ordinary request.')
        if capacity:
            notice += f" Maintenance threshold: {int(capacity['tokens'] * THRESHOLD):,} for this route."
            notice += (f" Basis: observed {capacity['tokens']:,}-token input allowance under the provider's default output allocation."
                       if capacity['basis'] == 'observed_default_allocation' else
                       " Basis: advertised total context; default output allocation is unknown and shares capacity with input.")
        else:
            notice += " Maintenance threshold unavailable: route context window unverified."
        notice += f" Measurement: {meter['estimate_method']}. Actual provider capacity remains authoritative."
        request['messages'][-1] = policy.append_context(original_last, notice + working)
        validate_request(request)
        return request

    def _context_header(self, view):
        return f'Archive retrieval: search_history(query="...") then read_file(path=...).\n'

    def _initial_messages(self, profile, view, *, limit=None, include_startup=True):
        messages = [{'role': 'system', 'content': self.system}, {'role': 'user', 'content': self.task}]
        if self._startup is not None:
            reference = self._startup.results[0].get('metadata', {}).get('persistent_reference')
            if reference:
                # Keep attributed intake advice separate from replaceable working
                # understanding and stale preparation facts. The ordinary view
                # includes it once; provider rejection may temporarily page it.
                reference_limit = limit if include_startup else 0
                shown = self._page(reference, reference_limit, 'intake reference',
                                   self._startup.originals)
                messages[-1] = provider_policy(profile).append_context(messages[-1],
                    '\n\n' + shown + f'\nExact reference: {self._startup.originals}\n')
        if include_startup and self._startup is not None and view.startup:
            # Preparation belongs before the first owner action. Reattaching it
            # after every current status made an initially unapplied candidate
            # look unapplied again after the owner had edited and checked it.
            initial = ('\n\n[Initial execution observation E0.R1 — recorded before the first owner action]\n'
                       'This is the starting snapshot, not current working-tree or process state.\n'
                       + self._page(self._startup.results[0]['display'], limit, 'E0.R1', self._startup.originals)
                       + f'\nExact initial observation: {self._startup.originals}\n[End initial execution observation]\n')
            messages[-1] = provider_policy(profile).append_context(messages[-1], initial)
        return messages

    @staticmethod
    def inspection_schemas(schemas):
        """One read-only tool surface for maintenance and provider recovery."""
        schemas = copy.deepcopy([tool for tool in schemas
                   if tool['function']['name'] in ('search_history', 'compact_context', 'read_file', 'job')])
        for tool in schemas:
            if tool['function']['name'] == 'job':
                tool['function']['description'] = 'Inspect current execution and captured output for an owned session. This phase permits inspection only.'
                tool['function']['parameters'] = {'type': 'object', 'properties': {'action': {'type': 'string', 'enum': ['inspect']}, 'id': {'type': 'string', 'minLength': 1}}, 'required': ['action', 'id'], 'additionalProperties': False}
            if tool['function']['name'] == 'read_file':
                tool['function']['description'] = 'Read owned immutable evidence or a saved summary, using a path from search_history or an earlier result. Use line/byte paging, or search for a case-insensitive literal with context_lines (default 8). Repository and mutable scratch files are unavailable during this phase.'
        return schemas

    def _render_recovery(self, model, profile, schemas, notes, status, exchanges, view, *,
                         recovery_detail='', fallback=False, maintenance=False):
        schemas = self.inspection_schemas(schemas)
        policy = provider_policy(profile)
        limit = 8000  # Temporary recovery page size; not a capacity claim.
        base = self._initial_messages(profile, view, limit=limit, include_startup=not fallback)
        rejected_through = (self._provider_capacity_exchange if self._provider_capacity_revision is not None
                            else len(exchanges))
        chain = [(number, ex) for number, ex in enumerate(exchanges, 1) if number not in view.hidden]
        boundary = self._latest_user_boundary(exchanges, view)
        projection = self._response_projection(exchanges, view, profile)
        final_position = self._last_message_position(exchanges, view)
        older_tools = self._older_tool_exchanges(exchanges, view)
        messages = copy.deepcopy(base)
        for number, ex in enumerate(exchanges, 1):
            if number in view.hidden or (fallback and number <= rejected_through):
                messages.extend(copy.deepcopy(m) for m in ex.messages if m['role'] == 'user')
        for number, ex in chain:
            page_limit = limit if number <= rejected_through else None
            # Only the rejected prefix's reasoning is temporarily absent;
            # accepted recovery reasoning and inspection results remain complete.
            omit_reasoning = number <= rejected_through
            if fallback and number <= rejected_through:
                # Keep the rejected prefix omitted even after inspection adds a
                # new continuation. Old indivisible tool arguments must not return.
                continue
            leading = self._leading_responses(number, ex, view, policy, model, boundary,
                projection=projection, omit_reasoning=omit_reasoning, visible_limit=page_limit)
            if leading:
                messages[-1] = policy.append_context(messages[-1], leading)
            fragment = self._exchange(number, ex, view, policy, model,
                                      page_limit, boundary,
                                      final=bool(final_position and number == final_position[0]),
                                      omit_reasoning=omit_reasoning, visible_limit=page_limit,
                                      response_projection=projection, mask_outputs=number in older_tools)
            messages.extend(fragment)
            if not fragment and ex.harness:
                messages[-1] = policy.append_context(messages[-1], self._harness_text(number, ex, page_limit, old=number <= max(older_tools, default=0)))
            messages[-1] = policy.append_context(messages[-1],
                f'\n[Recovery exchange E{number}; source_model={ex.model}; recorded_at={ex.recorded_at}; '
                f'exact original: {ex.originals}; chronological archived continuation, no action replayed]\n')

        def header(omitted=False):
            text = (f'Provider capacity recovery: {recovery_detail}\n'
                    f'Temporary inspection view; ordinary selection is unchanged. '
                    f'Original exchanges through E{rejected_through} remain in {self.runtime / "history"}. '
                    'Use search_history and read_file for exact evidence.\n')
            if omitted:
                text += 'After repeated rejection, earlier call/result groups are temporarily absent.\n'
                latest = next(((n, ex) for n, ex in reversed(chain) if n <= rejected_through and ex.results), None)
                if latest:
                    number, ex = latest
                    for j, result in enumerate(ex.results[:4], 1):
                        text += self._page(result.get('display', ''), 1200, f'E{number}.R{j}', ex.originals) + '\n'
            return text

        body = header(omitted=fallback)
        if entry := view.continuation:
            if fallback:
                body += ('\nContinuation summary: temporarily absent after repeated provider rejection; '
                         f'its complete saved text is unchanged at {self._memory_path(entry)}. '
                         'Retrieve it with read_file before replacing an obligation whose detail you need.\n')
            else:
                body += (f'\n[Continuation summary — complete model interpretation]\n{entry.text}\n'
                         f'Written by {entry.source_model}; replaces history through E{entry.through}. '
                         f'Exact saved text: {self._memory_path(entry)}.\n')
        body += '\n[End model working context]\n'
        return self._finish_request(model, profile, schemas, notes, status, exchanges, view,
                                    messages, body, recovery=True, maintenance=maintenance)

    def _render(self, model, profile, schemas, notes, status, exchanges, view, *, recovery=False,
                recovery_detail='', fallback=False, maintenance=False):
        if recovery:
            return self._render_recovery(model, profile, schemas, notes, status, exchanges, view,
                recovery_detail=recovery_detail, fallback=fallback, maintenance=maintenance)
        limit = None  # No general text clipping; only older tool results get receipts.
        messages = self._initial_messages(profile, view)
        continuation = view.continuation
        if continuation:
            # A provider-compatible context carrier, not a fabricated user instruction.
            messages[-1] = provider_policy(profile).append_context(messages[-1],
                '\n[Continuation summary — model interpretation]\n' + continuation.text
                + f'\nContext compacted through E{continuation.through}; original evidence remains archived.\n')
        user_boundary = self._latest_user_boundary(exchanges, view)
        projection = self._response_projection(exchanges, view, profile)
        final_position = self._last_message_position(exchanges, view)
        older_tools = self._older_tool_exchanges(exchanges, view)
        for number, ex in enumerate(exchanges, 1):
            if number in view.hidden:
                messages.extend(copy.deepcopy(m) for m in ex.messages if m['role'] == 'user')
            if number not in view.hidden:
                leading = self._leading_responses(number, ex, view, provider_policy(profile), model, user_boundary, projection=projection)
                if leading:
                    messages[-1] = provider_policy(profile).append_context(messages[-1], leading)
                fragment = self._exchange(number, ex, view, provider_policy(profile), model, limit, user_boundary,
                                          final=bool(final_position and number == final_position[0]), response_projection=projection,
                                          mask_outputs=number in older_tools)
                messages.extend(fragment)
                if not fragment and ex.harness:
                    text = self._harness_text(number, ex, limit, old=number <= max(older_tools, default=0))
                    messages[-1] = provider_policy(profile).append_context(messages[-1], text)
        body = self._context_header(view) + '\n[End model working context]\n'
        return self._finish_request(model, profile, schemas, notes, status, exchanges, view, messages,
                                    body, maintenance=maintenance)

    def mark_provider_capacity(self, detail: str):
        """Record actual provider input rejection; never infer it from byte counts."""
        if self._provider_capacity_revision != self.view.revision:
            self._provider_capacity_rejections = 0
        self._provider_capacity_revision = self.view.revision
        self._provider_capacity_exchange = len(self.exchanges)
        self._provider_capacity_detail = str(detail)
        self._provider_capacity_rejections += 1

    def _prepare(self, model, profile, schemas, notes, status, exchanges, view, force=False, *, maintenance=False):
        recovery = force or self._provider_capacity_revision == view.revision
        request = self._render(model, profile, schemas, notes, status, exchanges, view,
            recovery=recovery,
            recovery_detail=self._provider_capacity_detail or ('Provider input rejection reported by caller.' if force else ''),
            fallback=recovery and (self._provider_capacity_rejections > 1 or (force and self.recovering)), maintenance=maintenance)
        return request, recovery

    def build(self, model, profile, schemas, notes, status, force=False, *, maintenance=False):
        self._build_args = (model, profile, schemas, notes, status)
        started = time.monotonic()
        request, recovery = self._prepare(model, profile, schemas, notes, status, tuple(self.exchanges), self.view, force,
                                         maintenance=maintenance)
        self.recovering = recovery
        self.last_meter = self._measure(request, profile)
        self.construction.append({'seconds': time.monotonic() - started, **self.last_meter, 'recovery': recovery})
        return request

    def preview(self, model, profile, schemas, notes, status, messages=(), results=(), source_model='standard', harness=()):
        exchanges = tuple(self.exchanges) + ((Exchange(tuple(messages), '', tuple(results), source_model, harness=tuple(harness)),) if messages or harness else ())
        return self._prepare(model, profile, schemas, notes, status, exchanges, self.view)[0]

    def prepare_edit(self, proposed, *, pending_messages, pending_results, source_model, status, notes, next_schemas=None, maintenance=False):
        """Validate the replacement request without guessing whether it fits."""
        if self._build_args is None:
            raise ValueError('Build a request before editing its view')
        model, profile, schemas, _, _ = self._build_args
        schemas = next_schemas if next_schemas is not None else schemas
        exchanges = tuple(self.exchanges) + (Exchange(tuple(pending_messages), '', tuple(pending_results), source_model),)
        request = self._render(model, profile, schemas, notes, status, exchanges, proposed)
        estimate = self._measure(request, profile)['estimate']
        return ('Context compacted. Continuation summary replaced completed history and the previous summary. '
                'Original instructions and current execution state remain. '
                f'Next request preview: approximately {estimate:,} input tokens. '
                'Provider acceptance has not yet been established. Earlier evidence: search_history, then read_file.')

    @staticmethod
    def view_record(view):
        return {'revision': view.revision,
                'continuation': vars(view.continuation) if view.continuation else None}


    @staticmethod
    def _archive_match(path, query):
        """Search what a record says, not JSON escaping used to store it.

        Field/role provenance distinguishes observations from model prose and
        earlier queries. Decoded excerpts are not byte spans in the JSON file;
        the immutable original and its result paths remain the recovery source.
        """
        from .tools import _regular_file
        needle = query.casefold()

        def find(value, field='', role=None):
            if isinstance(value, dict):
                if re.fullmatch(r'messages\[\d+\]', field):
                    role = value.get('role')
                for key, item in value.items():
                    child = f'{field}.{key}' if field else key
                    if key == 'arguments' and isinstance(item, str):
                        try:
                            item = json.loads(item)
                        except ValueError:
                            pass  # Rejected proposals may have invalid arguments.
                    match = find(item, child, role)
                    if match is not None:
                        return match
            elif isinstance(value, list):
                for i, item in enumerate(value):
                    match = find(item, f'{field}[{i}]', role)
                    if match is not None:
                        return match
            elif isinstance(value, str):
                found = value.casefold().find(needle)
                if found >= 0:
                    folded = index = 0
                    while index < len(value) and folded + len(value[index].casefold()) <= found:
                        folded += len(value[index].casefold())
                        index += 1
                    first = max(0, index - 120)
                    return {'path': str(path), 'field': field,
                            'role': role or ('model-authored summary' if field.startswith(('working_view.continuation', 'working_view.memory')) else 'recorded result or metadata'),
                            'excerpt': value[first:first + 480],
                            'scope': 'Decoded archived text; field identifies its source, not a byte offset in the JSON original.'}
            return None

        with _regular_file(Path(path)) as (stream, size):
            return find(json.loads(stream.read(size)))


    def search(self, query='', start=1, limit=20):
        validate_context_call('search_history', dict(query=query, start=start, limit=limit))
        matches = []
        archived = ([(0, self._startup)] if self._startup is not None else [])
        archived.extend(enumerate(self.exchanges, 1))
        for number, exchange in archived:
            excerpt = self._archive_match(exchange.originals, query) if query else None
            if query and not excerpt:
                continue
            matches.append({'exchange': f'E{number}', 'original': exchange.originals,
                            'match': excerpt,
                            'results': [{'ref': f'E{number}.R{j+1}', 'path': r.get('full_path'),
                                         'outcome': self._facts(r.get('metadata', {}), command_limit=240)}
                                        for j, r in enumerate(exchange.results)]})
        page = matches[start-1:start-1+limit]
        end = start - 1 + len(page) if page else 0
        return (f'Archive matches {start if page else 0}–{end} of {len(matches)}. '
                f'Query: {json.dumps(query)} (literal, case-insensitive).\n'
                + (f'No matches on requested page at start {start}; use start=1 to return to the first page.\n' if not page and start > 1 else '')
                + 'Scope: decoded conversation text, tool arguments, result metadata and saved summaries; '
                'not undisplayed bytes of full saved outputs. To recover those, locate the command or result, then read_file its saved output, optionally with search and context_lines. '
                'A match can be an observation, model interpretation or earlier query; inspect its field and role.\n'
                + '\n'.join(json.dumps(row, ensure_ascii=False) for row in page)
                + f'\nNext start: {end+1 if page and end < len(matches) else "EOF"}. Use read_file on an original or result path.')
