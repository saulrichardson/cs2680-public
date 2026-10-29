#!/usr/bin/env python3
"""Local operator UI. Reads immutable evidence; launches only explicit new runs.
The Haskell harness remains responsible for execution and context construction.
"""

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import threading
import time
import uuid
import tempfile
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlparse

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from src.store import Store
from scripts.evidence import read_only_projection

TIMELINE_KINDS = (
    "task",
    "session_start",
    "response",
    "intent",
    "observation",
    "summary",
    "finalization",
)


def read_json(path, default=None):
    try:
        return json.loads(path.read_text())
    except (FileNotFoundError, ValueError):
        return default


def object_row(row):
    return dict(
        ref="obj:" + str(row["id"]),
        kind=row["kind"],
        scope=row["scope"],
        body=json.loads(row["body"]),
        sha256=row["sha256"],
        alias=row["alias"],
        created=row["created"],
    )


class Runs:
    def __init__(self, evidence, sessions, binary=None, connection_file=None):
        self.connection_file = (
            Path(connection_file).expanduser().resolve() if connection_file else None
        )
        self.connection = (
            read_json(self.connection_file, {}) if self.connection_file else {}
        )
        self.sessions = Path(sessions).resolve()
        self.sessions.mkdir(parents=True, exist_ok=True)
        self.binary = Path(binary).resolve() if binary else None
        self.paths, self.processes = {}, {}
        self.lock = threading.Lock()
        self.inspections = {}
        self.inspection_lock = threading.Lock()
        for root in [*evidence, self.sessions]:
            root = Path(root).resolve()
            paths = (
                [root]
                if root.name == "context.sqlite3"
                else root.rglob("context.sqlite3")
            )
            for path in paths:
                self.register(path)
        # Include a launch which stopped before creating its database.
        for path in self.sessions.glob("*/ui-run.json"):
            self.register(path.parent / "context.sqlite3")

    def register(self, path):
        path = Path(path).resolve()
        ident = hashlib.sha256(str(path).encode()).hexdigest()[:16]
        self.paths[ident] = path
        return ident

    def path(self, ident):
        if ident not in self.paths:
            raise ValueError("Unknown run")
        return self.paths[ident]

    def environment(self):
        # Capture configuration per launch. Changing the connection never reroutes an active run.
        env = dict(os.environ)
        if self.connection:
            env.update(
                A4_MODEL=self.connection["model"],
                A4_BASE_URL="https://openrouter.ai/api/v1",
                A4_API_KEY=self.connection["key"],
            )
        elif env.get("OPENROUTER_API_KEY") and not env.get("A4_API_KEY"):
            env.update(
                A4_MODEL=env.get("OPENROUTER_MODEL", "qwen/qwen3.6-35b-a3b"),
                A4_BASE_URL="https://openrouter.ai/api/v1",
                A4_API_KEY=env["OPENROUTER_API_KEY"],
            )
        if self.connection.get("reasoning_effort"):
            env["A4_REASONING_EFFORT"] = self.connection["reasoning_effort"]
        return env

    def config(self):
        env = self.environment()
        model = env.get("A4_MODEL") or env.get("CS2680_MODEL_STANDARD")
        endpoint = env.get("A4_BASE_URL") or env.get("CS2680_BASE_URL")
        configured = bool(
            model and endpoint and (env.get("A4_API_KEY") or env.get("CS2680_API_KEY"))
        )
        binary = bool(
            self.binary and self.binary.is_file() and os.access(self.binary, os.X_OK)
        )
        return dict(
            schema_version=1,
            provider_configured=configured,
            binary_available=binary,
            can_start=configured and binary,
            model=model,
            reasoning_effort=env.get("A4_REASONING_EFFORT"),
            provider="OpenRouter"
            if endpoint == "https://openrouter.ai/api/v1"
            else "Configured API"
            if endpoint
            else None,
            missing=[
                x
                for x, ok in [
                    ("Provider connection", configured),
                    ("Native Haskell executable", binary),
                ]
                if not ok
            ],
        )

    def connect(self, payload):
        model, key = payload.get("model"), payload.get("key")
        if not isinstance(model, str) or not model.strip():
            raise ValueError("Enter an OpenRouter model ID")
        model = model.strip()
        if key is None or key == "":
            env = self.environment()
            key = (
                env.get("A4_API_KEY")
                if env.get("A4_BASE_URL") == "https://openrouter.ai/api/v1"
                else env.get("OPENROUTER_API_KEY")
            )
        if not isinstance(key, str) or not key.strip():
            raise ValueError("Enter your OpenRouter API key")
        key = key.strip()
        if "\n" in key or "\r" in key:
            raise ValueError("Invalid API key")
        # Read-only authentication/model checks: no inference or purchased credits.
        try:
            headers = {"Authorization": "Bearer " + key}
            with urlopen(
                Request("https://openrouter.ai/api/v1/key", headers=headers), timeout=20
            ) as r:
                data = json.load(r).get("data", {})
            if data.get("limit_remaining") is not None and data["limit_remaining"] <= 0:
                raise ValueError("The OpenRouter key has no remaining allowance")
            with urlopen(
                Request("https://openrouter.ai/api/v1/models", headers=headers),
                timeout=20,
            ) as r:
                models = json.load(r).get("data", [])
        except HTTPError as e:
            code = e.code
            e.close()
            raise ValueError(
                f"OpenRouter connection check returned HTTP {code}; check the key and account"
            ) from None
        except (URLError, TimeoutError):
            raise ValueError(
                "Could not reach OpenRouter; the connection was not changed"
            ) from None
        selected = next((m for m in models if m.get("id") == model), None)
        if not selected:
            raise ValueError("Model ID is not in the current OpenRouter catalog")
        if "tools" not in selected.get("supported_parameters", []):
            raise ValueError(
                "This model does not advertise tool support; choose a model with tools"
            )
        effort = payload.get("reasoning_effort", self.connection.get("reasoning_effort"))
        if effort is not None and (not isinstance(effort, str) or not effort.strip()):
            raise ValueError("Reasoning effort must be a nonempty string or null")
        connection = {"model": model, "key": key}
        if effort is not None:
            connection["reasoning_effort"] = effort.strip()
        if self.connection_file:
            self.connection_file.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            fd, name = tempfile.mkstemp(
                prefix=".connection-", dir=self.connection_file.parent
            )
            try:
                with os.fdopen(fd, "w") as f:
                    json.dump(connection, f)
                os.replace(name, self.connection_file)
            finally:
                if os.path.exists(name):
                    os.unlink(name)
        self.connection = connection
        return self.config()

    def summary(self, ident):
        path = self.path(ident)
        meta = read_json(path.parent / "ui-run.json", {})
        initial = read_json(path.parent / "initial.json", {})
        task = (
            meta.get("task")
            or initial.get("task")
            or path.parent.name.replace("test_", "").replace("_", " ")
        )
        final = read_json(path.parent / "final.json")
        process = self.processes.get(ident)
        exit_code = process.poll() if process else None
        status = (
            (
                "running"
                if exit_code is None
                else "failed"
                if exit_code != 0
                else "completed"
            )
            if process
            else ("completed" if final else "unobserved" if meta else "recorded")
        )
        labels = {
            "test_full_investigation_challenge_compaction_and_exact_grade": "Caller repair & independent review",
            "test_command_line_search_pipes_unicode_errors_and_database_retrieval": "Search, edit & evidence retrieval",
            "test_failed_grade_leaves_owner_free_to_repair": "Repair after an unsuccessful check",
            "test_capacity_rejection_and_invalid_batch_have_no_side_effects": "Context capacity & continuation",
            "test_failed_recording_preserves_intent_and_never_replays_command": "Interrupted observation recording",
            "test_rate_rejection_retries_same_request_without_output_cap": "Provider retry & completion",
            "test_uncertain_grade_is_not_replayed_or_reported_success": "Uncertain grading receipt",
        }
        synthetic = not bool(meta) and "test_" in path.parent.name
        return dict(
            id=ident,
            title=meta.get("title") or labels.get(path.parent.name, task[:72]),
            task=task,
            status=status,
            synthetic=synthetic,
            created=meta.get(
                "created", path.stat().st_mtime if path.exists() else time.time()
            ),
            outcome=(
                "passed"
                if final.get("passed")
                else "failed"
                if final.get("graded")
                else "ungraded"
            )
            if final and status == "completed"
            else None,
            repository=meta.get("repository")
            or initial.get("workspace", {}).get("root"),
            exit_code=exit_code,
            overlapping_runs=meta.get("overlapping_runs", []),
        )

    def snapshot(self, ident):
        path = self.path(ident)
        summary = self.summary(ident)
        if not path.exists():
            return dict(
                schema_version=1,
                run=summary,
                revision=0,
                records=[],
                index=[],
                links=[],
                counts={},
            )
        with Store(path, readonly=True) as store:
            store.db.execute("BEGIN")
            index = [
                dict(
                    ref="obj:" + str(r["id"]),
                    kind=r["kind"],
                    scope=r["scope"],
                    alias=r["alias"],
                    created=r["created"],
                )
                for r in store.db.execute(
                    "SELECT id,kind,scope,alias,created FROM objects ORDER BY id"
                )
            ]
            records = [
                object_row(r)
                for r in store.db.execute(
                    "SELECT * FROM objects WHERE kind IN ("
                    + ",".join("?" for _ in TIMELINE_KINDS)
                    + ") ORDER BY id",
                    TIMELINE_KINDS,
                )
            ]
            links = [
                dict(
                    source="obj:" + str(r["source"]),
                    relation=r["relation"],
                    target="obj:" + str(r["target"]),
                )
                for r in store.db.execute(
                    "SELECT * FROM links ORDER BY source,relation,target"
                )
            ]
            counts = {
                r["kind"]: r["n"]
                for r in store.db.execute(
                    "SELECT kind,count(*) n FROM objects GROUP BY kind"
                )
            }
            revision = store.db.execute(
                "SELECT COALESCE(MAX(id),0) FROM objects"
            ).fetchone()[0]
        # No inference that a still-open historical job is currently running.
        return dict(
            schema_version=1,
            run=summary,
            revision=revision,
            records=records,
            index=index,
            links=links,
            counts=counts,
        )

    def inspect_request(self, ident, ref):
        # Immutable sources; the only executable protocol allowed is store_get.
        # Do not cache unavailable results: installing a binary can repair them.
        with Store(self.path(ident), readonly=True) as store:
            store.db.execute("BEGIN")
            record = store.get(ref, kind="request")
            binary_stamp = None
            if self.binary and self.binary.is_file():
                stat = self.binary.stat()
                binary_stamp = (str(self.binary), stat.st_mtime_ns, stat.st_size)
            key = (ident, record["ref"], record["sha256"], binary_stamp)
            with self.inspection_lock:
                cached = self.inspections.get(key)
            if cached is not None:
                return cached
            result = dict(request_ref=record["ref"], request=record["body"],
                          scope=record["scope"], reconstruction="unavailable",
                          mapping_verified=False, mapping=[], inspection=None)
            if binary_stamp:
                try:
                    result.update(read_only_projection(store, self.binary,
                        dict(mode="inspect_request", record=record)))
                except (OSError, ValueError, RuntimeError, KeyError) as error:
                    result["warning"] = "Source reconstruction unavailable: " + str(error)
            else:
                result["warning"] = "No inspection executable available. Showing the recorded request without inferred provenance."
            if result["reconstruction"] != "unavailable":
                with self.inspection_lock:
                    # Bound retained large request bodies in long-lived servers.
                    if len(self.inspections) >= 16:
                        self.inspections.pop(next(iter(self.inspections)))
                    self.inspections[key] = result
            return result

    def launch(self, payload):
        if not self.config()["can_start"]:
            raise ValueError(
                "Configure the provider and native Haskell executable before starting a run"
            )
        task, repository = payload.get("task"), payload.get("repository", "")
        if not isinstance(task, str) or not task.strip():
            raise ValueError("A task is required")
        if not isinstance(repository, str):
            raise ValueError("Workspace path must be text")
        resume_id = payload.get("resume_id")
        resume_folder = None
        if resume_id:
            previous = self.summary(resume_id)
            process = self.processes.get(resume_id)
            if process and process.poll() is None:
                raise ValueError("This conversation is still running; its executor owns the current turn")
            resume_folder = self.path(resume_id).parent
            repository = previous['repository'] or repository
        repo = Path(repository).expanduser().resolve() if repository.strip() else None
        if repo is not None:
            if not repo.is_dir():
                raise ValueError("Workspace must be an existing folder")
            if repo == self.sessions or repo in self.sessions.parents or self.sessions in repo.parents:
                raise ValueError("Choose a workspace outside session evidence")
        with self.lock:
            overlaps = []
            for ident in self.paths:
                if ident == resume_id:
                    continue
                previous = self.summary(ident)
                previous_root = Path(previous["repository"]).resolve() if previous["repository"] else None
                if repo is not None and previous_root is not None and previous["status"] in ("running", "unobserved") and (
                    repo == previous_root or repo in previous_root.parents or previous_root in repo.parents
                ):
                    overlaps.append(ident)
            folder = resume_folder or self.sessions / uuid.uuid4().hex
            folder.mkdir(exist_ok=resume_folder is not None)
            if repo is None:
                repo = self.sessions.parent / "workspaces" / folder.name
                repo.mkdir(parents=True)
            task_path = folder / ("turn-" + uuid.uuid4().hex + ".txt")
            task_path.write_text(task)
            prior = read_json(folder / "ui-run.json", {})
            (folder / "ui-run.json").write_text(
                json.dumps(
                    dict(
                        task=prior.get("task", task),
                        title=prior.get("title", task.strip().splitlines()[0][:72]),
                        repository=str(repo),
                        created=prior.get("created", time.time()),
                        overlapping_runs=overlaps,
                    )
                )
            )
            with (folder / "launch.log").open("ab") as output:
                process = subprocess.Popen(
                    [
                        sys.executable,
                        str(ROOT / "scripts/local.py"),
                        "--workspace",
                        str(repo),
                        "--task",
                        str(task_path),
                        "--archive",
                        str(folder),
                        "--binary",
                        str(self.binary),
                    ],
                    cwd=ROOT,
                    stdout=output,
                    stderr=subprocess.STDOUT,
                    start_new_session=True,
                    env=self.environment(),
                )
            ident = self.register(folder / "context.sqlite3")
            self.processes[ident] = process
        return dict(self.summary(ident), overlapping_runs=overlaps)


def make_server(runs, host="127.0.0.1", port=8765, assets=None):
    assets = Path(assets or ROOT / "ui/dist").resolve()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def reply(self, value, status=200):
            self.send_bytes(
                json.dumps(value, ensure_ascii=False).encode(),
                "application/json",
                status,
            )

        def send_bytes(self, raw, kind, status=200, attachment=None):
            self.send_response(status)
            self.send_header("Content-Type", kind)
            self.send_header("Content-Length", str(len(raw)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header(
                "Content-Security-Policy",
                "default-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'",
            )
            if attachment:
                self.send_header(
                    "Content-Disposition", 'attachment; filename="' + attachment + '"'
                )
            self.end_headers()
            self.wfile.write(raw)

        def valid_host(self):
            return self.headers.get("Host", "") in (
                f"127.0.0.1:{self.server.server_port}",
                f"localhost:{self.server.server_port}",
            )

        def do_GET(self):
            if not self.valid_host():
                return self.reply({"error": "Loopback host required"}, 403)
            try:
                url = urlparse(self.path)
                parts = [unquote(x) for x in url.path.split("/") if x]
                q = parse_qs(url.query)
                if parts == ["api", "config"]:
                    return self.reply(runs.config())
                if parts == ["api", "runs"]:
                    return self.reply(
                        {
                            "runs": sorted(
                                [runs.summary(i) for i in list(runs.paths)],
                                key=lambda r: r["created"],
                                reverse=True,
                            )
                        }
                    )
                if len(parts) >= 3 and parts[:2] == ["api", "runs"]:
                    ident = parts[2]
                    if len(parts) == 3:
                        return self.reply(runs.snapshot(ident))
                    if len(parts) == 6 and parts[3] == "requests" and parts[5] == "inspection":
                        return self.reply(runs.inspect_request(ident, parts[4]))
                    if parts[3:] == ["search"]:
                        with Store(runs.path(ident), readonly=True) as store:
                            return self.reply(
                                store.query(
                                    text=q.get("q", [""])[0],
                                    kind=q.get("kind", [None])[0],
                                    after=int(q.get("after", ["0"])[0]),
                                )
                            )
                    if parts[3:] == ["log"]:
                        path = runs.path(ident).parent / "launch.log"
                        return self.reply(
                            {
                                "text": path.read_text(errors="replace")[-64000:]
                                if path.exists()
                                else "",
                                "tail_characters": 64000,
                            }
                        )
                    if len(parts) == 5 and parts[3] == "objects":
                        with Store(runs.path(ident), readonly=True) as store:
                            return self.reply(store.get(parts[4]))
                    if len(parts) == 5 and parts[3] == "blob":
                        with Store(runs.path(ident), readonly=True) as store:
                            if q.get("download") == ["1"]:
                                record = store.get(parts[4])
                                row = store.db.execute(
                                    "SELECT bytes FROM blobs WHERE object_id=?",
                                    (store.number(parts[4]),),
                                ).fetchone()
                                if row is None:
                                    raise ValueError("Object has no binary content")
                                if (
                                    hashlib.sha256(row[0]).hexdigest()
                                    != record["body"]["sha256"]
                                ):
                                    raise ValueError("Blob integrity check failed")
                                return self.send_bytes(
                                    row[0],
                                    "application/octet-stream",
                                    attachment=parts[4].replace(":", "-") + ".bin",
                                )
                            return self.reply(
                                store.blob(
                                    parts[4], int(q.get("offset", ["0"])[0]), 16000
                                )
                            )
                if parts and parts[0] == "api":
                    return self.reply({"error": "Unknown endpoint"}, 404)
                target = (assets / unquote(url.path).lstrip("/")).resolve()
                if not target.is_relative_to(assets):
                    return self.reply({"error": "Unknown asset"}, 404)
                if not target.is_file():
                    target = assets / "index.html"
                if not target.exists():
                    return self.reply(
                        {
                            "error": "Build the frontend with npm run build in assignment4/ui"
                        },
                        503,
                    )
                mime = {
                    ".html": "text/html",
                    ".js": "application/javascript",
                    ".css": "text/css",
                    ".svg": "image/svg+xml",
                }.get(target.suffix, "application/octet-stream")
                return self.send_bytes(target.read_bytes(), mime)
            except (ValueError, sqlite3.Error, FileNotFoundError) as e:
                self.reply({"error": str(e)}, 400)

        def do_POST(self):
            origin = self.headers.get("Origin")
            if (
                not self.valid_host()
                or self.headers.get("X-Investigator-UI") != "1"
                or (
                    origin
                    and origin
                    not in (
                        f"http://127.0.0.1:{self.server.server_port}",
                        f"http://localhost:{self.server.server_port}",
                    )
                )
            ):
                return self.reply({"error": "Same-origin UI request required"}, 403)
            if self.path not in ("/api/runs", "/api/connection"):
                return self.reply({"error": "Unknown endpoint"}, 404)
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length <= 1048576:
                    raise ValueError("Request must be 1..1048576 bytes")
                payload = json.loads(self.rfile.read(length))
                if not isinstance(payload, dict):
                    raise ValueError("Expected an object")
                if self.path == "/api/connection":
                    self.reply(runs.connect(payload))
                else:
                    self.reply(runs.launch(payload), 201)
            except (ValueError, OSError) as e:
                self.reply({"error": str(e)}, 400)

    return ThreadingHTTPServer((host, port), Handler)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--evidence",
        type=Path,
        action="append",
        help="Database or directory to discover; repeat to add more",
    )
    parser.add_argument("--sessions", type=Path, default=Path.home() / ".local/share/investigator/sessions")
    parser.add_argument("--binary", type=Path)
    parser.add_argument("--port", type=int, default=8794)
    parser.add_argument(
        "--connection-file",
        type=Path,
        default=Path.home() / ".config/investigator/connection.json",
    )
    args = parser.parse_args()
    binary = args.binary
    if binary is None and shutil.which("cabal"):
        result = subprocess.run(
            ["cabal", "list-bin", "exe:investigator"],
            cwd=ROOT,
            capture_output=True,
            text=True,
        )
        if result.returncode == 0:
            binary = Path(result.stdout.strip())
    evidence = args.evidence or [ROOT / ".run/object-store-release-check/scenarios", ROOT / ".run/ui-sessions"]
    server = make_server(
        Runs(evidence, args.sessions, binary, args.connection_file), port=args.port
    )
    print(f"Investigator: http://127.0.0.1:{args.port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
