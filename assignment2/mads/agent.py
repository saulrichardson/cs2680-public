"""One continuous model loop; action evidence and completion stay distinct."""
import argparse
import copy
import os
import signal
import sys
from pathlib import Path
from .store import Store, EvidenceError, dump, atomic_json
from .context import Context, CapacityBlocked, parse_arguments, render_exchange, REASONING_REPLAY_MODES
from .provider import Provider
from .tools import Tools, TOOLS, validate
from .workflow import session_notice
from . import responses
from .routes import route
from .runs import create_manifest

from .prompting import SYSTEM

REPLAY_FIELDS = ("role", "content", "reasoning_content", "reasoning", "reasoning_details")


def replay_message(response):
    """Validate the whole envelope before any call can produce effects."""
    if not isinstance(response, dict):
        raise ValueError("Expected a message object")
    message = {k: v for k, v in response.items() if v is not None}
    if message.get("role") != "assistant":
        raise ValueError("Expected an assistant message")
    for field in ("content", "reasoning_content", "reasoning"):
        if field in message and not isinstance(message[field], str):
            raise ValueError(f"{field} must be text")
    if "reasoning_details" in message and (not isinstance(message["reasoning_details"], list)
            or any(not isinstance(item, dict) for item in message["reasoning_details"])):
        raise ValueError("reasoning_details must be a list of objects")
    ids = set()
    if "tool_calls" in message and not isinstance(message["tool_calls"], list):
        raise ValueError("tool_calls must be a list")
    for call in message.get("tool_calls", []):
        if not isinstance(call, dict):
            raise ValueError("Tool calls must be objects")
        identifier, function = call.get("id"), call.get("function")
        if not isinstance(identifier, str) or not identifier.strip():
            raise ValueError("Tool calls require a nonempty ID")
        if identifier in ids:
            raise ValueError(f"Duplicate tool-call ID: {identifier}")
        if call.get("type") != "function" or not isinstance(function, dict):
            raise ValueError("Expected a function tool-call envelope")
        if not isinstance(function.get("name"), str) or not function["name"].strip():
            raise ValueError("Tool calls require a function name")
        if not isinstance(function.get("arguments"), str):
            raise ValueError("Tool-call arguments must be a JSON string")
        ids.add(identifier)
    # Keep native reasoning fields verbatim, including structured provider blocks.
    replay = {k: message[k] for k in REPLAY_FIELDS if k in message}
    if "tool_calls" in message:
        replay["tool_calls"] = [{k: call[k] for k in ("id", "type", "function")}
                                for call in message["tool_calls"]]
    return replay, ids



class Agent:
    def __init__(self, repo, task, run, provider, store=None, steps=0, execution=None, task_context="full", review_instructions=None, session_request=None, user_messages=None, session_role="solver", **context_options):
        # The reviewer uses the same loop, with no further ownership transfer.
        if session_role not in ('solver', 'reviewer'):
            raise ValueError('session_role must be solver or reviewer')
        self.session_role = session_role
        if review_instructions is not None and not isinstance(review_instructions, str):
            raise ValueError('review_instructions must be text')
        self.review_instructions = review_instructions
        if session_request is not None and not isinstance(session_request, str):
            raise ValueError('session_request must be text')
        if user_messages is not None and (not isinstance(user_messages, (list, tuple)) or
                                          any(not isinstance(text, str) for text in user_messages)):
            raise ValueError('user_messages must be a list of explicit user-message text')
        self.session_request = session_request
        self.user_messages = list(user_messages or [])
        self.review_started = False
        self.review_result = None
        self.proposed_summary = None
        self.session_closed = False
        self._settlement_failure_handles = set()
        self.finalization_problem = None
        self.completion_origin = None
        context_options.setdefault('reasoning_replay', os.environ.get('MADS_REASONING_REPLAY', 'all'))
        self.tool_schemas = TOOLS
        self.run_root=Path(run).resolve()
        self.store=store or Store(self.run_root/'journal')
        self.provider=provider
        self._handoff_fresh = not (self.run_root/'run-manifest.json').exists() and not (self.run_root/'memory').exists()
        self._handoff_seed_started = False
        self.tools=Tools(repo,self.run_root,self.store,original_task=task, execution=execution, task_context=task_context)
        self.context=Context(self.store,self.run_root/'memory',**context_options)
        self.task,self.steps=task,steps
        from .verification import Intentions
        self.tools.intentions=Intentions(self.run_root/'verification',task,self.user_messages)
        self.system=SYSTEM+'\nACTUAL RUN PATHS\n'+dump(dict(repository=str(self.tools.repo),
            working_file=str(self.context.working),memory=str(self.context.memory),
            journal=str(self.store.root),artifacts=str(self.run_root/'artifacts'),
            operation_guide=str(Path(__file__).with_name('OPERATIONS.md')),
            method_library=str(Path(__file__).with_name('methods')/'README.md'),
            review_prompts=str(Path(__file__).with_name('prompts')/'README.md'),
            source_trace=str(Path(__file__).with_name('source_trace.py'))))
        self.system += '\n' + session_notice(self.session_role)
        self.iterations,self.total_tokens=0,0
        create_manifest(self.run_root, task, provider.config)

    def add_user_message(self, content, source=None):
        """Record an explicit later user instruction, separate from agent notes/replay."""
        if not isinstance(content, str):
            raise ValueError('User message must be text')
        record = self.store.add('user_message', content=content, source=source)
        self.user_messages.append(content)
        self.tools.intentions.user_messages = list(self.user_messages)
        return record

    def child_context_options(self):
        """Inherit committed policy/capacity, never the parent's replay or account."""
        context = self.context
        return dict(input_limit=context.input_limit, total_limit=context.total_limit,
                    output_tokens=context.output_tokens, mask_at=context.mask_at,
                    pressure_at=context.pressure_at, keep_recent=context.keep_recent,
                    preview_head_percent=context.preview_head_percent,
                    reasoning_replay=context.reasoning_replay)

    def refresh_user_messages(self):
        """Launchers may append newly arrived user input; never generate a summary."""

    def settle_for_completion(self):
        """Expose teardown effects to the SAME final owner before accepting its claim.

        This is conditional process lifecycle work, not a test-success gate. Useful
        services remain available throughout solving/review. A failed settlement may
        be investigated; a later finish can report it unresolved and end with an
        explicit harness failure instead of being trapped in a retry gate.
        """
        pending = self.tools.jobs.unsettled()
        if not pending:
            return None
        handles = {state['job_id'] for state in pending}
        if handles <= self._settlement_failure_handles:
            self.finalization_problem = dict(status='error',
                error='Completion claimed with owned processes/captures still unresolved after settlement.',
                unsettled_jobs=pending)
            return None
        from .review import capture
        before = capture(self.tools)
        self.tools.effects = 'completion_process_settlement_started'
        cleanup = self.tools.jobs.close()
        failed = any(row.get('errors') or row.get('process', {}).get('state') == 'running' or
                     row.get('process', {}).get('capture_status') != 'complete' for row in cleanup)
        self._settlement_failure_handles = handles if failed else set()
        report = dict(completion='deferred_for_process_settlement',
            cleanup_outcome={'status':'failed' if failed else 'completed', 'jobs':cleanup},
            before_cleanup=before, after_cleanup=capture(self.tools),
            meaning='Owned process teardown just ran and may have changed files or flushed output. '
                    'Inspect consequential effects, repair/recheck as appropriate, then finish in this same '
                    'session. No additional reviewer or passing-test requirement. If cleanup remains '
                    'unresolved, report it honestly; a later claim may end with a separate harness failure.')
        artifact = self.store.blob(dump(report))
        record = self.store.add('completion_processes_settled', blob=artifact)
        from .reassessment import _outcome_summary
        return {**_outcome_summary(report), 'settlement_record':record,
                'settlement_report':str(self.store.verify(artifact))}

    def new_session(self, *args, **kwargs):
        """Fresh context with the same runtime class; launchers may carry live controls."""
        return type(self)(*args, **kwargs)

    def seed_handoff(self, bundle, *, original_task=None, initial_account=None):
        """Import exact attributable selections into a fresh local context, before compile/run."""
        from .handoff import seed_agent
        result = seed_agent(self, bundle, original_task=original_task, initial_account=initial_account)
        self.tools.intentions.user_messages = list(self.user_messages)
        return result

    def compile(self, current=None, restart=None):
        self._request_user_revision = len(self.user_messages)
        self.tools.intentions.user_messages = list(self.user_messages)
        account = self.context.account()
        runtime=self.tools.jobs.status()
        runtime['execution_targets'] = dict(default=self.tools.execution.default,
            names=list(self.tools.execution.describe()['targets']))
        runtime['task_source'] = self.tools.task_state.packet()
        if self.tools.intentions.rows:
            runtime["verification_intentions"]=self.tools.intentions.packet()
        return self.context.compile(self.system,self.task,self.tool_schemas,self.provider.config['model'],
                                    runtime,current,restart,account_snapshot=account,
                                    request_options=self.provider.request_options(),
                                    request_transform=getattr(self.provider, 'prepare_request', None),
                                    user_messages=self.user_messages + ([self.session_request] if self.session_request else []))

    def prepare(self, calls):
        prepared=[]
        for call in calls:
            try:
                arguments=parse_arguments(call['function']['arguments'])
                name=call['function']['name']
                validate(name,arguments,self.tool_schemas)
                if name=='context_restart':
                    self.context.validate_restart(arguments)
                prepared.append((arguments,None))
            except (ValueError,TypeError) as exc:
                prepared.append((None,str(exc)))
        return prepared

    def batch(self, assistant, response_record):
        group={'id':self.store.add('exchange_started',assistant_record=response_record),
               'assistant':assistant,'results':[],'fresh':True}
        self.refresh_user_messages()
        stale_input = len(self.user_messages) != getattr(self, '_request_user_revision', len(self.user_messages))
        if stale_input:
            group.setdefault('notices', []).append({'role':'user', 'content':
                'HARNESS: New explicit user input arrived during the last response. Its calls were not executed. '
                'Reconcile the newly delivered instructions and continue in this same session.'})
            self.store.add('response_deferred_for_user_input', assistant_record=response_record,
                           previous_user_revision=getattr(self, '_request_user_revision', None),
                           current_user_revision=len(self.user_messages))
        restart=None
        calls=assistant.get('tool_calls',[])
        completion_deferred=False
        for call,(arguments,error) in zip(calls,self.prepare(calls)):
            name=call['function']['name']
            if name == 'finish' and not stale_input:
                self.refresh_user_messages()
                if len(self.user_messages) != getattr(self, '_request_user_revision', len(self.user_messages)):
                    stale_input = True
                    self.store.add('completion_deferred_for_user_input', assistant_record=response_record,
                                   current_user_revision=len(self.user_messages))
            invocation=self.store.add('invocation',assistant_record=response_record,exchange_id=group['id'],
                call_id=call['id'],tool_name=name,raw_arguments=call['function']['arguments'],
                **({'native_item_id':call['native_item_id']} if 'native_item_id' in call else {}))
            result={'invocation_id':invocation,'call_id':call['id'],'tool_name':name,'effects':'none'}
            if 'native_item_id' in call:
                result['native_item_id']=call['native_item_id']
            self.tools.effects='none'
            if stale_input:
                result.update(status='skipped', reason='New user input arrived after this response was requested')
            elif error:
                result.update(status='rejected',error=error)
            elif self.tools.done is not None or self.session_closed or completion_deferred:
                result.update(status='skipped',reason='Earlier completion request')
            elif self.tools.jobs.fault.is_set():
                raise EvidenceError('Capture failure; no further effects executed')
            else:
                try:
                    if name=='finish' and self.session_role == 'solver':
                        from .reassessment import run_review
                        # A completion proposal consumes this session exactly once, even if
                        # review startup/transport/recording fails. Never silently bypass review.
                        self.proposed_summary = arguments['summary']
                        self.review_started = True
                        self.session_closed = True
                        self.store.add('completion_proposed_for_review', claim=arguments, invocation_id=invocation)
                        atomic_json(self.run_root/'solver-result.json', dict(
                            agent_outcome={'status':'proposed_completion', 'claim':self.proposed_summary},
                            iterations=self.iterations, total_tokens=self.total_tokens))
                        value = run_review(self, self.proposed_summary, instructions=self.review_instructions)
                        self.review_result = value
                        child_claim = value['child_outcome'].get('agent_outcome', {})
                        if child_claim.get('status') == 'completed':
                            self.tools.done = child_claim['claim']
                        self.completion_origin = dict(kind='fresh_session_review', report=value['report'],
                            report_record=value['report_record'], child_run=value['child_run'],
                            child_journal=value['child_journal'])
                    elif name=='context_restart':
                        if restart is not None:
                            raise ValueError('Only one valid restart request per response')
                        value={'context_change':'pending'}
                        if 'account' in arguments:
                            updated=self.tools.write(str(self.context.working), arguments['account'],
                                                     arguments.get('expected_account_sha256'))
                            value['account_update']={key:updated[key] for key in
                                ('path','before_sha256','after_sha256','observation_record')}
                        restart={'arguments':arguments,'index':len(group['results'])}
                    elif name=='context_configure':
                        group.setdefault('configurations',[]).append(
                            {'arguments':arguments,'index':len(group['results'])})
                        value={'context_change':'pending'}
                    elif name.startswith('context_'):
                        value=getattr(self.context,name)(**arguments)
                    else:
                        settled = self.settle_for_completion() if name == 'finish' else None
                        if settled is not None:
                            value = settled
                            completion_deferred = True
                        else:
                            value=getattr(self.tools,name)(**arguments)
                    status='running' if value.get('process',{}).get('state')=='running' else 'completed'
                    result.update({'status':status,'effects':self.tools.effects,**value})
                except (ValueError,TypeError,KeyError,UnicodeError) as exc:
                    result.update(status='rejected' if self.tools.effects=='none' else 'failed',
                                  effects=self.tools.effects,error=str(exc))
                except OSError as exc:
                    result.update(status='failed',effects=self.tools.effects,error=str(exc))
                except EvidenceError:
                    # Keep the known effect in the outer emergency outcome if persistence itself failed.
                    self.last_uncertain={'invocation_id':invocation,'effects':self.tools.effects,
                                         'status':'unknown','recording_failed':True}
                    raise
            try:
                record=self.store.add('tool_result',invocation_id=invocation,call_id=call['id'],result=result)
            except EvidenceError:
                self.last_uncertain={'invocation_id':invocation,'effects':result['effects'],
                    'status':'unknown','observation_before_recording_failure':result['status']}
                raise
            result['result_record']=record
            group['results'].append(result)
        return group,restart

    def run(self):
        outcome={'agent_outcome':{'status':'not_completed'},'harness_outcome':{'status':'running'},
                 'export_outcome':{'status':'not_requested'}}
        try:
            self.store.add('run_start',task=self.task,system=self.system,route=self.provider.config,tools=self.tool_schemas,
                           workdir=str(self.tools.repo),run_root=str(self.run_root),
                           session_request=self.session_request, user_messages=self.user_messages,
                           workflow='solve-review', session_role=self.session_role,
                           review_instructions=self.review_instructions)
            request=self.compile()
            while not self.steps or self.iterations < self.steps:
                if self.tools.jobs.fault.is_set():
                    raise EvidenceError('Process output capture failed')
                self.iterations+=1
                try:
                    response=self.provider.complete(request,self.context.context_id,purpose='agent')
                except Exception as exc:
                    if not self.provider.context_rejection(exc):
                        raise
                    # One visible corrective attempt, never an identical transparent retry.
                    previous=dump(request)
                    self.context.reduce_capacity(request,
                        getattr(self.provider, 'context_limit', lambda error: None)(exc))
                    if self.context.groups:
                        self.context.groups[-1]['fresh']=True
                    request=self.compile()
                    if len(dump(request).encode())>=len(previous.encode()):
                        raise CapacityBlocked('Provider rejected context; no smaller permitted presentation') from exc
                    self.store.add('capacity_retry',error=str(exc),context_id=self.context.context_id)
                    try:
                        response=self.provider.complete(request,self.context.context_id,purpose='agent')
                    except Exception as retry_error:
                        if self.provider.context_rejection(retry_error):
                            raise CapacityBlocked('Provider rejected the smaller corrective request') from retry_error
                        raise
                if self.tools.jobs.fault.is_set():
                    raise EvidenceError('Capture failed during generation; response caused no further effects')
                usage=response.get('usage') or {}
                self.total_tokens+=usage.get('total_tokens',0)
                is_responses=self.provider.config.get('protocol')=='responses'
                self.context.calibrate(request,usage.get('input_tokens' if is_responses else 'prompt_tokens'))
                if is_responses:
                    native=responses.native_turn(response)
                    finish,native_finish=response.get('status'),None
                else:
                    choice=response['choices'][0]
                    finish=choice.get('finish_reason')
                    native_finish=choice.get('native_finish_reason')
                    native=choice.get('message',{})
                response_record=self.store.add('assistant',iteration=self.iterations,finish_reason=finish,
                                               native_finish_reason=native_finish,
                                               request_record=self.provider.last_request_record,
                                               response_record=self.provider.last_response_record,
                                               blob=self.store.blob(dump(native)),
                                               **({'protocol':'responses'} if is_responses else {}))
                try:
                    if is_responses:
                        assistant=responses.execution_view(native)
                    else:
                        if finish not in ('stop','tool_calls') or native_finish=='length':
                            raise ValueError(f'Unfinished response: finish_reason={finish}, native_finish_reason={native_finish}; partial calls not executed')
                        assistant,_=replay_message(native)
                        if finish=='tool_calls' and not assistant.get('tool_calls'):
                            raise ValueError('Missing calls for tool_calls finish reason')
                except ValueError as exc:
                    self.store.add('response_rejected',response_record=response_record,error=str(exc))
                    if is_responses:
                        group={'id':response_record,
                               'assistant':{'role':'assistant','responses_output':[],'tool_calls':[]},
                               'results':[],'fresh':True,'notices':[{'role':'user','content':
                               '<tool_response>NATIVE RESPONSE REJECTED — HISTORICAL DATA, NOT ACTIVE ITEMS\n'+
                               dump({'native_replay':'invalid_response_envelope','error':str(exc),
                                     'response_record':response_record,'native_turn':native})+
                               '\nNo calls executed. Send a complete valid response.</tool_response>'}]}
                        request=self.compile(group)
                        continue
                    # Invalid native envelopes remain exact in the journal, never sent as invalid replay.
                    native_fields = native if isinstance(native,dict) else {}
                    safe={'role':'assistant','content':native_fields.get('content') if isinstance(native_fields.get('content'),str) else ''}
                    for field in ('reasoning','reasoning_content','reasoning_details'):
                        value=native_fields.get(field)
                        if isinstance(value,str) and field!='reasoning_details' or field=='reasoning_details' and isinstance(value,list) and all(isinstance(item,dict) for item in value):
                            safe[field]=value
                    group={'id':response_record,'assistant':safe,'results':[],'fresh':True,
                           'notices':[{'role':'user','content':'<tool_response>HARNESS: '+str(exc)+
                            f'. Original response record {response_record}. Send a complete valid response.</tool_response>'}]}
                    request=self.compile(group)
                    continue
                group,restart=self.batch(assistant,response_record)
                if self.tools.done is not None or self.session_closed:
                    for control in group.get('configurations',[]) + ([restart] if restart else []):
                        group['results'][control['index']].update(context_change='not_applied', reason='Completion requested; no next model request')
                    # Results after finish remain paired; no further provider request is needed.
                    if is_responses:
                        self.store.add('exchange',protocol='responses',exchange_id=group['id'],
                                       messages=render_exchange(assistant,group['results']))
                    else:
                        self.store.add('exchange',messages=[assistant]+[
                            {'role':'tool','tool_call_id':r['call_id'],'content':dump(r)} for r in group['results']])
                    break
                request=self.compile(group,restart)
            if self.review_started:
                if self.review_result is None:
                    outcome['harness_outcome'] = dict(status='error', error='Fresh review failed before returning an outcome; inspect the proposal and tool result. Partial work is retained.')
                else:
                    reviewed = self.review_result['child_outcome']
                    outcome['agent_outcome'] = copy.deepcopy(reviewed['agent_outcome'])
                    outcome['harness_outcome'] = copy.deepcopy(reviewed['harness_outcome'])
                    if reviewed['cleanup_outcome']['status'] != 'completed':
                        outcome['harness_outcome'] = dict(status='error',
                            error='Fresh reviewer cleanup incomplete; final source is not attested',
                            previous=outcome['harness_outcome'])
            elif self.tools.done is not None:
                outcome['agent_outcome']={'status':'completed','claim':self.tools.done}
                outcome['harness_outcome']=self.finalization_problem or {'status':'completed'}
            else:
                outcome['harness_outcome']={'status':'operator_turn_limit'}
        except BaseException as exc:
            if not isinstance(exc,(Exception,KeyboardInterrupt)):
                raise
            status='interrupted' if isinstance(exc,KeyboardInterrupt) else 'capacity_blocked' if isinstance(exc,CapacityBlocked) else 'error'
            outcome['harness_outcome']={'status':status,'error':str(exc),'type':type(exc).__name__,
                                        'last_effect':getattr(self,'last_uncertain',None)}
            if self.tools.done is not None:
                outcome['agent_outcome']={'status':'completed','claim':self.tools.done}
            try:
                self.store.add('termination',**outcome['harness_outcome'])
            except Exception as recording_error:
                print(f'Evidence store failure: {recording_error}; outcome: {dump(outcome)}',file=sys.stderr)
        finally:
            cleanup=self.tools.jobs.close()
            failed=any(item.get('errors') or item.get('process',{}).get('state')=='running' or
                       item.get('process',{}).get('capture_status')!='complete' for item in cleanup)
            outcome['cleanup_outcome']={'status':'failed' if failed else 'completed','jobs':cleanup}
            if self.review_started:
                outcome['solver_outcome'] = dict(status='proposed_completion', claim=self.proposed_summary)
                outcome['review_outcome'] = (copy.deepcopy(self.review_result) if self.review_result is not None
                                            else {'status':'failed_before_outcome', 'partial_work_retained':True})
                if self.review_result is not None and self.review_result['child_outcome']['cleanup_outcome']['status'] != 'completed':
                    outcome['cleanup_outcome']['status'] = 'failed'
                    outcome['cleanup_outcome']['review_cleanup'] = self.review_result['child_outcome']['cleanup_outcome']
            outcome.update(iterations=self.iterations,total_tokens=self.total_tokens,
                           workflow='solve-review', session_role=self.session_role)
            if self.completion_origin is not None:
                outcome['completion_origin'] = copy.deepcopy(self.completion_origin)
            try:
                self.store.add('run_end',**outcome)
            except Exception as exc:
                outcome['harness_outcome']={'status':'error','error':f'Final recording failed: {exc}',
                                            'previous':outcome['harness_outcome']}
            try:
                atomic_json(self.run_root/'result.json', outcome)
            except OSError as exc:
                outcome['harness_outcome']={'status':'error','error':f'Outcome file failed: {exc}',
                                            'previous':outcome['harness_outcome']}
                print(dump(outcome),file=sys.stderr)
        return outcome


def transport_retries(value):
    """CLI spelling for the Provider's explicit retry policy."""
    if value.lower() in ('unlimited', 'none'):
        return None
    try:
        count = int(value)
        if count >= 0:
            return count
    except ValueError:
        pass
    raise argparse.ArgumentTypeError('expected a nonnegative integer or unlimited')


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__,formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    parser.add_argument('repo',type=Path,help='Existing software workspace')
    task_input=parser.add_mutually_exclusive_group(required=True)
    task_input.add_argument('--task',type=Path,help='UTF-8 original task file')
    task_input.add_argument('-p','--prompt',help='Original task text (Assignment interface)')
    parser.add_argument('--run-dir',type=Path,help='New or empty evidence directory outside the workspace')
    parser.add_argument('--log',action='store_true',help='Export Assignment JSONL plus complete native evidence')
    parser.add_argument('--log-dir',type=Path,default=Path('madsLoop_logs'),help='Assignment trace destination when --log')
    parser.add_argument('--handoff-bundle',type=Path,help='Validated fresh-session seed; exact --task binding, releasable selections')
    parser.add_argument('--user-message-file', type=Path, action='append', default=[],
                        help='Explicit later user message as UTF-8; repeat in order. Preserved separately from the original task and agent notes')
    parser.add_argument('--review-prompt', type=Path, default=os.environ.get('MADS_REVIEW_PROMPT'),
                        help='Optional UTF-8 instruction file for the configured fresh completion review; snapshotted before requests')
    parser.add_argument('--steps',type=int,default=0,help='Implementation-session request limit; 0 is unlimited. The fresh reviewer has no request limit.')
    parser.add_argument('--input-tokens',type=int,default=220000)
    parser.add_argument('--total-tokens',type=int,default=262144)
    parser.add_argument('--output-tokens',type=int,default=32768)
    parser.add_argument('--mask-tokens',type=int,default=160000)
    parser.add_argument('--pressure-tokens',type=int,default=180000)
    parser.add_argument('--reasoning-replay',choices=REASONING_REPLAY_MODES,
                        default=os.environ.get('MADS_REASONING_REPLAY', 'all'),
                        help='Replay plain Chat reasoning from all, latest, or no retained exchanges; '
                             'structured/Responses reasoning is unchanged. Defaults to MADS_REASONING_REPLAY or all')
    parser.add_argument('--transport-timeout',type=float,default=120,help='Transport inactivity seconds (default: 120); 0 disables; no task-duration limit')
    parser.add_argument('--transport-retries',type=transport_retries,default=2,help='Additional classified transient attempts: nonnegative integer or unlimited (default: 2); billing/quota errors remain terminal')
    parser.add_argument('--provider',choices=('course','openrouter','openai'),help='Provider adapter; defaults to MADS_PROVIDER or course')
    parser.add_argument('--model',help='Model name passed through the selected adapter')
    parser.add_argument('--reasoning',choices=('low','medium','high','xhigh'),help='Responses reasoning effort')
    parser.add_argument('--dry-run',action='store_true',help='Validate local inputs/configuration without creating a run or calling the API')
    args=parser.parse_args(argv)
    run_id='run-'+__import__('uuid').uuid4().hex
    if args.run_dir is None:
        base=args.log_dir/'evidence' if args.log else Path.cwd()/'.runs'
        if base.resolve().is_relative_to(args.repo.resolve()):
            # Runtime evidence must not pollute the task workspace. The course
            # log is published to the requested cwd only AFTER the run finishes.
            base=args.repo.resolve().parent/'.mads-runs'
        args.run_dir=base/run_id
    if args.log:
        native=args.log_dir.resolve()/'evidence'/run_id
        if native != args.run_dir.resolve() and native.is_relative_to(args.run_dir.resolve()):
            parser.error('Log destination cannot be inside --run-dir')
    try:
        if not args.repo.is_dir():
            raise ValueError(f'Repository is not a directory: {args.repo}')
        task=args.task.read_bytes().decode('utf-8') if args.task else args.prompt
        review_instructions = args.review_prompt.read_bytes().decode('utf-8') if args.review_prompt else None
        user_messages = [path.read_bytes().decode('utf-8') for path in args.user_message_file]
        if args.handoff_bundle:
            from .handoff import _seed_inputs
            _seed_inputs(args.handoff_bundle, task)
        config=route(args.provider,args.model,args.reasoning)
        if not os.environ.get(config['key_env']):
            raise ValueError(config['key_env']+' is required')
        if args.reasoning_replay not in REASONING_REPLAY_MODES:
            raise ValueError('MADS_REASONING_REPLAY must be one of: all, latest, none')
        capacity=min(args.input_tokens,args.total_tokens-args.output_tokens)
        if not (0 <= args.mask_tokens < args.pressure_tokens <= capacity and
                0 < args.output_tokens < args.total_tokens):
            raise ValueError('Require 0 <= mask < pressure <= input capacity and positive output reserve')
        if args.transport_timeout < 0 or not __import__('math').isfinite(args.transport_timeout):
            raise ValueError('Transport timeout must be finite and nonnegative')
    except (OSError,ValueError) as exc:
        parser.error(str(exc))
    if args.steps<0 or args.run_dir.resolve().is_relative_to(args.repo.resolve()):
        parser.error('Nonnegative steps required; run directory must be outside repository')
    if args.run_dir.exists() and (not args.run_dir.is_dir() or any(args.run_dir.iterdir())):
        parser.error('Use an empty per-run directory; automatic resume is unsupported')
    print('Resolved configuration: '+dump(config),file=sys.stderr)
    if args.dry_run:
        return 0
    def interrupted(signum,frame):
        raise KeyboardInterrupt(f'Operator signal {signum}')
    signal.signal(signal.SIGTERM,interrupted)
    store=Store(args.run_dir/'journal')
    print(f'Journal: {store.path}',file=sys.stderr,flush=True)
    provider=None
    try:
        provider=Provider(store,config=config,timeout=args.transport_timeout or None,retries=args.transport_retries)
        agent=Agent(args.repo,task,args.run_dir,provider,store,args.steps,
                    review_instructions=review_instructions, user_messages=user_messages,
                    input_limit=args.input_tokens,total_limit=args.total_tokens,output_tokens=args.output_tokens,
                    mask_at=args.mask_tokens,pressure_at=args.pressure_tokens,
                    reasoning_replay=args.reasoning_replay)
        if args.handoff_bundle:
            agent.seed_handoff(args.handoff_bundle)
    except (OSError,ValueError,RuntimeError) as exc:
        if provider is not None:
            provider.close()
        result=dict(agent_outcome={'status':'not_started'},harness_outcome={'status':'error','error':str(exc)},
                    cleanup_outcome={'status':'unknown'},export_outcome={'status':'not_requested'})
        store.add('run_start',task=task,route=config,workdir=str(args.repo.resolve()),
                  run_root=str(args.run_dir.resolve()),startup_failed=True)
        store.add('run_end',**result)
        atomic_json(args.run_dir/'result.json',result)
    else:
        with provider:
            result=agent.run()
    log_failed=False
    if args.log:
        try:
            from .course_logs import export_course_log
            import shutil
            native=args.log_dir.resolve()/'evidence'/run_id
            if native != args.run_dir.resolve():
                # Original run_task copies only madsLoop_logs: make that tree portable.
                if native.is_relative_to(args.run_dir.resolve()):
                    raise ValueError('Log destination cannot be inside --run-dir')
                native.parent.mkdir(parents=True,exist_ok=True)
                shutil.copytree(args.run_dir,native)
            path=export_course_log(native,args.log_dir/(run_id+'.jsonl'))
            print(f'Course trace: {path}',file=sys.stderr)
        except Exception as exc:
            log_failed=True
            print(f'Course log export failed; native evidence remains at {args.run_dir}: {exc}',file=sys.stderr)
    print(dump(result),file=sys.stderr if args.prompt is not None else sys.stdout)
    return 0 if not log_failed and result['harness_outcome']['status']=='completed' and result['cleanup_outcome']['status']=='completed' else 1
