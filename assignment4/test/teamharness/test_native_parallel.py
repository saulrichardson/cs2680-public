"""Native multi-call exchanges through actual HTTP, edits, processes and recovery."""
import json
import os
from pathlib import Path
import shlex
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from src.teamharness.agents import AgentManager
from src.teamharness.provider import HTTPProvider
from src.teamharness.session import Session, context_status, request_bytes
from test_main_flow import repository, http_script, finish, audit_requests
from test_session import Script, tools, answer, SHELL
from receipt_fixture import original_receipt


class NativeParallel(unittest.TestCase):
    def until(self, predicate):
        end = time.monotonic() + 8
        while not predicate():
            self.assertLess(time.monotonic(), end, 'Effect did not reach the expected state')
            time.sleep(.01)

    def test_http_commands_overlap_and_separate_file_edits_share_one_response(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); repo = repository(root)
            case = self
            class Model:
                def __init__(self):
                    self.requests, self.errors, self.phase = [], [], 0
                def reply(self, request):
                    self.phase += 1
                    case.assertTrue(request['parallel_tool_calls'])
                    if self.phase == 1:
                        commands = []
                        for name, other, delay in [('first', 'second', .2), ('second', 'first', 0)]:
                            code = ("from pathlib import Path; import time; "
                                    f"Path('{name}.ready').write_text('ready'); "
                                    "end=time.monotonic()+5\n"
                                    f"while not Path('{other}.ready').exists():\n"
                                    " assert time.monotonic()<end, 'commands did not overlap'\n time.sleep(.01)\n"
                                    f"time.sleep({delay}); print('{name} overlapped')")
                            commands.append(('exec_command', {'command': shlex.quote(sys.executable)+' -c '+shlex.quote(code)}))
                        return tools(*commands)
                    recent = [m for m in request['messages'] if m['role']=='tool']
                    if self.phase == 2:
                        case.assertEqual([m['tool_call_id'] for m in recent], ['call_0', 'call_1'])
                        case.assertTrue(all(original_receipt(m['content'])['exit_code']==0 for m in recent))
                        return tools(*[('apply_patch', {'patch':f'*** Begin Patch\n*** Add File: {name}.txt\n+{value}\n*** End Patch\n'})
                                       for name,value in [('a','alpha'),('b','beta')]],
                                     ('apply_patch', {'patch':'invalid patch syntax'}))
                    if self.phase == 3:
                        rejected=original_receipt(recent[-1]['content'])
                        case.assertIs(rejected.get('applied',rejected.get('executed')),False)
                        case.assertEqual((repo/'a.txt').read_text(), 'alpha\n')
                        case.assertEqual((repo/'b.txt').read_text(), 'beta\n')
                        return tools(('exec_command', {'command':shlex.quote(sys.executable)+" -c "+shlex.quote(
                            "from pathlib import Path; assert Path('a.txt').read_text()=='alpha\\n'; assert Path('b.txt').read_text()=='beta\\n'; print('both files verified')")}))
                    case.assertEqual(original_receipt(recent[-1]['content'])['exit_code'], 0)
                    return finish('Two concurrent commands, two file edits, and a subsequent real check.')
            model=Model()
            with http_script(model) as env, patch.dict(os.environ, env):
                manager=AgentManager(root/'team', HTTPProvider)
                try:
                    ident=manager.create('main','Edit two independent files and check their contents.',repo)
                    manager.start(ident)
                    result=manager.wait(ident,20)
                    self.assertEqual(model.errors,[])
                    self.assertEqual(result['status'],'completed',result)
                    d=manager.descriptor(ident)
                    archived=audit_requests(Path(d['root'])/'session.sqlite3')
                    self.assertEqual(archived,model.requests)
                    groups=manager.parallel_groups(ident).list()
                    self.assertEqual(len(groups),2)
                    first=Path(groups[0]['archive'])
                    # Locate command group independent of directory enumeration order.
                    first=next(Path(g['archive']) for g in groups if g['members'][0]['name']=='exec_command')
                    descriptor=json.loads((first/'group.json').read_text())
                    self.assertTrue(all(m['effect_id'].endswith(':call_'+str(i)) for i,m in enumerate(descriptor['members'])))
                    self.assertGreater((first/'members/0000/result.json').stat().st_mtime_ns,
                                       (first/'members/0001/result.json').stat().st_mtime_ns)
                finally: manager.shutdown()

    def test_recovery_keeps_durable_member_and_never_replays_uncertain_member(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp); entered=threading.Event(); release=threading.Event(); executions=[]
            def execute(name,args,effect):
                executions.append(effect)
                if effect=='uncertain': entered.set(); release.wait(8)
                (root/(effect+'.txt')).write_text('once')
                return {'effect':effect,'executed':True}
            script=Script([answer('Observed without replay.')])
            session=Session(root/'session','investigator','system','task',script,[SHELL],execute,root)
            calls=[{'id':str(i),'name':'exec_command','args':{'command':effect},'effect_id':effect}
                   for i,effect in enumerate(['known','uncertain'])]
            state=session._load()
            message=tools(*[('exec_command',c['args']) for c in calls])['choices'][0]['message']
            for raw,c in zip(message['tool_calls'],calls):raw['id']=c['id']
            state['messages'].append(message)
            state['pending']={'calls':calls,'index':0,'started':True,'group_id':'interrupted',
                              'group_start':0,'group_end':2}
            session._save(state)
            groups=session._groups()
            groups.start([{'name':c['name'],'arguments':c['args']} for c in calls], 'interrupted',
                         effect_ids=[c['effect_id'] for c in calls])
            try:
                self.assertTrue(entered.wait(5))
                self.until(lambda:(groups.root/'interrupted/members/0000/result.json').exists())
                session.run()
                messages=[m for m in script.requests[0]['messages'] if m['role']=='tool']
                self.assertEqual(original_receipt(messages[0]['content'])['effect'],'known')
                self.assertEqual(original_receipt(messages[1]['content'])['state'],'unknown')
                self.assertEqual(sorted(executions),['known','uncertain'])
                self.assertEqual(len(audit_requests(session.database)),1)
            finally: release.set(); groups.shutdown()

    def test_stop_skips_queued_calls_without_killing_started_work(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp); entered=threading.Event(); release=threading.Event(); executions=[]
            def execute(name,args,effect):
                executions.append(args['command']); entered.set(); release.wait(8)
                (root/args['command']).write_text('finished')
                return {'executed':True}
            session=Session(root/'session','investigator','system','task',Script([
                tools(('exec_command',{'command':'started'}),('exec_command',{'command':'forbidden'}))]),
                [SHELL],execute,root)
            groups=session._groups();groups.max_workers=1
            thread=threading.Thread(target=session.run);thread.start()
            try:
                self.assertTrue(entered.wait(5));session.request_stop();thread.join(5)
                self.assertFalse(thread.is_alive())
                self.assertEqual(executions,['started'])
                self.assertFalse((root/'forbidden').exists())
                results=[original_receipt(m['content']) for m in session._load()['messages'] if m['role']=='tool']
                self.assertEqual(results[0]['state'],'unknown')
                self.assertFalse(results[1]['executed'])
            finally:release.set();thread.join(5);groups.shutdown()
            self.assertEqual((root/'started').read_text(),'finished')

    def test_legacy_resume_keeps_ordered_contract_until_explicit_change(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp); seen=[]
            def execute(name,args,effect):
                if args['command']=='write':(root/'value').write_text('written')
                else:self.assertEqual((root/'value').read_text(),'written')
                seen.append(args['command']);return {'executed':True}
            script=Script([tools(('exec_command',{'command':'write'}),('exec_command',{'command':'read'})),answer('done')])
            session=Session(root/'session','investigator','system','task',script,[SHELL],execute,root)
            state=session._load();state['config'].pop('tool_execution');session._save(state)
            resumed=Session(root/'session',provider=script,executor=execute,**Session.recorded_config(root/'session'))
            resumed.run();self.assertEqual(seen,['write','read'])
            self.assertNotIn('parallel_tool_calls',script.requests[0])
            resumed.reconfigure(reason='Enable concurrent responses explicitly',tool_execution='parallel')
            self.assertEqual(resumed._load()['config']['tool_execution'],'parallel')
            self.assertEqual(context_status(resumed)['active_request_bytes'],request_bytes(resumed._request(resumed._load())))
