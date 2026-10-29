"""Small local coding tools; complete evidence lives outside the task patch."""
from __future__ import annotations

from dataclasses import dataclass
import codecs
from collections import deque
from contextlib import contextmanager, nullcontext
import difflib
import hashlib
import io
from itertools import islice
import json
import os
from pathlib import Path
import re
import shlex
import stat
import subprocess
import sys
import tempfile
import time

from .jobs import Job, Jobs
from .job_output import FORMAT, open_extent


PAGE_LINES = 500
PAGE_BYTES = 100_000
EDIT_LOCATION_LIMIT = 20


@dataclass(frozen=True)
class ToolResult:
    display: str
    full_path: str
    metadata: dict


def tool_schemas() -> list[dict]:
    """The authoritative ordinary interface, also used by call validation."""
    specs = [
        ("read_file", "Read numbered lines and total length from a regular file, including installed dependency source outside the repository and exact saved output. Relative paths start at the repository root. Omit ranges for the first page; start_line/end_line are one-based and inclusive. For long single lines use zero-based start_byte/max_bytes (default 10000 bytes); max_bytes alone starts at byte 0. Do not combine line and byte ranges. Full selected bytes are saved even when the display is paginated or replaces invalid UTF-8. Pipes and devices are rejected without waiting. Supply search for a case-insensitive literal search of lines, with context_lines (default 8). Search pages show up to 20 matching lines and a next start_line; end_line bounds matches. Search cannot be combined with byte paging.",
         {"path": {"type": "string"}, "start_line": {"type": "integer", "minimum": 1}, "end_line": {"type": "integer", "minimum": 1},
          "search": {"type": "string", "minLength": 1}, "context_lines": {"type": "integer", "minimum": 0, "maximum": 100},
          "start_byte": {"type": "integer", "minimum": 0}, "max_bytes": {"type": "integer", "minimum": 1, "maximum": PAGE_BYTES}}, ["path"]),
        ("edit_file", "Replace exact text in an existing UTF-8 file in the repository or owned scratch space, including whitespace. Do not include displayed line numbers in old/new. Zero or ambiguous matches do not edit; use more surrounding text or replace_all to replace every match. Shows changed regions and saves exact pre-write bytes for recovery. Use exec_command to create, rename or delete files. Saved evidence cannot be edited.",
         {"path": {"type": "string"}, "old": {"type": "string", "minLength": 1}, "new": {"type": "string"}, "replace_all": {"type": "boolean"}}, ["path", "old", "new"]),
        ("list_jobs", "List owned command jobs, including completed jobs, with complete saved commands and state. Pages contain 20 records; start defaults to 1. Use a returned job ID with job to wait, inspect or stop that operation. Listing starts no process and does not wait or stop one.",
         {"start": {"type": "integer", "minimum": 1}}, []),
        ("job", "Observe or control an owned session ID from exec_command or list_jobs. wait reports new output after yield_time_ms (default 1000); inspect returns immediately without advancing the wait cursor; stop cancels the process group. Waiting never cancels. Quiet output alone does not establish a hang. Oversized observations show the beginning and end of the captured interval; inspect uses all captured output, while wait uses only new output. Full captured bytes are recoverable with read_file.",
         {"id": {"type": "string", "minLength": 1}, "action": {"type": "string", "enum": ["wait", "inspect", "stop"]}, "yield_time_ms": {"type": "integer", "minimum": 0, "description": "For action=wait, wait this many milliseconds for the next observation (default 1000). Reaching this wait does not cancel the command or change its requested hard deadline."}}, ["id", "action"]),
        ("exec_command", "Run a Bash command with pipefail. Commands are noninteractive; stdin is closed. Return output and a session ID; if still running, use job to wait, inspect, or stop. Yielding does not cancel. Stdout/stderr are saved automatically; oversized observations show the beginning and end of the captured interval. Page full output with read_file. Avoid head/tail merely to shorten build or test output: filters can buffer or hide diagnostics, and head can terminate the producer early. A completed shell's remaining children are stopped. Run a persistent service in the foreground with a short yield_time_ms to keep its session alive.",
         {"cmd": {"type": "string"},
          "workdir": {"type": "string", "minLength": 1, "description": "Working directory, absolute or relative to the repository root. Each call starts here; shell state does not persist across calls."},
          "yield_time_ms": {"type": "integer", "minimum": 0, "description": "Wait before returning, default 10000 ms. The process continues afterward."},
          "kill_after_ms": {"type": "integer", "minimum": 1, "description": "Optional cancellation deadline from launch. Omit to run until completion. Stops the process group even while you wait or inspect."}}, ["cmd", "workdir"]),
    ]
    return [{"type": "function", "function": {"name": name, "description": desc,
             "parameters": {"type": "object", "properties": props, "required": required, "additionalProperties": False}}}
            for name, desc, props, required in specs]


def legacy_tool_schemas() -> list[dict]:
    """Replay compatibility derives shared semantics from the ordinary interface."""
    schemas = tool_schemas()
    for schema in schemas:
        function = schema['function']
        if function['name'] == 'exec_command':
            function['name'] = 'bash'
            function['description'] = 'Run from the repository root. ' + function['description']
            parameters = function['parameters']
            parameters['properties']['command'] = parameters['properties'].pop('cmd')
            parameters['properties'].pop('workdir')
            parameters['required'] = ['command']
    schemas.append({'type': 'function', 'function': {'name': 'write_file',
        'description': 'Create or replace a regular UTF-8 file, making parent directories. Save exact pre-write bytes and submitted content before replacement; failure to retain recovery bytes prevents replacement. Returns a bounded diff and recovery paths. Pipes and devices are rejected before truncation.',
        'parameters': {'type': 'object', 'properties': {'path': {'type': 'string'}, 'content': {'type': 'string'}},
                       'required': ['path', 'content'], 'additionalProperties': False}}})
    return schemas


def validate_call(name: str, args: dict) -> None:
    """Reject a complete malformed call before any call in its batch runs."""
    schema = next((x["function"]["parameters"] for x in tool_schemas() + legacy_tool_schemas() if x["function"]["name"] == name), None)
    if schema is None:
        raise ValueError(f"Unknown tool {name!r}.")
    if not isinstance(args, dict):
        raise ValueError(f"{name} arguments must be a JSON object.")
    missing, extra = set(schema["required"]) - args.keys(), args.keys() - schema["properties"].keys()
    if missing or extra:
        raise ValueError(f"{name}: missing arguments {sorted(missing)}; unknown arguments {sorted(extra)}.")
    for key, value in args.items():
        rule = schema["properties"][key]
        valid = {"string": isinstance(value, str), "integer": type(value) is int,
                 "number": type(value) in (int, float), "boolean": type(value) is bool}[rule["type"]]
        if not valid:
            raise ValueError(f"{name}.{key} must be {rule['type']}.")
        if "enum" in rule and value not in rule["enum"]:
            raise ValueError(f"{name}.{key} must be one of {rule['enum']}.")
        if "minimum" in rule and value < rule["minimum"]:
            raise ValueError(f"{name}.{key} must be at least {rule['minimum']}.")
        if "maximum" in rule and value > rule["maximum"]:
            raise ValueError(f"{name}.{key} must be at most {rule['maximum']}.")
        if "exclusiveMinimum" in rule and not (value > rule["exclusiveMinimum"] and value < float("inf")):
            raise ValueError(f"{name}.{key} must be finite and positive.")
        if "minLength" in rule and len(value) < rule["minLength"]:
            raise ValueError(f"{name}.{key} must not be empty.")
    if name == "read_file" and args.get("end_line", args.get("start_line", 1)) < args.get("start_line", 1):
        raise ValueError("read_file.end_line must be at least start_line.")
    if name == "read_file" and {'start_byte', 'max_bytes'} & args.keys() and {'start_line', 'end_line'} & args.keys():
        raise ValueError("read_file byte paging cannot be combined with start_line/end_line.")
    if name == "read_file":
        if 'context_lines' in args and 'search' not in args:
            raise ValueError('read_file.context_lines requires search.')
        if 'search' in args and ({'start_byte', 'max_bytes'} & args.keys() or '\n' in args['search'] or '\r' in args['search']):
            raise ValueError('read_file.search is a single-line literal; use line ranges, not byte paging.')
    if "path" in args and not args["path"]:
        raise ValueError(f"{name}.path must not be empty.")


@contextmanager
def _regular_file(path: Path, *, writable: bool = False, create: bool = False):
    # Open without waiting for a FIFO counterpart, then validate that exact FD.
    # Do not truncate until this check succeeds. Resolve permitted symlinks in _path;
    # refuse a last-component symlink substituted between resolution and open.
    flags = (os.O_RDWR if writable else os.O_RDONLY) | os.O_NONBLOCK | getattr(os, "O_NOFOLLOW", 0)
    if create:
        flags |= os.O_CREAT | os.O_EXCL
    descriptor = os.open(path, flags, 0o666)
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode):
            raise ValueError(f"{path} must be a regular file; this opened path is not one. No content was changed. Inspect or repair the path with bash before retrying.")
        with os.fdopen(descriptor, "r+b" if writable else "rb", buffering=0) as stream:
            descriptor = None
            yield stream, info.st_size
    finally:
        if descriptor is not None:
            os.close(descriptor)


def _replace_contents(stream, raw: bytes) -> None:
    """In-place replacement; callers report uncertain effects after any write error."""
    stream.seek(0)
    stream.truncate(0)
    remaining = memoryview(raw)
    while remaining:
        count = stream.write(remaining)
        if not count:
            raise OSError("File write made no progress")
        remaining = remaining[count:]


def _line_numbers(text: str, positions):
    """Map ordered character offsets to lines without rescanning earlier text."""
    previous, line = 0, 1
    for offset in positions:
        line += text.count("\n", previous, offset)
        previous = offset
        yield line


def _stream_info(stream) -> tuple[int, int, str]:
    size = lines = 0
    last = b""
    digest = hashlib.sha256()
    stream.seek(0)
    remaining = os.fstat(stream.fileno()).st_size
    while remaining:
        chunk = stream.read(min(65536, remaining))
        if not chunk:
            raise OSError("File shrank during observation; inspect its current contents")
        remaining -= len(chunk)
        digest.update(chunk)
        size += len(chunk)
        lines += chunk.count(b"\n")
        last = chunk[-1:]
    return lines + int(bool(size) and last != b"\n"), size, digest.hexdigest()


def _info(path: Path) -> tuple[int, int, str]:
    """Count newline-delimited lines and bytes without retaining the output."""
    with _regular_file(path) as (stream, _):
        return _stream_info(stream)


def _preview(path: Path, numbered: bool = False, start: int = 1, offset: int = 0, byte_limit: int = PAGE_BYTES,
             line_limit: int = PAGE_LINES) -> tuple[str, int, int]:
    """One contiguous first page, never a head/tail splice or broken UTF-8."""
    extent = path.name.endswith(".extent.json")
    with (open_extent(path) if extent else nullcontext((path.open("rb"), None))) as (stream, record):
        try:
            stream.seek(offset)
            raw = stream.read(min(byte_limit + 1, max(0, record["bytes"] - offset)) if record else byte_limit + 1)
        finally:
            if not extent:
                stream.close()
    parts: list[str] = []
    used = shown_bytes = shown_lines = 0
    raw_lines = raw.split(b"\n")
    lines = [line + b"\n" for line in raw_lines[:-1]]
    if raw_lines[-1]:
        lines.append(raw_lines[-1])
    for line in lines[:line_limit]:
        prefix = f"{start + shown_lines} | " if numbered else ""
        room = byte_limit - used - len(prefix.encode())
        if room <= 0:
            break
        selected = line[:room]
        while True:
            decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
            text = decoder.decode(selected, final=len(selected) == len(line))
            pending = len(decoder.getstate()[0])
            excess = len(text.encode("utf-8")) - room
            if excess <= 0:
                # Defer only an incomplete trailing character, never invalid
                # bytes within the page. Display bytes and source bytes differ.
                selected = selected[:len(selected) - pending]
                break
            selected = selected[:max(0, len(selected) - (excess + 2) // 3)]
        if not selected:
            break
        rendered = prefix + text
        parts.append(rendered)
        used += len(rendered.encode("utf-8"))
        shown_bytes += len(selected)
        shown_lines += 1
        if len(selected) < len(line):
            break
    return "".join(parts), shown_lines, shown_bytes


def _output_tail(raw: bytes, byte_limit: int, line_limit: int) -> tuple[str, int]:
    """A bounded suffix, retaining its ending even when invalid bytes expand."""
    lines = raw.split(b"\n")
    parts = [line + b"\n" for line in lines[:-1]]
    if lines[-1]:
        parts.append(lines[-1])
    selected = b"".join(parts[-line_limit:])
    used = source_bytes = 0
    # Surrogate escapes retain the source-byte count for invalid UTF-8. Reading
    # three extra bytes before this window keeps a valid boundary character
    # available; the display budget excludes any partial character before it.
    for character in reversed(selected.decode("utf-8", errors="surrogateescape")):
        text = "\ufffd" if 0xDC80 <= ord(character) <= 0xDCFF else character
        size = len(text.encode("utf-8"))
        if used + size > byte_limit:
            break
        used += size
        source_bytes += len(character.encode("utf-8", errors="surrogateescape"))
    return (selected[-source_bytes:].decode("utf-8", errors="replace") if source_bytes else ""), source_bytes


def _job_preview(path: Path, offset: int) -> tuple[str, list[list[int]]]:
    """Show a small extent contiguously, otherwise its bounded beginning/end."""
    page, _, shown_bytes = _preview(path, offset=offset)
    with open_extent(path) as (stream, captured):
        end = captured["bytes"]
        if offset + shown_bytes == end:
            return page, [[offset, end]]

        def labels(head_end: int, tail_start: int) -> tuple[str, str]:
            return (
                f"Beginning of captured interval (source bytes [{offset}, {head_end}); zero-based, end exclusive):\n",
                f"\n[... source bytes [{head_end}, {tail_start}) omitted; {tail_start - head_end} bytes remain in saved output ...]\n"
                f"End of captured interval (source bytes [{tail_start}, {end}); zero-based, end exclusive):\n",
            )

        # Reserve the largest possible labels, including their line breaks,
        # inside the ordinary page budget rather than doubling that budget.
        widest = str(end)
        label_bytes = (len(labels(end, end)[0].encode("utf-8"))
                       + len(labels(end, end)[1].encode("utf-8")) + len(widest) - 1)
        content_bytes = PAGE_BYTES - label_bytes
        content_lines = PAGE_LINES - 4
        head, _, head_bytes = _preview(path, offset=offset, byte_limit=content_bytes // 2,
                                      line_limit=content_lines // 2)
        tail_limit = content_bytes - content_bytes // 2
        tail_offset = max(offset + head_bytes, end - tail_limit - 3)
        stream.seek(tail_offset)
        raw = stream.read(end - tail_offset)
        if len(raw) != end - tail_offset:
            raise OSError("Retained output shrank during preview")
        tail, tail_bytes = _output_tail(raw, tail_limit, content_lines - content_lines // 2)
        head_end, tail_start = offset + head_bytes, end - tail_bytes
        beginning, middle = labels(head_end, tail_start)
        return beginning + head + middle + tail, [[offset, head_end], [tail_start, end]]


class Tools:
    def __init__(self, root: Path, runtime: Path):
        self.root, self.runtime = Path(root).resolve(), Path(runtime).resolve()
        if self.runtime == self.root or self.root in self.runtime.parents:
            raise ValueError("Runtime must be outside the repository so evidence is not part of its patch.")
        self.out = self.runtime / "out"
        self.jobs = self.runtime / "jobs"
        self.out.mkdir(parents=True, exist_ok=True)
        self.jobs.mkdir(parents=True, exist_ok=True)
        self._counter = 0
        self._registry = Jobs(self.root, self.jobs)
        self._observations: dict[str, tuple[str, str]] = {}
        self._job_extents: dict[str, tuple[str, bool]] = {}

    def _path(self, value: str, *, repository_only: bool = False) -> Path:
        candidate = Path(value)
        candidate = (candidate if candidate.is_absolute() else self.root / candidate).resolve()
        roots = (self.root,) if repository_only else (self.root, self.runtime)
        if not any(candidate == base or base in candidate.parents for base in roots):
            raise ValueError("Path is outside the repository and owned runtime. Use a repository path or an indicated runtime artifact path.")
        return candidate

    def _label(self, path: Path) -> str:
        return str(path.relative_to(self.root)) if self.root in path.parents else str(path)

    def _writable(self, path: Path) -> None:
        evidence = (self.out, self.jobs, self.runtime / "history", self.runtime / "requests", self.runtime / "trace-spool", self.runtime / "graded-patches", self.runtime / "prior-memory")
        if any(path == directory or directory in path.parents for directory in evidence):
            raise ValueError("Saved observations and job artifacts are immutable tool evidence. Write interpretations to notes.md or a separate scratch file.")

    def _reserve(self) -> Path:
        while True:
            self._counter += 1
            path = self.out / f"{self._counter}.txt"
            try:
                path.touch(exist_ok=False)
                return path
            except FileExistsError:
                continue

    def _result(self, body: str, metadata: dict, *, archive: Path | None = None) -> ToolResult:
        archive = archive or self._reserve()
        if archive.stat().st_size == 0:
            archive.write_text(body, encoding="utf-8")
        page, lines, size = _preview(archive)
        total_lines, total_bytes, _ = _info(archive)
        metadata.setdefault("total_lines", total_lines)
        metadata.setdefault("total_bytes", total_bytes)
        if size < total_bytes:
            body = page + f"\nShowing first {lines} lines ({size} bytes) of {total_lines} lines, {total_bytes} bytes. Remaining output is saved."
        return ToolResult(body.rstrip("\n") + f"\nSaved result: {archive}\nRetrieve with read_file.", str(archive), metadata)

    def execute(self, name: str, args: dict) -> ToolResult:
        validate_call(name, args)
        try:
            result = getattr(self, "_" + name)(**args)
        except (OSError, ValueError, UnicodeError, re.error, subprocess.SubprocessError) as error:
            return self._result(f"{name} failed: {error}\nCheck the path, arguments, and reported environment before retrying.", {"kind": name, "error": True})
        meta = result.metadata
        if meta.get("kind") in ("read", "edit", "write") and meta.get("path") and meta.get("version"):
            previous = self._observations.get(meta["path"])
            if previous and previous[0] != meta["version"]:
                origin = f"at {previous[1]}" if previous[1] else "(no saved result was retained)"
                result = ToolResult(result.display + f"\nEarlier observation {origin} describes an older version of {meta['path']}; newly observed file SHA256 {meta['version']}.", result.full_path, meta)
            self._observations[meta["path"]] = (meta["version"], result.full_path)
        return result

    def notes(self, page_bytes: int | None = None) -> str:
        path, temporary, first = self.runtime / "notes.md", None, b""
        try:
            try:
                path.lstat()
            except FileNotFoundError:
                return ""
            path = self._path(str(path))
            # Nonblocking open also prevents an accidental FIFO from hanging the loop.
            with os.fdopen(os.open(path, os.O_RDONLY | os.O_NONBLOCK), "rb") as incoming:
                initial = os.fstat(incoming.fileno())
                if not stat.S_ISREG(initial.st_mode):
                    raise OSError("Notes path must be a regular file")
                if page_bytes is None:
                    page_bytes = initial.st_size
                first = incoming.read(min(page_bytes + 1, initial.st_size))
                if len(first) < min(page_bytes + 1, initial.st_size):
                    raise OSError("Notes shrank during observation")
                if initial.st_size <= page_bytes:
                    text = first.decode("utf-8")
                else:
                    temporary = self._reserve()
                    decoder = codecs.getincrementaldecoder("utf-8")()
                    invalid = None
                    with temporary.open("wb") as output:
                        chunk, remaining = first, initial.st_size
                        while chunk:
                            output.write(chunk)
                            remaining -= len(chunk)
                            if invalid is None:
                                try:
                                    decoder.decode(chunk, final=remaining == 0)
                                except UnicodeError as error:
                                    invalid = error
                            chunk = incoming.read(min(65536, remaining)) if remaining else b""
                        if remaining:
                            raise OSError("Notes shrank during observation; retained bytes are incomplete")
                    if invalid is not None:
                        raise invalid
                observed = os.fstat(incoming.fileno())
                if (observed.st_size, observed.st_mtime_ns) != (initial.st_size, initial.st_mtime_ns):
                    raise OSError("Notes changed during observation; retained bytes may be a partial concurrent write")
            if temporary is None:
                return text
            lines, size, version = _info(temporary)
            archive = self.out / f"notes-{version}.txt"
            if archive.exists():
                temporary.unlink()
            else:
                temporary.replace(archive)
            temporary = archive
            page, shown_lines, shown_bytes = _preview(archive, byte_limit=page_bytes)
            return f"Current notes snapshot: {lines} lines, {size} bytes; SHA256 {version}. Showing first {shown_lines} lines ({shown_bytes} bytes).\n{page}\nRemaining notes are saved exactly at {archive}; retrieve with read_file or bash."
        except (OSError, ValueError, UnicodeError) as error:
            retained = "No raw bytes could be retained."
            try:
                if temporary is None and first:
                    temporary = self._reserve()
                    temporary.write_bytes(first)
                if temporary is not None and temporary.exists():
                    _, size, version = _info(temporary)
                    archive = self.out / f"notes-{version}.txt"
                    if temporary != archive:
                        if archive.exists():
                            temporary.unlink()
                        else:
                            temporary.replace(archive)
                    retained = f"Observed raw bytes retained at {archive} ({size} bytes); this may be an incomplete observation. Retrieve with read_file or bash."
            except OSError as saving_error:
                retained = f"Raw-byte retention failed: {saving_error}."
            return f"Note observation error for {path}: {type(error).__name__}: {error}.\nNo current note content is available from this observation; no earlier note was substituted.\n{retained}\nRepair {self.runtime / 'notes.md'} with write_file using UTF-8 text, or inspect/fix the path with bash, then continue."

    def _read_file(self, path: str, start_line: int = 1, end_line: int | None = None,
                   start_byte: int | None = None, max_bytes: int | None = None,
                   search: str | None = None, context_lines: int = 8) -> ToolResult:
        source = Path(path)
        source = source if source.is_absolute() else self.root / source
        source = source.resolve()
        result = (self._search_file(source, search, context_lines, start_line, end_line) if search is not None else
                  self._read_resolved_file(source, start_line, end_line, start_byte, max_bytes))
        return result

    def _is_extent(self, source):
        return source.name.endswith('.extent.json') and self.runtime in source.parents

    def _read_resolved_file(self, source: Path, start_line: int = 1, end_line: int | None = None,
                            start_byte: int | None = None, max_bytes: int | None = None) -> ToolResult:
        if start_byte is not None or max_bytes is not None:
            return self._read_bytes(source, start_byte if start_byte is not None else 0,
                                    max_bytes if max_bytes is not None else 10000)
        archive = self._reserve()
        total = total_bytes = 0
        digest = hashlib.sha256()
        # Saved extents throughout the owned runtime preserve their relative
        # descriptor/data pair and exact captured bytes.
        extent = self._is_extent(source)
        with (open_extent(source) if extent else _regular_file(source)) as (incoming, captured), archive.open("wb") as output:
            remaining = captured["bytes"] if extent else captured
            number, last = 1, b""
            while remaining:
                chunk = incoming.read(min(65536, remaining))
                if not chunk:
                    raise OSError("File shrank during observation; inspect its current contents")
                remaining -= len(chunk)
                digest.update(chunk)
                total_bytes += len(chunk)
                last = chunk[-1:]
                offset = 0
                while offset < len(chunk):
                    newline = chunk.find(b"\n", offset)
                    stop = newline + 1 if newline >= 0 else len(chunk)
                    if number >= start_line and (end_line is None or number <= end_line):
                        output.write(chunk[offset:stop])
                    if newline >= 0:
                        number += 1
                    offset = stop
            total = number - 1 + int(bool(total_bytes) and last != b"\n")
        version = digest.hexdigest()
        if extent and version != captured["sha256"]:
            raise OSError("Retained output no longer matches its captured SHA256")
        end = min(end_line if end_line is not None else total, total)
        meta = {"kind": "read", "path": str(source), "start_line": start_line, "end_line": end,
                "total_lines": total, "total_bytes": total_bytes, "version": version}
        if total == 0:
            return ToolResult(f"{self._label(source)} is empty (0 lines, 0 bytes).\nSaved result (exact selected bytes): {archive}", str(archive), meta)
        if start_line > total:
            return ToolResult(f"{self._label(source)} has {total} lines; requested start line {start_line} is past EOF.\nRead {max(1, total - 23)}–{total} to inspect the end, or omit the range to start at line 1.\nSaved result (empty selected range): {archive}", str(archive), meta)
        page, shown_lines, shown_bytes = _preview(archive, numbered=True, start=start_line)
        selected_lines, selected_bytes, _ = _info(archive)
        shown_end = start_line + shown_lines - 1
        body = ("Resolved immutable job-output extent to exact captured bytes.\n" if extent else "")
        body += f"{self._label(source)} — lines {start_line}–{shown_end} of {total}; file {total_bytes} bytes\n{page}"
        if "\ufffd" in page:
            body += "\nUTF-8 preview may replace invalid byte sequences; saved selected bytes are exact. Inspect the encoding with exec_command before copying this display into an edit."
        if shown_bytes < selected_bytes:
            body += f"\nShowing first {shown_lines} lines ({shown_bytes} bytes) of selected lines {start_line}–{end}: {selected_lines} lines, {selected_bytes} bytes. Remaining output is saved."
        return ToolResult(body.rstrip("\n") + f"\nSaved result (exact selected bytes): {archive}\nRead the next range from {self._label(source)}, or inspect the saved original with read_file/bash.", str(archive), meta)

    def _search_file(self, source, query, context_lines, start_line, end_line):
        """Scan a captured version once; retain bounded line offsets, not whole lines."""
        extent = self._is_extent(source)
        needle = query.casefold()
        matches, selected, previous = [], {}, deque(maxlen=context_lines)
        match_count = following = 0
        next_line = None
        number, offset, line_start = 1, 0, 0
        digest = hashlib.sha256()
        decoder = codecs.getincrementaldecoder('utf-8')(errors='replace')
        tail, found, match_offset = '', False, None
        with (open_extent(source) if extent else _regular_file(source)) as (incoming, captured):
            initial = os.fstat(incoming.fileno())
            remaining = captured['bytes'] if extent else captured
            buffered = io.BufferedReader(incoming, buffer_size=65536)
            while remaining:
                # readline has a byte bound: a huge single line cannot consume
                # unbounded memory. Incremental decoding also handles split UTF-8.
                raw = buffered.readline(min(65536, remaining))
                if not raw:
                    raise OSError('Source shrank during search; inspect it again')
                remaining -= len(raw)
                digest.update(raw)
                offset += len(raw)
                complete = raw.endswith(b'\n') or not remaining
                text = tail + decoder.decode(raw, final=complete).casefold()
                if not found and needle in text:
                    match_offset = max(line_start, offset - len(raw) - len(query.encode()) * 2)
                    found = True
                tail = text[-max(0, len(needle) - 1):] if len(needle) > 1 else ''
                if not complete:
                    continue
                row = (number, line_start, offset, match_offset)
                eligible = found and number >= start_line and (end_line is None or number <= end_line)
                if following:
                    selected[number] = row
                    following -= 1
                if eligible:
                    match_count += 1
                    if len(matches) < 20:
                        matches.append(number)
                        selected.update((item[0], item) for item in previous)
                        selected[number] = row
                        following = context_lines
                    elif next_line is None:
                        next_line = number
                previous.append(row)
                number += 1
                line_start = offset
                decoder = codecs.getincrementaldecoder('utf-8')(errors='replace')
                tail, found, match_offset = '', False, None
            buffered.detach()  # Retain the validated raw FD for excerpt reads.
            version = digest.hexdigest()
            if extent and version != captured['sha256']:
                raise OSError('Retained output no longer matches its captured SHA256')
            total = number - 1
            meta = {'kind': 'read', 'range_mode': 'search', 'path': str(source),
                    'query': query, 'match_mode': 'case-insensitive literal',
                    'start_line': start_line, 'end_line': min(end_line or total, total),
                    'total_lines': total, 'total_bytes': offset, 'version': version,
                    'matching_lines': match_count, 'selected_match_lines': matches, 'next_start_line': next_line}
            body = (f'Literal search {query!r}, case-insensitive: {match_count} matching lines '
                    f'in searched lines {start_line}–{meta["end_line"]} of {total}; {offset} source bytes.\n'
                    f'Source SHA256: {version}. Selected first {len(matches)} matching lines with context.\n'
                    f'Matching line numbers in this page: {matches}.\n')
            # A descriptor identifies bytes, not a test verdict or lifecycle.
            if extent:
                body += f'Immutable captured output: {source}. Later output is outside this extent.\n'
                data = (source.parent / captured['data']).resolve()
                with self._registry.lock:
                    jobs = list(self._registry.records.values())
                job = next((j for j in jobs if j.output and j.output.data.resolve() == data), None)
                if job:
                    state = self._registry.record(job)
                    meta['operation'] = {k: state[k] for k in ('job_id', 'command', 'cwd', 'state', 'exit_code', 'cleanup_error')}
                    body += 'Current owned command observation (separate from searched extent): ' + json.dumps({k: v for k, v in meta['operation'].items() if k != 'command'}) + '\n'
                else:
                    body += 'Historical/external output: process completion is not established by this search; retrieve its original operation receipt.\n'
            if not match_count:
                body += 'No matching lines in this search scope. This is not a test verdict.\n'
            used, last = 0, 0
            for line, first, stop, hint in sorted(selected.values()):
                if line != last + 1:
                    body += '…\n'
                room = max(0, PAGE_BYTES - used)
                begin = max(first, hint - room // 4) if hint is not None and stop - first > room else first
                if begin != first or stop - first > room:
                    body += f'[Line excerpted near the match; exact byte page: start_byte={begin}, max_bytes={max(1, room)}.]\n'
                incoming.seek(begin)
                raw = incoming.read(min(stop - begin, room))
                rendered = raw.decode('utf-8', errors='replace')
                body += f'{line} | {rendered}' + ('' if rendered.endswith('\n') else '\n')
                used += len(raw)
                last = line
                if used >= PAGE_BYTES:
                    body += 'Search display byte page reached; matching line numbers above remain available for targeted reads.\n'
                    break
            final = os.fstat(incoming.fileno())
            if not extent and (initial.st_size, initial.st_mtime_ns) != (final.st_size, final.st_mtime_ns):
                raise OSError('Source changed during search; inspect its current version')
        if next_line is not None:
            body += 'More matches: read_file(' + json.dumps({'path': str(source), 'search': query,
                'context_lines': context_lines, 'start_line': next_line, **({'end_line': end_line} if end_line else {})}) + ').\n'
        return self._result(body, meta)

    def _read_bytes(self, source: Path, start_byte: int, max_bytes: int) -> ToolResult:
        archive = self._reserve()
        total, digest = 0, hashlib.sha256()
        extent = self._is_extent(source)
        with (open_extent(source) if extent else _regular_file(source)) as (incoming, captured), archive.open('wb') as output:
            remaining = captured['bytes'] if extent else captured
            while remaining:
                raw = incoming.read(min(65536, remaining))
                if not raw:
                    raise OSError('File shrank during byte observation; inspect its current contents')
                remaining -= len(raw)
                digest.update(raw)
                first = max(0, start_byte - total)
                last = min(len(raw), start_byte + max_bytes - total)
                if first < last:
                    output.write(raw[first:last])
                total += len(raw)
        version = digest.hexdigest()
        if extent and version != captured['sha256']:
            raise OSError('Retained output no longer matches its captured SHA256')
        selected = archive.read_bytes()
        end = start_byte + len(selected)
        meta = {'kind': 'read', 'path': str(source), 'range_mode': 'bytes', 'start_byte': start_byte,
                'end_byte': end, 'selected_bytes': len(selected), 'total_bytes': total, 'version': version,
                'selected_sha256': hashlib.sha256(selected).hexdigest()}
        body = ('Resolved immutable job-output extent to exact captured bytes.\n' if extent else '')
        if not total:
            body += f'{self._label(source)} is empty (0 bytes).\n'
        elif start_byte >= total:
            body += (f'{self._label(source)} has {total} bytes; requested start byte {start_byte} is at or past EOF.\n'
                     f'Use start_byte={max(0, total - max_bytes)}, max_bytes={max_bytes} to inspect its end.\n')
        else:
            body += (f'{self._label(source)} — bytes {start_byte}–{end} (zero-based, end exclusive) of {total}; '
                     f'{len(selected)} selected bytes.\n')
        body += (f'Source SHA256: {version}. Selected SHA256: {meta["selected_sha256"]}.\n'
                 'Byte-oriented UTF-8 display; invalid or split characters use replacement. Saved bytes are exact.\n'
                 + selected.decode('utf-8', errors='replace')
                 + f'\nSaved result (exact selected bytes): {archive}\n'
                 f'Read another byte page with start_byte={end}, max_bytes={max_bytes}; line ranges cannot be combined with byte paging.')
        return ToolResult(body, str(archive), meta)

    def _write_file(self, path: str, content: str) -> ToolResult:
        target = self._path(path)
        self._writable(target)
        existed = target.exists()
        raw = content.encode("utf-8")
        try:
            archive = self._reserve()
            archive.write_bytes(raw)
        except OSError as error:
            return self._prewrite_rejected("write", target, error, prior_exists=existed)
        target.parent.mkdir(parents=True, exist_ok=True)
        with _regular_file(target, writable=True, create=not existed) as (stream, _):
            meta = {"prior_exists": existed}
            if existed:
                try:
                    meta.update(self._retain_before_image(stream))
                except OSError as error:
                    return self._prewrite_rejected("write", target, error)
            try:
                _replace_contents(stream, raw)
            except OSError as error:
                return self._mutation_observation_failure("write", target, error, completed=False, metadata=meta, submitted=archive)
            try:
                lines, size, version = _stream_info(stream)
            except OSError as error:
                return self._mutation_observation_failure("write", target, error, completed=True, metadata=meta, submitted=archive)
        submitted_hash = hashlib.sha256(raw).hexdigest()
        body = (f"Wrote {self._label(target)} — {lines} lines, {size} bytes; {'replaced the existing file' if existed else 'created a new file'}.\n"
                f"Full submitted content: {archive} (SHA256 {submitted_hash}).\n{self._before_image_text(meta)}")
        if version != submitted_hash:
            body += f"\nPost-write observed SHA256 {version} differs from submitted bytes; another write may have intervened. Inspect the current file before editing again."
        if existed:
            try:
                diff_text, diff_meta = self._replacement_diff(Path(meta['before_image_path']), raw)
                meta.update(diff_meta)
                body += '\n' + diff_text
            except OSError as error:
                return self._mutation_observation_failure('write', target, error, completed=True,
                    metadata={**meta, 'version': version}, body=body, submitted=archive)
        return ToolResult(body, str(archive), {**meta, "kind": "write", "path": str(target), "version": version, "total_lines": lines, "total_bytes": size})

    def _replacement_diff(self, before: Path, submitted: bytes) -> tuple[str, dict]:
        """Observe a completed replacement without interpreting removed source."""
        def lf_lines(raw):
            parts = raw.split(b'\n')
            return [part + b'\n' for part in parts[:-1]] + ([parts[-1]] if parts[-1] else [])
        diff = self._reserve()
        with diff.open('wb') as output:
            for line in difflib.diff_bytes(difflib.unified_diff,
                    lf_lines(before.read_bytes()), lf_lines(submitted),
                    fromfile=b'saved-before', tofile=b'submitted'):
                output.write(line)
                if not line.endswith(b'\n'):
                    output.write(b'\n\\ No newline at end of file\n')
        lines, size, version = _info(diff)
        page, shown_lines, shown_bytes = _preview(diff)
        text = (f'Replacement diff: saved pre-write bytes → submitted bytes, not a later repository snapshot.\n'
                f'Full diff: {diff} ({lines} lines, {size} bytes; SHA256 {version}). Retrieve with read_file or bash.\n')
        if not size:
            text += 'No byte changes from the saved pre-write content.'
        else:
            if shown_bytes < size:
                text += f'Showing first {shown_lines} lines ({shown_bytes} bytes) of the diff; remaining changes are saved.\n'
            text += page
        return text, {'diff_path': str(diff), 'diff_sha256': version, 'diff_bytes': size, 'diff_lines': lines}

    def _retain_before_image(self, stream) -> dict:
        """Copy the validated descriptor before truncation, without replacing its inode."""
        initial = os.fstat(stream.fileno())
        descriptor, name = tempfile.mkstemp(prefix="before-", suffix=".bin", dir=self.out)
        saved, remaining, digest = Path(name), initial.st_size, hashlib.sha256()
        try:
            with os.fdopen(descriptor, "wb") as output:
                stream.seek(0)
                while remaining:
                    chunk = stream.read(min(65536, remaining))
                    if not chunk:
                        raise OSError("File shrank while retaining recovery bytes")
                    output.write(chunk)
                    digest.update(chunk)
                    remaining -= len(chunk)
            observed = os.fstat(stream.fileno())
            if (observed.st_size, observed.st_mtime_ns) != (initial.st_size, initial.st_mtime_ns):
                raise OSError("File changed while retaining recovery bytes; inspect it again")
        except OSError:
            try:
                saved.unlink()
            except OSError:
                pass
            raise
        return {"prior_exists": True, "before_image_path": str(saved),
                "before_image_sha256": digest.hexdigest(), "before_image_bytes": initial.st_size}

    @staticmethod
    def _before_image_text(meta):
        if meta.get("before_image_path"):
            return (f"Exact pre-write bytes: {meta['before_image_path']} ({meta['before_image_bytes']} bytes; SHA256 {meta['before_image_sha256']}). "
                    "Retrieve with read_file or bash; use these bytes for recovery after inspecting the current file. In-place writes preserve existing permissions and hard links; concurrent writers are not locked.")
        return "The path did not previously exist; there are no earlier file bytes to restore."

    def _prewrite_rejected(self, kind, target, error, *, prior_exists=True):
        return ToolResult(f"No {kind} started for {self._label(target)}: required pre-write evidence could not be retained: {type(error).__name__}: {error}.\n"
                          "No file content was changed. Inspect the current file with read_file and repair runtime output storage with bash before retrying.", "",
                          {"kind": kind, "path": str(target), "prior_exists": prior_exists, "error": True,
                           "changed": False, "effect_status": "not_started", "observation_retained": False})

    def _mutation_observation_failure(self, kind, target, error, *, completed, metadata=None, body="", submitted=None):
        state = "completed" if completed else "uncertain"
        failure = f"{type(error).__name__}: {error}"
        if completed:
            text = f"{kind.title()} completed for {self._label(target)}, but its observation could not be retained: {failure}.\n"
        else:
            text = f"Writing {self._label(target)} raised {failure}; the file may have been partially changed. Completion is uncertain.\n"
        if body:
            page = body[:PAGE_BYTES].encode("utf-8")[:PAGE_BYTES].decode("utf-8", errors="ignore")
            text += "Unarchived result preview:\n" + page + "\n"
        text += "No saved result is available for this observation. Inspect the current file with read_file before another mutation; do not blindly repeat the edit or write to recreate a receipt."
        if submitted:
            text += f"\nFull submitted content was saved before the write: {submitted} (not a post-write observation)."
        if metadata is not None and "prior_exists" in metadata:
            text += "\n" + self._before_image_text(metadata)
        return ToolResult(text, "", {**(metadata or {}), **({'submitted_path': str(submitted)} if submitted else {}), "kind": kind, "path": str(target),
                                    "effect_status": state, "changed": True if completed else None,
                                    "observation_retained": False, "observation_error": failure})

    def _edit_file(self, path: str, old: str, new: str, replace_all: bool = False) -> ToolResult:
        target = self._path(path)
        self._writable(target)
        with _regular_file(target, writable=True) as (stream, extent):
            raw = stream.read(extent)
            if len(raw) != extent:
                raise OSError("File shrank during observation; inspect its current contents")
            original = raw.decode("utf-8")
            count = original.count(old)
            meta = {"kind": "edit", "path": str(target), "version": hashlib.sha256(raw).hexdigest(),
                    "match_count": count}
            if not count:
                hint = "Check indentation, whitespace, or whether this region changed since your read."
                candidates = original.split("\n") if original else []
                if original.endswith("\n"):
                    candidates.pop()
                wanted = old.split("\n", 1)[0]
                match = difflib.get_close_matches(wanted, candidates, n=1, cutoff=0.5)
                if match:
                    number = candidates.index(match[0]) + 1
                    hint += f"\nClosest candidate near line {number}:\n{number} | {match[0]}"
                return self._result(f"No edit made: expected text matched 0 times in {self._label(target)}.\n{hint}", {**meta, "changed": False, "edit_outcome": "no_matches"})
            if count > 1 and not replace_all:
                starts = (match.start() for match in islice(re.finditer(re.escape(old), original), EDIT_LOCATION_LIMIT))
                locations = list(_line_numbers(original, starts))
                sample = f"Showing first {len(locations)} of {count} match locations.\n" if count > len(locations) else ""
                return self._result(f"No edit made: {count} matches at lines {', '.join(map(str, locations))} in {self._label(target)}.\n{sample}Supply more surrounding text or use replace_all=true.", {**meta, "changed": False, "edit_outcome": "ambiguous"})
            updated = original.replace(old, new)
            if updated == original:
                return self._result(f"No edit made: old and new text are identical in {self._label(target)}.", {**meta, "changed": False, "edit_outcome": "identical"})
            starts = [match.start() for match in re.finditer(re.escape(old), original)]
            try:
                before = self._retain_before_image(stream)
                if before["before_image_sha256"] != meta["version"]:
                    raise OSError("File changed since the edit's read; inspect the current file before editing")
            except OSError as error:
                return self._prewrite_rejected("edit", target, error)
            meta.update(before)
            try:
                _replace_contents(stream, updated.encode("utf-8"))
            except OSError as error:
                return self._mutation_observation_failure("edit", target, error, completed=False, metadata={k: v for k, v in meta.items() if k != "version"})
        lines = updated.split("\n") if updated else []
        if updated.endswith("\n"):
            lines.pop()
        regions, changed = [], []
        current = [offset + index * (len(new) - len(old)) for index, offset in enumerate(starts)]
        span_offsets = current if new else starts
        span_length = len(new if new else old)
        positions = (point for offset in span_offsets for point in (offset, offset + span_length - 1))
        numbered = iter(_line_numbers(updated if new else original, positions))
        remaining = iter(_line_numbers(updated, current)) if not new else None
        for start, finish in zip(numbered, numbered):
            changed.append(f"{start}–{finish}")
            if not new:
                start = finish = min(len(lines), next(remaining))
            if lines:
                regions.append((max(1, start - 2), min(len(lines), finish + 2)))
        merged: list[tuple[int, int]] = []
        for start, end in regions:
            if merged and start <= merged[-1][1] + 1:
                merged[-1] = (merged[-1][0], max(merged[-1][1], end))
            else:
                merged.append((start, end))
        label = "Changed lines" if new else "Deleted text at original lines"
        shown = changed[:EDIT_LOCATION_LIMIT]
        body = f"Updated {self._label(target)}: {count} replacement{'s' if count != 1 else ''}. {label} {', '.join(shown)}.\n"
        if len(changed) > len(shown):
            body += (f"Showing first {len(shown)} of {len(changed)} changed ranges; "
                     "the complete range list follows the file context in the saved result.\n")
        for start, end in merged:
            body += f"Context lines {start}–{end} of {len(lines)}:\n" + "\n".join(f"{n} | {lines[n-1]}" for n in range(start, end + 1)) + "\n"
        if not lines:
            body += "The file is now empty (0 lines).\n"
        body += self._before_image_text(meta) + "\n"
        if len(changed) > len(shown):
            body += f"Complete {label.lower()} ({len(changed)} ranges): {', '.join(changed)}.\n"
        meta.update(version=hashlib.sha256(updated.encode("utf-8")).hexdigest(), changed=True, edit_outcome="applied")
        try:
            return self._result(body, meta)
        except (OSError, UnicodeError) as error:
            return self._mutation_observation_failure("edit", target, error, completed=True, metadata=meta, body=body)

    @staticmethod
    def _job_controls(record: dict, *, detail: bool = False) -> str:
        """Routine replies show chosen deadlines; inspection includes launch details."""
        text = ''
        if detail:
            text += f"Process identity at launch: PID {record['pid']}; process group {record['pid']}.\n"
            text += "Identity alone does not establish current liveness or progress.\n"
        if record['timeout_ms'] is None:
            if detail:
                text += "Cancellation deadline: none requested.\n"
        else:
            text += (f"Cancellation requested at launch: stop the process group after {record['timeout_ms']} ms. "
                     "\n")
            if record['state'] == 'running' and not record['cleanup_error']:
                remaining = max(0, record['timeout_ms'] / 1000 - record['elapsed_seconds'])
                text += f"Approximately {remaining:.3f} s remain before cancellation; job(wait) does not extend the deadline.\n"
        if record['state'] == 'timed_out':
            text += "Your requested cancellation deadline expired; the process group was stopped. This is a supervision outcome, not a compiler or test verdict.\n"
        return text

    def _observe_job(self, job: Job, kind: str, *, inspect: bool = False) -> ToolResult:
        with job.observation_lock:
            try:
                return self._observe_job_output(job, kind, inspect=inspect)
            except (OSError, UnicodeError, ValueError) as error:
                record = self._registry.record(job)
                outcome = "No shell exit code is known." if record["exit_code"] is None else f"Shell exit code: {record['exit_code']}."
                failure = f"{type(error).__name__}: {error}"
                body = (f"Job {job.id} was started and remains owned; initial cwd: {record['cwd']}.\n"
                        f"State: {record['state']}. {outcome} Elapsed execution time: {record['elapsed_seconds']:.3f} s.\n"
                        + self._job_controls(record, detail=True) +
                        f"Output observation/retention failed: {failure}. No saved output is available for this observation.\n"
                        f"Do not rerun the command to recover output. Use job(id={job.id!r}, action='inspect' or 'wait' or 'stop').\n"
                        f"Status path: {job.status}\n"
                        "Inspect or repair the indicated runtime paths with bash, then inspect this job again.")
                if record["status_error"] or record["status_pending"]:
                    body += f"\nStatus file may be absent or stale: {record['status_error'] or 'persistence pending'}; this result uses in-memory state."
                if record["cleanup_error"]:
                    body += f"\nProcess-group cleanup not confirmed: {record['cleanup_error']}."
                if record["state"] != "running":
                    body += "\nFinal output was not captured in this observation; inspect this job again."
                return ToolResult(body, "", {"kind": kind, **record, "observation_retained": False,
                                             "observation_error": failure, "final_output_captured": False})

    def _observe_job_output(self, job: Job, kind: str, *, inspect: bool = False) -> ToolResult:
        record = self._registry.record(job)
        process_observation = None
        # A job can transition from running to terminal once. A terminal result
        # must capture/revalidate its log after cleanup, not reuse an earlier prefix.
        for _ in range(2):
            offset = 0 if inspect else job.reported_bytes
            archive, capture = job.output.snapshot()
            total_lines, total_bytes = capture["lines"], capture["bytes"]
            page, output_byte_ranges = _job_preview(archive, offset) if offset < total_bytes else ("", [])
            process_observation = self._registry.process_snapshot(job) if record['state'] == 'running' else None
            latest = self._registry.record(job)
            became_terminal = record["state"] == "running" and latest["state"] != "running"
            record = latest
            if not became_terminal:
                break
        state = record["state"]
        final_output = state != "running" and not record["cleanup_error"] and capture["capture_complete"]
        outcome = (("Process-group cleanup is not confirmed; " +
                    ("shell exit is not known." if record["exit_code"] is None else
                     f"shell exited {record['exit_code']} (not group completion).")) if record["cleanup_error"] else
                   "Command is still running; no exit code is known." if state == "running" else
                   f"Command exited {record['exit_code']}." if state == "completed" else
                   f"Command {state.replace('_', ' ')}; process group stopped; exit code {record['exit_code']}.")
        command_source = "the original command tool call" if record["status_error"] or record["status_pending"] else str(job.status)
        command = job.command[:1000] + (f"… (complete command in {command_source})" if len(job.command) > 1000 else "")
        stopped = 'UNFINISHED — cancelled; no completed command verdict. ' if state in ('timed_out', 'terminated') else ''
        body = (f"Job {job.id} · {stopped}State: {state}. {outcome} "
                f"Elapsed execution time: {record['elapsed_seconds']:.3f} s.\nInitial cwd: {record['cwd']}\nCommand: {command}\n"
                f"Output observed: {total_lines} lines, {total_bytes} bytes.\n")
        diagnostics = self._job_controls(record, detail=inspect)
        activity = ''
        if state == 'running' and process_observation:
            if process_observation['status'] == 'unavailable':
                activity += f"Current process snapshot unavailable: {process_observation['reason']}. This does not establish zero activity.\n"
            else:
                activity += (f"Current owned process snapshot (Linux /proc; process group/session {process_observation['process_group']}):\n"
                         "PID | command name | state | cumulative user+system CPU seconds\n")
                for row in process_observation['processes']:
                    activity += f"{row['pid']} | {json.dumps(row['name'], ensure_ascii=False)} | {row['state']} | {row['cpu_seconds']:.3f}\n"
                activity += "R=runnable, S=sleeping, D=uninterruptible wait, Z=zombie. CPU time is cumulative, not a rate or completion estimate; process activity does not establish useful progress or a hang.\n"
                if process_observation['capped']:
                    activity += "Snapshot capped at 16 matching processes or 4096 directory entries; additional processes may be omitted.\n"
                if process_observation['unreadable_entries']:
                    activity += f"{process_observation['unreadable_entries']} process entries were unreadable or disappeared; group membership may be incomplete.\n"
        else:
            process_observation = None
        body += activity
        if state != "running" and not final_output:
            body += "Final output is not confirmed; inspect this job again after cleanup.\n"
        if inspect and record["status_error"]:
            body += f"Status file unavailable or stale: {record['status_error']}. This observation uses in-memory state; supervision does not depend on that file. Use job to inspect or stop.\n"
            body += f"Status path (write failed; may be absent or stale): {job.status}\n"
        elif inspect and record["status_pending"]:
            diagnostics += "Status persistence pending; observation uses in-memory state.\n"
        if record["cleanup_error"]:
            body += f"Process-group cleanup not confirmed: {record['cleanup_error']}. Inspect or stop this job again.\n"
        if capture["capture_error"]:
            body += f"Output capture failed: {capture['capture_error']}; this extent contains only committed bytes. Final output is incomplete; do not rerun automatically.\n"
        elif not capture["capture_complete"] and state != "running":
            body += "Output collector has not reached EOF; final output is not yet captured. Use job wait/inspect for a later extent.\n"
        output_page_start = len(body)
        if total_bytes > offset:
            body += (("Captured output (from byte 0; inspection does not advance the wait cursor):\n" if inspect else
                      f"New output since previous command/wait/stop observation (from byte {offset}):\n") + page)
            if "\ufffd" in page:
                body += "\nUTF-8 preview may replace invalid or incomplete byte sequences; extent recovery returns the exact captured bytes."
        else:
            body += "No captured output." if inspect else "No new output."
        if state == "running" and not record["cleanup_error"]:
            body += f"\nYielding did not cancel this job. Use job(id={job.id!r}, action='wait' or 'inspect' or 'stop')."
        status_label = ("Status path (write failed; may be absent or stale)" if record["status_error"] else
                        "Status path (update pending)" if record["status_pending"] else "Status")
        recovery = f"{shlex.quote(sys.executable)} {shlex.quote(str(Path(__file__).with_name('job_output.py')))} --cat {shlex.quote(str(archive))}"
        body += (f"\nSaved-output reference (immutable): {archive}\n"
                 "read_file resolves the exact captured bytes; shell reads of this path return a JSON descriptor, not stdout/stderr.")
        body += " Search this saved output without rerunning: read_file(" + json.dumps({"path": str(archive), "search": "error", "context_lines": 8}) + ")."
        body += (" Final output captured." if final_output else " Output capture is not final.")
        if diagnostics:
            body += '\n' + diagnostics.rstrip()
        if inspect:
            body += f"\nShell recovery command: {recovery}\n{status_label}: {job.status}"
        else:
            body += f"\nDetails/recovery: job(id={job.id!r}, action='inspect')."
        if not inspect:
            job.reported_bytes = total_bytes
        self._job_extents[job.id] = (str(archive), final_output)
        return ToolResult(body, str(archive), {"kind": kind, **record, "total_lines": total_lines,
                                             "total_bytes": total_bytes, "final_output_captured": final_output,
                                             "output_byte_ranges": output_byte_ranges,
                                             "output_page_start": output_page_start, "output_format": FORMAT,
                                             "output_recovery_command": recovery, "capture_error": capture["capture_error"],
                                             "process_observation": process_observation})

    def _exec_command(self, cmd: str, workdir: str, yield_time_ms: int = 10000, kill_after_ms: int | None = None):
        directory = Path(workdir)
        directory = (directory if directory.is_absolute() else self.root / directory).resolve()
        if not directory.is_dir():
            raise ValueError(f'Working directory does not exist: {directory}')
        return self._bash(cmd, yield_time_ms, kill_after_ms, str(directory))

    def _bash(self, command: str, yield_time_ms: int = 10000, kill_after_ms: int | None = None, workdir: str | None = None) -> ToolResult:
        previous = set(self._registry.records)
        try:
            job = self._registry.start(command, kill_after_ms, cwd=workdir)
        except (OSError, ValueError) as error:
            created = [j for key, j in self._registry.records.items() if key not in previous]
            if created:
                return self._observe_job(created[-1], "bash")
            return ToolResult(f"Command was not started in {workdir or self.root}: {type(error).__name__}: {error}. Repair runtime output storage or the reported launch environment before retrying.", "",
                              {"kind": "bash", "command": command, "cwd": str(workdir or self.root), "error": True,
                               "effect_status": "not_started", "observation_retained": False})
        self._wait_job(job, yield_time_ms)
        return self._observe_job(job, "bash")

    def _list_jobs(self, start: int = 1) -> ToolResult:
        with self._registry.lock:
            jobs = list(self._registry.records.values())
        page = [self._registry.record(job) for job in jobs[start - 1:start + 19]]
        archive = self._reserve()
        header = f"Recorded jobs {start}–{start + len(page) - 1} of {len(jobs)}.\n" if page else f"No jobs at start {start}; {len(jobs)} jobs recorded.\n"
        continuation = f"Next page: list_jobs(start={start + len(page)})." if start + len(page) <= len(jobs) else "End of job listing."
        with archive.open("w", encoding="utf-8") as output:
            output.write(header + continuation + "\n")
            for record in page:
                output.write(json.dumps(record, ensure_ascii=False) + "\n")
        rows = [f"Job {r['job_id']}: {r['state']}; shell exit {r['exit_code']}"
                + ('; process-group cleanup unconfirmed; use job inspect/stop' if r['cleanup_error'] else '')
                + f"; launch PID/PGID {r['pid']}; cancellation deadline: {'none requested' if r['timeout_ms'] is None else str(r['timeout_ms']) + ' ms from launch'}; command {r['command'][:240]}{'…' if len(r['command']) > 240 else ''}" for r in page]
        return self._result(header + "\n".join(rows) + "\n" + continuation + "\nFull page records include complete commands, cwd, log/status paths and persistence state.",
                            {"kind": "job_list", "total_jobs": len(jobs), "start": start, "count": len(page)}, archive=archive)

    def _job(self, action: str, id: str, yield_time_ms: int = 1000) -> ToolResult:
        job = self._registry.get(id)
        if action == "stop":
            self._registry.stop(job)
        elif action == "wait":
            self._wait_job(job, yield_time_ms)
        return self._observe_job(job, "job", inspect=action == "inspect")

    def recovery_job(self, args: dict, *, purpose="recovery") -> ToolResult:
        """One bounded read-only process observation for context inspection."""
        validate_call('job', args)
        if args.get('action') != 'inspect':
            raise ValueError("Recovery permits only job(action='inspect', id=...). No process action ran.")
        original = self._job(**args)
        receipt = self._reserve()
        receipt.write_text(original.display + '\nComplete command (JSON string): ' +
                           json.dumps(original.metadata.get('command', ''), ensure_ascii=False) + '\n', encoding='utf-8')
        suffix = (f"\n[Bounded {purpose} job observation. Exact complete observation: {receipt}. "
                  f"Exact output extent: {original.full_path or 'not retained'}. "
                  "Use read_file pages; the saved observation includes its exact output extent. No process was started, stopped or waited on.]")
        # Bound the actual JSON text representation, including escaped controls.
        def size(value):
            return len(json.dumps(value, ensure_ascii=False).encode('utf-8'))
        if size(suffix) > 16000:
            raise ValueError('Recovery observation path exceeds the serialized page allowance')
        low, high = 0, len(original.display)
        while low < high:
            middle = (low + high + 1) // 2
            if size(original.display[:middle] + suffix) <= 16000:
                low = middle
            else:
                high = middle - 1
        display = original.display[:low] + suffix
        metadata = {key: value for key, value in original.metadata.items() if key not in ('output_format', 'output_page_start')}
        if len(metadata.get('command', '')) > 1000:
            metadata['command'] = metadata['command'][:1000] + '… [complete command in saved observation receipt]'
            metadata['command_truncated'] = True
        metadata.update(kind='job', recovery_observation=True, observation_receipt=str(receipt),
                        output_extent_path=original.full_path, observation_truncated=low < len(original.display))
        return ToolResult(display, str(receipt), metadata)

    @staticmethod
    def _wait_job(job, milliseconds):
        deadline = time.monotonic() + milliseconds / 1000
        if job.done.wait(max(0, deadline - time.monotonic())):
            job.output.done.wait(max(0, deadline - time.monotonic()))

    def job_status(self) -> str:
        """Recurring state contains live work and unresolved results, not job history."""
        with self._registry.lock:
            jobs = list(self._registry.records.values())
        records = [self._registry.record(job) for job in jobs]
        active = sum(r['state'] == 'running' for r in records)
        failures = sum(bool(r['cleanup_error']) for r in records)
        attention = []
        for record in records:
            extent = self._job_extents.get(record['job_id'])
            if record['state'] == 'running' or record['cleanup_error'] or not extent or not extent[1]:
                attention.append(record)
        attention.sort(key=lambda r: (not bool(r['cleanup_error']), r['state'] != 'running'))
        rows = [f"Active jobs: {active}; {failures} cleanup failures."]
        shown = 0
        for record in attention[:8]:
            command = record['command'][:160] + ('…' if len(record['command']) > 160 else '')
            row = f"Job {record['job_id']}: {record['state']}; command {command}"
            if record['state'] != 'running':
                row += f"; exit {record['exit_code']}"
                extent = self._job_extents.get(record['job_id'])
                if not extent or not extent[1]:
                    row += "; final output not yet returned"
            if record['cleanup_error']:
                row += f"; cleanup not confirmed: {record['cleanup_error'][:160]}"
            if len(('\n'.join(rows) + row).encode()) > 3600:
                break
            rows.append(row)
            shown += 1
        if shown < len(attention):
            rows.append(f"Showing {shown} of {len(attention)} jobs needing attention.")
        if attention:
            rows.append("job(id=..., action='inspect') retrieves current outcome/output. Complete registry: list_jobs(start=1).")
        return "\n".join(rows)

    def output_capture_status(self) -> dict:
        records = [(job.id, job.output.record()) for job in self._registry.records.values() if job.output]
        pending = [ident for ident, record in records if not record["capture_complete"] and not record["capture_error"]]
        errors = [{"job_id": ident, "error": record["capture_error"], "captured_bytes": record["bytes"]}
                  for ident, record in records if record["capture_error"]]
        return {"total": len(records), "complete": len(records) - len(pending) - len(errors),
                "pending_job_ids": pending, "errors": errors,
                "note": "Captured output completion is separate from process cleanup; pending/error extents contain only committed bytes."}

    def close(self) -> None:
        try:
            self._registry.close()
        finally:
            deadline = time.monotonic() + .1
            for job in self._registry.records.values():
                if job.output:
                    job.output.done.wait(max(0, deadline - time.monotonic()))
