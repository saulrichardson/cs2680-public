"""Execution-route composition exercised with real Bash and local argv wrappers.

No Docker daemon, SSH server, network, evaluator material or model calls needed.
The mock implements argv forwarding or SSH-style remote shell joining; it does
not claim to test transport availability, target cleanup, or Jobs integration.
"""
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile
import unittest

from mads.execution import ExecutionTargets


_MOCK_TRANSPORT = '''\
import json, os, sys
mode, record, initial_cwd, literal = sys.argv[1:5]
payload = sys.argv[5:]
with open(record, 'w', encoding='utf-8') as out:
    json.dump(dict(host_cwd=os.getcwd(), literal=literal, payload=payload), out)
if initial_cwd:
    os.chdir(initial_cwd)
if mode == 'argv':
    os.execvp(payload[0], payload)
elif mode == 'shell':
    # Like SSH, join command arguments before the destination shell reparses.
    os.execv('/bin/sh', ['sh', '-c', ' '.join(payload)])
else:
    raise ValueError(mode)
'''


class ExecutionFixtures(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        # macOS /var aliases /private/var; assertions concern actual launch cwd.
        self.root = Path(temporary.name).resolve()
        self.host = self.root/'host repo'
        self.remote = self.root/'target initial cwd'
        self.host.mkdir()
        self.remote.mkdir()
        self.mock = self.root/'mock transport.py'
        self.mock.write_text(_MOCK_TRANSPORT)
        self.record = self.root/'transport record.json'
        self.literal = "literal ' $HOME ; $(touch PREFIX_INJECTED)\nnot code"
        self.routes = ExecutionTargets(self.host)

    def prefix(self, mode='argv'):
        return [sys.executable, str(self.mock), mode, str(self.record),
                str(self.remote), self.literal]

    def execute(self, command, target=None, *, input='', env=None):
        plan = self.routes.plan(command, target)
        return subprocess.run(plan.argv, cwd=plan.cwd, input=input, text=True,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env)


class ActualExecutionTests(ExecutionFixtures):
    def test_unconfigured_host_has_exact_historical_argv_and_behavior(self):
        commands = [
            '',
            'pwd -P',
            'printf "<%s>\\n" "$0" "$#"; printf err >&2; false | true',
            "printf '%s\\n' 'ordinary Bash command'; printf content > created; cat created",
        ]
        for command in commands:
            with self.subTest(command=command):
                plan = self.routes.plan(command)
                original = ['bash', '-o', 'pipefail', '-c', command]
                self.assertEqual(list(plan.argv), original)
                self.assertEqual(plan.cwd, str(self.host))
                expected = subprocess.run(original, cwd=self.host, input='', text=True,
                                          stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                actual = self.execute(command)
                self.assertEqual((actual.returncode, actual.stdout, actual.stderr),
                                 (expected.returncode, expected.stdout, expected.stderr))
        self.assertEqual(self.execute('false | true').returncode, 1)

    def test_argv_prefix_launches_from_host_but_cd_occurs_inside_target(self):
        directory = self.remote/'repo'
        directory.mkdir()
        self.routes.set('linux', argv_prefix=self.prefix(), cwd='repo')
        command = ('printf "DIR=<%s>\\n" "$PWD"; '
                   'printf "%s|%s\\n" "$0" "$#"; printf err >&2; cat; exit 7')
        result = self.execute(command, 'linux', input='input $HOME\nsecond line\n')
        self.assertEqual(result.returncode, 7)
        self.assertEqual(result.stdout, f'DIR=<{directory}>\nbash|0\ninput $HOME\nsecond line\n')
        self.assertEqual(result.stderr, 'err')
        observed = json.loads(self.record.read_text())
        self.assertEqual(observed['host_cwd'], str(self.host))
        self.assertEqual(observed['literal'], self.literal)
        self.assertEqual(observed['payload'][-1], command)
        self.assertFalse((self.host/'repo').exists())
        self.assertFalse((self.host/'PREFIX_INJECTED').exists())
        self.assertFalse((self.remote/'PREFIX_INJECTED').exists())

    def test_ssh_style_shell_transport_preserves_quoted_command_and_stdin(self):
        value = "single ' quote; double \" quote; $HOME; $(touch COMMAND_INJECTED)\nnext π"
        command = 'printf "%s\\n" '+shlex.quote(value)+'; cat; false | true'
        self.routes.set("SSH ' π", argv_prefix=self.prefix('shell'), transport='shell')
        result = self.execute(command, "SSH ' π", input='stdin, not a script\n')
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout, value+'\nstdin, not a script\n')
        self.assertEqual(result.stderr, '')
        observed = json.loads(self.record.read_text())
        self.assertEqual(len(observed['payload']), 1)
        self.assertEqual(observed['host_cwd'], str(self.host))
        self.assertFalse((self.host/'COMMAND_INJECTED').exists())
        self.assertFalse((self.remote/'COMMAND_INJECTED').exists())
        self.assertFalse((self.host/'PREFIX_INJECTED').exists())

    def test_cwd_is_literal_data_with_quotes_shell_syntax_newlines_and_leading_dash(self):
        name = "-directory ' ; $(touch CWD_INJECTED) ;\nπ"
        directory = self.remote/name
        directory.mkdir()
        for transport in ('argv', 'shell'):
            with self.subTest(transport=transport):
                self.routes.set('odd directory', argv_prefix=self.prefix(transport),
                                cwd=name, transport=transport)
                result = self.execute('pwd -P; printf "%s|%s\\n" "$0" "$#"', 'odd directory')
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout, str(directory)+'\nbash|0\n')
                self.assertFalse((self.host/'CWD_INJECTED').exists())
                self.assertFalse((self.remote/'CWD_INJECTED').exists())

    def test_lone_dash_cwd_names_a_directory_not_oldpwd(self):
        directory = self.remote/'-'
        directory.mkdir()
        for transport in ('argv', 'shell'):
            with self.subTest(transport=transport):
                self.routes.set('dash', argv_prefix=self.prefix(transport), cwd='-', transport=transport)
                result = self.execute('pwd -P', 'dash', env={**os.environ, 'OLDPWD': str(self.host)})
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout, str(directory)+'\n')

    def test_cd_failure_does_not_execute_user_command(self):
        for transport in ('argv', 'shell'):
            with self.subTest(transport=transport):
                self.routes.set('missing cwd', argv_prefix=self.prefix(transport),
                                cwd='not present', transport=transport)
                result = self.execute('printf unexpected > COMMAND_RAN', 'missing cwd')
                self.assertNotEqual(result.returncode, 0)
                self.assertTrue(result.stderr)
                self.assertEqual(result.stdout, '')
                self.assertFalse((self.host/'COMMAND_RAN').exists())
                self.assertFalse((self.remote/'COMMAND_RAN').exists())

    def test_cdpath_cannot_redirect_target_relative_directory_or_add_output(self):
        actual = self.remote/'repo'
        wrong_parent = self.root/'cdpath'
        wrong = wrong_parent/'repo'
        actual.mkdir()
        wrong.mkdir(parents=True)
        (actual/'marker').write_text('target directory\n')
        (wrong/'marker').write_text('wrong CDPATH directory\n')
        self.routes.set('target', argv_prefix=self.prefix(), cwd='repo')
        result = self.execute('cat marker', 'target', env={**os.environ, 'CDPATH': str(wrong_parent)})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, 'target directory\n')

    def test_default_can_be_selected_and_host_explicitly_overrides_it(self):
        self.routes.set('other local cwd', cwd=str(self.remote))
        self.routes.select('other local cwd')
        default = self.execute('pwd -P')
        explicit = self.execute('pwd -P', 'host')
        self.assertEqual(default.returncode, 0, default.stderr)
        self.assertEqual(default.stdout, str(self.remote)+'\n')
        self.assertEqual(explicit.returncode, 0, explicit.stderr)
        self.assertEqual(explicit.stdout, str(self.host)+'\n')

    def test_new_bash_still_does_not_retain_variables_or_cd(self):
        self.routes.set('local target', cwd=str(self.remote))
        first = self.execute('MADS_EXECUTION_TEST_ONLY=value; mkdir moved; cd moved', 'local target')
        self.assertEqual(first.returncode, 0, first.stderr)
        second = self.execute('printf "%s\\n" "${MADS_EXECUTION_TEST_ONLY-unset}"; pwd -P', 'local target')
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertEqual(second.stdout, 'unset\n'+str(self.remote)+'\n')

    def test_preview_argv_can_be_executed_without_rebuilding_it_for_either_transport(self):
        directory = self.remote/"repo ' ; $HOME\nπ"
        directory.mkdir()
        command = 'pwd -P; printf "%s|%s\\n" "$0" "$#"; cat; false | true'
        for transport in ('argv', 'shell'):
            with self.subTest(transport=transport):
                self.record.unlink(missing_ok=True)
                self.routes.set('previewed', argv_prefix=self.prefix(transport),
                                cwd=directory.name, transport=transport)
                preview = self.routes.preview(command, 'previewed')
                self.assertFalse(self.record.exists(), 'preview must not run the wrapper')
                self.assertFalse(preview['executed'])
                self.assertFalse(preview['sample_command'])
                self.assertEqual(preview['host_cwd'], str(self.host))
                self.assertEqual(preview['target_cwd'], directory.name)
                self.assertEqual(preview['command'], command)
                # Run exactly the returned argv, not a retyped launch stand-in.
                result = subprocess.run(preview['argv'], cwd=preview['host_cwd'],
                                        input='stdin preserved\n', text=True,
                                        stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                self.assertEqual(result.returncode, 1, result.stderr)
                self.assertEqual(result.stdout, str(directory)+'\nbash|0\nstdin preserved\n')
                self.assertEqual(result.stderr, '')
                observed = json.loads(self.record.read_text())
                self.assertEqual(observed['host_cwd'], str(self.host))
                self.assertEqual(observed['literal'], self.literal)
                self.assertEqual(len(observed['payload']), 1 if transport == 'shell' else 8)

    def test_missing_executable_is_an_execution_error_not_a_configuration_gate(self):
        absent = str(self.root/'not installed')
        self.routes.set('unavailable', argv_prefix=[absent])
        self.assertIn('unavailable', self.routes.describe()['targets'])
        with self.assertRaises(FileNotFoundError):
            self.execute('printf not reached', 'unavailable')


class RegistryTests(ExecutionFixtures):
    def test_host_preview_preserves_exact_commands_and_labels_only_omitted_samples(self):
        sample = self.routes.preview()
        self.assertTrue(sample['sample_command'])
        self.assertFalse(sample['executed'])
        self.assertEqual(sample['command'], 'pwd')
        self.assertEqual(sample['target'], 'host')
        self.assertEqual(sample['argv'], ['bash', '-o', 'pipefail', '-c', 'pwd'])
        self.assertEqual(sample['host_cwd'], str(self.host))
        self.assertIsNone(sample['target_cwd'])
        for command in ('', 'pwd', "printf 'quotes; $HOME\\nπ'"):
            with self.subTest(command=command):
                preview = self.routes.preview(command)
                self.assertFalse(preview['sample_command'])
                self.assertFalse(preview['executed'])
                self.assertEqual(preview['command'], command)
                self.assertEqual(preview['argv'], ['bash', '-o', 'pipefail', '-c', command])
        self.assertEqual(self.routes.snapshot(), {'targets': {}, 'default': 'host'})

    def test_preview_is_detached_and_does_not_select_the_explicit_target(self):
        self.routes.set('target', cwd='relative directory')
        self.routes.select('target')
        before = self.routes.snapshot()
        selected = self.routes.preview()
        explicit = self.routes.preview('true', 'host')
        self.assertEqual(selected['target'], 'target')
        self.assertEqual(selected['target_cwd'], 'relative directory')
        self.assertEqual(explicit['target'], 'host')
        self.assertEqual(self.routes.snapshot(), before)
        selected['definition']['argv_prefix'].append('changed returned value')
        selected['argv'].clear()
        self.assertEqual(self.routes.preview()['definition']['argv_prefix'], [])
        self.assertTrue(self.routes.preview()['argv'])
        self.assertEqual(self.routes.snapshot(), before)
        with self.assertRaises(ValueError):
            self.routes.preview('true', 'unconfigured')
        with self.assertRaises(ValueError):
            self.routes.preview('contains\0NUL')
        self.assertEqual(self.routes.snapshot(), before)

    def test_preview_does_not_rewrite_prefixes_or_gate_unavailable_definitions(self):
        for prefix in (['/bin/bash'], [str(self.root/'absent executable')],
                       ['docker', 'exec', '-i', 'not provisioned']):
            with self.subTest(prefix=prefix):
                self.routes.set('unchecked', argv_prefix=prefix, cwd='/not/a/host/path')
                before = self.routes.snapshot()
                preview = self.routes.preview('printf user command', 'unchecked')
                self.assertEqual(preview['argv'][:len(prefix)+1], prefix+['bash'])
                self.assertEqual(preview['definition']['argv_prefix'], prefix)
                self.assertEqual(preview['target_cwd'], '/not/a/host/path')
                self.assertEqual(self.routes.snapshot(), before)
                self.assertFalse(preview['executed'])
                self.assertIn('$0 label', preview['notice'])

    def test_discovery_replacement_removal_and_default_fallback(self):
        self.assertEqual(self.routes.snapshot(), {'targets': {}, 'default': 'host'})
        described = self.routes.describe()
        self.assertEqual(described['host_cwd'], str(self.host))
        self.assertEqual(list(described['targets']), ['host'])
        name = "自由 route with spaces ; '"
        self.routes.set(name, argv_prefix=['docker', 'exec', '-i', 'container'],
                        cwd='/app', description='Linux dependencies live here.')
        self.routes.select(name)
        described = self.routes.describe()
        self.assertEqual(described['default'], name)
        self.assertEqual(described['targets'][name]['cwd'], '/app')
        self.assertEqual(described['targets'][name]['description'], 'Linux dependencies live here.')
        self.routes.set(name, cwd='/different')
        replaced = self.routes.describe()['targets'][name]
        self.assertEqual(replaced['argv_prefix'], [])
        self.assertEqual(replaced['cwd'], '/different')
        self.assertEqual(replaced['description'], '')
        result = self.routes.remove(name)
        self.assertEqual(result['default'], 'host')
        self.assertNotIn(name, result['targets'])

    def test_current_snapshot_roundtrip_and_child_independence(self):
        prefix = ['docker', 'exec', '-i', 'editable container']
        self.routes.set('linux', argv_prefix=prefix, cwd='/app')
        prefix[-1] = 'caller mutation'
        self.routes.select('linux')
        options = json.loads(json.dumps(self.routes.snapshot()))
        child_repo = self.root/'child repo'
        child = ExecutionTargets(child_repo, **options)
        self.assertEqual(child.snapshot(), self.routes.snapshot())
        options['targets']['linux']['argv_prefix'].clear()
        self.assertEqual(child.plan('true').argv[:4], ('docker', 'exec', '-i', 'editable container'))
        self.assertEqual(child.plan('true').cwd, str(child_repo))
        self.assertNotIn('host_cwd', child.snapshot())
        child.remove('linux')
        self.assertEqual(child.default, 'host')
        self.assertEqual(self.routes.default, 'linux')
        self.assertIn('linux', self.routes.snapshot()['targets'])

    def test_launch_provenance_cannot_drift_when_registry_or_discovery_changes(self):
        self.routes.set('mutable', argv_prefix=['wrapper', 'old arg'], cwd='/old')
        plan = self.routes.plan('printf old', 'mutable')
        original = plan.provenance()
        self.routes.set('mutable', argv_prefix=['different'], cwd='/new')
        description = self.routes.describe()
        description['targets']['mutable']['argv_prefix'].append('outside change')
        original['definition']['argv_prefix'].append('evidence copy change')
        original['argv'].clear()
        self.assertEqual(plan.target.cwd, '/old')
        self.assertEqual(plan.provenance()['definition']['argv_prefix'], ['wrapper', 'old arg'])
        self.assertTrue(plan.provenance()['argv'])
        self.assertEqual(self.routes.plan('true', 'mutable').target.argv_prefix, ('different',))
        self.assertIn('not attested', plan.provenance()['process_scope'])

    def test_invalid_updates_are_rejected_without_changing_registry(self):
        self.routes.set('present', cwd='/app')
        self.routes.select('present')
        before = self.routes.snapshot()
        invalid = [
            {'argv_prefix': 'docker exec'}, {'argv_prefix': ['docker', 7]},
            {'argv_prefix': ['']}, {'argv_prefix': ['bash\0']},
            {'cwd': ''}, {'cwd': 7}, {'cwd': '/app\0'},
            {'transport': 'automatic'}, {'transport': 'shell'}, {'description': None},
        ]
        for definition in invalid:
            with self.subTest(definition=definition):
                with self.assertRaises(ValueError):
                    self.routes.set('present', **definition)
                self.assertEqual(self.routes.snapshot(), before)
        for operation in (lambda: self.routes.select('unknown'),
                          lambda: self.routes.remove('unknown'),
                          lambda: self.routes.set('host', cwd='/different'),
                          lambda: self.routes.remove('host')):
            with self.assertRaises(ValueError):
                operation()
            self.assertEqual(self.routes.snapshot(), before)

    def test_unknown_and_malformed_configurations_fail_before_launch(self):
        for options in ({'targets': []}, {'targets': {'name': []}},
                        {'targets': {'name': {'misspelled_cwd': '/app'}}},
                        {'targets': {'host': {}}}, {'default': 'absent'}):
            with self.subTest(options=options):
                with self.assertRaises(ValueError):
                    ExecutionTargets(self.host, **options)
        for command, target in ((None, None), ('a\0b', None), ('true', ''),
                                ('true', 'absent'), ('true', 5)):
            with self.subTest(command=command, target=target):
                with self.assertRaises(ValueError):
                    self.routes.plan(command, target)

    def test_shell_configuration_serializes_and_has_no_implicit_probing_or_deadline(self):
        self.routes.set('server', argv_prefix=['ssh', '-T', 'server alias'],
                        cwd='/not/a/host/path', transport='shell', description='Remote repository')
        restored = ExecutionTargets(self.host, **json.loads(json.dumps(self.routes.snapshot())))
        self.assertEqual(restored.describe(), self.routes.describe())
        plan = restored.plan('false | true', 'server')
        self.assertEqual(plan.argv[:3], ('ssh', '-T', 'server alias'))
        self.assertEqual(len(plan.argv), 4)
        self.assertEqual(plan.cwd, str(self.host))
        self.assertEqual(plan.target.cwd, '/not/a/host/path')


if __name__ == '__main__':
    unittest.main()
