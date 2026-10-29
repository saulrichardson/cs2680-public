"""Packaged allocation through independent real sandboxes, workers and HTTP.

Synthetic public repairs only. Checks pause/resume, negative recommendations,
completion takeover and discarded exploratory edits. No cloud or live model.
"""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
from http.server import ThreadingHTTPServer
import importlib.util
import json
import os
from pathlib import Path
import re
import sqlite3
import subprocess
import sys
import threading
import types
from unittest.mock import patch


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--package',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--task-count',type=int,default=3)
    parser.add_argument('--skip-count',type=int,help='Exercise the packaged skip policy on this many real task sandboxes')
    parser.add_argument('--fail-selected-finalization',type=int,
                        help='Lose one selected task finalization reply after recording its actual effect')
    args=parser.parse_args()
    if args.task_count < 0 or (args.skip_count is not None and args.skip_count < 0):
        parser.error('Fixture task and skip counts must be nonnegative')
    if args.skip_count is None and args.task_count != 3:
        parser.error('The compatibility scenario has exactly three tasks')
    package=args.package.resolve(); repo=Path(__file__).resolve().parents[3]
    sys.path[:0]=[str(package),str(repo/'test/teamharness'),str(repo)]
    from src.agentic_loop import Agent
    from test_main_flow import repository, call, finish, facts, http_script, audit_requests
    from src.backend import course_request
    output=args.output.resolve(); output.mkdir(parents=True,exist_ok=False)
    os.chdir(output)
    tasks, sources, bases, sandboxes = [], {}, {}, []
    scenario_skip=args.skip_count is not None
    target=max(0,args.task_count-args.skip_count) if scenario_skip else 2
    expected_selected=set(range(target))
    if args.fail_selected_finalization is not None and args.fail_selected_finalization not in expected_selected:
        parser.error('Failure injection must identify an expected selected task')
    for ident in range(args.task_count):
        directory=output/str(ident); directory.mkdir()
        source=repository(directory); sources[ident]=source
        if scenario_skip:
            (source/'identity.txt').write_text('Synthetic allocation source '+str(ident)+'\n')
            subprocess.run(['git','-C',str(source),'add','identity.txt'],check=True)
            subprocess.run(['git','-C',str(source),'-c','user.name=Fixture','-c','user.email=fixture@local',
                            'commit','-qm','task source identity'],check=True)
        bases[ident]=subprocess.check_output(['git','-C',str(source),'rev-parse','HEAD'],text=True).strip()
        # Each handler has its own module globals, as independent course containers do.
        spec=importlib.util.spec_from_file_location('allocation_sandbox_'+str(ident),package/'src/sandbox_server.py')
        module=importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
        module.ROOT=str(source); module.WORK_ROOT=str(output/'task-state')
        server=ThreadingHTTPServer(('127.0.0.1',0),module.Handler)
        threading.Thread(target=server.serve_forever,daemon=True).start(); sandboxes.append(server)
        tasks.append({'task':ident,'sandbox_url':f'http://127.0.0.1:{server.server_port}',
            'workdir':str(source),'problem_statement':f'Repair answer(n) to return n unchanged. Synthetic task {ident}.'})

    plan={'plan':'Repair answer and execute the actual public consumer.',
          'checks':[{'name':'consumer','argv':[sys.executable,'-B','-c','from answer import answer; assert answer(3)==3']}]}
    class Script:
        def __init__(self):
            self.requests,self.errors,self.steps,self.first_owner=[],[],{},{}
            self.implementation_steps={}
            self.exploration_responses=Counter()
            self.decision_responses=Counter()
            self.lock=threading.Lock()
            self.pressure_lock=threading.Lock(); self.pressure_ready=threading.Event()
            self.active_provider=0; self.peak_provider=0; self.model_slots=None
        def reply(self,request):
            with self.pressure_lock:
                self.active_provider+=1; self.peak_provider=max(self.peak_provider,self.active_provider)
                if self.model_slots is not None and self.active_provider==self.model_slots:
                    self.pressure_ready.set()
            try:
                if self.model_slots is not None:
                    assert self.pressure_ready.wait(30),'Fixture provider never received the configured concurrent model wave'
                return self.respond(request)
            finally:
                with self.pressure_lock: self.active_provider-=1
        def respond(self,request):
            with self.lock:
                f=facts(request); agent=f['agent_id']; root=Path(f['archive_root'])
                task_parts=[re.fullmatch(r'teamharness-([0-9]+)',part) for part in root.parts]
                identifiers=[int(match[1]) for match in task_parts if match]
                assert len(identifiers)==1,root
                ident=identifiers[0]
                self.first_owner.setdefault(ident,agent)
                step=self.steps.get(agent,0); self.steps[agent]=step+1
                fresh=agent != self.first_owner[ident]
                boundary=f.get('planning_boundary',{})
                names={tool['function']['name'] for tool in request.get('tools',[])}
                if boundary.get('version')==2 or (fresh and not boundary.get('enabled')):
                    if 'repair_plan' not in names:
                        assert not fresh and 'read_file' in names and 'finish_role' not in names
                        self.exploration_responses[agent]+=1
                        if step==0 and (scenario_skip or ident==2):
                            return call('exec_command',{'command':"printf 'def answer(n):\\n    return n\\n' > answer.py"})
                        return call('read_file',{'path':'answer.py'})
                    if names=={'repair_plan'}:
                        assert not fresh,'Verification owner was sent through initial planning again'
                        self.decision_responses[agent]+=1
                        assert self.decision_responses[agent]==1
                        readiness,effort=((('repair','small') if ident<5 else ('repair','medium') if ident<9 else
                                          ('experiment','small') if ident<13 else ('experiment','large') if ident<18 else
                                          ('search','small') if ident<22 else ('blocked','small')) if scenario_skip else
                                         ('repair' if ident==0 else 'experiment' if ident==1 else 'search','small'))
                        choice=({'action':'continue','plan':plan['plan']} if ident==0 else
                                {'action':'skip','reason':'The inspected answer implementation needs an explicitly selected repair attempt.'})
                        return call('repair_plan',{**choice,'assessment':{'readiness':readiness,'effort':effort}})
                    assert ident in expected_selected,'A reserve owner entered implementation'
                    assert {'repair_plan','exec_command','finish_role'} <= names
                    assert fresh or 'planning_admitted' in str(request['messages'])
                    implemented=self.implementation_steps.get(agent,0)
                    self.implementation_steps[agent]=implemented+1
                    if implemented==0:
                        return call('repair_plan',{'action':'set',**plan})
                    if implemented==1:
                        expression='n if n < 7 else -1' if not scenario_skip and ident==0 and not fresh else 'n'
                        return call('exec_command',{'command':"printf 'def answer(n):\\n    return "+expression+"\\n' > answer.py"})
                    assert implemented==2,'Implementation continued after its explicit completion request'
                    return finish('Synthetic repair returned for verification.')
                if scenario_skip:
                    assert not fresh,'Skip scenario unexpectedly restarted its original owner'
                    # Readiness/effort establish a fixed public fixture order,
                    # independently of arrival order, recommendations and grades.
                    readiness,effort=(('repair','small') if ident<5 else ('repair','medium') if ident<9 else
                                     ('experiment','small') if ident<13 else ('experiment','large') if ident<18 else
                                     ('search','small') if ident<22 else ('blocked','small'))
                    if step==0:
                        return call('exec_command',{'command':"printf 'def answer(n):\\n    return n\\n' > answer.py"})
                    if step==1:
                        choice=({'action':'continue',**plan} if ident==0 else
                                {'action':'skip','reason':'An explicit negative recommendation is retained as model judgment.'})
                        return call('repair_plan',{**choice,'assessment':{'readiness':readiness,'effort':effort}})
                    assert ident in expected_selected,'A reserve task continued after its paused planning result'
                    assert 'planning_admitted' in str(request['messages'])
                    if step==2:
                        return call('repair_plan',{'action':'set',**plan})
                    if step==3:
                        return call('exec_command',{'command':"printf 'def answer(n):\\n    return n\\n' > answer.py"})
                    assert step==4,'Selected owner repeated implementation after completion'
                    return finish('Synthetic selected repair returned for verification.')
                if fresh:
                    assert not f['planning_boundary'].get('pause_after_planning'),f
                if step==0 and ident==2:
                    return call('exec_command',{'command':"printf 'def answer(n):\\n    return n\\n' > answer.py"})
                if step==0 or (ident==2 and step==1):
                    if fresh or ident==0:
                        return call('repair_plan',{'action':'continue',**plan,
                            **({'assessment':{'readiness':'repair','effort':'small'}} if not fresh else {})})
                    return call('repair_plan',{'action':'skip','reason':'I have not established a confident repair path.',
                        'assessment':{'readiness':'experiment' if ident==1 else 'search','effort':'small'}})
                assert ident!=2,'Unselected task continued after its planning decision'
                assert 'planning_admitted' in str(request['messages']) or fresh
                if ident==1 and step==1:
                    return call('repair_plan',{'action':'set',**plan})
                edit_step=2 if ident==1 else 1
                if step==edit_step:
                    expression='n if n < 7 else -1' if ident==0 and not fresh else 'n'
                    return call('exec_command',{'command':"printf 'def answer(n):\\n    return "+expression+"\\n' > answer.py"})
                return finish('Synthetic repair returned for verification.')

    class Dispatcher:
        def __init__(self):
            self.pending=list(reversed(tasks)); self.submitted={}; self.grades={i:[] for i in sources}; self.finished={}
            self.finalizations=Counter()
            self.lock=threading.Lock()
        def next_task(self):
            return self.pending.pop(0) if self.pending else None
        def submit_patch(self,ident,text):
            self.submitted[ident]=text.encode()
        def continue_task(self,ident): pass
        def evaluate(self,ident):
            with self.lock:
                raw=self.submitted[ident]; index=len(self.grades[ident])
                target=output/f'grade-{ident}-{index}'
                subprocess.run(['git','clone','-q',str(sources[ident]),str(target)],check=True)
                subprocess.run(['git','-C',str(target),'reset','--hard',bases[ident]],check=True,capture_output=True)
                subprocess.run(['git','-C',str(target),'apply','--binary','-'],input=raw,check=True,capture_output=True)
                result=subprocess.run([sys.executable,'-B','-c',
                    'from answer import answer; print(sum(answer(n)!=n for n in range(10)))'],cwd=target,check=True,capture_output=True,text=True)
                value={'tests_failed':int(result.stdout),'tests_total':10,'attempt':index}
                self.grades[ident].append({'receipt':value,'sha256':hashlib.sha256(raw).hexdigest()})
                return value
        def done(self,ident,reason,calls):
            self.finalizations[ident]+=1
            assert self.finalizations[ident]==1,'Course finalization was replayed'
            if reason=='allocation_discarded':
                assert ident not in expected_selected and self.submitted[ident]==b''
                result={'tests_failed':10,'tests_total':10}
            else:
                assert reason=='completed',(ident,reason)
                assert self.grades[ident][-1]['sha256']==hashlib.sha256(self.submitted[ident]).hexdigest()
                result=self.grades[ident][-1]['receipt']
            self.finished[ident]=result
            if ident==args.fail_selected_finalization:
                raise OSError('Fixture lost finalization reply after durable course effect; do not replay')
            return result
        def logger(self,ident): return self
        def api_request(self,*args): pass

    script=Script(); dispatcher=Dispatcher()
    course_module=types.ModuleType('dispatcher')
    class DispatcherShutdown(Exception): pass
    course_module.DispatcherShutdown=DispatcherShutdown
    campaign=json.loads((package/'src/campaign.json').read_text())
    original_campaign=json.loads(json.dumps(campaign))
    script.model_slots=campaign.get('model_slots')
    if scenario_skip:
        assert campaign['allocation'].get('skip_count')==args.skip_count and 'count' not in campaign['allocation'],campaign
    else:
        campaign['solve_seconds']=90
    campaign.update(python=sys.executable,state_root=str(output/'task-state'))
    (package/'src/campaign.json').write_text(json.dumps(campaign))
    try:
        with patch.dict(sys.modules,{'dispatcher':course_module}),http_script(script) as env,patch.dict(os.environ,{'CS2680_BASE_URL':env['A4_BASE_URL'],
                'CS2680_MODEL_STANDARD':env['A4_MODEL'],'CS2680_API_KEY':'fixture-upstream-key'}):
            Agent(dispatcher).run()
    finally:
        for server in sandboxes:
            server.shutdown(); server.server_close()
    assert not script.errors,script.errors
    result=json.loads((output/'madsServe_logs/allocation/result.json').read_text())
    assert set(result['selected'])==expected_selected,result
    assert {row['task_id'] for row in result['failures']}==({args.fail_selected_finalization}
        if args.fail_selected_finalization is not None else set()),result
    assert set(dispatcher.finished)==set(range(args.task_count)),dispatcher.finished
    assert all(dispatcher.finished[i]['tests_failed']==(0 if i in expected_selected else 10) for i in sources)
    selection=json.loads((output/'madsServe_logs/allocation/selection.json').read_text())
    # This is a compatibility oracle against the existing, independently tested
    # selector. The expected 26->13 / small-queue cardinality is asserted above.
    from src.teamharness.allocation import select
    rows=selection['selected']+selection['reserve']
    legacy=select(list(reversed(rows)),target)
    assert [row['task_id'] for row in legacy['selected']]==result['selected']
    assert {row['task_id'] for row in rows}==set(range(args.task_count))
    original=[]
    identities=[]
    admitted=[]
    for ident in range(args.task_count):
        archive=output/'madsServe_logs'/str(ident)/'teamharness'
        for database in (archive/'run/agents/agents').glob('*/session.sqlite3'):
            original.extend(audit_requests(database))
        planned=json.loads((archive/'planning.json').read_text())['planning']
        workflow=json.loads((archive/'run/workflow.json').read_text())
        launch=json.loads((archive/'launch.json').read_text())
        assert workflow['task_deadline_unix']==planned['deadline']==launch['deadline']
        assert launch['course_base']==bases[ident]
        assert json.loads((archive/'task.json').read_text())==tasks[ident]
        if ident in expected_selected:
            initial=workflow['agents'].get('original_main',workflow['agents']['main'])
            assert initial==planned['agent_id']
            assert workflow['task_deadline_unix']==planned['deadline']
            returned=json.loads((archive/'run/agents/agents'/initial/'runner.json').read_text())
            assert returned['session_id']==planned['session_id']
            if not scenario_skip and ident==0: assert len(workflow['handoff_history'])==1
            if scenario_skip or ident==1: assert len(list((archive/'run/agents/agents').glob('*/agent.json')))==1
            candidate=json.loads((archive/'candidate.json').read_text())
            raw=(archive/'candidate.patch').read_bytes()
            assert raw==dispatcher.submitted[ident]
            assert candidate['sha256']==hashlib.sha256(raw).hexdigest()
            assert candidate['base']==bases[ident],candidate
            assert dispatcher.grades[ident][-1]['sha256']==candidate['sha256']
            admitted.append(ident)
            assert (archive/'admit-result.json').is_file()
            identities.append({'task':ident,'base':bases[ident],'candidate_sha256':candidate['sha256'],
                               'initial_agent':initial,'session':returned['session_id'],'deadline':planned['deadline']})
        else:
            assert (archive/'discarded.patch').stat().st_size>0
            assert not (archive/'candidate.patch').exists()
            assert workflow['state']=='planning_complete'
            assert dispatcher.submitted[ident]==b'' and not dispatcher.grades[ident]
            assert not (archive/'admit-result.json').exists()
            assert not list((archive/'run/agents/agents').glob('*/planning-admission.json'))
    assert set(admitted)==expected_selected,'Selected failure caused automatic reserve backfill'
    normalize=lambda request:json.dumps(request,sort_keys=True)
    projected=[json.loads(course_request(json.dumps(request).encode())[0]) for request in original]
    assert Counter(map(normalize,projected))==Counter(map(normalize,script.requests))
    scheduler_evidence=None
    if campaign.get('model_slots') is not None:
        root=output/'madsServe_logs/model-scheduler'
        snapshot=json.loads((root/'final.json').read_text())
        cap=campaign['model_slots']
        assert snapshot['max_inflight']==cap and snapshot['closed'] and not snapshot['archive_error']
        assert all(snapshot[key]==0 for key in ('queued','inflight','active_upstreams','unknown_count','open_tasks','refusal_count'))
        assert snapshot['task_count']==args.task_count
        assert snapshot['request_count']==snapshot['arrival_count']==len(script.requests)
        assert script.peak_provider==cap
        # Bind scheduler task/identity/body to original native transport records,
        # using archived raw bytes before the course adapter's known projection.
        expected={}
        for ident in sources:
            archive=output/'madsServe_logs'/str(ident)/'teamharness'
            for database in (archive/'run/agents/agents').glob('*/context.sqlite3'):
                db=sqlite3.connect(database.resolve().as_uri()+'?mode=ro',uri=True)
                try:
                    for number,body in db.execute("SELECT id,body FROM objects WHERE kind='transport_request'"):
                        record=json.loads(body)
                        raws=db.execute("SELECT b.bytes FROM blobs b JOIN objects o ON o.id=b.object_id JOIN links l ON l.source=o.id WHERE o.kind='raw_request' AND l.relation='transport_request' AND l.target=?",(number,)).fetchall()
                        assert len(raws)==1
                        identity=(str(ident),hashlib.sha256(record['logical_request_id'].encode()).hexdigest())
                        assert identity not in expected,'Unexpected repeated native transport attempt'
                        expected[identity]=course_request(raws[0][0])[0]
                finally: db.close()
        observed={}; scheduled=[]
        for record in snapshot['requests']:
            identity=(record['task_id'],record['request_id_sha256'])
            assert identity in expected and identity not in observed
            folder=root/'requests'/f"{record['sequence']:06d}"
            body=(folder/'request.json').read_bytes()
            assert body==expected[identity] and hashlib.sha256(body).hexdigest()==record['request_sha256']
            response=(folder/'response.body').read_bytes()
            assert hashlib.sha256(response).hexdigest()==record['response_sha256']
            assert record['state']=='completed' and record['response_complete'] and record['upstream_status']==200
            assert json.loads((folder/'receipt.json').read_text())==record
            observed[identity]=body; scheduled.append(json.loads(body))
        assert observed==expected
        assert Counter(map(normalize,scheduled))==Counter(map(normalize,script.requests))
        active=set(); peak=0; started=set(); finished=set()
        for event in map(json.loads,(root/'events.jsonl').read_text().splitlines()):
            if event['event']=='started':
                assert event['sequence'] not in started
                started.add(event['sequence']); active.add(event['sequence']); peak=max(peak,len(active))
                assert len(active)<=cap,'Persisted scheduler admitted beyond the configured cap'
            elif event['event']=='completed':
                assert event['sequence'] in active
                active.remove(event['sequence']); finished.add(event['sequence'])
        assert not active and len(started)==len(finished)==len(expected) and peak==cap
        scheduler_evidence={'cap':cap,'persisted_peak_inflight':peak,'observed_provider_peak':script.peak_provider,
            'task_count':snapshot['task_count'],'requests':len(expected),'all_task_request_body_identities_match':True,
            'all_responses_complete_and_hashed':True,'no_uncertain_or_queued_work_at_close':True,
            'snapshot_sha256':hashlib.sha256((root/'final.json').read_bytes()).hexdigest()}
    report={'passed':True,'selected':result['selected'],'tasks_finished':args.task_count,
        'negative_recommendation_admitted':any(row.get('recommendation')=='skip' for row in selection['selected']),
        'same_session_resumed':True,
        'completion_takeover_after_allocation':not scenario_skip,'discarded_edits_not_submitted':True,
        'all_requests_reconstructed':True,'model_quality_measured':False,'platform':sys.platform,
        'original_campaign':original_campaign,'fixture_campaign_overrides':{key:campaign[key] for key in campaign
            if campaign[key]!=original_campaign.get(key)},'skip_count':args.skip_count,'target':target,
        'selected_identities':identities,'nonselected_empty_submissions':args.task_count-len(admitted),
        'deterministic_legacy_ranking_preserved':True,'no_automatic_backfill':True,
        'lost_finalization_reply':args.fail_selected_finalization,'recorded_failures':result['failures'],
        'finalizations_each_once':dict(dispatcher.finalizations),
        'exploration_responses':dict(script.exploration_responses),'decision_responses':dict(script.decision_responses),
        'scheduler':scheduler_evidence}
    (output/'CHECK.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps(report))


if __name__=='__main__': main()
