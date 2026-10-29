#!/usr/bin/env python3
"""Run the identical Haskell loop against a local workspace."""
import argparse
import json
import os
from pathlib import Path
import sys
import tempfile
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.bridge import Bridge, Workspace, HERE
from src.api import Provider

p = argparse.ArgumentParser()
p.add_argument('--workspace', '--repo', dest='workspace', help='Existing folder; omit for a new empty workspace')
p.add_argument('--enable-review', action='store_true', help='Enable an independent investigator and its guidance')
p.add_argument('--task', required=True, help='Public task text file')
p.add_argument('--binary', required=True)
p.add_argument('--archive', help='Evidence directory; reuse to resume without replaying prior actions')
a = p.parse_args()
if a.archive and not a.workspace:
    saved = Path(a.archive).expanduser() / 'initial.json'
    if saved.exists():
        a.workspace = json.loads(saved.read_text())['workspace']['root']
root = Path(a.workspace).expanduser().resolve() if a.workspace else Path(tempfile.mkdtemp(prefix='investigator-workspace-'))
if not root.is_dir():
    p.error('Workspace must be an existing directory')
archive = Path(a.archive).resolve() if a.archive else Path(tempfile.mkdtemp(prefix='a4-run-'))
archive.mkdir(parents=True, exist_ok=True)
if root == archive or root in archive.parents:
    p.error('Archive must be outside the task repository')
ws = Workspace(str(root), str(archive / 'workspace'), capture_patch=False)
bridge = Bridge(ws, archive, Provider(archive), review_enabled=a.enable_review)
settings = None
if effort := os.environ.get('A4_REASONING_EFFORT'):
    settings = {'model': bridge.provider.model, 'parallel_tool_calls': False,
                'reasoning': {'effort': effort, 'exclude': False}}
try:
    result = bridge.run(Path(a.binary).resolve(), Path(a.task).read_text(), (HERE/'prompt.txt').read_text(), request_settings=settings)
finally:
    bridge.close()
print(result)
print('Evidence:', archive)
