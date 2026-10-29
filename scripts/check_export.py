#!/usr/bin/env python3
"""Check the curated tree without credentials, imports or paid services."""
import argparse
import ast
import json
from pathlib import Path
import re
import subprocess
from urllib.parse import unquote

ROOT = Path(__file__).resolve().parents[1]
EMAIL = re.compile(rb"[A-Za-z0-9_.+%-]{1,80}@[A-Za-z0-9.-]{1,80}\.[A-Za-z]{2,24}\b")
EXAMPLE_DOMAINS = {b"example.com", b"example.org", b"example.net", b"example.invalid", b"users.noreply.github.com"}
FIXTURES = {f"assignment1/fixtures/{name}.jsonl" for name in (
    "flat", "flat-allowed", "failed", "subagent", "subagent-forward",
)}
RULES = {
    "provider token": rb"\bsk-(?:proj-|ant-api\d*-|or-v1-)?[A-Za-z0-9_-]{20,}",
    "GitHub token": rb"\b(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{40,})",
    "AWS identifier": rb"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b",
    "signed URL": rb"(?i)X-Amz-(?:Signature=[a-f0-9]{64}|Credential=(?:AKIA|ASIA)[A-Z0-9]{16})",
    "private key": rb"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----\s*[A-Za-z0-9+/]{32,}",
    "JWT": rb"\beyJ[A-Za-z0-9_-]{15,}\.[A-Za-z0-9_-]{15,}\.[A-Za-z0-9_-]{15,}",
    "literal credential": rb"(?i)(?:CS2680_API_KEY|OPENAI_API_KEY|OPENROUTER_API_KEY|ANTHROPIC_API_KEY|AWS_SECRET_ACCESS_KEY|password|access_token)[\"' \t]{0,4}[:=][\"' \t]{0,4}[A-Za-z0-9_+/=-]{24,}",
    "personal workstation": rb"/Users/(?!example/|me/|name/|test/)[A-Za-z][A-Za-z0-9_.-]{2,40}/",
}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--history", action="store_true", help="also inspect all reachable Git blobs and commit metadata")
    args = parser.parse_args()
    if (ROOT / ".git").exists():
        paths = subprocess.check_output(["git", "-C", str(ROOT), "ls-files", "-z"]).decode().split("\0")
        paths = [p for p in paths if p]
    else:
        paths = [p.relative_to(ROOT).as_posix() for p in ROOT.rglob("*") if p.is_file()]
    errors = []
    compiled = [(name, re.compile(pattern)) for name, pattern in RULES.items()]
    def sensitive(data, name):
        for rule, regex in compiled:
            for match in regex.finditer(data):
                line = data.count(b"\n", 0, match.start()) + 1
                errors.append(f"{name}:{line}: {rule} candidate (value withheld)")
        for match in EMAIL.finditer(data):
            domain = match.group().rsplit(b"@", 1)[1].lower()
            if domain not in EXAMPLE_DOMAINS and not domain.endswith((b".invalid", b".test")):
                line = data.count(b"\n", 0, match.start()) + 1
                errors.append(f"{name}:{line}: non-example contact address (value withheld)")

    for name in paths:
        path = ROOT / name
        if path.is_symlink():
            errors.append(f"{name}: symlink needs review")
            continue
        if name in {"PUBLIC_EXPORT_MANIFEST.json", "assignment1/release-manifest.json", "assignment1/writeup.md", "assignment2/writeup.tex"}:
            errors.append(f"{name}: unnecessary release or submission record")
        if name.startswith("assignment4/experiments/"):
            errors.append(f"{name}: private campaign material")
        if name.startswith("assignment4/research/") and not (
            name.startswith("assignment4/research/repair-guidance/solver/")
            or name.startswith("assignment4/research/repair-guidance/integration/")
            or name in {"assignment4/research/repair-guidance/README.md", "assignment4/research/repair-guidance/manifest.json"}
        ):
            errors.append(f"{name}: internal research records")
        parts = set(path.relative_to(ROOT).parts)
        if parts & {"developer_logs", "sessions", "starter_code", "mytest", ".local", ".scratch"}:
            errors.append(f"{name}: excluded private directory")
        if path.suffix.lower() in {".gz", ".zip", ".zst", ".pdf", ".sqlite", ".db", ".pem", ".key"}:
            errors.append(f"{name}: excluded artifact")
        if path.suffix == ".jsonl" and name not in FIXTURES:
            errors.append(f"{name}: unreviewed transcript")
        if path.name.startswith(".env") or path.name == "student_name.json":
            errors.append(f"{name}: local configuration or identity")
        if name in {"assignment2/evaluation_scripts/agent_task_input.json", "assignment2/evaluation_scripts/task_test.json"}:
            errors.append(f"{name}: excluded course dataset")
        data = path.read_bytes()
        sensitive(data, name)
        if path.suffix == ".py":
            try:
                ast.parse(data, filename=name)
            except SyntaxError as exc:
                errors.append(f"{name}:{exc.lineno}: {exc.msg}")
        if name in FIXTURES:
            for line in data.decode().splitlines():
                json.loads(line)
        if path.suffix == ".md":
            for link in re.findall(r"\]\(([^)]+)\)", data.decode()):
                if "://" in link or link.startswith(("#", "mailto:", "/")):
                    continue
                target = unquote(link.split("#")[0])
                if target and not (path.parent / target).exists():
                    errors.append(f"{name}: missing Markdown target {target}")
    if args.history:
        if not (ROOT / ".git").exists():
            raise SystemExit("--history requires a Git checkout")
        objects = subprocess.check_output(["git", "-C", str(ROOT), "rev-list", "--objects", "--all"]).decode().splitlines()
        process = subprocess.Popen(["git", "-C", str(ROOT), "cat-file", "--batch"], stdin=subprocess.PIPE, stdout=subprocess.PIPE)
        scanned = 0
        for entry in objects:
            oid, _, name = entry.partition(" ")
            process.stdin.write((oid + "\n").encode())
            process.stdin.flush()
            header = process.stdout.readline().split()
            data = process.stdout.read(int(header[-1]))
            process.stdout.read(1)
            if header[1] == b"blob":
                sensitive(data, "history:" + oid[:12] + ":" + name)
                scanned += 1
            elif header[1] == b"commit":
                sensitive(data, "commit:" + oid[:12])
        process.stdin.close()
        if process.wait() != 0:
            errors.append("Git history inspection failed")
        print(f"Inspected {scanned} reachable historical file versions and commit metadata.")
    if errors:
        raise SystemExit("\n".join(errors))
    print(f"Validated {len(paths)} files: source syntax, fixture JSON, local Markdown targets and export policy.")


if __name__ == "__main__":
    main()
