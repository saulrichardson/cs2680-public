"""Source-identical engine installation and executable launch contract; no GPU."""
import argparse
import ast
import hashlib
import json
import os
from pathlib import Path
import shlex
import shutil
import stat
import subprocess
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from lab.adapters.teamharness import engines


EXPECTED = {
    'moe_runner_backend': 'flashinfer_cutlass', 'attention_backend': 'flashinfer',
    'sampling_backend': 'pytorch', 'mamba_ssm_dtype': 'bfloat16',
    'reasoning_parser': 'qwen3', 'tool_call_parser': 'qwen3_coder',
    'enable_metrics': True, 'enable_cache_report': True,
    'context_length': 262144, 'mem_fraction_static': .90,
    'mamba_radix_cache_strategy': 'extra_buffer', 'random_seed': 20261028,
}


def digest(data):
    return hashlib.sha256(data).hexdigest()


class EngineProfiles(unittest.TestCase):
    def install(self, root):
        output = root / 'package with spaces'
        metadata = engines.install_engine(output, 'stock-c5')
        return output, metadata

    def test_exact_engine_assets_and_checkpoint_without_solver_dependencies(self):
        with tempfile.TemporaryDirectory() as tmp:
            output, metadata = self.install(Path(tmp))
            self.assertEqual(engines.PROFILES, ('stock-c5', 'stock-c5-headroom'))
            self.assertEqual({str(p.relative_to(output)) for p in output.rglob('*') if p.is_file()},
                             {'engine/engine.json', 'engine/flags.txt', 'engine/launch.sh'})
            for name, identity in metadata['files'].items():
                path = output / 'engine' / name
                self.assertEqual(digest(path.read_bytes()), identity['sha256'])
                self.assertEqual(len(path.read_bytes()), identity['bytes'])
                self.assertEqual(stat.S_IMODE(path.stat().st_mode), identity['mode'])
            self.assertEqual(json.loads((output / 'engine/engine.json').read_text()), {
                'format': 1, 'engine': 'sglang',
                'model': 'nvidia/Qwen3.6-35B-A3B-NVFP4@1355db6a052410cfd62085d94b58866fd0f2c3c5'})
            self.assertEqual(metadata['framework']['qualified_version'], '0.5.20')
            self.assertEqual(metadata['source_archive']['sha256'],
                '5702dd2892d031acc99065927bfa7d05198151f59f8cd503c23e6a84a1b111a4')

    def test_launcher_syntax_quoted_reference_and_exit_status(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); output, _ = self.install(root)
            script = output / 'engine/launch.sh'
            subprocess.run(['bash', '-n', str(script)], check=True)
            binary = root / 'bin'; binary.mkdir()
            stub = binary / 'a4-serve'
            stub.write_text('#!/bin/bash\nprintf "%s\\n" "$@"\nexit 7\n')
            stub.chmod(0o755)
            result = subprocess.run([str(script)], env={**os.environ,
                'PATH': str(binary) + os.pathsep + os.environ.get('PATH', ''),
                'A4_ENGINE_DIR': str(output / 'engine')}, capture_output=True, text=True)
            self.assertEqual(result.returncode, 7)
            self.assertEqual(result.stdout.splitlines(), ['--flags-file', str(output / 'engine/flags.txt')])
            self.assertEqual(result.stderr, '')
            self.assertTrue(Path(result.stdout.splitlines()[1]).is_file())

    def test_flags_are_the_fixed_stock_parameters(self):
        with tempfile.TemporaryDirectory() as tmp:
            output, _ = self.install(Path(tmp))
            flags = shlex.split((output / 'engine/flags.txt').read_text(), comments=True)
            parser = argparse.ArgumentParser(allow_abbrev=False)
            for name, value in EXPECTED.items():
                options = {'action': 'store_true'} if type(value) is bool else {'type': type(value), 'required': True}
                parser.add_argument('--' + name.replace('_', '-'), **options)
            self.assertEqual(vars(parser.parse_args(flags)), EXPECTED)
            self.assertFalse(any(flag.startswith(('--speculative', '--max-running-requests', '--api-key', '--model-path')) for flag in flags))

    def test_no_overwrite_and_invalid_profile_has_no_output_effect(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); output, _ = self.install(root)
            before = {str(p.relative_to(output)): p.read_bytes() for p in output.rglob('*') if p.is_file()}
            with self.assertRaises(FileExistsError):
                engines.install_engine(output, 'stock-c5')
            self.assertEqual(before, {str(p.relative_to(output)): p.read_bytes() for p in output.rglob('*') if p.is_file()})
            for profile in ('experimental', '../stock-c5', None):
                with self.assertRaises(ValueError):
                    engines.install_engine(root / 'invalid', profile)
                self.assertFalse((root / 'invalid').exists())

    def test_changed_missing_or_linked_source_refused_before_output_creation(self):
        for kind in ('changed', 'missing', 'symlink', 'mode'):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp); source = root / 'profiles'
                source.mkdir()
                shutil.copy2(engines._ROOT / 'stock-c5.json', source / 'stock-c5.json')
                shutil.copytree(engines._ROOT / 'stock-c5', source / 'stock-c5')
                flags = source / 'stock-c5/flags.txt'
                if kind == 'changed': flags.write_text(flags.read_text() + '--speculative-algorithm NEXTN\n')
                elif kind == 'missing': flags.unlink()
                elif kind == 'symlink':
                    data = flags.read_bytes(); flags.unlink()
                    (source / 'external').write_bytes(data); flags.symlink_to(source / 'external')
                else: flags.chmod(0o755)
                with patch.object(engines, '_ROOT', source), self.assertRaises(ValueError):
                    engines.install_engine(root / 'output', 'stock-c5')
                self.assertFalse((root / 'output').exists())

    @unittest.skipUnless(os.environ.get('TEAMHARNESS_ENGINE_SOURCE_ZIP'), 'optional original ZIP identity check')
    def test_authorized_source_zip_engine_bytes_and_modes(self):
        source = Path(os.environ['TEAMHARNESS_ENGINE_SOURCE_ZIP'])
        with tempfile.TemporaryDirectory() as tmp:
            output, metadata = self.install(Path(tmp))
            self.assertEqual(digest(source.read_bytes()), metadata['source_archive']['sha256'])
            with zipfile.ZipFile(source) as archive:
                members = [i for i in archive.infolist() if i.filename.startswith('engine/')]
                self.assertEqual({i.filename for i in members}, {'engine/' + name for name in metadata['files']})
                for info in members:
                    path = output / info.filename
                    self.assertEqual(path.read_bytes(), archive.read(info))
                    self.assertEqual(stat.S_IMODE(path.stat().st_mode), (info.external_attr >> 16) & 0o777)

    @unittest.skipUnless(os.environ.get('TEAMHARNESS_SGLANG_WHEEL'), 'optional pinned SGLang source declaration check')
    def test_pinned_framework_declarations_parse_every_retained_parameter(self):
        wheel = Path(os.environ['TEAMHARNESS_SGLANG_WHEEL'])
        with tempfile.TemporaryDirectory() as tmp:
            output, metadata = self.install(Path(tmp))
            self.assertEqual(digest(wheel.read_bytes()), metadata['qualification']['pinned_wheel_sha256'])
            with zipfile.ZipFile(wheel) as archive:
                server = archive.read('sglang/srt/server_args.py')
                self.assertEqual(digest(server), metadata['qualification']['pinned_server_args_sha256'])
                distribution = archive.read('sglang-0.5.20.dist-info/METADATA').decode()
                self.assertIn('\nName: sglang\n', '\n' + distribution)
                self.assertIn('\nVersion: 0.5.20\n', distribution)
                declared = {}
                for name in archive.namelist():
                    if not name.startswith('sglang/srt/arg_groups/fields/') or not name.endswith('.py'):
                        continue
                    for node in ast.walk(ast.parse(archive.read(name))):
                        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and node.target.id in EXPECTED:
                            declared[node.target.id] = node.annotation
                self.assertEqual(set(declared), set(EXPECTED))
                # Build a CPU-only parser from the pinned field type declarations;
                # no SGLang import, installation, backend resolution or GPU claim.
                parser = argparse.ArgumentParser(allow_abbrev=False)
                for name, annotation in declared.items():
                    if isinstance(annotation, ast.Subscript) and isinstance(annotation.value, ast.Name) and annotation.value.id == 'A':
                        annotation = annotation.slice.elts[0]
                    if isinstance(annotation, ast.Subscript) and isinstance(annotation.value, ast.Name) and annotation.value.id == 'Optional':
                        annotation = annotation.slice
                    self.assertIsInstance(annotation, ast.Name)
                    primitive = {'bool': bool, 'str': str, 'float': float, 'int': int}[annotation.id]
                    self.assertIs(primitive, type(EXPECTED[name]))
                    options = {'action': 'store_true'} if primitive is bool else {'type': primitive, 'required': True}
                    parser.add_argument('--' + name.replace('_', '-'), **options)
                flags = shlex.split((output / 'engine/flags.txt').read_text(), comments=True)
                self.assertEqual(vars(parser.parse_args(flags)), EXPECTED)


if __name__ == '__main__':
    unittest.main()
