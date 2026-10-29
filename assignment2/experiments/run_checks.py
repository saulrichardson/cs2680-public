"""Run a reviewed set of offline regression checks, never discover scripts by glob.

See docs/offline-checks.md. The runner is stdlib-only; the selected checks may
require the harness's installed dependencies. This is an allowlist, not a sandbox.
"""
from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shlex
import signal
import subprocess
import sys
import time
from typing import Mapping, Sequence


REPOSITORY = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class Check:
    script: Path
    description: str
    artifact_dir: bool = False


# Reviewed local fixtures only. Scripts outside this catalog require an explicit
# invocation with their own inputs or isolated environment.
CHECKS = {
    "context_tail": Check(Path("checks/context_tail.py"), "Tail/account layout, local protocol fixtures", True),
    "context_inspection": Check(Path("checks/context_inspection.py"), "Context inspection, retention and local jobs", True),
    "checkpoint": Check(Path("checks/checkpoint.py"), "Checkpoint effects and staged context adoption"),
    "navigation": Check(Path("checks/navigation.py"), "Snapshot search and guarded file edits"),
    "evidence_versions": Check(Path("checks/evidence_versions.py"), "Versioned evidence and immutable recovery"),
    "evidence_delivery": Check(Path("checks/evidence_delivery.py"), "First delivery and exact-retention fixtures", True),
    "retention_recovery": Check(Path("checks/retention_recovery.py"), "Oversized retention and recovery fixtures", True),
    "compact_receipts": Check(Path("checks/compact_receipts.py"), "Compact receipts and exact content recovery", True),
    "jobs_sampling": Check(Path("checks/jobs_sampling.py"), "Process sampling, capture and cleanup"),
    "review_affordances": Check(Path("checks/review_affordances.py"), "Local stdin accounting and unsent-byte recovery", True),
    "inspection": Check(Path("checks/inspection.py"), "Read-only journal inspector and exact export"),
    "workspace_snapshot": Check(Path("checks/workspace_snapshot.py"), "Workspace snapshot provenance and safe refusal"),
}
DEFAULT_CHECKS = tuple(CHECKS)
# Optional mechanical checks; scripts needing external inputs or isolation stay excluded.
CHECKS.update({
    name: Check(Path('checks')/(name+'.py'), description, True)
    for name, description in (
        ('boundaries','Filesystem/process and presentation boundaries'),
        ('context_boundaries','Context fit and replay boundaries'),
        ('context_candidates','Staged policy/restart candidates'),
        ('mask_restoration','Reversible masks in both native protocols'),
        ('presentation','UTF-8 coordinates and causal retrieval'),
        ('responses_protocol','Local Responses adapter and SDK transport'),
        ('responses_audit','Independent Responses replay/tamper audit'),
        ('export','Cleanup-gated Git export including partial work'),
    )
})


def command(check: Check, artifact_dir: Path | str) -> list[str]:
    argv = [sys.executable, "-u", str(REPOSITORY / check.script)]
    if check.artifact_dir:
        argv.append(str(artifact_dir))
    return argv


def validate(names: Sequence[str], output: Path, checks: Mapping[str, Check]) -> None:
    # Validate the WHOLE selection before even creating output parents.
    unknown = [name for name in names if name not in checks]
    if unknown:
        raise ValueError("Unknown check(s): " + ", ".join(unknown) + "; use --list")
    duplicates = [name for name, count in Counter(names).items() if count > 1]
    if duplicates:
        raise ValueError("Duplicate check(s): " + ", ".join(duplicates))
    if not names:
        raise ValueError("No checks selected")
    if os.path.lexists(output):  # Also refuse files and dangling symlinks.
        raise ValueError(f"Output destination already exists; refusing overwrite: {output}")
    if os.name != "posix":
        raise ValueError("Offline checks require POSIX process groups and local bash")


def child_environment(directory: Path) -> dict[str, str]:
    # Do not forward host credentials, Python startup/optimization settings,
    # shell profiles or an ambient PYTHONPATH. No credential values are read.
    return {
        "PATH": os.pathsep.join((str(Path(sys.executable).parent), os.defpath)),
        "HOME": str(directory / "home"),
        "TMPDIR": str(directory / "tmp"),
        "PYTHONPATH": str(REPOSITORY),
        "PYTHONUNBUFFERED": "1",
        "PYTHONUTF8": "1",
        "PYTHONNOUSERSITE": "1",
        "PYTHONDONTWRITEBYTECODE": "1",
    }


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def save_summary(output: Path, summary: dict) -> None:
    # Readers see a complete old or new JSON document, not a half-written one.
    temporary = output / ".summary.json.tmp"
    with temporary.open("w", encoding="utf-8") as stream:
        json.dump(summary, stream, ensure_ascii=True, indent=2)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, output / "summary.json")


def announce(message: str) -> None:
    try:
        print(message, flush=True)
    except BrokenPipeError:
        # A closed console must not discard evidence or orphan the child.
        pass


class Interrupts:
    """Record signals without throwing across Popen's process-ownership boundary."""

    def __init__(self) -> None:
        self.received: list[int] = []
        self.previous: dict = {}

    @property
    def signum(self) -> int | None:
        return self.received[0] if self.received else None

    def receive(self, signum, frame) -> None:
        self.received.append(signum)

    def __enter__(self):
        for signum in (signal.SIGINT, signal.SIGTERM):
            self.previous[signum] = signal.signal(signum, self.receive)
        return self

    def __exit__(self, *exc):
        for signum, handler in self.previous.items():
            signal.signal(signum, handler)


def clean_process_group(proc: subprocess.Popen) -> dict:
    """Give Python finally blocks time to run, then escalate for the whole group.

    Called even after a normal leader exit: it may have left children holding the
    log files. A killed orphan can remain a zombie until its own parent reaps it;
    killpg(0) alone therefore cannot prove that a group is still executing.
    """
    result = {"process_group": proc.pid, "signals_sent": [], "errors": [],
              "leader_reaped": False}

    def exists() -> bool:
        proc.poll()  # Reap the leader if possible before probing the group.
        try:
            os.killpg(proc.pid, 0)
            return True
        except ProcessLookupError:
            return False
        except OSError as exc:
            result["errors"].append(f"probe: {exc}")
            return True

    for signum, grace in ((signal.SIGINT, 1.0), (signal.SIGTERM, 1.0), (signal.SIGKILL, 0.0)):
        if not exists():
            break
        try:
            os.killpg(proc.pid, signum)
            result["signals_sent"].append(signal.Signals(signum).name)
        except ProcessLookupError:
            break
        except OSError as exc:
            result["errors"].append(f"{signal.Signals(signum).name}: {exc}")
        deadline = time.monotonic() + grace
        while time.monotonic() < deadline and exists():
            time.sleep(0.05)
    try:
        proc.wait(timeout=3)
        result["leader_reaped"] = True
    except subprocess.TimeoutExpired:
        result["errors"].append("leader did not exit after process-group cleanup")
    return result


def execute(row: dict, output: Path, summary: dict, interrupts: Interrupts) -> None:
    directory = Path(row["directory"])
    directory.mkdir()
    (directory / "home").mkdir()
    (directory / "tmp").mkdir()
    # Do NOT create directory/artifacts: several checks insist on a new path.
    start = time.monotonic()
    proc = None
    row.update(status="running", started_at=now())
    save_summary(output, summary)
    try:
        with Path(row["stdout_log"]).open("xb") as stdout, Path(row["stderr_log"]).open("xb") as stderr:
            if interrupts.signum is not None:
                row.update(status="skipped", reason="interrupted before launch")
                return
            try:
                proc = subprocess.Popen(
                    row["argv"], cwd=row["cwd"], env=row["environment"],
                    stdin=subprocess.DEVNULL, stdout=stdout, stderr=stderr,
                    start_new_session=True, shell=False,
                )
            except (OSError, ValueError) as exc:
                row.update(status="launch_failed", launch_error={
                    "type": type(exc).__name__, "message": str(exc),
                })
                return
            row["pid"] = proc.pid
            try:
                save_summary(output, summary)
                while proc.poll() is None and interrupts.signum is None:
                    time.sleep(0.05)
            finally:
                row["cleanup"] = clean_process_group(proc)
                row["exit_code"] = proc.returncode
            if interrupts.signum is not None:
                row.update(status="interrupted", reason="runner received a signal")
            elif proc.returncode == 0 and not row["cleanup"]["errors"]:
                row["status"] = "passed"
            else:
                row["status"] = "failed"
    finally:
        row["finished_at"] = now()
        row["duration_seconds"] = time.monotonic() - start


def run_checks(names: Sequence[str], output: Path, *, checks: Mapping[str, Check] = CHECKS) -> int:
    """Run validated checks. Registry injection is for local unit fixtures, not CLI plugins."""
    output = Path(output).absolute()
    validate(names, output, checks)
    rows = []
    for index, name in enumerate(names, 1):
        check = checks[name]
        directory = output / f"{index:02d}-{name}"
        rows.append({
            "name": name, "status": "pending", "argv": command(check, directory / "artifacts"),
            "cwd": str(REPOSITORY), "environment": child_environment(directory),
            "directory": str(directory),
            "artifact_dir": str(directory / "artifacts") if check.artifact_dir else None,
            "stdout_log": str(directory / "stdout.log"), "stderr_log": str(directory / "stderr.log"),
            "pid": None, "exit_code": None, "launch_error": None,
            "started_at": None, "finished_at": None, "duration_seconds": None,
            "cleanup": None,
        })
    summary = {
        "schema_version": 1, "status": "running", "repository": str(REPOSITORY),
        "python": sys.executable, "python_version": sys.version, "output": str(output),
        "selected": list(names), "started_at": now(), "finished_at": None,
        "duration_seconds": None, "runner_exit_code": None,
        "interruption_signals": [], "runner_error": None, "checks": rows,
    }
    start = time.monotonic()
    with Interrupts() as interrupts:
        # exist_ok=False closes the normal preflight/mkdir race without reuse.
        output.mkdir(parents=True, exist_ok=False)
        current = None
        try:
            save_summary(output, summary)
            for row in rows:
                if interrupts.signum is not None:
                    break
                current = row
                execute(row, output, summary, interrupts)
                save_summary(output, summary)
                announce(f"{row['name']}: {row['status']} (exit={row['exit_code']}, "
                         f"{row['duration_seconds']:.3f}s)")
                current = None
        except Exception as exc:
            summary["runner_error"] = {"type": type(exc).__name__, "message": str(exc)}
            if current is not None and current["status"] in ("pending", "running"):
                current.update(status="incomplete", reason="runner error; see runner_error")
        finally:
            for row in rows:
                if row["status"] == "pending":
                    row.update(status="skipped", reason="interrupted" if interrupts.signum else "runner error")
            summary["interruption_signals"] = list(interrupts.received)
            if interrupts.signum is not None:
                summary["status"], code = "interrupted", 128 + interrupts.signum
            elif summary["runner_error"] is not None:
                summary["status"], code = "error", 1
            elif all(row["status"] == "passed" for row in rows):
                summary["status"], code = "passed", 0
            else:
                summary["status"], code = "failed", 1
            summary.update(finished_at=now(), duration_seconds=time.monotonic() - start,
                           runner_exit_code=code, counts=dict(Counter(row["status"] for row in rows)))
            save_summary(output, summary)
    announce(f"{summary['status']}: {summary['counts']}\nSummary: {output / 'summary.json'}")
    return code


def main(argv: Sequence[str] | None = None, *, checks: Mapping[str, Check] = CHECKS,
         defaults: Sequence[str] = DEFAULT_CHECKS) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("names", nargs="*", metavar="CHECK", help="allowlisted names, in execution order; omit for defaults")
    parser.add_argument("--list", action="store_true", help="list names and exact command templates without running")
    parser.add_argument("--output", type=Path, metavar="NEW_DIR", help="new evidence directory (required unless --list)")
    args = parser.parse_args(argv)
    if args.list:
        if args.names or args.output is not None:
            parser.error("--list cannot be combined with CHECK names or --output")
        for name, check in checks.items():
            marker = "default" if name in defaults else "optional"
            print(f"{name} [{marker}]: {check.description}\n  {shlex.join(command(check, '<unique-artifact-dir>'))}")
        return 0
    if args.output is None:
        parser.error("--output NEW_DIR is required; use --list to see offline checks")
    try:
        return run_checks(args.names or defaults, args.output, checks=checks)
    except (ValueError, OSError) as exc:
        parser.error(str(exc))


if __name__ == "__main__":
    sys.exit(main())
