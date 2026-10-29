"""Contract checks against a temporary copy of the real frozen course reroute.

Set TEAMHARNESS_COURSE_SOURCE to a dev/ directory containing course_origin.py,
openrouter_origin.py and course-routes/openrouter-fleet.json when running outside
this research checkout. No source fixture, remote request or live route is edited.
"""
from __future__ import annotations

import copy
import importlib.util
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import types
import unittest
import uuid

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from lab.adapters.teamharness.route import patch_origin


def load_origin(directory: Path):
    package_name = '_team_route_' + uuid.uuid4().hex
    package = types.ModuleType(package_name)
    package.__path__ = [str(directory)]
    sys.modules[package_name] = package
    for name in ('openrouter_origin', 'course_origin'):
        spec = importlib.util.spec_from_file_location(package_name + '.' + name, directory / (name + '.py'))
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
    return module, package_name


class CourseRouteContract(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        default = ROOT / '.local/evaluations/ahe-qwen-revised-hard51/fleet-source/dev'
        cls.source = Path(os.environ.get('TEAMHARNESS_COURSE_SOURCE', str(default)))
        required = ('course_origin.py', 'openrouter_origin.py', 'course-routes/openrouter-fleet.json')
        if not all((cls.source / name).is_file() for name in required):
            raise unittest.SkipTest('Set TEAMHARNESS_COURSE_SOURCE to the existing frozen course dev/ source')
        cls.original = {name: (cls.source / name).read_bytes() for name in required}
        cls.temp = tempfile.TemporaryDirectory()
        root = Path(cls.temp.name)
        cls.base_dir = root / 'original'
        cls.new_dir = root / 'extended'
        for directory in (cls.base_dir, cls.new_dir):
            directory.mkdir()
            for name in required:
                target = directory / name
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(cls.source / name, target)
        cls.baseline, cls.base_package = load_origin(cls.base_dir)
        cls.receipt = patch_origin(cls.new_dir / 'course_origin.py')
        cls.extended, cls.new_package = load_origin(cls.new_dir)
        cls.config = json.loads(cls.original['course-routes/openrouter-fleet.json'])

    @classmethod
    def tearDownClass(cls):
        for name, original in cls.original.items():
            if (cls.source / name).read_bytes() != original:
                raise AssertionError('A source fixture changed while testing')
        for name in tuple(sys.modules):
            if name.startswith((cls.base_package, cls.new_package)):
                sys.modules.pop(name, None)
        cls.temp.cleanup()

    def request(self):
        return {'model': 'standard', 'messages': [{'role': 'system', 'content': 'Fix the task.'},
                {'role': 'user', 'content': 'Investigate and edit.'}], 'stream': False}

    def test_native_reasoning_tool_and_refusal_continuation_preserved(self):
        request = self.request()
        request['messages'].extend([
            {'role': 'assistant', 'content': None, 'refusal': None,
             'reasoning': 'Inspect the file first.', 'reasoning_content': 'Retained provider field.',
             'reasoning_details': [{'type': 'reasoning.text', 'text': 'Prior reasoning', 'index': 0}],
             'tool_calls': [{'id': 'call_one', 'type': 'function',
                             'function': {'name': 'execute', 'arguments': '{"command":"pwd"}'}}]},
            {'role': 'tool', 'tool_call_id': 'call_one', 'content': '{"status":0,"stdout":"/work"}'}])
        original = copy.deepcopy(request)
        with self.assertRaises(ValueError):
            self.baseline.translate(request, self.config)
        translated = self.extended.translate(request, self.config)
        self.assertEqual(translated['messages'], original['messages'])
        self.assertEqual(request, original)
        self.assertEqual(translated['model'], self.config['routes']['standard']['model'])
        self.assertEqual(translated['provider'], self.config['routes']['standard']['provider'])

    def test_compaction_schema_is_forwarded_without_tools_or_content_changes(self):
        request = self.request()
        request['messages'].append({'role': 'user', 'content': 'Write a model-authored continuation.'})
        request['response_format'] = {'type': 'json_schema', 'json_schema': {
            'name': 'continuation', 'strict': True, 'schema': {'type': 'object',
            'properties': {'continuation': {'type': 'string'}}, 'required': ['continuation'],
            'additionalProperties': False}}}
        translated = self.extended.translate(request, self.config)
        self.assertEqual(translated['response_format'], request['response_format'])
        self.assertEqual(translated['messages'], request['messages'])
        self.assertNotIn('tools', translated)
        self.assertNotIn('tool_choice', translated)
        self.assertEqual(translated['provider'], self.config['routes']['standard']['provider'])

    def test_json_object_forwarded(self):
        request = {**self.request(), 'response_format': {'type': 'json_object'}}
        self.assertEqual(self.extended.translate(request, self.config)['response_format'], request['response_format'])

    def test_bad_formats_rejected(self):
        invalid = [None, [], 'json', {'type': 'text'}, {'type': 'json_object', 'other': True},
                   {'type': 'json_schema'}, {'type': 'json_schema', 'json_schema': {}},
                   {'type': 'json_schema', 'json_schema': {'name': '', 'schema': {}}},
                   {'type': 'json_schema', 'json_schema': {'name': 'x', 'schema': []}},
                   {'type': 'json_schema', 'json_schema': {'name': 'x', 'schema': {}, 'strict': 'true'}},
                   {'type': 'json_schema', 'json_schema': {'name': 'x', 'schema': {}, 'extra': 1}}]
        for value in invalid:
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.extended.translate({**self.request(), 'response_format': value}, self.config)

    def test_refusal_requires_assistant_and_null_or_string(self):
        for role, refusal in [('user', None), ('tool', None), ('system', 'no'), ('assistant', {}), ('assistant', 2)]:
            request = self.request()
            request['messages'].append({'role': role, 'content': '', 'refusal': refusal})
            with self.subTest(role=role, refusal=refusal), self.assertRaises(ValueError):
                self.extended.translate(request, self.config)
        request = self.request()
        request['messages'].append({'role': 'assistant', 'content': None, 'refusal': 'Declined action.'})
        self.assertEqual(self.extended.translate(request, self.config)['messages'], request['messages'])

    def test_additions_rejected_on_other_upstreams(self):
        for upstream in ('course', 'openai'):
            config = copy.deepcopy(self.config)
            config['provider'] = upstream
            config['routes']['standard'] = {'upstream': upstream, 'model': 'standard'}
            for addition in ('refusal', 'response_format'):
                request = self.request()
                if addition == 'refusal':
                    request['messages'].append({'role': 'assistant', 'content': None, 'refusal': None})
                else:
                    request['response_format'] = {'type': 'json_object'}
                with self.subTest(upstream=upstream, addition=addition), self.assertRaises(ValueError):
                    self.extended.translate(request, config)

    def test_all_existing_routes_and_sampling_are_unchanged(self):
        self.extended.validate_routes(self.config)
        for alias in self.config['routes']:
            request = {**self.request(), 'model': alias, 'max_tokens': 50, 'temperature': 0.1,
                       'tools': [{'type': 'function', 'function': {'name': 'execute', 'description': 'Execute shell',
                                  'parameters': {'type': 'object', 'properties': {'command': {'type': 'string'}}}}}],
                       'tool_choice': 'auto'}
            with self.subTest(alias=alias):
                self.assertEqual(self.extended.translate(request, self.config), self.baseline.translate(request, self.config))
        for effort in ('none', 'medium'):
            request = {**self.request(), 'reasoning_effort': effort}
            self.assertEqual(self.extended.translate(request, self.config), self.baseline.translate(request, self.config))
        for upstream in ('course', 'openai'):
            config = copy.deepcopy(self.config)
            config['provider'] = upstream
            config['routes']['standard'] = {'upstream': upstream, 'model': 'standard'}
            self.assertEqual(self.extended.translate(self.request(), config), self.baseline.translate(self.request(), config))

    def test_extension_refuses_second_application_without_changing_bytes(self):
        path = self.new_dir / 'course_origin.py'
        before = path.read_bytes()
        with self.assertRaises(ValueError):
            patch_origin(path)
        self.assertEqual(path.read_bytes(), before)
        self.assertNotEqual(self.receipt['before_sha256'], self.receipt['after_sha256'])


if __name__ == '__main__':
    unittest.main()
