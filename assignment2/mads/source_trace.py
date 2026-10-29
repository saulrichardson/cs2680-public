"""Observe source provenance while running a Python target, without pre-importing it.

    python /path/to/source_trace.py --report /tmp/origins.json \\
        --expect mypackage=/workspace/src -- -m pytest tests/test_feature.py
    python -m mads.source_trace --report /tmp/origins.json -- app.py argument
    python source_trace.py --report /tmp/origins.json -- -c 'import mypackage'

This optional, stdlib-only launcher runs in the *target* interpreter (Python 3.8+).
It does not change the working directory, install dependencies, select a checkout,
or fail a successful target because an origin differs from an expectation. Choose
PYTHONPATH/interpreter/options yourself, exactly as for the unwrapped command.
Repeated --expect PACKAGE=ROOT entries allow several roots for a namespace.

The JSON report separates exec audit events, sampled module metadata, and source
file hashes. exec events observe code submitted to exec/eval/import, not every
function call. A marshal hash identifies the observed code object in this Python
version; a file hash samples bytes on disk, NOT proof those bytes produced the
code. Rewritten tests, stale pyc files, generated code and custom loaders matter.
Unobserved modules are not proof of absence. Origins are observational metadata,
not a security boundary or a correctness certificate.

Like other in-process launchers, this is NOT perfectly transparent. It imports
stdlib support modules, installs an audit hook, adds wrapper/runpy traceback
frames, and exposes a different sys.orig_argv/process command line. Existing audit
hooks, timing, stack inspection, sys.modules, profiling, and site startup can
notice it. It does not install a tracing/profiling hook or pre-import the requested
packages. Normal script/-m/-c argv, __main__, and initial search-path semantics
are preserved; interpreter flags belong before this launcher, not after --.
Python source files are supported as scripts (not zip/directory/pyc entrypoints).
Invalid-entrypoint diagnostics name sys.executable, which can differ from an
embedding/OS-specific native launcher name. Additional traceback frames are
expected for invalid source and target exceptions.

A 'running' report is written before target execution; finalization is in a
finally block, before interpreter shutdown and target atexit handlers. os._exit,
fatal signals or a killed process may leave only that initial report. Child
processes (including fork children) are not instrumented and cannot finalize the
parent report. Report-writing failures are reported best-effort without replacing the target's exception/exit status. Initial invalid options or
an unwritable report prevent execution. This module is intended as a one-shot CLI,
not a reloadable in-process execution service (Python audit hooks cannot be removed).
"""

import argparse
import builtins
import hashlib
import importlib.machinery
import json
import marshal
import os
from pathlib import Path
import runpy
import stat
import sys
import tempfile
import textwrap
import threading
import time
import types


SCHEMA = "mads.python_source_trace.v1"


def _safe_value(value):
    # Describing an outcome must not call application-defined __str__/__repr__
    # (or conversion methods on primitive subclasses) a second time.
    if type(value) in (type(None), bool, int, float, str):
        return value
    cls = type(value)
    return {"type": type.__getattribute__(cls, "__module__") + "." +
                    type.__getattribute__(cls, "__qualname__"), "not_formatted": True}


def _error(exc):
    cls = type(exc)
    return {"type": type.__getattribute__(cls, "__module__") + "." +
                    type.__getattribute__(cls, "__qualname__"),
            "args": [_safe_value(value) for value in BaseException.args.__get__(exc)],
            "message_not_formatted": True}


def _warn(message):
    # The target may replace/close either stream, or make write() itself raise.
    # Diagnostics are useful, but must never replace the target's outcome.
    for name in ("stderr", "__stderr__"):
        try:
            stream = getattr(sys, name, None)
            stream.write(message + "\n")
            stream.flush()
            return
        except BaseException:
            pass


def _resolve(path):
    # realpath resolves relative paths itself. abspath first would collapse ..
    # lexically before following a symlink and could identify a different file.
    return os.path.realpath(path)


def _sample(path):
    """Hash only regular files; a synthetic co_filename must not open a FIFO."""
    try:
        before = os.stat(path)
        if not stat.S_ISREG(before.st_mode):
            return {"status": "not_regular_file"}
        digest = hashlib.sha256()
        flags = os.O_RDONLY | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_NOCTTY", 0)
        with os.fdopen(os.open(path, flags), "rb") as handle:
            # Nonblocking open avoids a FIFO replacement race after stat.
            opened = os.fstat(handle.fileno())
            if not stat.S_ISREG(opened.st_mode):
                return {"status": "not_regular_file"}
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
            after = os.fstat(handle.fileno())
        fields = ("st_dev", "st_ino", "st_size", "st_mtime_ns", "st_ctime_ns")
        stable = all(getattr(before, key) == getattr(after, key) for key in fields)
        return {"status": "sampled", "sha256": digest.hexdigest(), "size": after.st_size,
                "stable_during_sample": stable}
    except (OSError, ValueError) as exc:
        return {"status": "unavailable", "error": _error(exc)}


def _atomic_json(path, data):
    path = Path(path)
    fd, temporary = tempfile.mkstemp(prefix="." + path.name + ".", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(data, handle, indent=2, sort_keys=True, ensure_ascii=True)
            handle.write("\n")
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _locations(value):
    if isinstance(value, (list, tuple)):
        return [item for item in value if isinstance(item, str)]
    # importlib's namespace path caches its portions in _path. Do not iterate
    # arbitrary user objects or invoke a lazy module/path object's __getattr__.
    try:
        values = object.__getattribute__(value, "__dict__")
    except (AttributeError, TypeError):
        return []
    cached = values.get("_path") if isinstance(values, dict) else None
    return [item for item in cached if isinstance(item, str)] if isinstance(cached, list) else []


def _module_metadata(name, module):
    if not isinstance(module, types.ModuleType):
        return None
    try:
        values = object.__getattribute__(module, "__dict__")
    except (AttributeError, TypeError):
        values = None
    # Some extension module proxies (for example cffi's Lib) are ModuleType
    # instances without a normal dictionary. Keep an explicitly unknown row,
    # rather than aborting the snapshot of every subsequent module.
    metadata_available = isinstance(values, dict)
    if not metadata_available:
        values = {}
    spec = values.get("__spec__")
    try:
        spec_values = object.__getattribute__(spec, "__dict__")
    except (AttributeError, TypeError):
        spec_values = {}
    if not isinstance(spec_values, dict):
        spec_values = {}
    origin = spec_values.get("origin")
    filename = values.get("__file__")
    # Keep both: disagreement itself may be relevant to custom loaders.
    origin = origin if isinstance(origin, str) else None
    filename = filename if isinstance(filename, str) else None
    locations = _locations(values.get("__path__"))
    path = filename or (origin if origin not in ("built-in", "frozen") else None)
    return {"name": name, "file": filename, "spec_origin": origin,
            "metadata_status": "sampled" if metadata_available else "dictionary_unavailable",
            "resolved_file": _resolve(path) if path and not path.startswith("<") else None,
            "namespace_locations": locations,
            "resolved_namespace_locations": [_resolve(item) for item in locations]}


def _within(path, roots):
    for root in roots:
        try:
            if os.path.commonpath([path, root]) == root:
                return True
        except ValueError:
            pass
    return False


class SourceRecorder:
    def __init__(self, expectations):
        self.expectations = expectations
        self.pid = os.getpid()
        self.active = False
        self.lock = threading.RLock()
        self.local = threading.local()
        self.files = {}
        self.modules = {}
        self.errors = []
        self.exec_events = 0
        self.launcher_main = sys.modules.get("__main__")
        self.final_modules = set()
        self.initial_modules = self._observe_modules("before_target", scoped=False)

    def _wanted(self, name):
        return name == "__main__" or any(name == p or name.startswith(p + ".")
                                          for p in self.expectations)

    def _observe_modules(self, phase, scoped):
        observed = set()
        for name, module in list(sys.modules.items()):
            if not isinstance(name, str) or (scoped and not self._wanted(name)):
                continue
            if name == "__main__" and module is self.launcher_main:
                continue
            data = _module_metadata(name, module)
            if data is None:
                continue
            key = (name, data["file"], data["spec_origin"], data["resolved_file"],
                   tuple(data["resolved_namespace_locations"]))
            observed.add(key)
            if key not in self.modules:
                self.modules[key] = dict(data, first_observed=phase, last_observed=phase)
            else:
                self.modules[key]["last_observed"] = phase
        return observed

    def audit(self, event, args):
        # Check ownership before touching locks/thread-local state inherited by
        # os.fork(). A vanished parent thread may have held the recorder lock.
        if os.getpid() != self.pid:
            return
        if not self.active or event != "exec" or getattr(self.local, "inside", False):
            return
        self.local.inside = True
        try:
            with self.lock:
                self.exec_events += 1
                code = args[0]
                filename = code.co_filename
                synthetic = filename.startswith("<") and filename.endswith(">")
                resolved = None if synthetic else _resolve(filename)
                key = (filename, resolved)
                if key not in self.files:
                    self.files[key] = {"filename": filename, "resolved_path": resolved,
                                       "first_event": self.exec_events, "exec_events": 0,
                                       "first_file_sample": _sample(resolved) if resolved else None,
                                       "code_objects": {}}
                item = self.files[key]
                item["exec_events"] += 1
                # This is a digest of the actual submitted code object, NOT a
                # source digest and not cross-version bytecode equivalence.
                digest = hashlib.sha256(marshal.dumps(code)).hexdigest()
                objects = item["code_objects"]
                if digest not in objects:
                    objects[digest] = {"marshal_sha256": digest, "name": code.co_name,
                                       "first_lineno": code.co_firstlineno, "exec_events": 0}
                objects[digest]["exec_events"] += 1
                self._observe_modules("exec_event", scoped=True)
        except Exception as exc:
            # Observation failure must not turn working target code into failure.
            self.errors.append(_error(exc))
        finally:
            self.local.inside = False

    def finish(self):
        if os.getpid() != self.pid:
            return {}
        self.active = False
        with self.lock:
            try:
                self.final_modules = self._observe_modules("after_target", scoped=False)
            except Exception as exc:
                self.errors.append(_error(exc))
            files = []
            samples = {}
            for item in self.files.values():
                item = dict(item)
                item["code_objects"] = list(item["code_objects"].values())
                path = item["resolved_path"]
                item["final_file_sample"] = _sample(path) if path else None
                samples[path] = item["final_file_sample"]
                first, last = item["first_file_sample"], item["final_file_sample"]
                item["same_sampled_bytes"] = (first["sha256"] == last["sha256"]
                                               if first and last and "sha256" in first and "sha256" in last else None)
                files.append(item)
            modules = []
            for key, item in self.modules.items():
                item = dict(item)
                name, path = item["name"], item["resolved_file"]
                # Presence refers to this name+origin metadata row, not merely
                # a reused sys.modules name. Historical rows are retained.
                item["present_at_start"] = key in self.initial_modules
                item["present_at_end"] = key in self.final_modules
                if path not in samples and path:
                    samples[path] = _sample(path)
                item["final_file_sample"] = samples.get(path)
                modules.append(item)
            expectations = []
            for package, roots in self.expectations.items():
                observed = [m for m in modules if m["name"] == package or m["name"].startswith(package + ".")]
                outside, unknown = [], []
                for module in observed:
                    paths = ([module["resolved_file"]] if module["resolved_file"] else module["resolved_namespace_locations"])
                    if not paths:
                        unknown.append(module["name"])
                    for path in paths:
                        if not _within(path, roots):
                            outside.append({"module": module["name"], "path": path,
                                            "first_observed": module["first_observed"]})
                expectations.append({"package": package, "expected_roots": roots,
                                     "observed_modules": sorted({m["name"] for m in observed}),
                                     "outside_expected_roots": outside,
                                     "unknown_filesystem_origin": sorted(set(unknown)),
                                     "package_observed": bool(observed)})
            return {"exec_events": self.exec_events, "executed_files": files,
                    "module_observations": sorted(modules, key=lambda m: (m["name"], m["file"] or "")),
                    "expectations": expectations, "observation_errors": self.errors}


def _entry(args, parser):
    if args and args[0] == "--":
        args = args[1:]
    if not args:
        parser.error("supply -- followed by -m MODULE, -c CODE, or a Python source file")
    if args[0] in ("-m", "-c"):
        if len(args) < 2:
            parser.error(args[0] + " requires an argument")
        return {"mode": "module" if args[0] == "-m" else "command", "value": args[1], "args": args[2:]}
    if args[0].startswith("-"):
        parser.error("interpreter options belong before the launcher; supported targets are -m, -c and source files")
    return {"mode": "script", "value": args[0], "args": args[1:]}


def _execute(entry):
    mode, value = entry["mode"], entry["value"]
    sys.argv = [{"command": "-c", "module": "-m"}.get(mode, value)] + entry["args"]
    # Match the native target's path[0], not the launcher's installation path.
    # -I/-P deliberately suppress this entry, so do not add one in those modes.
    if not (getattr(sys.flags, "safe_path", False) or sys.flags.isolated):
        initial = ("" if mode == "command" else os.getcwd() if mode == "module"
                   else os.path.dirname(_resolve(value)))
        sys.path[0:1] = [initial]
    module = types.ModuleType("__main__")
    namespace = module.__dict__
    namespace.update(__builtins__=builtins, __package__=None, __spec__=None,
                     __loader__=importlib.machinery.BuiltinImporter)
    sys.modules["__main__"] = module
    if mode == "module":
        # This is the runtime's own `python -m` implementation. Unlike public
        # run_module(alter_sys=True), it does NOT restore the launcher's __main__
        # or argv[0] before target atexit callbacks. Its private API is an explicit
        # compatibility dependency, exercised in the supported interpreters.
        runpy._run_module_as_main(value, alter_argv=True)
        return
    if mode == "script":
        # CPython 3.9+ makes __file__ absolute without normalizing its spelling.
        # Native path[0] above uses the *physical* directory instead.
        filename = os.path.join(os.getcwd(), value) if sys.version_info >= (3, 9) else value
        namespace.update(__file__=filename, __cached__=None,
                         __loader__=importlib.machinery.SourceFileLoader("__main__", filename))
        try:
            handle = open(value, "rb")
        except OSError as exc:
            _warn("{}: can't open file {!r}: [Errno {}] {}".format(
                sys.executable, filename, exc.errno, exc.strerror))
            raise SystemExit(2)
        with handle:
            code = compile(handle.read(), filename, "exec")
    else:
        # CPython 3.14's _PyUnicode_Dedent implements textwrap.dedent semantics
        # for -c; older interpreters deliberately keep their indentation errors.
        if sys.version_info >= (3, 14):
            value = textwrap.dedent(value)
        code = compile(value, "<string>", "exec")
    # Keep the target __main__ through final sampling and interpreter shutdown,
    # so its atexit callbacks/import __main__ see the expected namespace.
    try:
        exec(code, namespace)
    finally:
        if mode == "script":
            # CPython's ordinary file runner removes these two fields before
            # atexit (unlike -m). The exec-time observation retains their values.
            namespace.pop("__file__", None)
            namespace.pop("__cached__", None)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--report", required=True, type=Path, help="JSON path; parent directory must exist")
    parser.add_argument("--expect", action="append", default=[], metavar="PACKAGE=ROOT",
                        help="report matching module origins outside these roots; repeatable, never a pass/fail gate")
    parser.add_argument("target", nargs=argparse.REMAINDER)
    options = parser.parse_args(argv)
    if not hasattr(sys, "addaudithook"):
        parser.error("source_trace requires Python 3.8+ audit hooks")
    entry = _entry(options.target, parser)
    expectations = {}
    for value in options.expect:
        package, sep, root = value.partition("=")
        if not sep or not package or not root:
            parser.error("--expect must be PACKAGE=ROOT with both values nonempty")
        expectations.setdefault(package, []).append(_resolve(root))
    report_path = options.report.absolute()
    recorder = SourceRecorder(expectations)
    report = {"schema": SCHEMA, "state": "running", "entry": entry,
              "pid": os.getpid(), "started_at_unix": time.time(),
              "interpreter": {"executable": sys.executable, "version": sys.version,
                              "implementation": sys.implementation.name, "flags": repr(sys.flags)},
              "launcher": {"file": str(Path(__file__).resolve()), "sample": _sample(__file__)},
              "initial_cwd": os.getcwd(), "launcher_sys_path": list(sys.path),
              "expectations": expectations,
              "limits": ["exec audit events are not a function-call trace",
                         "file hashes sample disk bytes, not a source-to-code attestation",
                         "module origins are sampled metadata, not an import security boundary",
                         "support imports and runpy change the process/stack; no artificial target imports",
                         "no subprocess, pre-startup, or interpreter-shutdown/atexit instrumentation"]}
    try:
        _atomic_json(report_path, report)
    except (OSError, ValueError) as exc:
        parser.error("cannot create initial report: " + str(exc))
    sys.addaudithook(recorder.audit)
    recorder.active = True
    try:
        _execute(entry)
    except BaseException as exc:
        report["outcome"] = {"kind": "system_exit" if isinstance(exc, SystemExit) else "exception",
                             "exception": _error(exc)}
        if isinstance(exc, SystemExit):
            report["outcome"]["requested_exit_code"] = _safe_value(SystemExit.code.__get__(exc))
        raise
    else:
        report["outcome"] = {"kind": "returned"}
    finally:
        if os.getpid() == recorder.pid:
            try:
                report.update(recorder.finish())
                report.update(state="finished_before_shutdown", finished_at_unix=time.time(),
                              final_cwd=os.getcwd(), final_sys_path=list(sys.path))
                _atomic_json(report_path, report)
            except BaseException:
                # Even formatting an application-defined reporting exception
                # could fail. Preserve the original target outcome regardless.
                _warn("source_trace: final report failed: the initial report may be incomplete")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
