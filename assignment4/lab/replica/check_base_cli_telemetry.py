"""Exercise the exact vendor-cache cleanup without touching real AWS state."""
import json
from pathlib import Path
import sqlite3
import tempfile
import uuid
import image_snapshot as image

with tempfile.TemporaryDirectory() as temporary:
    system = Path(temporary)
    image.CACHE = system / 'opt/a4-image-cache'
    image.CACHE.mkdir(parents=True)
    def cache(home):
        root = system / home / '.aws'
        directory = root / 'cli/cache'; directory.mkdir(parents=True)
        with sqlite3.connect(directory / 'session.db') as db:
            db.execute('CREATE TABLE session (key TEXT PRIMARY KEY, session_id TEXT NOT NULL, timestamp INTEGER NOT NULL)')
            db.execute('CREATE TABLE host_id (key INTEGER PRIMARY KEY, id TEXT UNIQUE NOT NULL)')
            db.execute('INSERT INTO host_id VALUES (?,?)', (0,str(uuid.uuid4())))
            db.execute('INSERT INTO session VALUES (?,?,?)', ('a'*12,'b'*12,1700000000))
        return root
    first = cache('root'); second = cache('home/ubuntu')
    foreign = first / 'credentials'; foreign.write_text('fixture-must-survive')
    before = {p: p.read_bytes() for p in system.rglob('*') if p.is_file()}
    try:
        image.remove_base_cli_telemetry(image.CACHE, system)
        raise AssertionError('Unknown AWS state was accepted')
    except ValueError:
        pass
    assert all(p.read_bytes() == raw for p, raw in before.items())
    foreign.unlink()
    image.remove_base_cli_telemetry(image.CACHE, system)
    assert not first.exists() and not second.exists()
    receipt = json.loads((image.CACHE / 'base-cli-telemetry.json').read_text())
    assert len(receipt) == 2 and all(row['state'] == 'removed_vendor_session_ids' for row in receipt)
    (image.CACHE / 'base-cli-telemetry.json').unlink()
    first = cache('root')
    with sqlite3.connect(first / 'cli/cache/session.db') as db:
        db.execute('CREATE TABLE credentials (value TEXT)')
    before = (first / 'cli/cache/session.db').read_bytes()
    try:
        image.remove_base_cli_telemetry(image.CACHE, system)
        raise AssertionError('Unknown database schema was accepted')
    except ValueError:
        pass
    assert (first / 'cli/cache/session.db').read_bytes() == before
print(json.dumps({'passed':True,'valid_vendor_cache_removed':True,'unknown_files_and_schema_preserved':True}))
