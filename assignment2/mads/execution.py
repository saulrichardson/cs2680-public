"""Named, model-editable execution routes; not an access policy or sandbox.

This module only composes a launch. Jobs remains responsible for subprocesses,
stdin, capture and explicit timeouts. An unset configuration is exactly the
historical host launch: ``bash -o pipefail -c COMMAND`` in the repository.

``argv`` transports append Bash argv directly (e.g. ``docker exec -i NAME``).
``shell`` transports append one POSIX-shell-quoted command argument, for wrappers
such as ``ssh -T HOST`` that reparse their command on the other side. This mode
must be chosen explicitly: inspecting an executable's name cannot establish its
argument semantics. Prefixes are always local argv, never local shell text. The
harness supplies Bash; a local route with a different cwd uses an empty prefix,
not ``["bash"]``. ``preview`` composes the very same launch without executing it.

Definitions, including descriptions, are visible in discovery and evidence.
Do not put credentials in them; use ordinary credential files or your launcher.
No executable, host, container, command, filesystem or duration allowlist applies.
"""
from collections.abc import Mapping
from dataclasses import dataclass
import os
import shlex


# The directory and user command are data, not interpolated into this program.
# A second, exec'd Bash preserves COMMAND's $0/$#/positional-argument semantics.
# CDPATH must not redirect a relative target cwd or produce incidental stdout.
# Prefix relative paths so a literal directory named '-' is not Bash's OLDPWD.
_CHANGE_DIRECTORY = ('case "$1" in /*) ;; *) set -- "./$1" "$2";; esac; '
                     'CDPATH= cd -- "$1" && exec bash -o pipefail -c "$2"')
_NOTICE = (
    'Definitions describe launch configuration, not a checked environment. '
    'The harness supplies Bash: for a local cwd use argv_prefix=[], not ["bash"]. '
    'argv transport appends Bash argv to the prefix; shell transport appends one '
    'POSIX-quoted Bash command string. Prefixes launch on the host; cwd is literal '
    'inside target Bash. Use action=preview for exact argv without execution. '
    'The host route is always available. Host process-group cleanup does not '
    'attest termination of container/remote processes; use target-native cleanup '
    'when needed. Ordinary shell authority is unchanged.'
)
_PREVIEW_NOTICE = (
    'Not executed: no subprocess, executable lookup, directory check or target '
    'probe. host_cwd starts the local prefix. target_cwd is the literal directory '
    'passed to cd inside target Bash; null means no cd, inheriting the launcher\'s '
    'destination cwd (host_cwd for the host route). It is not an observed or '
    'resolved target path. mads-target-cwd, when present in argv, is only the '
    'internal Bash $0 label, not an executable to install or invoke.'
)


def _text(value, label, *, nonempty=False):
    if not isinstance(value, str) or '\0' in value or (nonempty and not value):
        suffix = ', nonempty' if nonempty else ''
        raise ValueError(f'{label} must be a NUL-free{suffix} string')
    return value


@dataclass(frozen=True)
class ExecutionTarget:
    name: str
    argv_prefix: tuple = ()
    cwd: str = None
    transport: str = 'argv'
    description: str = ''

    def __post_init__(self):
        _text(self.name, 'target name', nonempty=True)
        if not isinstance(self.argv_prefix, (list, tuple)):
            raise ValueError('argv_prefix must be an array of strings, not shell text')
        prefix = tuple(_text(part, 'argv_prefix item') for part in self.argv_prefix)
        if prefix and not prefix[0]:
            raise ValueError('argv_prefix executable must not be empty')
        object.__setattr__(self, 'argv_prefix', prefix)
        if self.cwd is not None:
            _text(self.cwd, 'target cwd', nonempty=True)
        if self.transport not in ('argv', 'shell'):
            raise ValueError('transport must be argv or shell')
        if self.transport == 'shell' and not prefix:
            raise ValueError('shell transport needs a prefix that accepts a command string')
        _text(self.description, 'description')

    def config(self):
        """A detached, JSON-serializable definition (name is the registry key)."""
        return dict(argv_prefix=list(self.argv_prefix), cwd=self.cwd,
                    transport=self.transport, description=self.description)


_HOST = ExecutionTarget('host', description='Fresh host Bash in the repository.')


@dataclass(frozen=True)
class LaunchPlan:
    argv: tuple
    cwd: str
    target: ExecutionTarget

    def provenance(self):
        """Exact planned argv and definition, unaffected by later registry edits.

        Recording a plan is not proof that its executable or target was reached.
        Jobs should add this to launch/state evidence rather than claiming that
        host process-group ownership extends to a remote/container workload.
        """
        return dict(target=self.target.name, definition=self.target.config(),
                    host_cwd=self.cwd, argv=list(self.argv),
                    process_scope='owned host process group; remote/container termination '
                                  'is not attested by host cleanup')


class ExecutionTargets:
    """A registry for one Agent, with immutable launch plans and copy snapshots.

    ``ExecutionTargets(repo, **snapshot)`` restores configuration. The intended
    Agent option is ``execution={"targets": {name: definition}, "default": name}``.
    Pass the *current* snapshot to the reviewer, not the Agent's initial options.
    Snapshot excludes host cwd: a new Agent supplies its own repository explicitly.
    No environment probing, shell execution or subprocess deadline is implicit.
    """

    def __init__(self, host_cwd, targets=None, default='host'):
        self.host_cwd = _text(os.fspath(host_cwd), 'host cwd', nonempty=True)
        self._targets = {}
        self._default = 'host'
        if targets is None:
            targets = {}
        if not isinstance(targets, Mapping):
            raise ValueError('targets must be an object keyed by target name')
        fields = {'argv_prefix', 'cwd', 'transport', 'description'}
        for name, definition in targets.items():
            if not isinstance(definition, Mapping):
                raise ValueError(f'Target {name!r} must be an object')
            extra = set(definition) - fields
            if extra:
                raise ValueError(f'Target {name!r} has unknown fields: '
                                 + ', '.join(sorted(map(repr, extra))))
            self.set(name, **definition)
        self.select(default)

    @property
    def default(self):
        return self._default

    def _lookup(self, name):
        _text(name, 'target name', nonempty=True)
        if name == 'host':
            return _HOST
        try:
            return self._targets[name]
        except KeyError:
            raise ValueError(f'Unknown execution target {name!r}; discover or set a target first') from None

    @staticmethod
    def _editable_name(name):
        _text(name, 'target name', nonempty=True)
        if name == 'host':
            raise ValueError('host is the built-in unchanged route; use another name for a custom route')

    def set(self, name, *, argv_prefix=(), cwd=None, transport='argv', description=''):
        """Create or fully replace a named definition; existing plans stay intact."""
        self._editable_name(name)
        target = ExecutionTarget(name, argv_prefix, cwd, transport, description)
        self._targets = {**self._targets, name: target}
        return self.describe()

    def remove(self, name):
        """Remove a named route; removing the default resets it to host."""
        self._editable_name(name)
        self._lookup(name)
        self._targets = {key: value for key, value in self._targets.items() if key != name}
        if self._default == name:
            self._default = 'host'
        return self.describe()

    def select(self, name):
        """Choose the default for future calls; explicit plan targets override it."""
        self._lookup(name)
        self._default = name
        return self.describe()

    def snapshot(self):
        """Detached options for serialization/inheritance, without discovery prose."""
        return dict(targets={name: target.config() for name, target in self._targets.items()},
                    default=self._default)

    def describe(self):
        """Discover all routes, the host launch cwd and the current default."""
        return dict(host_cwd=self.host_cwd, default=self._default,
                    targets={'host': _HOST.config(),
                             **{name: target.config() for name, target in self._targets.items()}},
                    notice=_NOTICE)

    def preview(self, command=None, target=None):
        """Return the actual launch composition, never a probe or availability check.

        An omitted command uses a labeled, nonexecuted ``pwd`` sample. Explicit
        commands (including the empty string) remain exact. All returned data is
        detached from the registry, just like launch provenance.
        """
        sample = command is None
        command = 'pwd' if sample else command
        plan = self.plan(command, target)
        return dict(plan.provenance(), target_cwd=plan.target.cwd,
                    command=command, sample_command=sample, executed=False,
                    notice=_PREVIEW_NOTICE)

    def plan(self, command, target=None):
        """Compose argv without executing or interpolating configuration as code.

        A target cwd is literal (no host expansion, resolution or existence test).
        Relative paths are resolved by Bash inside the selected target, after the
        prefix has entered that environment. The caller still launches from the
        host repository. COMMAND retains its ordinary, fully authorized Bash role.
        """
        _text(command, 'command')
        definition = self._lookup(self._default if target is None else target)
        if definition.cwd is None:
            bash = ('bash', '-o', 'pipefail', '-c', command)
        else:
            bash = ('bash', '-o', 'pipefail', '-c', _CHANGE_DIRECTORY,
                    'mads-target-cwd', definition.cwd, command)
        payload = (shlex.join(bash),) if definition.transport == 'shell' else bash
        return LaunchPlan(definition.argv_prefix + payload, self.host_cwd, definition)
