"""Append-only SQLite objects. No workflow, provider, or model decisions live here."""
import base64
import hashlib
import json
import sqlite3
import time
from pathlib import Path


def encoded(value):
    return json.dumps(value, ensure_ascii=False, separators=(',', ':'), sort_keys=True)


def content_pieces(raw):
    """Stable large JSON strings are shared regardless of their position.

    A tool result, instruction or assistant message is usually one such string.
    Small syntax/metadata spans stay bounded. Joining pieces reproduces the
    original canonical JSON byte for byte; nothing is summarized or discarded.
    """
    start = cursor = 0
    while True:
        quote = raw.find('"', cursor)
        if quote < 0:
            break
        # CPython's JSON string scanner avoids a Python/regex walk over every
        # character of the repeated transcript. Input is encoded JSON already.
        _, end = json.decoder.scanstring(raw, quote + 1)
        cursor = end
        if end - quote >= 256:
            for offset in range(start, quote, 8192):
                yield raw[offset:min(offset + 8192, quote)]
            yield raw[quote:end]
            start = end
    for offset in range(start, len(raw), 8192):
        yield raw[offset:offset + 8192]


class Store:
    def __init__(self, path, readonly=False):
        self.path = Path(path)
        if not readonly:
            self.path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.path.resolve().as_uri()+'?mode=ro', uri=True) if readonly else sqlite3.connect(self.path)
        self.db.row_factory = sqlite3.Row
        self.db.execute('PRAGMA foreign_keys=ON')
        self.db.execute('PRAGMA synchronous=FULL')
        shape = self.db.execute("SELECT type FROM sqlite_master WHERE name='objects'").fetchone()
        self.shared = shape is None or shape['type'] == 'view'
        if readonly:
            return
        if self.shared:
            if shape is None:
                self._initialize_shared()
            return
        self.db.executescript("""
          CREATE TABLE IF NOT EXISTS objects (
            id INTEGER PRIMARY KEY, kind TEXT NOT NULL, scope TEXT NOT NULL,
            body TEXT NOT NULL, sha256 TEXT NOT NULL, created REAL NOT NULL,
            alias TEXT UNIQUE);
          CREATE TABLE IF NOT EXISTS links (
            source INTEGER NOT NULL REFERENCES objects(id),
            relation TEXT NOT NULL, target INTEGER NOT NULL REFERENCES objects(id),
            PRIMARY KEY(source, relation, target));
          CREATE TABLE IF NOT EXISTS blobs (
            object_id INTEGER PRIMARY KEY REFERENCES objects(id), bytes BLOB NOT NULL);
          CREATE TRIGGER IF NOT EXISTS immutable_blobs_update BEFORE UPDATE ON blobs
            BEGIN SELECT RAISE(ABORT,'blobs are immutable'); END;
          CREATE TRIGGER IF NOT EXISTS immutable_blobs_delete BEFORE DELETE ON blobs
            BEGIN SELECT RAISE(ABORT,'blobs are immutable'); END;
          CREATE INDEX IF NOT EXISTS by_scope_kind ON objects(scope,kind,id);
          CREATE INDEX IF NOT EXISTS incoming ON links(target,source);
          CREATE TRIGGER IF NOT EXISTS immutable_objects_update BEFORE UPDATE ON objects
            BEGIN SELECT RAISE(ABORT,'objects are immutable'); END;
          CREATE TRIGGER IF NOT EXISTS immutable_objects_delete BEFORE DELETE ON objects
            BEGIN SELECT RAISE(ABORT,'objects are immutable'); END;
          CREATE TRIGGER IF NOT EXISTS immutable_links_update BEFORE UPDATE ON links
            BEGIN SELECT RAISE(ABORT,'links are immutable'); END;
          CREATE TRIGGER IF NOT EXISTS immutable_links_delete BEFORE DELETE ON links
            BEGIN SELECT RAISE(ABORT,'links are immutable'); END;
        """)

    def _initialize_shared(self):
        """New archives share immutable content; legacy databases stay untouched.

        Native SQLite views retain the original objects/blobs read interface,
        including body hashes, JSON extraction and read-only fleet observers.
        No custom SQLite function or runtime import is needed to read an archive.
        """
        self.db.executescript("""BEGIN IMMEDIATE;
          CREATE TABLE IF NOT EXISTS object_headers (
            id INTEGER PRIMARY KEY, kind TEXT NOT NULL, scope TEXT NOT NULL,
            sha256 TEXT NOT NULL, created REAL NOT NULL, alias TEXT UNIQUE);
          CREATE TABLE IF NOT EXISTS content_pieces (
            sha256 TEXT PRIMARY KEY, text TEXT NOT NULL);
          CREATE TABLE IF NOT EXISTS object_parts (
            object_id INTEGER NOT NULL REFERENCES object_headers(id), position INTEGER NOT NULL,
            content_sha256 TEXT NOT NULL REFERENCES content_pieces(sha256),
            PRIMARY KEY(object_id,position));
          CREATE TABLE IF NOT EXISTS links (
            source INTEGER NOT NULL REFERENCES object_headers(id), relation TEXT NOT NULL,
            target INTEGER NOT NULL REFERENCES object_headers(id), PRIMARY KEY(source,relation,target));
          CREATE TABLE IF NOT EXISTS byte_contents (sha256 TEXT PRIMARY KEY, bytes BLOB NOT NULL);
          CREATE TABLE IF NOT EXISTS blob_refs (
            object_id INTEGER PRIMARY KEY REFERENCES object_headers(id),
            content_sha256 TEXT NOT NULL REFERENCES byte_contents(sha256));
          CREATE VIEW IF NOT EXISTS objects AS
            SELECT h.id,h.kind,h.scope,
              COALESCE((SELECT group_concat(piece,'') FROM
                (SELECT c.text AS piece FROM object_parts p JOIN content_pieces c
                   ON c.sha256=p.content_sha256 WHERE p.object_id=h.id ORDER BY p.position)), '') AS body,
              h.sha256,h.created,h.alias FROM object_headers h;
          CREATE VIEW IF NOT EXISTS blobs AS
            SELECT r.object_id,c.bytes FROM blob_refs r JOIN byte_contents c ON c.sha256=r.content_sha256;
          CREATE INDEX IF NOT EXISTS by_scope_kind ON object_headers(scope,kind,id);
          CREATE INDEX IF NOT EXISTS incoming ON links(target,source);
        """)
        for table in ('object_headers', 'content_pieces', 'object_parts', 'links', 'byte_contents', 'blob_refs'):
            for operation in ('UPDATE', 'DELETE'):
                self.db.execute(f'CREATE TRIGGER IF NOT EXISTS immutable_{table}_{operation} BEFORE {operation} ON {table} '
                                "BEGIN SELECT RAISE(ABORT,'archive content is immutable'); END")
        for view in ('objects', 'blobs'):
            for operation in ('UPDATE', 'DELETE'):
                self.db.execute(f'CREATE TRIGGER IF NOT EXISTS immutable_{view}_{operation} INSTEAD OF {operation} ON {view} '
                                "BEGIN SELECT RAISE(ABORT,'archive content is immutable'); END")
        self.db.commit()

    def _insert(self, kind, scope, raw, links, alias):
        digest = hashlib.sha256(raw.encode()).hexdigest()
        if self.shared:
            ident = self.db.execute('INSERT INTO object_headers(kind,scope,sha256,created,alias) VALUES (?,?,?,?,?)',
                                   (kind, scope, digest, time.time(), alias)).lastrowid
            for position, piece in enumerate(content_pieces(raw)):
                key = hashlib.sha256(piece.encode()).hexdigest()
                self.db.execute('INSERT OR IGNORE INTO content_pieces VALUES (?,?)', (key, piece))
                self.db.execute('INSERT INTO object_parts VALUES (?,?,?)', (ident, position, key))
        else:
            ident = self.db.execute('INSERT INTO objects(kind,scope,body,sha256,created,alias) VALUES (?,?,?,?,?,?)',
                                   (kind, scope, raw, digest, time.time(), alias)).lastrowid
        for relation, target in dict.fromkeys((link['relation'], self.number(link['ref'])) for link in links):
            self.db.execute('INSERT INTO links VALUES (?,?,?)', (ident, relation, target))
        return ident

    def number(self, ref):
        if not isinstance(ref, str) or not ref.startswith('obj:') or not ref[4:].isdigit():
            raise ValueError('Expected obj:N reference')
        return int(ref[4:])

    def put(self, kind, scope, body, links=(), alias=None):
        raw = encoded(body)
        with self.db:
            ident = self._insert(kind, scope, raw, links, alias)
        return 'obj:' + str(ident)

    def get(self, ref, kind=None, scope=None):
        if ref.startswith('event:'):
            row = self.db.execute('SELECT * FROM objects WHERE alias=?', (ref,)).fetchone()
        else:
            row = self.db.execute('SELECT * FROM objects WHERE id=?', (self.number(ref),)).fetchone()
        if row is None or (scope is not None and row['scope'] not in (scope, 'shared')):
            raise ValueError('Object unavailable in this context')
        if kind is not None and row['kind'] != kind:
            raise ValueError('Stored object kind does not match typed reference')
        if hashlib.sha256(row['body'].encode()).hexdigest() != row['sha256']:
            raise ValueError('Object content integrity check failed')
        refs = self.db.execute('SELECT relation,target,scope FROM links JOIN objects ON objects.id=links.target WHERE source=? ORDER BY relation,target', (row['id'],)).fetchall()
        return dict(ref='obj:'+str(row['id']), kind=row['kind'], scope=row['scope'],
                    body=json.loads(row['body']), sha256=row['sha256'], alias=row['alias'],
                    links=[dict(relation=x['relation'],ref='obj:'+str(x['target'])) for x in refs
                           if scope is None or x['scope'] in (scope,'shared')])

    def query(self, scope=None, kind=None, text='', after=0, limit=50):
        if not isinstance(after,int) or after < 0 or not isinstance(limit,int) or not 1 <= limit <= 100:
            raise ValueError('after must be nonnegative; limit must be 1..100')
        where, args = ['id > ?'], [after]
        if scope is not None:
            where.append("scope IN (?, 'shared')")
            args.append(scope)
        if kind:
            where.append('kind=?')
            args.append(kind)
        if text:
            where.append('instr(body, ?) > 0')
            args.append(text)
        rows = self.db.execute('SELECT id,kind,scope,alias,sha256 FROM objects WHERE '+ ' AND '.join(where)+' ORDER BY id LIMIT ?', args+[limit+1]).fetchall()
        page = rows[:limit]
        return dict(objects=[dict(ref='obj:'+str(r['id']),kind=r['kind'],scope=r['scope'],alias=r['alias'],sha256=r['sha256']) for r in page],
                    next_after=page[-1]['id'] if page else after, complete=len(rows)<=limit)

    def references(self, ref, scope=None, kind=None, limit=100):
        """Read incoming provenance edges; never infer containment from text."""
        target = self.get(ref, scope=scope)
        if not isinstance(limit, int) or not 1 <= limit <= 100:
            raise ValueError('limit must be 1..100')
        where, args = ['links.target=?'], [self.number(target['ref'])]
        if scope is not None:
            where.append('objects.scope=?')
            args.append(scope)
        if kind is not None:
            where.append('objects.kind=?')
            args.append(kind)
        rows = self.db.execute('SELECT DISTINCT objects.id,objects.kind FROM links '
            'JOIN objects ON objects.id=links.source WHERE '+' AND '.join(where)+
            ' ORDER BY objects.id LIMIT ?', args+[limit+1]).fetchall()
        return dict(objects=[dict(ref='obj:'+str(row['id']), kind=row['kind']) for row in rows[:limit]],
                    complete=len(rows)<=limit)

    def put_blob(self, kind, scope, raw, metadata=None, links=(), alias=None):
        # Object and bytes commit together. Nested put() transactions would break this.
        body = dict(metadata or {}, encoding='binary', bytes=len(raw),
                    sha256=hashlib.sha256(raw).hexdigest())
        text = encoded(body)
        with self.db:
            ident = self._insert(kind, scope, text, links, alias)
            if self.shared:
                self.db.execute('INSERT OR IGNORE INTO byte_contents VALUES (?,?)', (body['sha256'], raw))
                self.db.execute('INSERT INTO blob_refs VALUES (?,?)', (ident, body['sha256']))
            else:
                self.db.execute('INSERT INTO blobs VALUES (?,?)', (ident,raw))
        return 'obj:'+str(ident)

    def blob(self, ref, offset=0, count=65536, scope=None):
        record = self.get(ref, scope=scope)
        if offset < 0 or not 1 <= count <= 65536:
            raise ValueError('Invalid blob page')
        row = self.db.execute('SELECT bytes FROM blobs WHERE object_id=?',
                              (self.number(record['ref']),)).fetchone()
        if row is None:
            raise ValueError('Object has no binary content')
        raw = row[0]
        if hashlib.sha256(raw).hexdigest() != record['body']['sha256']:
            raise ValueError('Blob integrity check failed')
        part = raw[offset:offset+count]
        return dict(ref=record['ref'], offset=offset, next_offset=offset+len(part),
                    total_bytes=len(raw), complete=offset+len(part)>=len(raw),
                    base64=base64.b64encode(part).decode(),content=part.decode(errors='replace'))

    def handle(self, op, payload):
        if op == 'put':
            return {'ref': self.put(**payload)}
        if op == 'get':
            return self.get(**payload)
        if op == 'query':
            return self.query(**payload)
        if op == 'references':
            return self.references(**payload)
        raise ValueError('Unknown database operation')

    def close(self):
        self.db.close()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()
