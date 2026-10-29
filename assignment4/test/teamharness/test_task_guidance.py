import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from src.teamharness.agents import AgentManager
from src.teamharness.prompts import role_names
from src.teamharness.task_guidance import problem_digest, select_guidance


class TaskGuidance(unittest.TestCase):
    def test_exact_selection_and_tamper_rejection(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'guidance.json'
            self.assertIsNone(select_guidance(path, {}))
            entries = []
            for problem, text in [('first', 'First instructions'), ('second', 'Second instructions')]:
                entries.append(dict(problem_sha256=problem_digest({'problem_statement': problem}),
                                    text=text, text_sha256=hashlib.sha256(text.encode()).hexdigest()))
            path.write_text(json.dumps({'version': 1, 'entries': entries}))
            self.assertEqual(select_guidance(path, {'problem_statement': 'second'}), 'Second instructions')
            with self.assertRaises(ValueError):
                select_guidance(path, {'problem_statement': 'first '})
            entries[0]['text'] = 'altered'
            path.write_text(json.dumps({'version': 1, 'entries': entries}))
            with self.assertRaises(ValueError):
                select_guidance(path, {'problem_statement': 'second'})

    def test_all_roles_nested_and_resume_preserve_supplement(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / 'repo'
            repo.mkdir()
            requests = []
            class Provider:
                model = 'fixture'
                def complete(self, request):
                    requests.append(json.loads(json.dumps(request)))
                    return {'choices': [{'finish_reason': 'tool_calls', 'message': {'role': 'assistant', 'content': None,
                        'tool_calls': [{'id': 'finish', 'type': 'function', 'function': {'name': 'finish_role',
                            'arguments': json.dumps({'outcome': 'complete', 'report': 'Observed guidance'})}}]}}]}
            manager = AgentManager(root / 'team', lambda _: Provider(), prompt_supplement='Distinct behavioral experiment.')
            for role in role_names():
                parent = manager.create(role, 'Public task', repo)
                child = manager.create('investigator', 'Investigate the boundary', repo, parent=parent)
                for ident in (parent, child):
                    self.assertIn('Distinct behavioral experiment.', manager.descriptor(ident)['system'])
                    manager.start(ident)
                    self.assertEqual(manager.wait(ident, 5)['status'], 'completed')
            self.assertEqual(len(requests), 2 * len(role_names()))
            for request in requests:
                self.assertIn('Distinct behavioral experiment.', request['messages'][0]['content'])
                self.assertTrue(request['tools'])
            manager.shutdown()
            resumed = AgentManager(root / 'team', lambda _: None)
            self.assertEqual(manager.role_prompts, resumed.role_prompts)
            with self.assertRaises(ValueError):
                AgentManager(root / 'team', lambda _: None, prompt_supplement='Different experiment')

    def test_existing_team_cannot_gain_supplement(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            AgentManager(root, lambda _: None)
            with self.assertRaises(ValueError):
                AgentManager(root, lambda _: None, prompt_supplement='new')
