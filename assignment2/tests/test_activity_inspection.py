import json
from pathlib import Path
import tempfile
import unittest

from mads.inspection.inspect_run import Journal
from tests import test_decision_inspection as decision_tests

Fixture = decision_tests.Fixture


class ActivityTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.f = Fixture(self.root)

    def test_both_native_protocols_and_exact_pointers(self):
        for protocol in ('chat', 'responses'):
            turn = self.f.turn(protocol=protocol, arguments='{"path":"' + 'é'*200 + '"}')
            a = Journal(self.root).activity(last=1, preview_chars=20)
            self.assertEqual(a['latest']['http_request']['record'], turn['wire'])
            self.assertEqual(Path(a['latest']['http_request']['payload']).read_bytes(), turn['raw'])
            row = a['turns'][0]
            self.assertEqual(row['source']['record'], turn['assistant'])
            self.assertEqual(len(row['calls']), 1)
            call = row['calls'][0]
            self.assertGreater(call['arguments']['omitted_chars'], 0)
            self.assertEqual(call['state'], 'result_recorded')
            captured = call['outcomes'][0]['captured_output']
            self.assertEqual(captured['text'], Path(captured['path']).read_text()[:20])
            self.assertIn('not necessarily', captured['scope'])
            if protocol == 'responses':
                self.assertNotIn('recorded_reasoning', row)
                self.assertNotIn('opaque', json.dumps(a))
            else:
                self.assertIn('recorded_reasoning', row)

    def test_pending_reused_calls_and_live_incomplete_tail(self):
        first = self.f.turn()
        second = self.f.turn(outcomes=False)
        with self.f.store.path.open('ab') as file:
            file.write(b'{"id":')
        a = Journal(self.root).activity(last=2)
        self.assertEqual(a['incomplete_tail_bytes'], 6)
        self.assertEqual(a['turns'][0]['calls'][0]['state'], 'result_recorded')
        self.assertEqual(a['turns'][1]['calls'][0]['state'], 'no_result_in_observed_prefix')
        self.assertEqual(a['turns'][1]['calls'][0]['outcomes'], [])
        self.assertEqual(a['turns'][0]['calls'][0]['outcomes'][0]['source']['record'], first['results'][0])
        self.assertEqual(a['latest']['assistant']['record'], second['assistant'])

    def test_bounds_empty_and_zero_preview(self):
        j = Journal(self.root)
        self.assertEqual(j.activity()['turns'], [])
        for kw in ({'last': 0}, {'last': True}, {'last': 21}, {'preview_chars': -1}, {'preview_chars': 8001}):
            with self.assertRaises(ValueError):
                j.activity(**kw)
        self.f.turn()
        self.assertEqual(Journal(self.root).activity(preview_chars=0)['turns'][0]['calls'][0]['arguments']['text'], '')

    def test_offline_cli(self):
        self.f.turn()
        result = decision_tests.DecisionInspectionTests.cli(self, 'activity', '--last', '1', '--preview-chars', '30')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(json.loads(result.stdout)['turns']), 1)

    # --- New cursor and invocation-count tests ---

    def test_next_after_record_default(self):
        """Without after_record, next_after_record reflects the journal head."""
        self.f.turn()
        turn_id = self.f.turn()['assistant']
        a = Journal(self.root).activity()
        # All events should be present
        self.assertEqual(a['next_after_record'], len(Journal(self.root).events))
        self.assertIsInstance(a['turns'], list)
        self.assertGreater(len(a['turns']), 0)

    def test_next_after_record_empty_journal(self):
        """Empty journal returns 0 for next_after_record.

        The Fixture always creates events, so we test with a truly empty journal.
        """
        # Create a Journal over a path with no events.jsonl
        import tempfile
        from pathlib import Path as P
        tmp = tempfile.mkdtemp()
        try:
            jdir = P(tmp) / 'journal'
            jdir.mkdir(parents=True)
            (jdir / 'events.jsonl').touch()
            j = Journal(jdir)
            a = j.activity()
            self.assertEqual(a['next_after_record'], 0)
            self.assertEqual(a['turns'], [])
            self.assertEqual(a['invocations_by_tool'], {})
        finally:
            import shutil
            shutil.rmtree(tmp, ignore_errors=True)

    def test_invocations_by_tool_present_and_scoped(self):
        """invocations_by_tool counts journal invocation events for the entire prefix."""
        self.f.turn()
        turn = self.f.turn()
        a = Journal(self.root).activity()
        self.assertIn('invocations_by_tool', a)
        self.assertIn('invocations_by_tool_scope', a)
        # 'read' should be counted
        self.assertIn('read', a['invocations_by_tool'])
        self.assertEqual(a['invocations_by_tool']['read'], 2)
        # Scope must be present and descriptive
        self.assertIn('entire observed journal prefix', a['invocations_by_tool_scope'])

    def test_cursor_no_filtering_when_none(self):
        """When after_record is not provided, all turns are returned."""
        self.f.turn()
        second = self.f.turn()
        a = Journal(self.root).activity()
        self.assertEqual(len(a['turns']), 2)

    def test_cursor_with_after_record(self):
        """after_record returns turns with activity (assistant/invocation/result) after N.

        Since the fixture always includes outcomes, both turns' invocations/results
        have higher IDs than the first turn's assistant record, so both are included.
        """
        first = self.f.turn()
        second = self.f.turn()
        j = Journal(self.root)
        # Both turns' invocations/results have IDs > first['assistant']
        a = Journal(self.root).activity(after_record=first['assistant'])
        self.assertEqual(len(a['turns']), 2)
        # The second turn's assistant record is the latest
        self.assertEqual(a['turns'][-1]['source']['record'], second['assistant'])

    def test_cursor_rejected_beyond_head(self):
        """Only beyond-head cursors are rejected; the returned cursor is reusable."""
        self.f.turn()
        j = Journal(self.root)
        head = len(j.events)
        self.assertEqual(j.activity(after_record=head)['turns'], [])
        with self.assertRaises(ValueError) as ctx:
            j.activity(after_record=head + 100)
        self.assertIn('beyond', str(ctx.exception))

    def test_cursor_with_last_filtering(self):
        """after_record + last: --last is applied after cursor filtering."""
        turn1 = self.f.turn()
        turn2 = self.f.turn()
        turn3 = self.f.turn()
        # Cursor after first turn → includes turns 2 and 3
        # Then last=1 → keeps only turn 3
        a = Journal(self.root).activity(after_record=turn1['assistant'], last=1)
        self.assertEqual(len(a['turns']), 1)
        self.assertEqual(a['eligible_turns'], 3)
        self.assertEqual(a['omitted_turns'], 2)
        self.assertEqual(a['turns'][0]['source']['record'], turn3['assistant'])

    def test_cursor_late_result_for_old_turn(self):
        """Poll, append only an old turn's result, then poll with the actual cursor."""
        for protocol in ('chat', 'responses'):
            first = self.f.turn(protocol=protocol, outcomes=False)
            second = self.f.turn(protocol=protocol)
            before = Journal(self.root).activity(last=1)
            cursor = before['next_after_record']
            self.assertEqual(Journal(self.root).activity(after_record=cursor)['turns'], [])
            late = self.f.store.add('tool_result', invocation_id=first['invocations'][0],
                                   call_id='call-reused', result={'status': 'completed', 'effects': 'none'})
            with self.f.store.path.open('ab') as file:
                file.write(b'{"id":')
            result = decision_tests.DecisionInspectionTests.cli(
                self, 'activity', '--after-record', str(cursor), '--last', '1')
            self.assertEqual(result.returncode, 0, result.stderr)
            data = json.loads(result.stdout)
            self.assertEqual(data['next_after_record'], late)
            self.assertEqual(data['incomplete_tail_bytes'], 6)
            self.assertEqual(len(data['turns']), 1)
            self.assertEqual(data['turns'][0]['source']['record'], first['assistant'])
            self.assertEqual(data['turns'][0]['calls'][0]['outcomes'][0]['source']['record'], late)
            self.assertNotEqual(data['turns'][0]['source']['record'], second['assistant'])
            with self.f.store.path.open('r+b') as file:
                file.seek(-6, 2); file.truncate()

    def test_roundtrip_cursor_cli_and_empty_prefix(self):
        empty = Journal(self.root).activity(after_record=0)
        self.assertEqual(empty['turns'], [])
        self.f.turn()
        first = decision_tests.DecisionInspectionTests.cli(self, 'activity')
        self.assertEqual(first.returncode, 0, first.stderr)
        cursor = json.loads(first.stdout)['next_after_record']
        second = decision_tests.DecisionInspectionTests.cli(
            self, 'activity', '--after-record', str(cursor))
        self.assertEqual(second.returncode, 0, second.stderr)
        data = json.loads(second.stdout)
        self.assertEqual(data['turns'], [])
        self.assertEqual(data['next_after_record'], cursor)
        self.assertEqual(data['invocations_by_tool'], {'read': 1})
        for invalid in (-1, True, 1.5, '1'):
            with self.assertRaises(ValueError):
                Journal(self.root).activity(after_record=invalid)

    def test_cursor_responses_protocol(self):
        """Cursor filtering works with native Responses protocol."""
        self.f.turn(protocol='responses')
        self.f.turn(protocol='responses')
        a = Journal(self.root).activity(after_record=1, last=1)
        self.assertEqual(len(a['turns']), 1)
        self.assertEqual(a['next_after_record'], len(Journal(self.root).events))
        self.assertIn('invocations_by_tool', a)

    def test_cursor_invocations_independent_of_cursor_view(self):
        """invocations_by_tool reflects the entire prefix, not just cursor view."""
        self.f.turn()
        self.f.turn()
        a = Journal(self.root).activity(after_record=1, last=1)
        # Even with after_record=1 and last=1, invocations_by_tool shows all
        self.assertEqual(a['invocations_by_tool']['read'], 2)
        self.assertEqual(len(a['turns']), 1)

    def test_cli_after_record(self):
        """CLI accepts --after-record and returns filtered turns."""
        self.f.turn()
        self.f.turn()
        result = decision_tests.DecisionInspectionTests.cli(
            self, 'activity', '--after-record', '1', '--last', '1')
        self.assertEqual(result.returncode, 0, result.stderr)
        data = json.loads(result.stdout)
        self.assertEqual(len(data['turns']), 1)
        self.assertIn('next_after_record', data)
        self.assertIn('invocations_by_tool', data)

    def test_cli_after_record_rejected_invalid(self):
        """CLI rejects --after-record strictly beyond journal head."""
        self.f.turn()
        j = Journal(self.root)
        head = len(j.events)
        result = decision_tests.DecisionInspectionTests.cli(
            self, 'activity', '--after-record', str(head + 1))
        self.assertNotEqual(result.returncode, 0)

    def test_cursor_preserves_existing_output(self):
        """Without cursor, output keys remain the same (with additive metadata)."""
        self.f.turn()
        a_with = Journal(self.root).activity()
        a_without = Journal(self.root).activity()
        # Both should have the same structural keys
        for key in ('scope', 'journal', 'journal_observed_sha256', 'journal_head',
                     'incomplete_tail_bytes', 'last_recorded_at', 'latest', 'turns',
                     'navigation'):
            self.assertIn(key, a_with)
            self.assertIn(key, a_without)
        # Additive keys always present now
        self.assertIn('next_after_record', a_without)
        self.assertIn('invocations_by_tool', a_without)
        self.assertIn('invocations_by_tool_scope', a_without)

    def test_invocation_counts_journal_events_not_provider_call_ids(self):
        """invocations_by_tool counts journal invocation events, not provider call IDs.

        Reused provider call IDs in separate turns are distinct journal invocations
        and should each be counted.
        """
        # First turn with call_id='call-reused'
        turn1 = self.f.turn()
        # Second turn reusing the same call_id
        # The fixture always uses call_id='call-reused' by default
        turn2 = self.f.turn()
        a = Journal(self.root).activity()
        # Both turns have an invocation for 'read'
        self.assertEqual(a['invocations_by_tool']['read'], 2)
        # The invocations_by_tool reflects the entire prefix, independent of last/cursor
        a2 = Journal(self.root).activity(after_record=1, last=1)
        self.assertEqual(a2['invocations_by_tool']['read'], 2)
