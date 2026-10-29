"""Explicit AWS development-grader dependency access; no solver policy changes."""
import hashlib
from pathlib import Path


def grade_dependency_access(arguments):
    """Change only the recognized original evaluator's grading container network.

    Tests and captured patch remain literal. Go resolves the actual candidate's
    imports during the original test execution, rather than relying on a guessed
    cache. This is an environment intervention, not official offline parity.
    """
    args = list(arguments)
    if not args or args[0] != 'run' or '--name' not in args:
        raise ValueError('Dependency access requires an identified grading invocation')
    if not args[args.index('--name') + 1].startswith('a3grade_'):
        raise ValueError('Dependency access is only for original evaluator containers')
    mounts = {}
    for i, argument in enumerate(args[:-1]):
        if argument in ('-v', '--volume'):
            fields = args[i + 1].split(':')
            if len(fields) >= 2:
                mounts[fields[1]] = fields
    if set(('/tests', '/replay', '/logs/verifier')) - mounts.keys():
        raise ValueError('Original grader binds are incomplete')
    if mounts['/tests'][2:] != ['ro'] or mounts['/replay'][2:] != ['ro']:
        raise ValueError('Verifier and replay inputs must remain read-only')
    patch = Path(mounts['/replay'][0]) / 'model.patch'
    raw = patch.read_bytes()
    if not raw:
        raise ValueError('Dependency access requires the captured nonempty candidate')
    if args.count('--network') != 1:
        raise ValueError('Expected one explicit original grading network')
    at = args.index('--network') + 1
    if args[at] != 'none':
        raise ValueError('Original grading network differs from the reviewed evaluator')
    args[at] = 'bridge'
    args[1:1] = ['-e', 'GOPROXY=https://proxy.golang.org,direct',
                 '-e', 'GOSUMDB=sum.golang.org', '-e', 'GOTOOLCHAIN=auto']
    return args, {'mode': 'aws_dependency_network', 'network': 'bridge',
        'candidate_sha256': hashlib.sha256(raw).hexdigest(), 'candidate_bytes': len(raw),
        'go_proxy': 'https://proxy.golang.org,direct', 'checksum_verification': 'sum.golang.org',
        'go_toolchain': 'auto',
        'original_test_scripts': True, 'solver_network_changed': False,
        'reference_solutions_used': False, 'official_offline_environment_parity': False}


def install(source):
    """Instrument a new, unfrozen fleet source at its existing grader boundary."""
    path = Path(source) / 'dev/grading_docker.py'
    text = path.read_text()
    boundary = "            environment = grader_environment(args, os.environ.get('A3_GRADING_ENVIRONMENT', 'stock'))"
    if text.count(boundary) != 1:
        raise ValueError('Unrecognized original grader-wrapper boundary')
    helper = Path(__file__).read_text().split('\ndef install(source):')[0]
    replacement = boundary + '''
            args, dependency_access = grade_dependency_access(args)
            environment.update(base_mode=environment['mode'], mode='aws_dependency_network',
                classification='Original verifier with AWS dependency network; development intervention',
                dependency_access=dependency_access)
'''
    text = text.replace(boundary, replacement)
    entrypoint = "if __name__ == '__main__':"
    if text.count(entrypoint) != 1:
        raise ValueError('Unrecognized grader entry point')
    path.write_text(text.replace(entrypoint, helper + '\n\n' + entrypoint))
