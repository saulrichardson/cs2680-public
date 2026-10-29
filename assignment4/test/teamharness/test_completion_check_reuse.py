"""Completion-history regressions found by generated repair sequences.

The external counter establishes actual executions independently of receipts.
Current and historical gate executions must be visible to later completion,
including a real execution whose result was lost.
"""
from pathlib import Path
import shutil
import unittest
from unittest.mock import patch

from src.teamharness import repair
from test_repair_properties import RepairMachine


class CompletionCheckReuse(unittest.TestCase):
    def assert_no_implicit_repeat(self, lose_result, historical=False):
        machine = RepairMachine(tracing=False)
        self.addCleanup(machine.teardown)
        machine.edit(0)

        def finish_again():
            candidate = machine.manager.checkpoint(machine.ident)['candidate']
            session = machine.manager._session(machine.ident)
            state = session._load()
            state['assistant_counter'] += 1
            session._save(state)
            return machine.workflow.verify_completion(machine.ident, {'patch': candidate})

        original = repair.grade_candidate

        def lost_after_execution(*args, **kwargs):
            original(*args, **kwargs)
            raise RuntimeError('Injected loss after actual check execution')

        if lose_result:
            with patch.object(repair, 'grade_candidate', lost_after_execution):
                first = finish_again()
        else:
            first = finish_again()
        self.assertEqual(first['state'], 'failed' if lose_result else 'passed')
        self.assertEqual(machine.count(), 1)
        if historical:
            agent_root = Path(machine.manager.descriptor(machine.ident)['root'])
            journal = agent_root / 'test-plan-runs' / ('completion-' + first['id'])
            legacy = Path(first['evidence_root']) / 'test-plan'
            shutil.move(str(journal), str(legacy))
        second = finish_again()
        self.assertEqual(first['candidate']['sha256'], second['candidate']['sha256'])
        self.assertEqual(machine.count(), 1,
                         'A later completion must discover the earlier exact-plan, exact-source execution')
        if lose_result:
            self.assertNotEqual(second['state'], 'passed')

    def test_finished_completion_check_is_reused_by_later_completion(self):
        self.assert_no_implicit_repeat(False)

    def test_uncertain_completion_check_is_not_implicitly_repeated(self):
        self.assert_no_implicit_repeat(True)

    def test_historical_finished_gate_execution_is_reused(self):
        self.assert_no_implicit_repeat(False, historical=True)

    def test_historical_uncertain_gate_execution_is_not_repeated(self):
        self.assert_no_implicit_repeat(True, historical=True)
