"""Execute the instrumented course start boundary; retain private tool routing."""
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest

from lab.adapters.teamharness.solver_dependencies import DEPENDENCY_ENV, OFFLINE_PREFLIGHT, instrument, install

START = '''def start_sandbox(t):
    inner = "server"
    sh("docker", "run", "-d", "--name", sbx(t.k), "--network", NET, "--cpus", SBX_CPUS, "--memory", SBX_MEM,
       "-e", "GOPROXY=off", "-e", "GOSUMDB=off", "-e", "GOTOOLCHAIN=local",
       "--entrypoint", "bash", t.sandbox_image, "-c", inner)
    return sh("docker", "exec", AGENT, "python3", "health")
'''

START += '\ndef sandbox_policy(out):\n    bad = 0\n    if True:\n        if True:\n' + OFFLINE_PREFLIGHT + '\n    return bad\n'


class SolverDependencies(unittest.TestCase):
    def test_real_boundary_keeps_private_route_and_adds_gateway_before_probe(self):
        calls = []
        ns = dict(sh=lambda *args: calls.append(args), sbx=lambda k: 'sandbox-'+str(k),
                  NET='private', SBX_CPUS='1', SBX_MEM='4g', AGENT='agent')
        exec(instrument(START), ns)
        ns['start_sandbox'](SimpleNamespace(k=7, sandbox_image='sha256:pristine'))
        run, gateway, health = calls
        self.assertEqual(run[run.index('--network')+1], 'private')
        self.assertEqual(run[run.index('--cpus')+1], '1')
        self.assertEqual(run[run.index('--memory')+1], '4g')
        self.assertEqual(run[-3:], ('sha256:pristine', '-c', 'server'))
        self.assertEqual(gateway, ('docker', 'network', 'connect', 'bridge', 'sandbox-7'))
        self.assertEqual(health[:3], ('docker', 'exec', 'agent'))
        for key, value in DEPENDENCY_ENV.items():
            self.assertIn(key+'='+value, run)
        self.assertIn('GOSUMDB=sum.golang.org', run)

    def test_declared_dependency_network_still_requires_proxy_isolation(self):
        observations=[]
        ns={'log':observations.append}
        exec(instrument(START), ns)
        for output, expected in [('INTERNET_REACHABLE\nchecked',0),
                                 ('checked',1),('PROXY_REACHABLE\nchecked',1),
                                 ('PROXY_REACHABLE\nINTERNET_REACHABLE\nchecked',1)]:
            self.assertEqual(ns['sandbox_policy'](output), expected)

    def test_solver_dependency_access_requires_grader_access(self):
        from lab.adapters.teamharness.environments import freeze
        with self.assertRaisesRegex(ValueError, 'same access in the grader'):
            freeze('unused', 'unused', ['0'], 'unused', solver_dependencies='network')

    def test_unknown_or_already_modified_source_is_not_silently_rewritten(self):
        for source in ('# unknown', instrument(START)):
            with self.assertRaisesRegex(ValueError, 'Unrecognized'):
                instrument(source)
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); target=root/'starter_code/evaluation_scripts/run_all.py'
            target.parent.mkdir(parents=True);target.write_text(START)
            install(root)
            self.assertEqual(target.read_text(), instrument(START))
