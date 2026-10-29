"""Offline runner contracts using tiny local scripts, never a model or provider.

Run from the repository: python -m unittest discover -s tests -v
"""
from contextlib import redirect_stdout
import io
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import textwrap
import time
import unittest
from unittest.mock import patch

REPOSITORY = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPOSITORY))
from experiments import run_checks as runner


@unittest.skipUnless(os.name == "posix", "runner requires POSIX process groups")
class RunnerTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="offline runner fixtures ")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.output = self.root / "output"
        self.cwd = self.root / "unrelated cwd"
        self.cwd.mkdir()

    def fixture(self, name, source, artifacts=False):
        path = self.root / f"{name} fixture;$.py"
        path.write_text(textwrap.dedent(source), encoding="utf-8")
        return runner.Check(path, "test fixture", artifacts)

    def run_fixtures(self, checks, names=None, output=None):
        console = io.StringIO()
        with redirect_stdout(console):
            code = runner.run_checks(list(checks) if names is None else names,
                                     self.output if output is None else output, checks=checks)
        return code, console.getvalue()

    def summary(self, output=None):
        return json.loads(((output or self.output) / "summary.json").read_text())

    def cli(self, *args):
        return subprocess.run([sys.executable, str(REPOSITORY / "experiments/run_checks.py"), *args],
                              cwd=self.cwd, env={"PATH": os.defpath}, capture_output=True, text=True)

    def test_reviewed_allowlist_and_required_arguments(self):
        expected = {
            "context_tail": True, "context_inspection": True, "checkpoint": False,
            "navigation": False, "evidence_versions": False, "evidence_delivery": True,
            "retention_recovery": True, "compact_receipts": True, "jobs_sampling": False,
            "review_affordances": True, "inspection": False, "workspace_snapshot": False,
        }
        self.assertEqual({name: runner.CHECKS[name].artifact_dir for name in runner.DEFAULT_CHECKS}, expected)
        self.assertEqual(set(runner.CHECKS)-set(expected), {'boundaries','context_boundaries',
            'context_candidates','mask_restoration','presentation','responses_protocol','responses_audit','export'})
        self.assertEqual(runner.DEFAULT_CHECKS, tuple(expected))
        for name, needs_artifacts in expected.items():
            argv = runner.command(runner.CHECKS[name], self.output / name)
            self.assertEqual(argv[:3], [sys.executable, "-u", str(REPOSITORY / "checks" / f"{name}.py")])
            self.assertEqual(argv[3:], [str(self.output / name)] if needs_artifacts else [])
            self.assertTrue(Path(argv[2]).is_file())

    def test_list_from_arbitrary_cwd_without_output(self):
        result = self.cli("--list")
        self.assertEqual(result.returncode, 0, result.stderr)
        for name in runner.DEFAULT_CHECKS:
            self.assertIn(f"{name} [default]:", result.stdout)
        self.assertIn(str(REPOSITORY / "checks/context_tail.py"), result.stdout)
        self.assertIn("<unique-artifact-dir>", result.stdout)
        self.assertEqual(list(self.cwd.iterdir()), [])
        with patch.object(runner.subprocess, "Popen") as launch, redirect_stdout(io.StringIO()):
            self.assertEqual(runner.main(["--list"]), 0)
        launch.assert_not_called()

    def test_missing_output_and_list_misuse_are_clear_errors(self):
        for args, message in [([], "--output NEW_DIR is required"),
                              (["--list", "navigation"], "cannot be combined"),
                              (["--list", "--output", str(self.output)], "cannot be combined")]:
            with self.subTest(args=args):
                result = self.cli(*args)
                self.assertEqual(result.returncode, 2)
                self.assertIn(message, result.stderr)
                self.assertNotIn("Traceback", result.stderr)
                self.assertFalse(self.output.exists())

    def test_unknown_duplicate_and_empty_selections_have_no_effects(self):
        checks = {"ok": self.fixture("ok", "raise AssertionError('must not launch')")}
        destination = self.root / "missing parent" / "output"
        for names, message in [(["ok", "live"], "Unknown check"),
                               (["ok", "ok"], "Duplicate check"), ([], "No checks")]:
            with self.subTest(names=names), patch.object(runner.subprocess, "Popen") as launch:
                with self.assertRaisesRegex(ValueError, message):
                    self.run_fixtures(checks, names, destination)
                launch.assert_not_called()
                self.assertFalse(destination.parent.exists())
        result = self.cli("navigation", "unregistered_check", "--output", str(destination))
        self.assertEqual(result.returncode, 2)
        self.assertIn("Unknown check", result.stderr)
        self.assertFalse(destination.parent.exists())

    def test_existing_directory_file_and_dangling_symlink_are_not_changed(self):
        directory = self.root / "existing dir"
        directory.mkdir()
        (directory / "sentinel").write_bytes(b"keep me\x00\xff")
        file = self.root / "existing file"
        file.write_bytes(b"keep file")
        link = self.root / "dangling link"
        link.symlink_to(self.root / "absent target")
        checks = {"ok": self.fixture("ok", "raise AssertionError('must not launch')")}
        for destination in (directory, file, link):
            with self.subTest(destination=destination), patch.object(runner.subprocess, "Popen") as launch:
                with self.assertRaisesRegex(ValueError, "already exists"):
                    self.run_fixtures(checks, output=destination)
                launch.assert_not_called()
        self.assertEqual(list(directory.iterdir()), [directory / "sentinel"])
        self.assertEqual((directory / "sentinel").read_bytes(), b"keep me\x00\xff")
        self.assertEqual(file.read_bytes(), b"keep file")
        self.assertTrue(link.is_symlink())
        self.assertFalse(link.exists())

    def test_happy_path_exact_argv_imports_environment_and_unique_artifacts(self):
        fixture = self.fixture("arguments", """
            import json, os, sys
            from pathlib import Path
            import mads.context
            assert len(sys.argv) == 2, sys.argv
            root = Path(sys.argv[1])
            assert not root.exists()
            root.mkdir()
            (root / 'observed.json').write_text(json.dumps({
                'argv': sys.argv, 'cwd': os.getcwd(), 'python': sys.executable,
                'import': mads.context.__file__, 'pid': os.getpid(),
                'environment': dict(os.environ), 'optimize': sys.flags.optimize,
            }))
            print('arguments accepted')
        """, artifacts=True)
        output = self.root / "output with spaces; 'quotes' $(not-a-command)"
        with patch.dict(os.environ, {"PYTHONOPTIMIZE": "2", "PYTHONPATH": "/not/the/harness",
                                    "UNRELATED_SECRET_SENTINEL": "must-not-propagate"}):
            code, console = self.run_fixtures({"one": fixture, "two": fixture}, output=output)
        self.assertEqual(code, 0, console)
        summary = self.summary(output)
        self.assertEqual(summary["status"], "passed")
        self.assertEqual(summary["runner_exit_code"], 0)
        self.assertEqual(summary["counts"], {"passed": 2})
        rows = summary["checks"]
        self.assertNotEqual(rows[0]["artifact_dir"], rows[1]["artifact_dir"])
        self.assertNotEqual(rows[0]["pid"], rows[1]["pid"])
        for row in rows:
            observed = json.loads((Path(row["artifact_dir"]) / "observed.json").read_text())
            self.assertEqual(row["argv"], [sys.executable, "-u", str(fixture.script), row["artifact_dir"]])
            self.assertEqual(observed["argv"], row["argv"][2:])
            self.assertEqual(observed["cwd"], str(REPOSITORY))
            self.assertEqual(observed["python"], sys.executable)
            self.assertEqual(Path(observed["import"]), REPOSITORY / "mads/context.py")
            self.assertEqual(observed["pid"], row["pid"])
            self.assertEqual(observed["optimize"], 0)
            self.assertEqual(observed["environment"]["PYTHONPATH"], str(REPOSITORY))
            self.assertNotIn("UNRELATED_SECRET_SENTINEL", observed["environment"])
            self.assertEqual(observed["environment"]["HOME"], str(Path(row["directory"]) / "home"))
            self.assertEqual(row["exit_code"], 0)
            self.assertGreater(row["duration_seconds"], 0)
            self.assertTrue(row["started_at"])
            self.assertTrue(row["finished_at"])
            self.assertEqual(row["cleanup"]["errors"], [])
            self.assertTrue(row["cleanup"]["leader_reaped"])
        self.assertIn("one: passed", console)
        self.assertIn(str(output / "summary.json"), console)

    def test_full_binary_logs_and_no_arg_script(self):
        fixture = self.fixture("binary", """
            import sys
            assert len(sys.argv) == 1, sys.argv
            sys.stdout.buffer.write(bytes(range(256)) * 2048)
            sys.stderr.buffer.write(bytes(reversed(range(256))) * 1024)
        """)
        code, console = self.run_fixtures({"binary": fixture})
        self.assertEqual(code, 0, console)
        row = self.summary()["checks"][0]
        self.assertEqual(Path(row["stdout_log"]).read_bytes(), bytes(range(256)) * 2048)
        self.assertEqual(Path(row["stderr_log"]).read_bytes(), bytes(reversed(range(256))) * 1024)
        self.assertIsNone(row["artifact_dir"])
        self.assertEqual(row["argv"], [sys.executable, "-u", str(fixture.script)])

    def test_failure_then_success_preserves_both_without_retry(self):
        count = self.root / "attempts"
        fail = self.fixture("fail", f"""
            import sys
            from pathlib import Path
            with Path({str(count)!r}).open('a') as f:
                f.write('attempt\\n')
            print('failed stdout')
            print('failed stderr', file=sys.stderr)
            sys.exit(7)
        """)
        good = self.fixture("good", "print('good evidence')")
        code, console = self.run_fixtures({"fail": fail, "good": good})
        self.assertEqual(code, 1)
        summary = self.summary()
        self.assertEqual(summary["status"], "failed")
        self.assertEqual(summary["runner_exit_code"], 1)
        self.assertEqual([row["status"] for row in summary["checks"]], ["failed", "passed"])
        bad_row, good_row = summary["checks"]
        self.assertEqual(bad_row["exit_code"], 7)
        self.assertIsNone(bad_row["launch_error"])
        self.assertEqual(Path(bad_row["stdout_log"]).read_bytes(), b"failed stdout\n")
        self.assertEqual(Path(bad_row["stderr_log"]).read_bytes(), b"failed stderr\n")
        self.assertEqual(Path(good_row["stdout_log"]).read_bytes(), b"good evidence\n")
        self.assertEqual(count.read_text(), "attempt\n")
        self.assertIn("good: passed", console)

    def test_launch_failure_then_success(self):
        bad = self.fixture("bad_launch", "raise AssertionError('not reached')")
        good = self.fixture("good", "print('after launch failure')")
        real_popen = subprocess.Popen

        def missing_executable(argv, **kwargs):
            # A real OS launch failure, not a script exiting 2 for a missing file.
            # Limit this fault injection to the first fixture's executable lookup.
            if argv[2] == str(bad.script):
                kwargs["executable"] = str(self.root / "nonexistent-python")
            return real_popen(argv, **kwargs)

        with patch.object(runner.subprocess, "Popen", side_effect=missing_executable) as launch:
            code, console = self.run_fixtures({"bad": bad, "good": good})
        self.assertEqual(code, 1)
        self.assertEqual(launch.call_count, 2)
        bad_row, good_row = self.summary()["checks"]
        self.assertEqual(bad_row["status"], "launch_failed")
        self.assertIsNone(bad_row["exit_code"])
        self.assertIsNone(bad_row["pid"])
        self.assertEqual(bad_row["launch_error"]["type"], "FileNotFoundError")
        self.assertIn("nonexistent-python", bad_row["launch_error"]["message"])
        self.assertGreater(bad_row["duration_seconds"], 0)
        self.assertEqual(Path(bad_row["stdout_log"]).read_bytes(), b"")
        self.assertEqual(Path(bad_row["stderr_log"]).read_bytes(), b"")
        self.assertEqual(good_row["status"], "passed")
        self.assertEqual(Path(good_row["stdout_log"]).read_bytes(), b"after launch failure\n")
        self.assertIn("launch_failed", console)

    def test_assertions_not_disabled_and_missing_script_is_completed_failure(self):
        assertion = self.fixture("assertion", "assert False, 'assertions remain enabled'")
        missing = runner.Check(self.root / "does not exist.py", "missing script")
        good = self.fixture("good", "print('still ran')")
        with patch.dict(os.environ, {"PYTHONOPTIMIZE": "2"}):
            code, _ = self.run_fixtures({"assertion": assertion, "missing": missing, "good": good})
        self.assertEqual(code, 1)
        rows = self.summary()["checks"]
        self.assertEqual([r["status"] for r in rows], ["failed", "failed", "passed"])
        self.assertIn(b"assertions remain enabled", Path(rows[0]["stderr_log"]).read_bytes())
        self.assertEqual(rows[1]["exit_code"], 2)
        self.assertIsNone(rows[1]["launch_error"])

    def fixture_driver(self, checks):
        driver = self.root / "driver.py"
        mapping = ",\n".join(f"{name!r}: runner.Check(Path({str(check.script)!r}), 'fixture', {check.artifact_dir!r})"
                             for name, check in checks.items())
        driver.write_text("from pathlib import Path\nimport sys\n"
                          f"sys.path.insert(0, {str(REPOSITORY)!r})\n"
                          "from experiments import run_checks as runner\n"
                          f"checks = {{{mapping}}}\n"
                          "sys.exit(runner.main(checks=checks, defaults=tuple(checks)))\n")
        return driver

    def test_cli_defaults_subset_and_relative_output_from_arbitrary_cwd(self):
        skipped_marker = self.root / "skipped"
        checks = {"one": self.fixture("one", f"from pathlib import Path; Path({str(skipped_marker)!r}).touch()"),
                  "two": self.fixture("two", "import mads.context; print('two')")}
        driver = self.fixture_driver(checks)
        for names, expected in [(["two"], ["two"]), ([], ["one", "two"])]:
            output = self.cwd / ("subset" if names else "default")
            result = subprocess.run([sys.executable, str(driver), *names, "--output", output.name],
                                    cwd=self.cwd, env={"PATH": os.defpath}, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(self.summary(output)["selected"], expected)
            self.assertEqual(skipped_marker.exists(), not names)

    def wait_for_file(self, path, proc):
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            if path.exists():
                return
            if proc.poll() is not None:
                self.fail(f"runner exited {proc.returncode} before readiness file {path}")
            time.sleep(0.02)
        self.fail(f"readiness timeout: {path}")

    def assert_not_executing(self, pid):
        # Orphan zombies may await container PID 1; distinguish them from leaks.
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                return
            stat = Path(f"/proc/{pid}/stat")
            try:
                if stat.read_text().rsplit(")", 1)[1].split()[0] == "Z":
                    return
            except FileNotFoundError:
                pass
            time.sleep(0.02)
        self.fail(f"process {pid} is still executing")

    def test_interruption_cleans_process_group_and_preserves_partial_summary(self):
        for first_signal in (signal.SIGINT, signal.SIGTERM):
            with self.subTest(signal=first_signal):
                output = self.root / f"interrupt-{first_signal}"
                slow = self.fixture("slow", """
                    import json, os, signal, subprocess, sys, time
                    from pathlib import Path
                    root = Path(sys.argv[1]); root.mkdir()
                    signal.signal(signal.SIGINT, signal.SIG_IGN)
                    signal.signal(signal.SIGTERM, signal.SIG_IGN)
                    child_source = (
                        'import signal, sys, time; from pathlib import Path; '
                        'signal.signal(signal.SIGINT, signal.SIG_IGN); '
                        'signal.signal(signal.SIGTERM, signal.SIG_IGN); '
                        'Path(sys.argv[1]).touch(); time.sleep(60)'
                    )
                    child = subprocess.Popen([sys.executable, '-c', child_source, str(root / 'child-ready')])
                    while not (root / 'child-ready').exists(): time.sleep(0.01)
                    print('slow stdout', flush=True)
                    print('slow stderr', file=sys.stderr, flush=True)
                    (root / 'ready.tmp').write_text(json.dumps([os.getpid(), child.pid]))
                    (root / 'ready.tmp').rename(root / 'ready.json')
                    time.sleep(60)
                """, artifacts=True)
                after_marker = self.root / f"after-{first_signal}"
                checks = {"before": self.fixture("before", "print('before evidence')"), "slow": slow,
                          "after": self.fixture("after", f"from pathlib import Path; Path({str(after_marker)!r}).touch()")}
                driver = self.fixture_driver(checks)
                proc = subprocess.Popen([sys.executable, str(driver), "--output", str(output)], cwd=self.cwd,
                                        env={"PATH": os.defpath}, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                        start_new_session=True)
                pids = []
                try:
                    ready = output / "02-slow/artifacts/ready.json"
                    self.wait_for_file(ready, proc)
                    pids = json.loads(ready.read_text())
                    in_progress = self.summary(output)
                    self.assertEqual([r["status"] for r in in_progress["checks"]], ["passed", "running", "pending"])
                    self.assertIsNone(in_progress["checks"][1]["exit_code"])
                    proc.send_signal(first_signal)
                    time.sleep(0.1)
                    proc.send_signal(signal.SIGINT)  # Repeated interrupts must not abort cleanup.
                    stdout, stderr = proc.communicate(timeout=15)
                    self.assertEqual(proc.returncode, 128 + first_signal, stderr.decode())
                    summary = self.summary(output)
                    self.assertEqual(summary["status"], "interrupted")
                    self.assertEqual(summary["runner_exit_code"], 128 + first_signal)
                    self.assertEqual(summary["interruption_signals"], [first_signal, signal.SIGINT])
                    before, current, after = summary["checks"]
                    self.assertEqual([r["status"] for r in summary["checks"]], ["passed", "interrupted", "skipped"])
                    self.assertEqual(current["exit_code"], -signal.SIGKILL)
                    self.assertEqual(current["cleanup"]["signals_sent"], ["SIGINT", "SIGTERM", "SIGKILL"])
                    self.assertTrue(current["cleanup"]["leader_reaped"])
                    self.assertEqual(current["cleanup"]["errors"], [])
                    self.assertEqual(Path(before["stdout_log"]).read_bytes(), b"before evidence\n")
                    self.assertEqual(Path(current["stdout_log"]).read_bytes(), b"slow stdout\n")
                    self.assertEqual(Path(current["stderr_log"]).read_bytes(), b"slow stderr\n")
                    self.assertIsNone(after["exit_code"])
                    self.assertIsNone(after["duration_seconds"])
                    self.assertIsNone(after["pid"])
                    self.assertFalse(Path(after["directory"]).exists())
                    self.assertFalse(after_marker.exists())
                    for pid in pids:
                        self.assert_not_executing(pid)
                finally:
                    # Test failures must not leak the fixture or its runner.
                    if pids:
                        try: os.killpg(pids[0], signal.SIGKILL)
                        except ProcessLookupError: pass
                    self.stop_fixture_driver(proc, output)

    def test_graceful_interruption_with_zero_exit_is_not_passing(self):
        slow = self.fixture("graceful", """
            import signal, sys, time
            from pathlib import Path
            root = Path(sys.argv[1]); root.mkdir()
            def stop(signum, frame):
                sys.exit(0)
            signal.signal(signal.SIGINT, stop)
            try:
                (root / 'ready').touch()
                time.sleep(60)
            finally:
                (root / 'cleaned').touch()
                print('graceful cleanup', flush=True)
        """, artifacts=True)
        checks = {"graceful": slow, "later": self.fixture("later", "raise AssertionError('not reached')")}
        driver = self.fixture_driver(checks)
        proc = subprocess.Popen([sys.executable, str(driver), "--output", str(self.output)], cwd=self.cwd,
                                env={"PATH": os.defpath}, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                start_new_session=True)
        try:
            artifacts = self.output / "01-graceful/artifacts"
            self.wait_for_file(artifacts / "ready", proc)
            proc.send_signal(signal.SIGINT)
            _, stderr = proc.communicate(timeout=15)
            self.assertEqual(proc.returncode, 130, stderr.decode())
            summary = self.summary()
            self.assertEqual(summary["status"], "interrupted")
            current, later = summary["checks"]
            self.assertEqual(current["exit_code"], 0)
            self.assertEqual(current["status"], "interrupted")
            self.assertEqual(later["status"], "skipped")
            self.assertEqual(current["cleanup"]["signals_sent"], ["SIGINT"])
            self.assertTrue((artifacts / "cleaned").exists())
            self.assertEqual(Path(current["stdout_log"]).read_bytes(), b"graceful cleanup\n")
            self.assert_not_executing(current["pid"])
        finally:
            self.stop_fixture_driver(proc, self.output)

    def test_normal_exit_also_cleans_leftover_group_and_logs(self):
        child_script = self.fixture("leftover", """
            import signal, sys, time
            from pathlib import Path
            def stop(signum, frame):
                print('child cleanup', flush=True)
                sys.exit(0)
            signal.signal(signal.SIGINT, stop)
            Path(sys.argv[1]).touch()
            time.sleep(60)
        """)
        leader = self.fixture("leader", f"""
            import json, os, subprocess, sys, time
            from pathlib import Path
            root = Path(sys.argv[1]); root.mkdir()
            child = subprocess.Popen([sys.executable, '-u', {str(child_script.script)!r}, str(root / 'child-ready')])
            while not (root / 'child-ready').exists(): time.sleep(0.01)
            (root / 'pids.json').write_text(json.dumps([os.getpid(), child.pid]))
            print('leader finished', flush=True)
        """, artifacts=True)
        driver = self.fixture_driver({"leader": leader})
        proc = subprocess.Popen([sys.executable, str(driver), "--output", str(self.output)], cwd=self.cwd,
                                env={"PATH": os.defpath}, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                start_new_session=True)
        try:
            _, stderr = proc.communicate(timeout=15)
            self.assertEqual(proc.returncode, 0, stderr.decode())
            row = self.summary()["checks"][0]
            self.assertEqual(row["status"], "passed")
            self.assertEqual(row["exit_code"], 0)
            self.assertIn("SIGINT", row["cleanup"]["signals_sent"])
            self.assertTrue(row["cleanup"]["leader_reaped"])
            self.assertEqual(row["cleanup"]["errors"], [])
            self.assertEqual(Path(row["stdout_log"]).read_bytes(), b"leader finished\nchild cleanup\n")
            for pid in json.loads((Path(row["artifact_dir"]) / "pids.json").read_text()):
                self.assert_not_executing(pid)
        finally:
            self.stop_fixture_driver(proc, self.output)

    def stop_fixture_driver(self, proc, output):
        # Even a failed readiness/assertion should not leave a fixture executing.
        try:
            rows = self.summary(output)["checks"]
        except (OSError, ValueError):
            rows = []
        for row in rows:
            if row["pid"] is not None:
                try:
                    os.killpg(row["pid"], signal.SIGKILL)
                except ProcessLookupError:
                    pass
        if proc.poll() is None:
            proc.kill()
        proc.communicate(timeout=5)

    def test_interrupt_at_launch_boundary_still_owns_child(self):
        slow = self.fixture("slow", "import time; time.sleep(60)")
        good = self.fixture("later", "raise AssertionError('must not run')")
        real_popen = subprocess.Popen
        children = []

        def interrupt_during_popen(*args, **kwargs):
            proc = real_popen(*args, **kwargs)
            children.append(proc)
            os.kill(os.getpid(), signal.SIGINT)
            return proc

        with patch.object(runner.subprocess, "Popen", side_effect=interrupt_during_popen):
            code, _ = self.run_fixtures({"slow": slow, "later": good})
        self.assertEqual(code, 130)
        current, later = self.summary()["checks"]
        self.assertEqual(current["status"], "interrupted")
        self.assertIsNotNone(current["exit_code"])
        self.assertEqual(later["status"], "skipped")
        self.assertEqual(len(children), 1)
        self.assertIsNotNone(children[0].poll())

    def test_runner_error_retains_finished_checks_and_marks_incomplete(self):
        checks = {"first": self.fixture("first", "print('finished')"),
                  "second": self.fixture("second", "print('not launched')"),
                  "third": self.fixture("third", "print('not launched either')")}
        real_execute = runner.execute

        def broken_log(row, *args):
            if row["name"] == "second":
                raise OSError("injected log creation failure")
            return real_execute(row, *args)

        with patch.object(runner, "execute", side_effect=broken_log):
            code, _ = self.run_fixtures(checks)
        summary = self.summary()
        self.assertEqual(code, 1)
        self.assertEqual(summary["status"], "error")
        self.assertIn("log creation failure", summary["runner_error"]["message"])
        self.assertEqual([r["status"] for r in summary["checks"]], ["passed", "incomplete", "skipped"])
        self.assertEqual(Path(summary["checks"][0]["stdout_log"]).read_bytes(), b"finished\n")


if __name__ == "__main__":
    unittest.main()
