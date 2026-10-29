"""Launcher isolation: exercise actual Jobs and imports, not shell-side rewrites."""
import json
import os
from pathlib import Path
import shlex
import sys
import tempfile
import unittest
from unittest.mock import patch

from mads.jobs import Jobs, _strip_boot_pythonpaths
from mads.store import Store


class BootPaths(unittest.TestCase):
    def test_literal_contract(self):
        cases = [
            ('/runtime', '/runtime', None),
            ('/runtime:', '/runtime', ''),
            ('::/runtime:/user:', '/runtime', '::/user:'),
            ('/runtime:/user/../runtime:/runtime/', '/runtime', '/user/../runtime:/runtime/'),
            ('/runtime:/user:/runtime:/last', ':/runtime::', '/user:/last'),
            ('/runtime:/user', '', '/runtime:/user'),
            ('/runtime', '::', '/runtime'),
            ('', '/runtime', ''),
            (None, '/runtime', None),
            ('/space path:/ spaced', '/space path', '/ spaced'),
        ]
        for value, marker, expected in cases:
            with self.subTest(value=value, marker=marker):
                env = {'MADS_BOOT_PYTHONPATH': marker, 'OTHER': 'untouched'}
                if value is not None:
                    env['PYTHONPATH'] = value
                before = dict(env)
                result = _strip_boot_pythonpaths(env)
                self.assertEqual(result.get('PYTHONPATH'), expected)
                self.assertEqual(env, before)
                self.assertEqual(result['OTHER'], 'untouched')
        original = {'PYTHONPATH': '/runtime'}
        self.assertIs(_strip_boot_pythonpaths(original), original)

    def test_malformed_is_whole_declaration_opt_out_with_warning(self):
        for marker in ('relative:/runtime', ' /runtime', '/runtime:relative'):
            with self.subTest(marker=marker), self.assertLogs('mads.jobs', 'WARNING') as logs:
                env = {'PYTHONPATH': '/runtime:user', 'MADS_BOOT_PYTHONPATH': marker}
                self.assertIs(_strip_boot_pythonpaths(env), env)
            self.assertIn('PYTHONPATH unchanged', logs.output[0])


class ActualJobImports(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.frozen = self.root/'frozen'
        self.workspace = self.root/'workspace'
        self.external = self.root/'external'
        for directory in (self.frozen, self.workspace, self.external):
            directory.mkdir()
        (self.frozen/'study_marker.py').write_text('VALUE = "frozen"\n')
        (self.workspace/'study_marker.py').write_text('VALUE = "edited"\n')
        # Script lives outside the workspace. Explicit user entry supplies the
        # legitimate import location; cwd alone is NOT asserted to be sys.path.
        self.script = self.external/'probe.py'
        self.script.write_text(
            'import json, os, study_marker\n'
            'print(json.dumps({"value": study_marker.VALUE, "file": study_marker.__file__, '
            '"path": os.environ.get("PYTHONPATH"), "keys": [k for k in '
            '["CS2680_API_KEY", "OPENAI_API_KEY", "OPENROUTER_API_KEY"] if k in os.environ]}))\n')
        self.jobs = Jobs(self.root/'jobs', Store(self.root/'journal'))
        self.addCleanup(self.jobs.close)

    def launch(self, pythonpath, declaration):
        env = dict(os.environ)
        env.pop('MADS_BOOT_PYTHONPATH', None)
        env['PYTHONPATH'] = pythonpath
        if declaration is not None:
            env['MADS_BOOT_PYTHONPATH'] = declaration
        # Synthetic values only: the test does not inspect any real credentials.
        for key in ('CS2680_API_KEY', 'OPENAI_API_KEY', 'OPENROUTER_API_KEY'):
            env[key] = 'synthetic-test-value'
        with patch.dict(os.environ, env, clear=True):
            before = dict(os.environ)
            job = self.jobs.start(shlex.quote(sys.executable)+' '+shlex.quote(str(self.script)), self.workspace)
            state, artifact = self.jobs.observe(job, wait_seconds=5)
            self.assertEqual(dict(os.environ), before)
        self.assertEqual(state['shell_exit_code'], 0, Path(artifact['path']).read_text())
        result = json.loads(Path(artifact['path']).read_text())
        self.assertEqual(result['keys'], [])
        return result

    def test_declared_boot_removed_but_user_order_and_empty_entries_preserved(self):
        path = f'{self.frozen}:{self.workspace}::/ordinary/user:'
        result = self.launch(path, str(self.frozen))
        self.assertEqual(result['value'], 'edited')
        self.assertEqual(result['file'], str(self.workspace/'study_marker.py'))
        self.assertEqual(result['path'], f'{self.workspace}::/ordinary/user:')

    def test_no_declaration_imports_frozen(self):
        path = f'{self.frozen}:{self.workspace}'
        result = self.launch(path, None)
        self.assertEqual(result['value'], 'frozen')
        self.assertEqual(result['file'], str(self.frozen/'study_marker.py'))
        self.assertEqual(result['path'], path)

    def test_all_boot_entries_unset_in_child(self):
        # -c legitimately includes cwd, unlike the outside probe script above.
        command = shlex.quote(sys.executable)+' -c '+shlex.quote(
            'import os,study_marker;print(repr(os.environ.get("PYTHONPATH")));print(study_marker.VALUE)')
        with patch.dict(os.environ, {'PYTHONPATH': str(self.frozen), 'MADS_BOOT_PYTHONPATH': str(self.frozen)}):
            job = self.jobs.start(command, self.workspace)
            state, artifact = self.jobs.observe(job, wait_seconds=5)
        self.assertEqual(state['shell_exit_code'], 0)
        self.assertEqual(Path(artifact['path']).read_text(), 'None\nedited\n')


if __name__ == '__main__':
    unittest.main()
