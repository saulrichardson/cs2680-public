"""The restrictive course wire surface must not disable multi-tool execution."""
import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
import urllib.request

spec=importlib.util.spec_from_file_location('src._course_request_test',Path(__file__).resolve().parents[2]/'lab/adapters/teamharness/backend.py')
backend=importlib.util.module_from_spec(spec);spec.loader.exec_module(backend)
ModelRelay,course_request=backend.ModelRelay,backend.course_request
from test_main_flow import http_script


class CourseRequestCompatibility(unittest.TestCase):
    def test_actual_http_omits_only_generation_hint_and_preserves_two_tool_calls(self):
        case=self
        response={'choices':[{'finish_reason':'tool_calls','message':{'role':'assistant','content':None,
                  'tool_calls':[{'id':name,'type':'function','function':{'name':'exec_command',
                    'arguments':json.dumps({'command':name})}} for name in ['first','second']]}}]}
        class Upstream:
            def __init__(self):self.requests=[];self.errors=[]
            def reply(self,request):
                case.assertEqual(set(request),{'model','messages','tools','tool_choice','stream'})
                return response
        model=Upstream()
        original={'model':'standard','messages':[{'role':'user','content':'Inspect both files.'}],
                  'tools':[],'tool_choice':'auto','stream':False,'parallel_tool_calls':True}
        raw=json.dumps(original).encode()
        with tempfile.TemporaryDirectory() as temp, http_script(model) as env:
            relay=ModelRelay('http://127.0.0.1:1',env['A4_BASE_URL'],'unused',audit_root=Path(temp))
            try:
                request=urllib.request.Request(relay.url+'/chat/completions',data=raw,
                    headers={'Authorization':'Bearer '+relay.token,'Content-Type':'application/json'})
                with urllib.request.urlopen(request) as result: actual=json.loads(result.read())
                self.assertEqual(actual,response)
                self.assertEqual(len(actual['choices'][0]['message']['tool_calls']),2)
                self.assertEqual(model.errors,[])
                self.assertEqual(model.requests[0]['messages'],original['messages'])
                receipt=json.loads(next(Path(temp).glob('*.json')).read_text())
                self.assertEqual(receipt['omitted_parameters'],['parallel_tool_calls'])
                self.assertEqual(receipt['original_sha256'],hashlib.sha256(raw).hexdigest())
                self.assertTrue(original['parallel_tool_calls'])
            finally:relay.close()
        for value in [None,0,'true',{}]:
            with self.assertRaises(ValueError):course_request(json.dumps({'parallel_tool_calls':value}).encode())
