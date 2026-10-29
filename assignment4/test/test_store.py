"""Database integrity with real SQLite transactions, reopen and scoped queries."""
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from src.store import Store

class StoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name)/'context.sqlite3'
        self.store = Store(self.path)
        self.addCleanup(self.store.close)

    def test_transaction_rollback_and_immutable_originals(self):
        ref = self.store.put('facts','owner',{'exit_code':7},alias='event:1')
        for operation in ('UPDATE objects SET body=\'{}\'', 'DELETE FROM objects'):
            with self.assertRaises(sqlite3.IntegrityError):
                with self.store.db:
                    self.store.db.execute(operation)
        with self.assertRaises(sqlite3.IntegrityError):
            self.store.put('summary','owner','not committed',[{'relation':'source','ref':'obj:9999'}])
        with self.assertRaises(sqlite3.IntegrityError):
            self.store.put('facts','owner',{},alias='event:1')
        self.assertEqual(len(self.store.query()['objects']),1)
        self.assertEqual(self.store.get(ref)['body'],{'exit_code':7})
        with Store(self.path,readonly=True) as reopened:
            self.assertEqual(reopened.get('event:1')['ref'],ref)
            with self.assertRaises(sqlite3.OperationalError):
                reopened.put('facts','owner',{})

    def test_scope_kind_and_literal_search_do_not_bypass_access(self):
        secret = self.store.put('message','owner','private owner material')
        shared = self.store.put('task','shared','public objective')
        own = self.store.put('summary','reviewer:1','independent hypothesis',
            [{'relation':'task','ref':shared},{'relation':'private_source','ref':secret}])
        with self.assertRaises(ValueError):
            self.store.get(secret,scope='reviewer:1')
        with self.assertRaises(ValueError):
            self.store.get(shared,kind='facts')
        self.assertEqual(self.store.query(scope='reviewer:1',text='private')['objects'],[])
        self.assertEqual(self.store.query(text="' OR 1=1 --")['objects'],[])
        self.assertEqual(self.store.get(own,scope='reviewer:1')['links'],[{'relation':'task','ref':shared}])

    def test_binary_pages_are_exact_and_persisted(self):
        raw = bytes(range(256))*1000
        ref = self.store.put_blob('output','owner',raw,{'job':'example'})
        import base64
        chunks, offset = [],0
        with Store(self.path,readonly=True) as reopened:
            while True:
                page = reopened.blob(ref,offset,7777)
                chunks.append(base64.b64decode(page['base64']))
                offset = page['next_offset']
                if page['complete']: break
        self.assertEqual(b''.join(chunks),raw)
        with self.assertRaises(sqlite3.IntegrityError):
            self.store.put_blob('candidate','owner',b'partial',links=[{'relation':'missing','ref':'obj:99999'}])
        self.assertEqual(len(self.store.query()['objects']),1)

    def test_repeated_plan_sources_are_one_graph_edge_without_losing_positions(self):
        source = self.store.put('message','owner',{'role':'user','content':'repeatable'})
        links = [{'relation':'source','ref':source}]*3
        view = self.store.put('view','owner',[source,source,source],links)
        blob = self.store.put_blob('output','owner',b'example',links=links)
        self.assertEqual(self.store.get(view)['body'],[source]*3)
        self.assertEqual(self.store.get(view)['links'],links[:1])
        self.assertEqual(self.store.get(blob)['links'],links[:1])
        count = self.store.db.execute('SELECT COUNT(*) FROM objects').fetchone()[0]
        with self.assertRaises(sqlite3.IntegrityError):
            self.store.put('view','owner',[],links+[{'relation':'source','ref':'obj:99999'}])
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM objects').fetchone()[0],count)

    def test_incoming_references_use_edges_and_scope_without_guessing(self):
        observation=self.store.put('observation','owner',{'exit_code':7})
        first=self.store.put('exchange','owner',[],[{'relation':'observation','ref':observation}])
        # A mention is not a provenance edge, and foreign parents are not hints.
        self.store.put('exchange','owner',[observation])
        self.store.put('exchange','reviewer:1',[],[{'relation':'observation','ref':observation}])
        result=self.store.references(observation,scope='owner',kind='exchange',limit=2)
        self.assertEqual(result,{'objects':[{'ref':first,'kind':'exchange'}],'complete':True})
        second=self.store.put('exchange','owner',[],[{'relation':'observation','ref':observation}])
        self.assertEqual(len(self.store.references(observation,scope='owner',kind='exchange',limit=2)['objects']),2)
        with self.assertRaises(ValueError):self.store.references(observation,scope='reviewer:1')
        self.assertEqual(self.store.get(observation)['body'],{'exit_code':7})
