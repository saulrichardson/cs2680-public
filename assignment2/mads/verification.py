"""Optional versioned expectations and process evidence, never a completion gate."""
import copy
import hashlib
from pathlib import Path
from .store import dump
from .review import capture as capture_review


class Intentions:
    def __init__(self, root, original_task, user_messages=()):
        self.root=Path(root); self.root.mkdir(parents=True,exist_ok=True)
        self.task=original_task
        self.user_messages=list(user_messages)
        self.rows={}
        self.events=[]
        self.bindings={}

    def save(self, tools, event):
        event=copy.deepcopy(event)
        event['journal']=str(tools.store.root)
        event['event_record']=tools.store.add('verification_intention',blob=tools.store.blob(dump(event)))
        self.events.append(event)
        # Complete immutable versions and current state are preserved separately.
        with (self.root/'events.jsonl').open('a') as stream:
            stream.write(dump(event)+'\n')
        (self.root/'current.json').write_text(dump(self.packet(compact=False))+'\n')
        tools.effects='verification_state_recorded'
        return {'verification':self.packet(), 'event':event}

    def packet(self, compact=True):
        rows=copy.deepcopy(list(self.rows.values()))
        for row in rows:
            for run in row['runs']:
                key=(run['journal'],run['job_id'])
                if key in self.bindings:
                    jobs=self.bindings[key]
                    run['process']=jobs.state(jobs.jobs[run['job_id']])
                if compact:
                    review=run.pop('before_review')
                    run['before_review_record']=review.get('observation_record')
                    if review.get('notice'):
                        run['before_review_notice']=review['notice']
                    state=run.get('process',{})
                    run['process']={k:state[k] for k in ('state','shell_exit_code','capture_status','observed_bytes') if k in state}
            if compact:
                row['execution_count']=len(row['runs'])
                row['runs']=row['runs'][-1:]
        return {'directory':str(self.root), 'original_task_sha256':hashlib.sha256(self.task.encode()).hexdigest(),
                'later_user_sources':[dict(index=i, sha256=hashlib.sha256(text.encode()).hexdigest())
                    for i,text in enumerate(self.user_messages)],
                'intentions':rows, 'uncited_original_task_lines':self.uncited_lines(), 'meaning':'Agent-authored coverage and interpretations, not authority. Processes are sampled; captures are historical. Source changes can invalidate results. Missing intentions can hide unmet requirements.'}

    def uncited_lines(self):
        """Literal citation gaps, deliberately not a semantic coverage score."""
        quotes=[row['requirement_source'].get('text', row['requirement'])
                for row in self.rows.values()]
        return [{'line':i,'text':line} for i,line in enumerate(self.task.splitlines(),1)
                if line.strip() and not any(line.strip() in q or q in line for q in quotes)]

    def requirement_source(self, requirement):
        """Attribute a literal quote without replacing original authority with notes."""
        if requirement in self.task:
            return dict(kind='original_task', sha256=hashlib.sha256(self.task.encode()).hexdigest())
        matches = [(i,text) for i,text in enumerate(self.user_messages) if requirement in text]
        if not matches:
            raise ValueError('Use source_ids from task(action="show"), or an exact quote '
                             'from the original task or a later user message. Attribution is not completeness.')
        i,text = matches[-1]
        return dict(kind='later_user_message', index=i, sha256=hashlib.sha256(text.encode()).hexdigest())

    def action(self, tools, action, id=None, requirement=None, entrypoint=None,
               scenario=None, expected=None, command=None, reason=None,
               assessment=None, evidence=None, wait_seconds=10, output_chars=12000, target=None,
               source_ids=None):
        if action=='status': return {'verification':self.packet()}
        if not id: raise ValueError('id is required')
        if action in ('plan','revise'):
            old=self.rows.get(id)
            if action=='plan' and old: raise ValueError('id exists; use revise with a reason')
            if action=='revise' and (not old or not reason): raise ValueError('revise requires an existing id and explicit reason')
            fields=dict(entrypoint=entrypoint,scenario=scenario,expected=expected,command=command,target=target)
            if action=='revise':
                fields={key:old[key] if value is None else value for key,value in fields.items()}
            if not all(fields[key] for key in ('entrypoint','scenario','expected','command')):
                raise ValueError('plan/revise requires entrypoint, scenario, expected and command')
            if source_ids is not None:
                if not source_ids:
                    raise ValueError('source_ids must select at least one source from task(action="show")')
                units = tools.task_state.packet(source_ids)['source_units']
                source = dict(kind='original_task', sha256=tools.task_state.sha256,
                              source_ids=list(source_ids), text=''.join(unit['text'] for unit in units))
                requirement = requirement or source['text']
            elif requirement is None and old:
                requirement=old['requirement']
                source=copy.deepcopy(old['requirement_source'])
            else:
                if not requirement:
                    raise ValueError('plan/revise requires source_ids or an exact requirement quote')
                source = self.requirement_source(requirement)
            row=dict(id=id,version=(old['version']+1 if old else 1),requirement=requirement, requirement_source=source,
                     **fields,interpretation=None,runs=[],authored_in=str(tools.store.root))
            self.rows[id]=row
            return self.save(tools,dict(action=action,previous=old,current=row,reason=reason))
        row=self.rows[id]
        if action=='run':
            if (any(x is not None for x in (requirement,entrypoint,scenario,expected,assessment,evidence,source_ids))
                    or (command is not None and command != row['command'])):
                raise ValueError('run executes the stored command unchanged; use action="run" and id only, '
                                 'or action="revise" with a reason to change the plan')
            before=capture_review(tools)
            result=tools.bash(row['command'],wait_seconds=wait_seconds,output_chars=output_chars,
                              target=target if target is not None else row.get('target'))
            job=result['process']['job_id']
            run=dict(job_id=job,journal=str(tools.store.root),command=row['command'],
                     execution_target=result['process'].get('execution_target','host'),
                     before_review=before,initial_observation=result['observation_record'],
                     output_path=result['process']['live_path'])
            row['runs'].append(run);row['interpretation']=None
            self.bindings[(str(tools.store.root),job)]=tools.jobs
            saved=self.save(tools,dict(action=action,id=id,version=row['version'],execution=run))
            tools.effects='process_started_and_verification_recorded'
            return {**result,**saved}
        if action=='interpret':
            if not assessment or not evidence or not reason:
                raise ValueError('interpret requires assessment, evidence paths/records and requirement-grounded reason')
            if assessment in ('supported','contradicted') and not row['runs']:
                raise ValueError('No command execution is linked to this version; run it, or mark blocked/unresolved')
            interpretation=dict(assessment=assessment,evidence=evidence,reason=reason,
                                authored_in=str(tools.store.root),execution_count=len(row['runs']))
            row['interpretation']=interpretation
            return self.save(tools,dict(action=action,id=id,version=row['version'],interpretation=interpretation))
        raise ValueError('unknown verification action')
