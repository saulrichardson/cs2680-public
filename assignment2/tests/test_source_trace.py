"""Execute the real source_trace CLI, comparing against the same native Python.

No model/provider, Docker or project dependency is needed. The same file can run
under a task interpreter, including Python 3.9, with the adjacent mads source.
These checks establish bounded launch/provenance behavior, not transparency for
all programs or proof that sampled files generated every observed code object.
"""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
TRACER = ROOT / "mads/source_trace.py"


# Native invocation is the independent oracle for entrypoint semantics. Avoid
# memory addresses, wrapper frames and sys.orig_argv (documented differences).
NAMESPACE = '''\
import atexit, builtins, json, os, sys
marker = "target main"
def describe():
    import __main__
    spec = globals().get("__spec__")
    loader = globals().get("__loader__")
    print(json.dumps(dict(
        name=__name__, package=__package__, file=globals().get("__file__"),
        cached=globals().get("__cached__"),
        spec_name=spec.name if spec else None,
        loader=(loader.__name__ if isinstance(loader, type) else type(loader).__name__),
        builtins_type=type(__builtins__).__name__, main_identity=__main__.__dict__ is globals(),
        marker=__main__.marker, argv=sys.argv, path=sys.path,
        trace=sys.gettrace(), profile=sys.getprofile(),
        leaked=sorted(set(globals()) & {"SourceRecorder", "parser", "options", "_execute"}),
    ), sort_keys=True))
describe()
atexit.register(describe)
'''


class SourceTraceTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.report = self.root / "report.json"
        self.env = dict(os.environ)
        self.env.pop("PYTHONPATH", None)
        self.env.pop("PYTHONSAFEPATH", None)
        self.env["PYTHONDONTWRITEBYTECODE"] = "1"

    def write(self, rel, data):
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(data, encoding="utf-8")
        return path

    def run_target(self, target, *, traced=True, expect=(), env=None, flags=(), report=None, input=""):
        command = [sys.executable, *flags]
        if traced:
            command += [str(TRACER), "--report", str(report or self.report)]
            for item in expect:
                command += ["--expect", item]
            command += ["--"]
        command += target
        result = subprocess.run(command, cwd=str(self.root), env=env or self.env,
                                input=input, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        return result

    def data(self):
        return json.loads(self.report.read_text())

    def assert_native_output(self, target, **kwargs):
        native = self.run_target(target, traced=False, **{k: v for k, v in kwargs.items() if k != "expect"})
        traced = self.run_target(target, **kwargs)
        self.assertEqual(traced.returncode, native.returncode, traced.stderr)
        self.assertEqual(traced.stdout, native.stdout)
        self.assertEqual(traced.stderr, native.stderr)
        return traced

    def test_command_namespace_argv_stdin_and_atexit_match_native(self):
        self.assert_native_output(["-c", NAMESPACE, "π ; $HOME", "--report", "literal\nline"])
        data = self.data()
        self.assertEqual(data["outcome"], {"kind": "returned"})
        self.assertEqual(data["state"], "finished_before_shutdown")
        self.assertEqual(data["observation_errors"], [])
        self.assert_native_output(["-c", "print(input())"], input="passed through\n")

    def test_relative_script_namespace_and_atexit_match_native(self):
        self.write("folder/app.py", NAMESPACE)
        self.assert_native_output(["folder/app.py", "alpha", "--expect", "not an option"])
        files = [f for f in self.data()["executed_files"] if f["resolved_path"] == str(self.root / "folder/app.py")]
        self.assertEqual(len(files), 1)
        self.assertTrue(files[0]["same_sampled_bytes"])
        self.assertEqual(files[0]["first_file_sample"]["sha256"], hashlib.sha256((self.root / "folder/app.py").read_bytes()).hexdigest())

    def test_module_namespace_relative_import_and_atexit_match_native(self):
        self.write("pack/__init__.py", "")
        self.write("pack/helper.py", "value = 42\n")
        self.write("pack/cli.py", "from .helper import value\nassert value == 42\n" + NAMESPACE)
        self.assert_native_output(["-m", "pack.cli", "one", "two"])
        main = [m for m in self.data()["module_observations"] if m["name"] == "__main__" and m["spec_origin"]]
        # Importing the parent package can observe __main__ before runpy has
        # assigned its final spec. Keep that real intermediate observation.
        self.assertEqual(len(main), 1)
        self.assertEqual(main[0]["spec_origin"], str(self.root / "pack/cli.py"))

    def test_package_main_and_target_argv_mutation_survive_shutdown(self):
        self.write("pack/__init__.py", "")
        self.write("pack/__main__.py", "import sys\nsys.argv = ['replaced', 'by target']\n" + NAMESPACE)
        self.assert_native_output(["-m", "pack", "discarded"])

    def test_isolated_interpreter_preserves_native_search_path(self):
        self.assert_native_output(["-c", NAMESPACE], flags=["-I"])

    @unittest.skipUnless(hasattr(sys.flags, "safe_path"), "-P introduced in Python 3.11")
    def test_safe_path_flag_preserves_native_search_path(self):
        self.write("app.py", NAMESPACE)
        self.assert_native_output(["app.py"], flags=["-P"])

    def test_wrong_installation_is_observed_without_changing_success_exit(self):
        checkout = self.root / "checkout"
        installed = self.root / "checkout-old"
        self.write("checkout/example_pkg/__init__.py", "value = 'edited'\n")
        target = self.write("checkout-old/example_pkg/__init__.py", "value = 'installed'\n")
        self.write("test_origin.py", "import unittest, example_pkg\nclass Test(unittest.TestCase):\n def test_value(self): self.assertEqual(example_pkg.value, 'installed')\n")
        env = dict(self.env, PYTHONPATH=str(installed))
        result = self.run_target(["-m", "unittest", "test_origin", "-q"], env=env, expect=["example_pkg=" + str(checkout)])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Ran 1 test", result.stderr)
        self.assertIn("OK", result.stderr)
        report = self.data()
        expectation = report["expectations"][0]
        self.assertTrue(expectation["package_observed"])
        self.assertEqual(expectation["outside_expected_roots"][0]["path"], str(target))
        file = next(f for f in report["executed_files"] if f["resolved_path"] == str(target))
        self.assertEqual(file["first_file_sample"]["sha256"], hashlib.sha256(target.read_bytes()).hexdigest())
        self.assertTrue(file["code_objects"][0]["marshal_sha256"])
        # Choosing the checkout remains the user's action, not an observer fix.
        env["PYTHONPATH"] = str(checkout)
        result = self.run_target(["-c", "import example_pkg; print(example_pkg.value)"], env=env, expect=["example_pkg=" + str(checkout)])
        self.assertEqual(result.stdout, "edited\n")
        self.assertEqual(self.data()["expectations"][0]["outside_expected_roots"], [])

    def test_expected_package_is_not_artificially_imported(self):
        self.write("danger/__init__.py", "raise RuntimeError('do not preimport')\n")
        result = self.run_target(["-c", "print('ordinary command')"], expect=["danger=" + str(self.root)])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "ordinary command\n")
        self.assertFalse(self.data()["expectations"][0]["package_observed"])

    def test_valid_import_order_is_not_disturbed_by_origin_expectations(self):
        self.write("ordered/__init__.py", "")
        self.write("ordered/a.py", "value = 42\nfrom . import b\n")
        self.write("ordered/b.py", "from .a import value\nassert value == 42\n")
        result = self.run_target(["-c", "import ordered.a; print(ordered.a.value)"], expect=["ordered.b=" + str(self.root)])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "42\n")
        self.assertEqual(self.data()["expectations"][0]["outside_expected_roots"], [])

    def test_failed_import_origin_survives_module_removal(self):
        module = self.write("broken/__init__.py", "raise ValueError('broken import')\n")
        result = self.run_target(["-c", "try:\n import broken\nexcept ValueError:\n print('caught')"], expect=["broken=" + str(self.root / "different")])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "caught\n")
        row = next(m for m in self.data()["module_observations"] if m["name"] == "broken")
        self.assertFalse(row["present_at_start"])
        self.assertFalse(row["present_at_end"])
        self.assertEqual(row["resolved_file"], str(module))
        self.assertTrue(self.data()["expectations"][0]["outside_expected_roots"])

    def test_namespace_package_portions_support_multiple_expected_roots(self):
        first, second = self.root / "one", self.root / "two"
        self.write("one/space/a.py", "value = 1\n")
        self.write("two/space/b.py", "value = 2\n")
        env = dict(self.env, PYTHONPATH=os.pathsep.join(map(str, [first, second])))
        code = "import space.a, space.b; print(space.a.value + space.b.value)"
        result = self.run_target(["-c", code], env=env, expect=["space=" + str(first), "space=" + str(second)])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "3\n")
        self.assertEqual(self.data()["expectations"][0]["outside_expected_roots"], [])
        self.run_target(["-c", code], env=env, expect=["space=" + str(first)])
        outside = self.data()["expectations"][0]["outside_expected_roots"]
        self.assertTrue(any(x["module"] == "space" and x["path"] == str(second / "space") for x in outside))
        self.assertTrue(any(x["module"] == "space.b" for x in outside))

    def test_path_alias_and_relative_expected_root_resolve_consistently(self):
        self.write("real/pkg/__init__.py", "value = 1\n")
        (self.root / "alias").symlink_to(self.root / "real", target_is_directory=True)
        result = self.run_target(["-c", "import pkg"], env=dict(self.env, PYTHONPATH=str(self.root / "alias")), expect=["pkg=real"])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.data()["expectations"][0]["outside_expected_roots"], [])

    def test_file_edit_after_import_is_not_reported_as_identical_source(self):
        path = self.write("changing.py", "value = 'before'\n")
        code = "import changing; from pathlib import Path; Path(changing.__file__).write_text(\"value = 'after'\\n\"); print(changing.value)"
        result = self.run_target(["-c", code])
        self.assertEqual(result.stdout, "before\n")
        file = next(f for f in self.data()["executed_files"] if f["resolved_path"] == str(path))
        self.assertFalse(file["same_sampled_bytes"])
        self.assertEqual(file["first_file_sample"]["sha256"], hashlib.sha256(b"value = 'before'\n").hexdigest())
        self.assertEqual(file["final_file_sample"]["sha256"], hashlib.sha256(path.read_bytes()).hexdigest())

    def test_virtual_filename_does_not_prevent_real_code_execution(self):
        code = "exec(compile(\"print('generated')\", 'no-such-source.py', 'exec'))"
        result = self.run_target(["-c", code])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "generated\n")
        file = next(f for f in self.data()["executed_files"] if f["filename"] == "no-such-source.py")
        self.assertEqual(file["first_file_sample"]["status"], "unavailable")
        self.assertTrue(file["code_objects"][0]["marshal_sha256"])

    def test_nonregular_co_filename_is_not_opened_as_source(self):
        os.mkfifo(str(self.root / "fifo"))
        result = self.run_target(["-c", "exec(compile(\"print('ok')\", 'fifo', 'exec'))"])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "ok\n")
        file = next(f for f in self.data()["executed_files"] if f["filename"] == "fifo")
        self.assertEqual(file["first_file_sample"]["status"], "not_regular_file")

    def test_site_preimport_is_labeled_preexisting_not_executed_by_observer(self):
        self.write("startpkg/__init__.py", "value = 1\n")
        self.write("sitecustomize.py", "import startpkg\n")
        result = self.run_target(["-c", "import startpkg; print(startpkg.value)"], env=dict(self.env, PYTHONPATH=str(self.root)), expect=["startpkg=" + str(self.root)])
        self.assertEqual(result.returncode, 0, result.stderr)
        data = self.data()
        row = next(m for m in data["module_observations"] if m["name"] == "startpkg")
        self.assertTrue(row["present_at_start"])
        self.assertFalse(any(f["resolved_path"] == row["resolved_file"] for f in data["executed_files"]))

    def test_system_exit_codes_and_messages_preserved(self):
        for value in ("None", "0", "7", "-1", "False", "True", "'user exit text'"):
            with self.subTest(value=value):
                self.assert_native_output(["-c", "import sys; print('before exit'); sys.exit(" + value + ")"])
                self.assertEqual(self.data()["outcome"]["kind"], "system_exit")

    def test_uncaught_exception_preserves_type_output_and_native_status(self):
        for code in ("print('before'); raise LookupError('actual target failure')", "raise KeyboardInterrupt()"):
            with self.subTest(code=code):
                native = self.run_target(["-c", code], traced=False)
                traced = self.run_target(["-c", code])
                self.assertEqual(traced.returncode, native.returncode)
                self.assertEqual(traced.stdout, native.stdout)
                self.assertEqual(traced.stderr.splitlines()[-1], native.stderr.splitlines()[-1])
                self.assertEqual(self.data()["outcome"]["kind"], "exception")

    def test_missing_module_matches_native_exit_and_message(self):
        self.assert_native_output(["-m", "no_such_source_trace_fixture_module"])
        self.assertEqual(self.data()["outcome"]["kind"], "system_exit")

    def test_os_exit_leaves_explicit_unfinished_report(self):
        result = self.run_target(["-c", "import os; os._exit(7)"])
        self.assertEqual(result.returncode, 7)
        self.assertEqual(self.data()["state"], "running")
        self.assertNotIn("outcome", self.data())

    def test_bad_initial_report_prevents_target_execution(self):
        result = self.run_target(["-c", "print('must not run')"], report=self.root / "missing" / "report.json")
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")
        self.assertIn("cannot create initial report", result.stderr)

    def test_final_report_failure_does_not_mask_target_exit(self):
        directory = self.root / "reports"
        directory.mkdir()
        report = directory / "origins.json"
        code = "import os, sys; os.unlink({!r}); os.rmdir({!r}); print('target ran'); sys.exit(7)".format(str(report), str(directory))
        result = self.run_target(["-c", code], report=report)
        self.assertEqual(result.returncode, 7)
        self.assertEqual(result.stdout, "target ran\n")
        self.assertIn("source_trace: final report failed:", result.stderr)

    def test_standalone_and_package_cli_match(self):
        env = dict(self.env, PYTHONPATH=str(ROOT))
        standalone = self.run_target(["-c", "print('entry parity')"], env=env)
        packaged = subprocess.run([sys.executable, "-m", "mads.source_trace", "--report", str(self.report), "--", "-c", "print('entry parity')"], cwd=str(self.root), env=env, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.assertEqual((standalone.returncode, standalone.stdout, standalone.stderr),
                         (packaged.returncode, packaged.stdout, packaged.stderr))
        self.assertEqual(self.data()["schema"], "mads.python_source_trace.v1")

    def test_symlink_dotdot_script_import_and_expected_root_match_native(self):
        (self.root / "real/sub").mkdir(parents=True)
        (self.root / "alias").symlink_to(self.root / "real/sub", target_is_directory=True)
        target = self.write("real/app.py", "import localhelper; print(localhelper.value)\n" + NAMESPACE)
        helper = self.write("real/localhelper.py", "value = 'physical sibling'\n")
        self.assert_native_output(["alias/../app.py"], expect=["localhelper=alias/.."])
        data = self.data()
        self.assertEqual(data["expectations"][0]["outside_expected_roots"], [])
        self.assertEqual(data["expectations"][0]["expected_roots"], [str(self.root / "real")])
        file = next(f for f in data["executed_files"] if f["resolved_path"] == str(target))
        self.assertEqual(file["filename"], str(self.root) + "/alias/../app.py")
        self.assertEqual(file["first_file_sample"]["sha256"], hashlib.sha256(target.read_bytes()).hexdigest())
        self.assertTrue(any(f["resolved_path"] == str(helper) for f in data["executed_files"]))

    def test_module_parent_initialization_sees_native_discovery_state(self):
        self.write("pack/__init__.py", "import sys, __main__\nprint(sys.argv, __main__.__loader__)\nassert sys.argv[0] == '-m'\n")
        self.write("pack/cli.py", NAMESPACE)
        self.assert_native_output(["-m", "pack.cli", "discovery argument"])

    def test_command_dedent_follows_selected_interpreter(self):
        cases = ["    print('indented')", "\n    if True:\n        print('multiline')\n  \n",
                 "\tprint('tab')\n\tprint('second')", " \tprint('mixed prefix')",
                 "    print('windows newline')\r\n", "print('no leading prefix')\n    print('invalid')"]
        for code in cases:
            with self.subTest(code=code):
                native = self.run_target(["-c", code], traced=False)
                traced = self.run_target(["-c", code])
                self.assertEqual(traced.returncode, native.returncode, traced.stderr)
                self.assertEqual(traced.stdout, native.stdout)
                # Invalid source has the documented additional wrapper frames.
                if native.returncode:
                    self.assertEqual(traced.stderr.splitlines()[-1], native.stderr.splitlines()[-1])
                else:
                    self.assertEqual(traced.stderr, native.stderr)

    @unittest.skipUnless(hasattr(os, "fork"), "POSIX fork semantics")
    def test_fork_child_neither_records_nor_overwrites_parent_report(self):
        code = """import os, sys, time
child = os.fork()
if child == 0:
    time.sleep(0.15)
    print('child', os.getpid(), flush=True)
    exec(compile('pass', 'CHILD_ONLY.py', 'exec'))
    sys.exit(7)
print('parent', os.getpid(), flush=True)
"""
        result = self.run_target(["-c", code])
        self.assertEqual(result.returncode, 0, result.stderr)
        pids = dict(line.split() for line in result.stdout.splitlines())
        self.assertNotEqual(pids["parent"], pids["child"])
        data = self.data()
        self.assertEqual(data["pid"], int(pids["parent"]))
        self.assertEqual(data["outcome"], {"kind": "returned"})
        self.assertFalse(any(f["filename"] == "CHILD_ONLY.py" for f in data["executed_files"]))

    def test_unavailable_diagnostic_streams_never_mask_target_outcome(self):
        for setup in ("sys.stderr = None", "sys.stderr.close()",
                      "sys.stderr = None; sys.__stderr__.close()",
                      "del sys.stderr; del sys.__stderr__",
                      "class Broken:\n def write(self, text): raise SystemExit(99)\n def flush(self): pass\nsys.stderr = sys.__stderr__ = Broken()"):
            with self.subTest(setup=setup):
                directory = self.root / "reports"
                directory.mkdir()
                report = directory / "report.json"
                code = "import os, sys\nos.unlink({!r})\nos.rmdir({!r})\n{}\nprint('target ran')\nsys.exit(7)".format(str(report), str(directory), setup)
                result = self.run_target(["-c", code], report=report)
                self.assertEqual(result.returncode, 7, result.stderr)
                self.assertEqual(result.stdout, "target ran\n")

    def test_target_exit_value_not_stringified_for_report(self):
        code = """class Message:
    def __str__(self):
        print('application conversion')
        return 'application exit text'
raise SystemExit(Message())
"""
        self.assert_native_output(["-c", code])
        self.assertEqual(self.data()["outcome"]["requested_exit_code"],
                         {"type": "__main__.Message", "not_formatted": True})
        # Primitive subclasses may also have application-defined conversion.
        self.assert_native_output(["-c", "class Text(str):\n def __str__(self):\n  print('string subclass converted')\n  return 'custom'\nraise SystemExit(Text('original'))"])

    def test_target_exception_not_stringified_for_report(self):
        code = "class Error(Exception):\n def __str__(self):\n  print('application conversion')\n  return 'application failure'\nraise Error('payload')"
        native = self.run_target(["-c", code], traced=False)
        traced = self.run_target(["-c", code])
        self.assertEqual(traced.returncode, native.returncode)
        self.assertEqual(traced.stdout, native.stdout)
        self.assertEqual(traced.stderr.splitlines()[-1], native.stderr.splitlines()[-1])
        self.assertTrue(self.data()["outcome"]["exception"]["message_not_formatted"])

    def test_presence_tracks_origin_not_just_reused_module_name(self):
        first = self.write("first/swap_pkg.py", "value = 'first'\n")
        second = self.write("second/swap_pkg.py", "value = 'second'\n")
        code = "import sys\nsys.path.insert(0, 'first')\nimport swap_pkg\nprint(swap_pkg.value)\ndel sys.modules['swap_pkg']\nsys.path[0] = 'second'\nimport swap_pkg\nprint(swap_pkg.value)"
        self.assert_native_output(["-c", code], expect=["swap_pkg=" + str(self.root)])
        rows = {m["resolved_file"]: m for m in self.data()["module_observations"] if m["name"] == "swap_pkg"}
        self.assertEqual(set(rows), {str(first), str(second)})
        self.assertFalse(rows[str(first)]["present_at_end"])
        self.assertTrue(rows[str(second)]["present_at_end"])
        self.assertTrue(all(not m["present_at_start"] for m in rows.values()))

    def test_missing_script_matches_native_status_and_error_details(self):
        native = self.run_target(["missing.py"], traced=False)
        traced = self.run_target(["missing.py"])
        self.assertEqual(traced.returncode, native.returncode)
        self.assertEqual(traced.returncode, 2)
        self.assertEqual(traced.stdout, native.stdout)
        # Framework Python's native program_name may differ from sys.executable.
        # Keep the actual missing filename/error comparison; only this documented
        # launcher-name prefix is excluded, not the target error or exit status.
        self.assertTrue(traced.stderr.startswith(sys.executable + ": can't open file "))
        self.assertEqual(traced.stderr.split(": can't open file ", 1)[1],
                         native.stderr.split(": can't open file ", 1)[1])
        self.assertEqual(self.data()["outcome"]["requested_exit_code"], 2)

    def test_stale_cache_demonstrates_disk_hash_is_not_source_attestation(self):
        path = self.write("cachedpkg.py", "value = 'old bytes'\n")
        result = subprocess.run([sys.executable, "-c", "import py_compile; py_compile.compile('cachedpkg.py', doraise=True)"],
                                cwd=str(self.root), env=self.env, text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        st = path.stat()
        path.write_text("value = 'new bytes'\n")
        os.utime(str(path), ns=(st.st_atime_ns, st.st_mtime_ns))
        self.assert_native_output(["-c", "import cachedpkg; print(cachedpkg.value)"])
        file = next(f for f in self.data()["executed_files"] if f["resolved_path"] == str(path))
        self.assertTrue(file["same_sampled_bytes"])
        self.assertEqual(file["first_file_sample"]["sha256"], hashlib.sha256(path.read_bytes()).hexdigest())
        # The observed disk bytes say 'new', but the actual imported code says
        # 'old'. This is a deliberately tested limitation, not an origin pass.
        self.assertEqual(self.run_target(["-c", "import cachedpkg; print(cachedpkg.value)"]).stdout, "old bytes\n")

    def test_module_proxy_without_dictionary_does_not_abort_later_observations(self):
        self.write("after_proxy.py", "value = 42\n")
        code = """import sys, types
class Proxy(types.ModuleType):
    @property
    def __dict__(self):
        raise AttributeError('extension proxy has no dictionary')
sys.modules['extension_proxy'] = Proxy('extension_proxy')
import after_proxy
print(after_proxy.value)
"""
        self.assert_native_output(["-c", code], expect=["extension_proxy=" + str(self.root), "after_proxy=" + str(self.root)])
        data = self.data()
        self.assertEqual(data["observation_errors"], [])
        rows = {m["name"]: m for m in data["module_observations"]}
        self.assertEqual(rows["extension_proxy"]["metadata_status"], "dictionary_unavailable")
        self.assertTrue(rows["extension_proxy"]["present_at_end"])
        self.assertTrue(rows["after_proxy"]["present_at_end"])
        self.assertEqual(data["expectations"][0]["unknown_filesystem_origin"], ["extension_proxy"])


if __name__ == "__main__":
    unittest.main()
