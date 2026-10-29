"""Owned process groups; status files report lifecycle state but never control it."""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, field
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import threading
import time

from .job_output import OutputCapture


def _proc_record(pid: int) -> dict:
    """Read kernel process facts; comm may contain spaces and parentheses."""
    with open(f"/proc/{pid}/stat", encoding="utf-8", errors="replace") as stream:
        raw = stream.read(4096)
    prefix, separator, tail = raw.rpartition(") ")
    head, opening, name = prefix.partition(" (")
    fields = tail.split()
    if not separator or not opening or int(head) != pid or len(fields) < 20:
        raise ValueError("unrecognized /proc stat record")
    return {"pid": pid, "name": name, "state": fields[0], "group": int(fields[2]),
            "session": int(fields[3]), "cpu_ticks": int(fields[11]) + int(fields[12]),
            "start_ticks": int(fields[19])}


@dataclass
class Job:
    id: str
    command: str
    process: subprocess.Popen | None
    log: Path
    status: Path
    started: float
    timeout_ms: int | None  # Persisted historical field; bash exposes kill_after_ms.
    cwd: str = ""
    pid: int = 0
    process_start_ticks: int | None = None  # Linux identity sampled before the waiter can reap it.
    state: str = "running"
    exit_code: int | None = None
    finished: float | None = None
    status_error: str | None = None
    cleanup_error: str | None = None
    finish_state: str | None = None  # First cleanup cause survives retries after shell exit.
    revision: int = 0
    persisted_revision: int = -1
    output: object = None
    reported_bytes: int = 0
    done: threading.Event = field(default_factory=threading.Event)
    lock: object = field(default_factory=threading.RLock)
    observation_lock: object = field(default_factory=threading.Lock)
    persistence_lock: object = field(default_factory=threading.Lock)


def stop_process(process: subprocess.Popen) -> None:
    """Reap the shell and stop remaining descendants, including after normal exit."""
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        process.wait()
        return
    try:
        process.wait(timeout=0.2)
    except subprocess.TimeoutExpired:
        pass
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    process.wait()


@contextmanager
def _defer_parent_sigterm():
    previous = signal.getsignal(signal.SIGTERM)
    if threading.current_thread() is not threading.main_thread() or not callable(previous):
        yield
        return
    pending = []
    def defer(signum, frame):
        pending[:] = [(signum, frame)]
    # A caught disposition resets at child exec; no inherited signal mask changes.
    signal.signal(signal.SIGTERM, defer)
    try:
        yield
    finally:
        signal.signal(signal.SIGTERM, previous)
        if pending:
            previous(*pending[0])


class Jobs:
    def __init__(self, root: Path, directory: Path):
        self.root, self.directory = root, directory
        self.records: dict[str, Job] = {}
        self.active: dict[str, Job] = {}
        self.threads: dict[str, threading.Thread] = {}
        self.lock = threading.Lock()
        self.pending_status: set[str] = set()
        self.status_writer: threading.Thread | None = None
        self.status_stopping = False

    def _schedule_status(self, job: Job) -> None:
        """Coalesce optional reports on one writer, outside solving/lifecycle paths."""
        failure = None
        with self.lock:
            self.pending_status.add(job.id)
            if self.status_writer is not None or self.status_stopping:
                return
            writer = threading.Thread(target=self._write_status, daemon=True)
            self.status_writer = writer
            try:
                writer.start()
            except RuntimeError as error:
                self.status_writer = None
                failure = error
        if failure:
            with job.lock:
                job.status_error = f"Optional status writer could not start: {failure}"

    def _write_status(self) -> None:
        while True:
            with self.lock:
                if self.status_stopping or not self.pending_status:
                    self.status_writer = None
                    return
                job = self.records[self.pending_status.pop()]
            self._persist(job)

    def record(self, job: Job) -> dict:
        with job.lock:
            return {"job_id": job.id, "command": job.command, "cwd": job.cwd or str(self.root), "pid": job.pid,
                    "state": job.state, "exit_code": job.exit_code,
                    "elapsed_seconds": round((job.finished or time.monotonic()) - job.started, 3),
                    "timeout_ms": job.timeout_ms, "log_path": None if job.output else str(job.log), "status_path": str(job.status),
                    "status_error": job.status_error, "cleanup_error": job.cleanup_error,
                    "status_pending": job.persisted_revision != job.revision}

    def process_snapshot(self, job: Job) -> dict:
        """Bounded observation of a live owned session, never a progress verdict."""
        def unavailable(reason):
            return {"status": "unavailable", "reason": reason}
        if not sys.platform.startswith("linux"):
            return unavailable("Linux /proc process observations are unavailable on this platform")
        with job.lock:
            process = job.process
            if job.state != "running" or process is None or process.poll() is not None:
                return unavailable("the owned process is no longer running")
            pid = process.pid
            start_ticks = job.process_start_ticks
        if start_ticks is None:
            return unavailable("the process start identity was unavailable at launch")
        try:
            ticks = os.sysconf("SC_CLK_TCK")
            leader = _proc_record(pid)
            if (ticks <= 0 or leader["start_ticks"] != start_ticks
                    or leader["group"] != pid or leader["session"] != pid):
                return unavailable("the live process-group/session identity could not be confirmed")
            rows, scanned, unreadable, capped = [leader], 0, 0, False
            with os.scandir("/proc") as entries:
                for entry in entries:
                    if scanned >= 4096 or len(rows) >= 16:
                        capped = True
                        break
                    scanned += 1
                    if not entry.name.isdecimal() or int(entry.name) == pid:
                        continue
                    try:
                        row = _proc_record(int(entry.name))
                    except (OSError, ValueError):
                        unreadable += 1
                        continue
                    if row["group"] == pid and row["session"] == pid:
                        rows.append(row)
            current = _proc_record(pid)
            if (current["start_ticks"], current["group"], current["session"]) != (leader["start_ticks"], pid, pid):
                return unavailable("the process identity changed during observation")
            with job.lock:
                if job.process is not process or job.state != "running" or process.poll() is not None:
                    return unavailable("the owned process completed during observation")
            return {"status": "observed", "observed_at": time.time(), "process_group": pid,
                    "session": pid, "entries_scanned": scanned, "unreadable_entries": unreadable,
                    "capped": capped, "processes": [
                        {"pid": row["pid"], "name": row["name"], "state": row["state"],
                         "cpu_seconds": round(row["cpu_ticks"] / ticks, 3)} for row in rows]}
        except (OSError, ValueError) as error:
            return unavailable(f"process snapshot could not be read: {type(error).__name__}: {error}")

    def _persist(self, job: Job) -> None:
        # A current writer refreshes any newer revision; other callers never wait
        # on status IO. No filesystem IO holds the lifecycle lock.
        if not job.persistence_lock.acquire(blocking=False):
            return
        locked = True
        try:
            while True:
                with job.lock:
                    record, revision = self.record(job), job.revision
                record.update(status_error=None, status_pending=False)
                try:
                    temporary = job.status.with_suffix(".tmp")
                    temporary.write_text(json.dumps(record), encoding="utf-8")
                    temporary.replace(job.status)
                except (OSError, UnicodeError, ValueError) as error:
                    with job.lock:
                        job.status_error = str(error)
                    return
                with job.lock:
                    job.persisted_revision, job.status_error = revision, None
                    if revision == job.revision:
                        # Release while lifecycle changes are excluded: a new
                        # revision can then obtain the writer instead of being lost.
                        job.persistence_lock.release()
                        locked = False
                        return
        finally:
            if locked:
                job.persistence_lock.release()

    def _finish(self, job: Job, state: str) -> None:
        try:
            with job.lock:
                process = job.process
                if process is None:
                    return
                if job.finish_state is None:
                    job.finish_state = state if process.poll() is None else "completed"
                try:
                    stop_process(process)
                except (OSError, subprocess.SubprocessError) as error:
                    job.cleanup_error = str(error)
                    job.exit_code = process.poll()
                else:
                    job.state, job.exit_code = job.finish_state, process.returncode
                    job.finished, job.process, job.cleanup_error = time.monotonic(), None, None
                    with self.lock:
                        self.active.pop(job.id, None)
                job.revision += 1
                process = None
                job.done.set()  # Completion is observable even if status IO blocks.
        finally:
            job.done.set()

    def _watch(self, job: Job) -> None:
        state = "completed"
        try:
            try:
                process = job.process
                if process is not None:
                    process.wait(timeout=None if job.timeout_ms is None else
                                 max(0, job.timeout_ms / 1000 - (time.monotonic() - job.started)))
            except subprocess.TimeoutExpired:
                state = "timed_out"
            finally:
                process = None
                self._finish(job, state)
                self._schedule_status(job)
        finally:
            with self.lock:
                self.threads.pop(job.id, None)

    def start(self, command: str, timeout_ms: int | None, *, cwd=None) -> Job:
        with self.lock:
            self.status_stopping = False
        ident = str(len(self.records) + 1)
        log, status = self.directory / f"{ident}.log", self.directory / f"{ident}.status.json"
        while log.exists() or status.exists() or (self.directory.parent / "out" / f"job-{ident}.data").exists():
            ident = str(int(ident) + 1)
            log, status = self.directory / f"{ident}.log", self.directory / f"{ident}.status.json"
        started = time.monotonic()
        output = OutputCapture(ident, self.directory.parent / "out", log)
        with _defer_parent_sigterm():
            process = subprocess.Popen(["bash", "-o", "pipefail", "-c", command], cwd=cwd or self.root, stdin=subprocess.DEVNULL,
                                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT, start_new_session=True, bufsize=0)
            try:
                job = Job(ident, command, process, log, status, started, timeout_ms, cwd=str(cwd or self.root), pid=process.pid, output=output)
                if sys.platform.startswith("linux"):
                    try:
                        job.process_start_ticks = _proc_record(process.pid)["start_ticks"]
                    except (OSError, ValueError):
                        pass  # Optional observations never prevent supervision of a launched job.
                thread = threading.Thread(target=self._watch, args=(job,), daemon=True)
                with self.lock:
                    self.records[ident] = self.active[ident] = job
                    self.threads[ident] = thread
                thread.start()
                output.start(process.stdout)
            except BaseException:
                # Even failed supervisor construction cannot leave a launched group.
                if ident in self.records:
                    self._finish(self.records[ident], "terminated")
                else:
                    stop_process(process)
                with self.lock:
                    self.threads.pop(ident, None)
                if process.stdout and not output.thread:
                    process.stdout.close()
                    output.error = "Output collector was not running after launch setup failed; capture is incomplete"
                    output.done.set()
                raise
        process = None
        self._schedule_status(job)
        return job

    def get(self, ident: str) -> Job:
        if ident not in self.records:
            raise ValueError(f"Unknown job {ident!r}; {len(self.records)} jobs recorded. Use list_jobs(start=1) to find an ID.")
        return self.records[ident]

    def stop(self, job: Job) -> None:
        self._finish(job, "terminated")
        self._schedule_status(job)

    def running(self) -> list[Job]:
        with self.lock:
            return list(self.active.values())

    def close(self) -> None:
        errors = []
        for job in self.running():
            self.stop(job)
            if job.cleanup_error:
                errors.append(f"job {job.id}: {job.cleanup_error}")
        with self.lock:
            threads = list(self.threads.values())
        for thread in threads:
            thread.join(timeout=1)
        # Optional IO may remain blocked; never join it on the solving path.
        # The writer stops after its current write; pending facts stay in records.
        with self.lock:
            self.status_stopping = True
            writer = self.status_writer
        if writer:
            writer.join(timeout=0.01)
        if errors:
            raise OSError("Job cleanup encountered errors after attempting every owned process group: " + "; ".join(errors))
