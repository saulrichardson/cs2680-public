"""Sampling defaults and native reasoning through actual recorded HTTP effects."""
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from hypothesis import given, settings, strategies as st

from src.teamharness.agents import AgentManager
from src.teamharness.main import Investigation
from src.teamharness.provider import HTTPProvider
from src.teamharness.session import Session
from test_course_request_compatibility import ModelRelay
from test_main_flow import repository, call, finish, http_script, audit_requests
from test_session import Script, answer, tools, SHELL


class SamplingReasoning(unittest.TestCase):
    @settings(max_examples=20, deadline=None, derandomize=True)
    @given(temperature=st.one_of(st.none(), st.floats(0, 2, allow_nan=False, allow_infinity=False)),
           other_setting=st.booleans())
    def test_new_defaults_and_explicit_values_survive_reopening(self, temperature, other_setting):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); work=root/'work'; work.mkdir()
            supplied={'max_tokens':128} if other_setting else {}
            if temperature is not None: supplied['temperature']=temperature
            model=Script([answer('first'),answer('second')])
            session=Session(root/'session','implementer','System','Task',model,[],lambda *_: {},work,
                            settings=supplied)
            session.run()
            recorded=Session.recorded_config(session.root)['settings']
            if temperature is not None:
                self.assertEqual(model.requests[0]['temperature'],temperature)
            self.assertEqual({k:model.requests[0][k] for k in recorded},recorded)
            frozen=Session.recorded_config(session.root)
            reopened=Session(root=session.root,provider=model,executor=lambda *_:{},**frozen)
            reopened.append_user('Continue the same session.'); reopened.run()
            self.assertEqual({k:model.requests[1][k] for k in recorded},recorded)
            self.assertEqual(reopened.session_id,session.session_id)
            self.assertEqual(bool(other_setting),'max_tokens' in model.requests[1])
            audit_requests(session.database)

    def test_recorded_backend_default_is_not_replaced_when_reopened(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); work=root/'work'; work.mkdir()
            model=Script([answer('first'),answer('second')])
            session=Session(root/'session','implementer','System','Task',model,[],lambda *_:{},work)
            # Existing records can deliberately omit sampling settings. Reopening
            # must not acquire a new default, including historical empty settings.
            session.reconfigure(reason='Explicit recorded provider-default configuration',settings={})
            session.run()
            frozen=Session.recorded_config(session.root)
            reopened=Session(root=session.root,provider=model,executor=lambda *_:{},**frozen)
            reopened.append_user('Continue.'); reopened.run()
            self.assertTrue(all('temperature' not in request for request in model.requests))
            self.assertEqual(Session.recorded_config(session.root)['settings'],{})
            audit_requests(session.database)

    def test_compaction_retains_explicit_temperature(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); work=root/'work'; work.mkdir()
            model=Script([tools(('compact',{})),answer('{"continuation":"Saved interpretation."}'),answer('done')])
            session=Session(root/'session','implementer','System','Task',model,[SHELL],lambda *_:{},work,
                            settings={'temperature':0.23})
            self.assertEqual(session.run()['status'],'completed')
            self.assertTrue(all(request['temperature']==0.23 for request in model.requests))
            audit_requests(session.database)

    def test_recorded_sampling_and_reasoning_survive_course_wire(self):
        marker='QWEN_REASONING_WIRE_SENTINEL_2680'
        class Model:
            def __init__(self): self.requests=[]; self.errors=[]
            def reply(self,request):
                if len(self.requests)==1:
                    value=call('exec_command',{'command':'printf observed > observation.txt'})
                    value['choices'][0]['message'].update(reasoning_content=marker,
                        reasoning_details=[{'type':'reasoning.text','text':'Attributed native reasoning.'}])
                    return value
                return finish('Observed the original command result.')
        model=Model()
        with tempfile.TemporaryDirectory() as tmp, http_script(model) as env:
            root=Path(tmp); repo=repository(root)
            relay=ModelRelay('http://127.0.0.1:1',env['A4_BASE_URL'],'fixture-upstream-key')
            try:
                with patch.dict(os.environ,{**env,'A4_BASE_URL':relay.url,'A4_API_KEY':relay.token}):
                    manager=AgentManager(root/'run/agents',HTTPProvider)
                    try:
                        flow=Investigation(manager,root/'run')
                        result=flow.run('Record a focused source observation.',repo)
                        self.assertEqual(result['state'],'completed',result)
                        self.assertEqual((repo/'observation.txt').read_text(),'observed')
                        self.assertEqual(len(model.requests),2)
                        ident=result['agents']['main']
                        recorded=Session.recorded_config(Path(manager.descriptor(ident)['root']))['settings']
                        self.assertTrue(all({k:request[k] for k in recorded}==recorded for request in model.requests))
                        previous=next(m for m in model.requests[1]['messages'] if m['role']=='assistant')
                        self.assertEqual(previous['reasoning_content'],marker)
                        self.assertEqual(previous['reasoning_details'][0]['type'],'reasoning.text')
                        database=Path(manager.inspect(result['agents']['main'])['root'])/'session.sqlite3'
                        self.assertEqual(len(audit_requests(database)),2)
                        evidence=os.environ.get('SAMPLING_REASONING_EVIDENCE')
                        if evidence:
                            output=Path(evidence); output.mkdir(parents=True,exist_ok=False)
                            for index,request in enumerate(model.requests):
                                (output/f'request-{index}.json').write_text(json.dumps(request,indent=2))
                            (output/'result.json').write_text(json.dumps({'requests':2,'settings':recorded,
                                'reasoning_content_forwarded':True,'native_details_forwarded':True,
                                'full_request_reconstruction':True,'live_gpu':False},indent=2))
                    finally: manager.shutdown()
            finally: relay.close()
        self.assertFalse(model.errors)
