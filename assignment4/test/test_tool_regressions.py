"""Behavioral edge cases for coding effects; assertions describe user-visible contracts."""
import base64
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import os
from pathlib import Path
import shlex
import tempfile
import time
import unittest
from unittest.mock import patch

from src.bridge import Bridge, Workspace
from src import workspace as effects


class ToolRegressions(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.repo = self.root/'source'
        self.repo.mkdir()
        self.ws = Workspace(str(self.repo),str(self.root/'state'),capture_patch=False)
        self.jobs = []
        self.addCleanup(self.cleanup_jobs)

    def call(self, op, **args):
        result = self.ws.effect(op,dict(workspace=self.ws.facts(),**args))
        if op == 'shell' and result.get('id'):
            self.jobs.append(result['id'])
        return result

    def cleanup_jobs(self):
        for job in self.jobs:
            if self.call('observe',id=job)['state'] != 'exited':
                self.call('job',id=job,action='kill')

    def complete(self, result):
        # Test watchdog, not a product timeout. Read explicit offsets to avoid consuming output.
        for _ in range(50):
            if result['state'] != 'running':
                return result
            result = self.call('job',id=result['id'],offset=0,yield_time_ms=100)
        self.fail('Fixture process did not finish')

    def read_saved(self, result, key):
        return Path(result['_artifacts'][key]['path']).read_bytes()

    def test_line_ranges_include_blank_lines_crlf_and_no_final_newline(self):
        for raw, first, count, content, last in [
            (b'a\n\n',1,20,b'a\n\n',2),
            (b'\n\n',2,1,b'\n',2),
            (b'a\r\nb\r\n',2,1,b'b\r\n',2),
            (b'a\rb\r',2,1,b'b\r',2),
            (b'alpha\nbeta',2,1,b'beta',2),
        ]:
            with self.subTest(raw=raw,first=first):
                (self.repo/'file').write_bytes(raw)
                got=self.call('read_file',path='file',start_line=first,line_count=count)
                self.assertEqual(got['content'].encode(),content)
                self.assertEqual(got['first_displayed_line'],first)
                self.assertEqual(got['last_displayed_line'],last)
                self.assertEqual(self.read_saved(got,'snapshot_ref'),raw)

    def test_empty_and_past_eof_reads_are_complete_without_invented_lines(self):
        for raw, options in [(b'',{}),(b'x',{'offset':100}),(b'x',{'start_line':100})]:
            with self.subTest(raw=raw,options=options):
                (self.repo/'file').write_bytes(raw)
                got=self.call('read_file',path='file',**options)
                self.assertEqual(got['content'],'')
                self.assertTrue(got['complete'])
                self.assertIsNone(got['first_displayed_line'])
                self.assertEqual(self.read_saved(got,'snapshot_ref'),raw)

    def test_all_byte_pages_reconstruct_unicode_and_binary_snapshot(self):
        raw=('aλ🙂\r\n'*100).encode()+bytes(range(256))
        (self.repo/'file').write_bytes(raw)
        first=self.call('read_file',path='file',max_output_bytes=1)
        artifact=first['_artifacts']['snapshot_ref']
        chunks=[]
        for offset in range(0,len(raw),37):
            page=self.call('patch',path=artifact['path'],offset=offset,bytes=37)
            chunks.append(base64.b64decode(page['base64']))
            self.assertEqual(page['next_offset'],min(offset+37,len(raw)))
        self.assertEqual(b''.join(chunks),raw)

    def test_missing_directory_fifo_and_symlink_loop_reads_fail_without_hanging(self):
        (self.repo/'directory').mkdir()
        os.mkfifo(self.repo/'pipe')
        (self.repo/'loop').symlink_to('loop')
        for path in ['absent','directory','pipe','loop']:
            with self.subTest(path=path):
                self.assertIn('error',self.call('read_file',path=path))
        self.assertIn('error',self.call('search_files',path='pipe'))

    def test_edit_sequence_multiline_unicode_empty_and_noop(self):
        path=self.repo/'odd name λ.txt'
        created=self.call('edit_file',path=path.name,operation='create',content='one\nα\nα\n')
        self.assertTrue(created['applied'])
        changed=self.call('edit_file',path=path.name,operation='replace',replacements=[
            {'old':'one\nα','new':'two\nβ'}, {'old':'α\n','new':''}, {'old':'two','new':'three'}])
        self.assertTrue(changed['applied'])
        self.assertEqual(path.read_text(),'three\nβ\n')
        self.assertEqual(changed['match_counts'],[1,1,1])
        noop=self.call('edit_file',path=path.name,operation='write',content=path.read_text())
        self.assertTrue(noop['applied'])
        self.assertFalse(noop['changed'])
        empty=self.call('edit_file',path=path.name,operation='write',content='')
        self.assertTrue(empty['applied'])
        self.assertEqual(path.read_bytes(),b'')
        deleted=self.call('edit_file',path=path.name,operation='delete')
        self.assertTrue(deleted['applied'])
        self.assertTrue(deleted['changed'])
        self.assertFalse(path.exists())

    def test_failed_edits_never_report_changed_source(self):
        path=self.repo/'file'; path.write_text('before')
        for operation,kwargs in [('create',{'content':'new'}),('replace',{'replacements':[{'old':'absent','new':'new'}]}),
                                 ('write',{'content':'new','expected_sha256':'0'*64})]:
            with self.subTest(operation=operation):
                got=self.call('edit_file',path='file',operation=operation,**kwargs)
                self.assertFalse(got['applied'])
                self.assertFalse(got.get('changed',False))
                self.assertEqual(path.read_text(),'before')
        with patch.object(effects.os,'replace',side_effect=PermissionError('simulated rename denied')):
            got=effects.edit_file(self.ws.facts(),{'path':'file','operation':'write','content':'after'})
        self.assertFalse(got['applied'])
        self.assertFalse(got.get('changed',False))
        self.assertEqual(path.read_text(),'before')
        self.assertEqual(list(self.repo.glob('.file.*')),[])

    def test_source_change_between_preparation_and_commit_is_preserved(self):
        path=self.repo/'file';path.write_text('before')
        original=effects.snapshot_bytes
        def raced(*args,**kwargs):
            value=original(*args,**kwargs)
            if kwargs.get('purpose')=='display_diff':
                path.write_text('external update')
            return value
        with patch.object(effects,'snapshot_bytes',side_effect=raced):
            got=effects.edit_file(self.ws.facts(),{'path':'file','operation':'write','content':'planned'})
        self.assertFalse(got['applied'])
        self.assertFalse(got.get('changed',False))
        self.assertEqual(path.read_text(),'external update')
        self.assertEqual(self.read_saved(got,'before_ref'),b'before')

    def test_create_race_does_not_overwrite_competing_file(self):
        path=self.repo/'file'
        original_link=effects.os.link
        def competing_link(source,target):
            if Path(target).resolve() != path.resolve():
                return original_link(source,target)
            Path(target).write_text('other writer')
            raise FileExistsError(str(target))
        with patch.object(effects.os,'link',side_effect=competing_link):
            got=effects.edit_file(self.ws.facts(),{'path':'file','operation':'create','content':'planned'})
        self.assertFalse(got['applied'])
        self.assertFalse(got.get('changed',False))
        self.assertEqual(path.read_text(),'other writer')

    def test_binary_replace_rejects_but_write_and_delete_are_explicit(self):
        path=self.repo/'binary';path.write_bytes(b'\xff\0')
        got=self.call('edit_file',path='binary',operation='replace',replacements=[{'old':'x','new':'y'}])
        self.assertFalse(got['applied'])
        self.assertEqual(path.read_bytes(),b'\xff\0')
        got=self.call('edit_file',path='binary',operation='write',content='text')
        self.assertTrue(got['applied'])
        self.assertEqual(self.read_saved(got,'before_ref'),b'\xff\0')

    def test_large_diff_retains_the_full_original(self):
        before='old\n'*7000; after='new\n'*7000
        (self.repo/'file').write_text(before)
        got=self.call('edit_file',path='file',operation='write',content=after)
        self.assertTrue(got['applied']);self.assertFalse(got['diff_complete'])
        self.assertGreater(len(self.read_saved(got,'diff_ref')),16000)
        self.assertEqual(self.read_saved(got,'before_ref'),before.encode())
        self.assertEqual(self.read_saved(got,'after_ref'),after.encode())

    def test_search_literals_paths_and_regex_through_observable_shell(self):
        (self.repo/'sub').mkdir()
        for name in ['a.py','sub/b.py','name\nwith newline.txt']:
            (self.repo/name).write_text('a.b\nAXb\nStraße\n')
        for args,count in [({'query':'a.b'},3),
                           ({'query':'STRASSE','ignore_case':True},3),({'glob':'*.py'},2),
                           ({'glob':'sub/*'},1),({'query':'not here'},0),({'path':'a.py','query':'a.b'},1),
                           ({'exclude':['sub/**']},2)]:
            with self.subTest(args=args):
                got=self.call('search_files',**args)
                self.assertEqual(got['matches'],count,got)
                rows=[json.loads(x) for x in self.read_saved(got,'results_ref').splitlines()]
                self.assertEqual(len(rows),count)
        (self.repo/'generated.js').write_text('from x;' * 50000)
        self.assertEqual(self.call('search_files',query='calendar/rrule')['matches'],0)
        self.assertIn('literal',self.call('search_files',query='from.*calendar/rrule',regex=True)['error'])
        searched = self.complete(self.call('shell', command="grep -Ein 'a.b' a.py", cwd='.'))
        self.assertEqual(searched['exit_code'],0)
        self.assertEqual(searched['output']['content'].splitlines(), ['1:a.b', '2:AXb'])
        self.assertIn('error',self.call('search_files',path='missing'))

    def test_search_walk_errors_are_not_exhaustive_negative_results(self):
        def inaccessible(root,onerror,followlinks):
            onerror(PermissionError(13,'denied',str(self.repo/'locked')))
            return iter([])
        with patch.object(effects.os,'walk',side_effect=inaccessible):
            got=effects.search_files(self.ws.facts(),{'query':'needle'})
        self.assertEqual(got['matches'],0)
        self.assertFalse(got['scan_complete'])
        self.assertEqual(got['errors'][0]['path'],str(self.repo/'locked'))

    def test_shell_language_constructs_and_error_statuses(self):
        cases=[("v='hello world'; printf '%s' \"$v\"",'hello world',0),
               ("printf 'a\\nb\\n' | tail -n 1",'b\n',0),
               ("printf '%s' \"$(printf nested)\"",'nested',0),
               ("cat <<'END'\nliteral $HOME `text`\nEND",'literal $HOME `text`\n',0),
               ("printf stdout; printf stderr >&2; exit 17",'stdoutstderr',17),
               ('nonexistent_command_a4_test','',127),
               ('if then','',2)]
        for command,content,code in cases:
            with self.subTest(command=command):
                got=self.complete(self.call('shell',command=command))
                self.assertEqual(got['exit_code'],code,got)
                if content:self.assertEqual(got['output']['content'],content)

    def test_binary_output_is_exact_and_concurrent_polls_do_not_duplicate_bytes(self):
        command='python3 -c '+shlex.quote('import sys;sys.stdout.buffer.write(bytes(range(256))*20)')
        got=self.complete(self.call('shell',command=command,yield_time_ms=0,max_output_bytes=1))
        # Explicit offsets used by complete() never advance the launch cursor.
        with ThreadPoolExecutor(max_workers=4) as pool:
            pages=list(pool.map(lambda _:self.call('job',id=got['id'],max_output_bytes=500)['output'],range(12)))
        pages.sort(key=lambda p:p['offset'])
        offsets=[p['offset'] for p in pages if p['next_offset']>p['offset']]
        self.assertEqual(len(offsets),len(set(offsets)))
        for left,right in zip(pages,pages[1:]):
            self.assertEqual(left['next_offset'],right['offset'])
        full=self.call('read_output',ref='job:'+got['id'],raw=True,bytes=65536)
        self.assertEqual(base64.b64decode(full['base64']),bytes(range(256))*20)
        self.assertEqual(pages[-1]['next_offset'],5120)

    def test_list_jobs_does_not_consume_output_or_restart_processes(self):
        archive=self.root/'archive';archive.mkdir()
        bridge=Bridge(self.ws,archive,None)
        self.addCleanup(bridge.close)
        got=bridge.handle('shell',{'workspace':self.ws.facts(),'command':'printf first; sleep .1; printf second',
                                 'yield_time_ms':0,'max_output_bytes':1})
        self.jobs.append(got['id'])
        self.complete(got)
        for _ in range(2):
            listing=bridge.handle('list_jobs',{'workspace':self.ws.facts()})
            self.assertEqual(len(listing['jobs']),1)
            self.assertEqual(listing['jobs'][0]['id'],got['id'])
            self.assertEqual(listing['jobs'][0]['state'],'exited')
        rest=bridge.handle('job',{'workspace':self.ws.facts(),'id':got['id']})
        self.assertEqual(got['output']['content']+rest['output']['content'],'firstsecond')

    def test_terminal_eof_rejection_and_signal_lifecycle(self):
        got=self.call('shell',command='cat',tty=True,yield_time_ms=100)
        refused=self.call('job',id=got['id'],action='close_stdin',yield_time_ms=0)
        self.assertFalse(refused['control']['executed'])
        self.assertEqual(refused['state'],'running')
        ended=self.complete(self.call('job',id=got['id'],action='terminate'))
        self.assertEqual(ended['state'],'exited')
        self.assertNotEqual(ended['exit_code'],0)
        again=self.call('job',id=got['id'],action='kill')
        self.assertFalse(again['control']['executed'])
        self.assertEqual(again['exit_code'],ended['exit_code'])

    def test_closed_pipe_input_and_repeated_eof_are_truthful(self):
        got=self.call('shell',command='cat; sleep .2',yield_time_ms=100)
        closed=self.call('job',id=got['id'],action='close_stdin',yield_time_ms=0)
        self.assertTrue(closed['control']['stdin_closed'])
        refused=self.call('job',id=got['id'],action='input',chars='cannot write',yield_time_ms=0)
        self.assertFalse(refused['control']['executed'])
        self.assertEqual(self.complete(refused)['exit_code'],0)

    def test_backpressure_can_resume_within_a_utf8_character_without_duplication(self):
        program="import pathlib,time,sys; print('ready',flush=True); " \
                "exec(\"while not pathlib.Path('release').exists(): time.sleep(.01)\"); " \
                "pathlib.Path('received').write_bytes(sys.stdin.buffer.read())"
        got=self.call('shell',command='python3 -u -c '+shlex.quote(program),yield_time_ms=100)
        chars='€'*21000
        messages=0
        for _ in range(8):
            sent=self.call('job',id=got['id'],action='input',chars=chars,yield_time_ms=0)
            receipt=sent['control']
            self.assertIn('bytes_written',receipt)
            messages+=1
            if not receipt['input_complete']:
                break
        else:self.fail('Fixture did not reach pipe backpressure')
        offset=receipt['next_input_offset']
        self.assertLess(offset,len(chars.encode()))
        (self.repo/'release').touch()
        for _ in range(100):
            sent=self.call('job',id=got['id'],action='input',chars=chars,input_offset=offset,yield_time_ms=0)
            receipt=sent['control']
            self.assertEqual(receipt['input_offset'],offset)
            offset=receipt['next_input_offset']
            if receipt['input_complete']:break
            time.sleep(.01)
        else:self.fail('Fixture receiver did not drain input')
        done=self.complete(self.call('job',id=got['id'],action='close_stdin'))
        self.assertEqual(done['exit_code'],0,done)
        raw=(self.repo/'received').read_bytes()
        self.assertEqual(raw,chars.encode()*messages)
        self.assertEqual(raw.decode(),chars*messages)

    def test_lost_control_acknowledgement_reports_uncertainty_without_replay(self):
        import socket
        import threading
        import uuid
        ident=uuid.uuid4().hex
        directory=Path(self.ws.state)/'jobs'/ident;directory.mkdir(parents=True)
        address=effects.socket_path(ident)
        server=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM)
        server.bind(str(address));server.listen(1)
        received=[]
        def disconnect():
            conn,_=server.accept()
            with conn:
                raw=b''
                while not raw.endswith(b'\n'):raw+=conn.recv(65536)
                received.append(json.loads(raw))
        thread=threading.Thread(target=disconnect);thread.start()
        try:
            result=effects.control_job(self.ws.facts(),ident,'input','once')
            thread.join(2)
            self.assertEqual(result['execution_state'],'unknown')
            self.assertFalse(result['replay_safe'])
            self.assertEqual(len(received),1)
            self.assertEqual(received[0]['chars'],'once')
            self.assertEqual(len(list(directory.glob('control-intent-*'))),1)
        finally:
            server.close();address.unlink(missing_ok=True)

    def test_closed_output_is_not_mistaken_for_process_completion(self):
        program='import os,time;os.close(1);os.close(2);time.sleep(.3);raise SystemExit(9)'
        got=self.call('shell',command='python3 -c '+shlex.quote(program),yield_time_ms=100)
        self.assertEqual(got['state'],'running')
        done=self.complete(got)
        self.assertEqual(done['exit_code'],9)
        self.assertEqual(done['output']['content'],'')

    def test_child_output_is_drained_after_parent_exits(self):
        child="import time;time.sleep(.1);print('late child output')"
        parent='import subprocess;subprocess.Popen([\"python3\",\"-c\",'+repr(child)+'])'
        done=self.complete(self.call('shell',command='python3 -c '+shlex.quote(parent),yield_time_ms=0))
        self.assertEqual(done['exit_code'],0)
        raw=self.call('read_output',ref='job:'+done['id'],raw=True)
        self.assertEqual(base64.b64decode(raw['base64']),b'late child output\n')

    def test_unknown_job_identifiers_are_actionable_without_launching(self):
        for ident in ['../escape','x','f'*32]:
            with self.subTest(ident=ident):
                self.assertIn('error',self.call('job',id=ident))
        self.assertFalse((self.root/'state/jobs').exists())

if __name__=='__main__':
    unittest.main()
