"""Actual prompt assembly/entrypoint regression tests, not model-quality claims."""
import contextlib
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from mads.agent import Agent, main
from mads.handoff import export_bundle, validate_bundle, render_bundle, main as handoff_main
from mads.prompting import CODING, RUNTIME, REVIEW_RICH, identities
from mads.store import Store, dump
from test_course_compat import Fixture
from scripted_provider import Scripted, call


TASK = 'Change the public behavior.\r\nPreserve café and "literal" bytes.\n'
CORRECTION = 'Later correction: retain the existing option.\r\nDo not discard it.\n'


def users(request):
    return [m['content'] for m in request['messages'] if m['role'] == 'user']


class PromptAssemblyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.repo = self.root/'repo'; self.repo.mkdir()
        self.run = self.root/'run'; self.store = Store(self.run/'journal')
        self.env = patch.dict(os.environ, {'CS2680_API_KEY':'offline-fixture-only',
            'PATH':os.environ.get('PATH', os.defpath)}, clear=True)
        self.env.start(); self.addCleanup(self.env.stop)
        self.addCleanup(signal.signal, signal.SIGTERM, signal.getsignal(signal.SIGTERM))

    def test_core_original_corrections_and_assignment_survive_restart(self):
        p = Scripted(self.store, [[call('context_restart', account='fallible account')],
                                  [call('finish', summary='known scope')]])
        assignment = 'CURRENT ASSIGNMENT\nCheck the existing software; full task governs.'
        a = Agent(self.repo, TASK, self.run, p, self.store,
                  user_messages=[CORRECTION], session_request=assignment, session_role='reviewer')
        self.assertEqual(a.run()['harness_outcome']['status'], 'completed')
        for request in p.requests:
            system = request['messages'][0]['content']
            self.assertIn(CODING, system)
            self.assertIn(RUNTIME, system)
            self.assertEqual(users(request)[:3], [TASK, CORRECTION, assignment])
        manifest = json.loads((self.run/'run-manifest.json').read_text())
        self.assertEqual(manifest['loaded_prompt_resources'], identities())
        for name in ('coding.md','runtime.md','review-rich.md'):
            relative = 'mads/prompts/'+name
            self.assertEqual(hashlib.sha256((self.run/'source'/relative).read_bytes()).hexdigest(),
                             manifest['source']['files'][relative])


    def test_default_fresh_review_is_rich_without_replacing_original_task(self):
        p = Scripted(self.store, [[call('finish', summary='Prior exact report\r\n')],
                                  [call('finish', summary='owner final')]],
                                  [[call('finish', summary='review result')]])
        a = Agent(self.repo, TASK, self.run, p, self.store, user_messages=[CORRECTION])
        self.assertEqual(a.run()['agent_outcome']['claim'], 'review result')
        request = p.child.requests[0]
        self.assertEqual(users(request)[:2], [TASK, CORRECTION])
        self.assertTrue(any(REVIEW_RICH in text and 'Prior exact report\r\n' in text
                            for text in users(request)))
        self.assertIn(CODING, request['messages'][0]['content'])

    def test_requirement_coverage_guidance_reaches_both_default_owners(self):
        # Inspect compiled requests, not only prompt files. This guards delivery,
        # not whether a live model follows the guidance or completes a task.
        p = Scripted(self.store, [[call('finish', summary='Fallible proposal')]],
                     [[call('context_restart', account='review observations')],
                      [call('finish', summary='Reviewed result')]])
        a = Agent(self.repo, TASK, self.run, p, self.store, user_messages=[CORRECTION])
        self.assertEqual(a.run()['agent_outcome']['claim'], 'Reviewed result')
        for request in p.requests + p.child.requests:
            system = request['messages'][0]['content']
            self.assertIn('Identify every distinct requirement', system)
            self.assertIn('requirement by requirement against the actual final implementation', system)
            self.assertIn('Do not call an unexamined requirement satisfied.', system)
            self.assertEqual(users(request)[:2], [TASK, CORRECTION])
        for request in p.child.requests:
            assignment = next(text for text in users(request) if REVIEW_RICH in text)
            self.assertIn('Independently identify every distinct requirement', assignment)
            self.assertIn('unchanged declarations and unmodified consumers outside the diff', assignment)
            self.assertIn('Repair actual omissions in this same reviewer session', assignment)
            self.assertIn('Fallible proposal', assignment)

    def test_real_cli_preserves_explicit_later_message(self):
        f = Fixture([[call('finish', summary='proposal')], [call('finish', summary='reviewed')]])
        correction = self.root/'correction.md'; correction.write_bytes(CORRECTION.encode())
        with patch('mads.agent.Provider', f.provider()), contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            code = main(['-p', TASK, str(self.repo), '--run-dir', str(self.root/'fresh-cli'),
                         '--user-message-file', str(correction)])
        self.assertEqual(code, 0)
        self.assertEqual(users(f.requests[0])[:2], [TASK, CORRECTION])
        self.assertIn(CODING, f.requests[0]['messages'][0]['content'])
        self.assertNotIn('Do NOT edit production source', dump(f.requests[0]))

    def test_course_runner_carries_custom_review_and_writable_resources(self):
        from mads.runner import run
        checkout = self.root/'checkout'; checkout.mkdir()
        source = Path(__file__).resolve().parents[1]
        from mads.runner import stage_source
        stage_source(source, checkout)
        tasks = checkout/'tasks.json'
        tasks.write_text(dump({'fixture':dict(instance_id='fixture',base_commit='abc',docker_image='fixture',
            problem_statement=json.dumps(TASK),requirements='requirements',interface='interface')}))
        review = self.root/'chosen-review.md'; review.write_bytes(b'Chosen review\r\nNo test gate.\n')
        env = {'MADSLOOP_REPO':str(checkout), 'TASKS_JSON':str(tasks),
               'MADS_REVIEW_PROMPT':str(review)}
        with patch.dict(os.environ, env), patch('mads.runner.subprocess.call', return_value=1) as launch, \
                patch('mads.runner.subprocess.run', return_value=subprocess.CompletedProcess([],0,stderr='')), \
                contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(run(0), 1)  # no mocked patch export, not a solver assertion failure
        args = launch.call_args.args[0]
        mount = next(x for x in args if x.endswith(':/agent:rw'))
        stage = Path(mount.removesuffix(':/agent:rw'))
        self.assertEqual((stage/'review-instructions.md').read_bytes(), review.read_bytes())
        self.assertIn('MADS_REVIEW_PROMPT=/agent/review-instructions.md', args)
        self.assertIn(TASK, (stage/'task.txt').read_bytes().decode())
        self.assertEqual((stage/'mads/prompts/coding.md').read_bytes(), CODING.encode())
        self.assertTrue((stage/'mads/inspection/inspect_run.py').is_file())
        self.assertEqual((stage/'docs/solve-review.md').read_bytes(), (source/'docs/solve-review.md').read_bytes())

    def make_bundle(self, messages, destination=None):
        task = self.root/'task.txt'; task.write_bytes(TASK.encode())
        account = self.root/'account.md'; account.write_text('fallible handoff account')
        prior = self.root/'prior'; store = Store(prior/'journal')
        record = store.add('observation', blob=store.blob('historical observation'))
        dest = destination or self.root/'bundle'
        result = export_bundle(prior, dest, task, account, [{'record_id':record}], user_messages=messages)
        return dest, result

    def test_versioned_handoff_carries_active_corrections_after_restart(self):
        bundle, manifest = self.make_bundle([CORRECTION])
        self.assertEqual(manifest['format'], 'mads-handoff-v2')
        self.assertEqual(validate_bundle(bundle), manifest)
        self.assertIn('Explicit later user messages', render_bundle(bundle))
        p = Scripted(self.store, [[call('context_restart', account='reassessed')],
                                  [call('finish', summary='continuation')]])
        a = Agent(self.repo, TASK, self.run, p, self.store, session_role='reviewer')
        a.seed_handoff(bundle)
        self.assertEqual(a.run()['agent_outcome']['claim'], 'continuation')
        for request in p.requests:
            self.assertEqual(users(request)[:2], [TASK, CORRECTION])
        self.assertEqual(a.task, TASK)

    def test_handoff_conflicting_user_messages_reject_before_seed_effects(self):
        bundle, _ = self.make_bundle([CORRECTION])
        a = Agent(self.repo, TASK, self.run, Scripted(self.store, []), self.store,
                  user_messages=['different correction'])
        with self.assertRaisesRegex(ValueError, 'Conflicting explicit user messages'):
            a.seed_handoff(bundle)
        self.assertEqual(self.store.events, [])
        self.assertEqual(a.context.working.read_bytes(), b'')
        self.assertFalse(a._handoff_seed_started)

    def test_handoff_cli_forwards_messages_instead_of_ignoring_option(self):
        task = self.root/'task.txt'; task.write_bytes(TASK.encode())
        account = self.root/'account.md'; account.write_bytes(b'')
        selections = self.root/'selections.json'; selections.write_text('[]')
        messages = self.root/'messages.json'; messages.write_text(dump([CORRECTION]))
        self.store.add('fixture')
        dest = self.root/'cli-bundle'
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(handoff_main(['export',str(self.run),str(dest),
                '--task-file',str(task),'--account-file',str(account),'--selections',str(selections),
                '--user-messages-file',str(messages)]), 0)
        manifest = validate_bundle(dest)
        self.assertEqual(json.loads((dest/manifest['user_messages']['file']).read_bytes()), [CORRECTION])

    def test_handoff_bad_message_type_is_rejected_without_export(self):
        with self.assertRaisesRegex(ValueError, 'list of strings'):
            self.make_bundle([{'not':'an explicit user message'}])
        self.assertFalse((self.root/'bundle').exists())

    def test_later_instruction_can_anchor_a_versioned_expectation_without_replacing_task(self):
        a = Agent(self.repo, TASK, self.run, Scripted(self.store, []), self.store, session_role='reviewer')
        original = dict(id='public', requirement='Change the public behavior.', entrypoint='program',
                        scenario='ordinary call', expected='old hypothesis', command='true')
        a.tools.verification(action='plan', **original)
        a.add_user_message(CORRECTION, source='explicit-fixture-user')
        revised = dict(original, requirement='retain the existing option.', expected='revised behavior')
        result = a.tools.verification(action='revise', reason='The later user instruction governs.', **revised)
        current = result['event']['current']; previous = result['event']['previous']
        self.assertEqual(current['requirement_source']['kind'], 'later_user_message')
        self.assertEqual(current['requirement_source']['index'], 0)
        self.assertEqual(current['requirement_source']['sha256'], hashlib.sha256(CORRECTION.encode()).hexdigest())
        self.assertEqual(previous['expected'], 'old hypothesis')
        self.assertEqual(previous['requirement_source']['kind'], 'original_task')
        self.assertEqual(a.task, TASK)
        self.assertEqual(a.tools.task_state.text, TASK)
        self.assertEqual(users(a.compile())[:2], [TASK, CORRECTION])

    def test_handoff_correction_is_immediately_available_to_verification_not_agent_notes(self):
        bundle, _ = self.make_bundle([CORRECTION])
        a = Agent(self.repo, TASK, self.run, Scripted(self.store, []), self.store, session_role='reviewer')
        a.seed_handoff(bundle)
        result = a.tools.verification(action='plan', id='later', requirement='retain the existing option.',
            entrypoint='public CLI', scenario='flag present', expected='accepted', command='true')
        self.assertEqual(result['event']['current']['requirement_source']['kind'], 'later_user_message')
        a.context.working.write_text('NOT_A_USER_REQUIREMENT')
        with self.assertRaisesRegex(ValueError, 'exact quote'):
            a.tools.verification(action='plan', id='invented', requirement='NOT_A_USER_REQUIREMENT',
                entrypoint='public CLI', scenario='flag present', expected='accepted', command='true')
