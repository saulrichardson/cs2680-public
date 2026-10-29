"""Owned stdout retention and immutable byte extents; no process supervision."""
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import sys
import threading

FORMAT = "job_output_extent_v1"


@contextmanager
def regular(path, mode="rb"):
    flags = (os.O_RDONLY if mode == "rb" else os.O_WRONLY | os.O_APPEND) | os.O_NONBLOCK | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags)
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise ValueError(f"Output evidence must be a regular file: {path}")
        with os.fdopen(descriptor, mode, buffering=0) as stream:
            descriptor = None
            yield stream
    finally:
        if descriptor is not None:
            os.close(descriptor)


def load_extent(path):
    path = Path(path)
    with regular(path) as stream:
        raw = stream.read(4097)
    if len(raw) > 4096:
        raise ValueError("Output extent descriptor is too large")
    record = json.loads(raw)
    if (not isinstance(record, dict) or record.get("format") != FORMAT
            or not isinstance(record.get("data"), str) or not re.fullmatch(r"job-[0-9]+\.data", record["data"])
            or any(type(record.get(key)) is not int or record[key] < 0 for key in ("bytes", "lines"))
            or not isinstance(record.get("sha256"), str) or not re.fullmatch(r"[a-f0-9]{64}", record["sha256"])):
        raise ValueError("Invalid job output extent descriptor")
    return record


@contextmanager
def open_extent(path):
    record = load_extent(path)
    with regular(Path(path).parent / record["data"]) as stream:
        if os.fstat(stream.fileno()).st_size < record["bytes"]:
            raise OSError("Retained output is shorter than its immutable captured extent")
        yield stream, record


def chunks(path):
    """Resolve exact captured bytes, validating the complete explicit retrieval."""
    with open_extent(path) as (stream, record):
        remaining, digest = record["bytes"], hashlib.sha256()
        while remaining:
            chunk = stream.read(min(65536, remaining))
            if not chunk:
                raise OSError("Retained output shrank during recovery")
            remaining -= len(chunk)
            digest.update(chunk)
            yield chunk
        if digest.hexdigest() != record["sha256"]:
            raise OSError("Retained output no longer matches the captured SHA256")


class OutputCapture:
    """One writer owns retained prefixes; stdout never comes from a mutable mirror."""
    def __init__(self, ident, directory, log):
        self.ident, self.directory, self.log = ident, Path(directory), Path(log)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.data = self.directory / f"job-{ident}.data"
        self.data.touch(exist_ok=False)
        with self.log.open("x") as notice:
            notice.write("Live log mirroring is retired. Use job inspect/wait, then read_file on the returned output extent descriptor or its explicit bash recovery command.\n")
        self.lock, self.done = threading.Lock(), threading.Event()
        self.size = self.newlines = 0
        self.last = b""
        self.digest = hashlib.sha256()
        self.error = None
        self.eof = False
        self.thread = None
        self.retained_bytes = self.scanned_bytes = 0

    def start(self, stream):
        self.thread = threading.Thread(target=self._drain, args=(stream,), daemon=True)
        try:
            self.thread.start()
        except BaseException:
            stream.close()
            self.error = "Output collector could not start; output capture is incomplete"
            self.done.set()
            raise

    def _write(self, stream, raw):
        remaining = memoryview(raw)
        while remaining:
            count = stream.write(remaining)
            if not count:
                raise OSError("Output retention write made no progress")
            remaining = remaining[count:]

    def _drain(self, incoming):
        try:
            with incoming, regular(self.data, "ab") as saved:
                while raw := incoming.read(65536):
                    # No lock spans IO. A failed write never publishes its suffix.
                    self._write(saved, raw)
                    digest = self.digest.copy()
                    digest.update(raw)
                    with self.lock:
                        self.digest = digest
                        self.size += len(raw)
                        self.newlines += raw.count(b"\n")
                        self.last = raw[-1:]
                        self.retained_bytes += len(raw)
                        self.scanned_bytes += len(raw)
                with self.lock:
                    self.eof = True
        except (OSError, ValueError) as error:
            with self.lock:
                self.error = f"{type(error).__name__}: {error}"
        finally:
            self.done.set()
            self.thread = None

    def record(self):
        with self.lock:
            return {"format": FORMAT, "data": self.data.name, "bytes": self.size,
                    "lines": self.newlines + int(bool(self.size) and self.last != b"\n"),
                    "sha256": self.digest.hexdigest(), "capture_complete": self.eof and not self.error,
                    "capture_error": self.error}

    def snapshot(self):
        record = self.record()
        path = self.directory / f"job-{self.ident}-{record['bytes']}.extent.json"
        if not path.exists():
            # Exclusive creation; publish only the immutable byte identity, not
            # lifecycle or EOF state, which can change without any new bytes.
            identity = {key: record[key] for key in ("format", "data", "bytes", "lines", "sha256")}
            try:
                with path.open("x", encoding="utf-8") as stream:
                    json.dump(identity, stream, separators=(",", ":"))
            except FileExistsError:
                pass
            except OSError:
                path.unlink(missing_ok=True)
                raise
        if load_extent(path) != {key: record[key] for key in ("format", "data", "bytes", "lines", "sha256")}:
            raise OSError("Saved output extent descriptor does not match the captured identity")
        return path, record


if __name__ == "__main__":
    if len(sys.argv) != 3 or sys.argv[1] != "--cat":
        raise SystemExit("Usage: python job_output.py --cat EXTENT_DESCRIPTOR")
    try:
        for chunk in chunks(sys.argv[2]):
            sys.stdout.buffer.write(chunk)
    except (OSError, ValueError) as error:
        raise SystemExit(f"Output recovery failed: {error}")
