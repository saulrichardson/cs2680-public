"""Exercise the journal's actual writer/read-only connection overlap.

Some macOS SQLite builds fail this workload with SQLITE_IOERR_LOCK. Preserve the
extended error/build evidence; neither retry nor downgrade read-only observers.
"""
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import tempfile
import threading
import unittest

from src.store import Store


class ArchiveConcurrency(unittest.TestCase):
    def test_writers_and_readonly_observers_preserve_every_original(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / 'session.sqlite3'
            bootstrap = Store(database)
            bootstrap.db.close()
            barrier = threading.Barrier(6)
            stop = threading.Event()
            failures = []
            iterations = 150
            def writer(worker):
                try:
                    barrier.wait(5)
                    for index in range(iterations):
                        if stop.is_set():
                            return
                        store = Store(database)
                        try:
                            store.put('observation', 'session', {'worker': worker, 'index': index})
                        finally:
                            store.db.close()
                except Exception as error:
                    failures.append({'worker': worker, 'role': 'writer', 'error': str(error),
                        'code': getattr(error, 'sqlite_errorcode', None),
                        'name': getattr(error, 'sqlite_errorname', None)})
                    stop.set()
            def reader(worker):
                try:
                    barrier.wait(5)
                    for _ in range(iterations):
                        if stop.is_set():
                            return
                        with closing(sqlite3.connect(database.resolve().as_uri() + '?mode=ro', uri=True)) as db:
                            db.execute('SELECT COUNT(*) FROM objects').fetchone()
                except Exception as error:
                    failures.append({'worker': worker, 'role': 'readonly observer', 'error': str(error),
                        'code': getattr(error, 'sqlite_errorcode', None),
                        'name': getattr(error, 'sqlite_errorname', None)})
                    stop.set()
            threads = [threading.Thread(target=writer, args=(worker,)) for worker in range(3)]
            threads += [threading.Thread(target=reader, args=(worker,)) for worker in range(3)]
            try:
                for thread in threads:
                    thread.start()
                for thread in threads:
                    thread.join(20)
                self.assertTrue(all(not thread.is_alive() for thread in threads), 'Archive stress did not settle')
                self.assertEqual(failures, [], json.dumps({'sqlite_version': sqlite3.sqlite_version,
                    'threadsafety': sqlite3.threadsafety, 'failures': failures}, indent=2))
                with closing(sqlite3.connect(database.resolve().as_uri() + '?mode=ro', uri=True)) as db:
                    self.assertEqual(db.execute('PRAGMA integrity_check').fetchone()[0], 'ok')
                    rows = [json.loads(row[0]) for row in db.execute("SELECT body FROM objects WHERE kind='observation'")]
                self.assertEqual({(row['worker'], row['index']) for row in rows},
                                 {(worker, index) for worker in range(3) for index in range(iterations)})
                self.assertEqual(len(rows), 3 * iterations)
            finally:
                stop.set()
                for thread in threads:
                    if thread.ident is not None:
                        thread.join(20)


if __name__ == '__main__':
    unittest.main()
