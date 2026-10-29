"""Recovery evidence and interrupted-owner identity, independent of a repair script."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch
from types import SimpleNamespace

from hypothesis import given, settings, strategies as st, example

from src.store import Store
from src.teamharness import repair, patching
from src.teamharness.agents import AgentManager
from src.teamharness.candidates import capture, snapshot, Grader
from src.teamharness.decision_context import source_context
from src.teamharness.main import Investigation, takeover_brief
from src.teamharness.presentation import runtime_observation
from src.teamharness.tools import ToolRuntime, TOOLS, validate, validation_rejection
from test_main_flow import repository


WORD = st.text(alphabet='abcXYZ012 αβ', min_size=1, max_size=18)


class RecoveryProperties(unittest.TestCase):
    @settings(max_examples=10, deadline=None)
    @given(label=WORD, variant=st.sampled_from(['empty', 'add', 'rename', 'delete', 'binary']), failed=st.integers(1, 100))
    @example(label='α β', variant='rename', failed=1)
    def test_brief_describes_exact_inherited_files_without_staging_or_changing_them(self, label, variant, failed):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); repo = repository(root)
            # Dirty initial work is part of starting source, not attributed to
            # the solver's repair. New untracked paths must appear in the brief.
            (repo / 'initial.txt').write_text(label)
            old_path = 'old ' + label + '.txt'
            if variant == 'rename': (repo / old_path).write_text('renamed source\n')
            frozen = snapshot(repo, root / 'baseline', root / 'freeze')
            work = Path(frozen['workspace'])
            names = []
            if variant == 'add':
                names = ['new ' + label + '.txt']
                (work / names[0]).write_text('new repair')
            elif variant == 'rename':
                names = sorted([old_path, 'renamed ' + label + '.txt'])
                (work / old_path).rename(work / ('renamed ' + label + '.txt'))
            elif variant == 'delete':
                names = ['answer.py']; (work / 'answer.py').unlink()
            elif variant == 'binary':
                names = ['new.bin']; (work / 'new.bin').write_bytes(b'\0\xff' + label.encode())
            candidate = capture(work, root / 'candidate.patch', frozen['revision'])
            before = subprocess.check_output(['git', '-C', str(work), 'status', '--porcelain', '-z'])
            original_patch = Path(candidate['patch_path']).read_bytes()
            completion = {'checks': {'configured_grader': {'passed': False, 'result': {'grade': {
                'receipt': {'tests_failed': failed, 'tests_total': failed + 1}}}},
                'interrupted': {'passed': False, 'state': 'uncertain'}}}
            deadline = time.time() + 45
            brief = takeover_brief(work, candidate, completion, deadline, root / 'handoff.json')
            self.assertEqual(brief['changed_files'], names)
            self.assertNotIn('initial.txt', brief['changed_files'])
            self.assertEqual(brief['head'], frozen['revision'])
            self.assertEqual(brief['candidate_sha256'], candidate['sha256'])
            self.assertEqual(brief['grade']['tests_failed'], failed)
            self.assertIn('interrupted', brief['incomplete_completion_checks'])
            self.assertLessEqual(brief['remaining_seconds_at_observation'], 45)
            self.assertEqual(subprocess.check_output(['git', '-C', str(work), 'status', '--porcelain', '-z']), before)
            self.assertEqual((work / 'initial.txt').read_text(), label)
            self.assertEqual(Path(candidate['patch_path']).read_bytes(), original_patch)

    @settings(max_examples=20, deadline=None)
    @given(enabled=st.sets(st.sampled_from(['apply_patch', 'exec_command', 'read_file'])), suffix=WORD)
    def test_unknown_tool_corrections_only_name_available_calls(self, enabled, suffix):
        schemas = {t['function']['name']: t['function']['parameters'] for t in TOOLS if t['function']['name'] in enabled}
        value = validation_rejection('unavailable_' + suffix, {}, ValueError('Unadvertised tool'), schemas)
        self.assertFalse(value['executed'])
        self.assertEqual(value['state'], 'not_executed')
        self.assertEqual(set(value['recovery']['available_tools']), enabled)
        for example in value['recovery']['available_examples']:
            self.assertIn(example['name'], enabled)
            validate(example['arguments'], schemas[example['name']])
        if 'apply_patch' in enabled:
            with tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp); work = root / 'work'; work.mkdir(); agent = root / 'agent'; agent.mkdir()
                (work / 'example.txt').write_text('old\n')
                runtime = ToolRuntime(None, 'a' * 32, {'root': str(agent), 'workspace': str(work),
                    'tools': [t for t in TOOLS if t['function']['name'] in enabled]})
                result = runtime('apply_patch', {'patch': '*** Begin Patch\n*** Update File: example.txt\n@@\n-WRONG\n+new\n*** End Patch\n'}, 'bad-context')
                example = result['recovery'].get('example_call')
                if example:
                    self.assertIn(example['name'], enabled)
                    validate(example['arguments'], schemas[example['name']])
                self.assertEqual((work / 'example.txt').read_text(), 'old\n')

    @settings(max_examples=12, deadline=None)
    @given(title=WORD.map(str.strip).filter(bool), prose=WORD.map(str.strip).filter(bool),
           invalid=st.sampled_from(['heading', 'path', 'check']))
    def test_failed_plan_selection_preserves_previous_plan_and_exposes_real_choices(self, title, prose, invalid):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); guides = root / 'guides'; guides.mkdir()
            (guides / 'chapter.md').write_text('# Chapter\n## ' + title + '\nUseful advice.\n')
            first = repair.register(root, guides, 'first', prose, [], [])
            self.assertEqual(first['registered_check_count'], 0)
            self.assertEqual(first['verification_readiness'], 'no_executable_checks')
            saved = (root / 'repair-plans/current.json').read_bytes()
            selected = [{'path': 'chapter.md', 'heading': 'absent-heading'}]
            checks = []
            if invalid == 'path': selected = [{'path': '../outside.md'}]
            if invalid == 'check': checks = [{'name': 'bad', 'argv': []}]
            with self.assertRaises(repair.PlanValidationError) as failure:
                repair.register(root, guides, 'rejected', 'new interpretation', checks, selected)
            self.assertEqual((root / 'repair-plans/current.json').read_bytes(), saved)
            self.assertFalse((root / 'repair-plans/rejected.json').exists())
            if invalid == 'heading':
                self.assertIn(title, failure.exception.recovery['available_headings'])
            accepted = repair.register(root, guides, 'corrected', prose,
                [{'name': 'consumer', 'argv': [sys.executable, '-c', 'raise SystemExit(0)']}],
                [{'path': 'chapter.md', 'heading': title}])
            self.assertEqual(accepted['registered_check_count'], 1)
            self.assertIn('Useful advice.', accepted['authored_guidance'][0]['text'])

    @settings(max_examples=6, deadline=None)
    @given(after_current=st.booleans(), prose=WORD.map(str.strip).filter(bool))
    def test_lost_plan_publication_is_uncertain_and_not_replayed(self, after_current, prose):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); work = root / 'work'; work.mkdir(); agent = root / 'agent'; agent.mkdir()
            guides = root / 'guides'; guides.mkdir()
            repair.register(agent, guides, 'old', 'Original plan', [], [])
            original_current = (agent / 'repair-plans/current.json').read_bytes()
            manager = SimpleNamespace(guide_root=guides, workflow=SimpleNamespace(
                report={'agents': {'main': 'a' * 32}, 'completion_gate': {'enabled': True}}))
            runtime = ToolRuntime(manager, 'a' * 32, {'root': str(agent), 'workspace': str(work), 'tools': TOOLS})
            original_save = repair.save; writes = []
            def lost(path, value):
                original_save(path, value); writes.append(str(path))
                if (Path(path).name == 'current.json') == after_current:
                    raise OSError('Publication result lost after write')
            args = {'action': 'set', 'plan': prose}
            with patch.object(repair, 'save', lost):
                result = runtime('repair_plan', args, 'publish')
            self.assertEqual(result['execution_state'], 'unknown')
            self.assertFalse(result['replay_safe'])
            self.assertIsNot(result.get('executed'), False)
            count = len(writes)
            self.assertEqual(runtime('repair_plan', args, 'publish'), result)
            self.assertEqual(len(writes), count)
            current = (agent / 'repair-plans/current.json').read_bytes()
            if after_current:
                self.assertEqual(json.loads(current)['plan'], prose)
            else:
                self.assertEqual(current, original_current)

    @settings(max_examples=8, deadline=None)
    @given(label=WORD, fault=st.sampled_from(['before_write', 'after_write', 'uncertain']))
    def test_partial_or_uncertain_edits_are_not_presented_as_rejected_or_replayed(self, label, fault):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); work = root / 'work'; work.mkdir(); agent = root / 'agent'; agent.mkdir()
            for name in ('one.txt', 'two.txt'): (work / name).write_text('old\n')
            runtime = ToolRuntime(None, 'a' * 32, {'root': str(agent), 'workspace': str(work), 'base': None, 'tools': TOOLS})
            args = {'patch': '*** Begin Patch\n*** Update File: one.txt\n@@\n-old\n+first\n*** Update File: two.txt\n@@\n-old\n+' + label + '\n*** End Patch\n'}
            original = patching._commit_file
            writes = []
            def injected(path, *a, **kw):
                writes.append(path.name)
                if path.name == 'two.txt' and fault == 'before_write': raise OSError('injected before second write')
                result = original(path, *a, **kw)
                if path.name == 'two.txt' and fault == 'after_write': raise OSError('injected after second write')
                return result
            if fault == 'uncertain':
                execute = runtime.execute
                def lost(*a):
                    execute(*a)
                    raise RuntimeError('lost publication after actual writes')
                with patch.object(runtime, 'execute', lost): receipt = runtime('apply_patch', args, 'edit')
            else:
                with patch.object(patching, '_commit_file', injected): receipt = runtime('apply_patch', args, 'edit')
            self.assertEqual((work / 'one.txt').read_text(), 'first\n')
            self.assertEqual((work / 'two.txt').read_text(), 'old\n' if fault == 'before_write' else label + '\n')
            before = {p.name: p.read_bytes() for p in work.iterdir()}
            self.assertEqual(runtime('apply_patch', args, 'edit'), receipt)
            self.assertEqual({p.name: p.read_bytes() for p in work.iterdir()}, before)
            with Store(agent / 'session.sqlite3') as store:
                store.put('tool_result', 'session', {'name': 'apply_patch', 'receipt': receipt, 'effect_id': 'edit'})
            source = source_context(agent)
            self.assertEqual(source['last_source_action']['outcome'], 'unknown' if fault == 'uncertain' else 'partial')
            rendered = runtime_observation({'source_context': source})
            self.assertNotIn('this call changed no files', rendered)
            self.assertIn(source['last_source_action']['outcome'], rendered)


class FreshInvestigatorRecovery(unittest.TestCase):
    def test_partial_takeover_reuses_its_dated_assignment_on_resume(self):
        from test_completion_gate import RepairModel, GRADE
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); repo = repository(root); model = RepairModel(self)
            manager = AgentManager(root / 'run/agents', lambda _: model, deadline=time.time() + 60)
            workflow = Investigation(manager, root / 'run', completion_gate=True)
            create = manager.create; created = []
            def lost(*args, **kwargs):
                ident = create(*args, **kwargs); created.append(ident)
                if len(created) == 2: raise RuntimeError('Lost owner-creation result')
                return ident
            try:
                with patch.object(manager, 'create', lost), self.assertRaisesRegex(RuntimeError, 'Lost owner-creation'):
                    workflow.run('Repair the public answer.', repo, Grader(GRADE))
                saved = next((root / 'run/takeover').glob('*/handoff.json'))
                original = saved.read_bytes()
                assignment = manager.descriptor(created[1])['task']
                resumed = Investigation(manager, root / 'run')
                result = resumed.resume()
                self.assertEqual(result['state'], 'completed')
                self.assertEqual(saved.read_bytes(), original)
                self.assertEqual(manager.descriptor(created[1])['task'], assignment)
                self.assertEqual(result['agents']['main'], created[1])
            finally:
                manager.shutdown()
