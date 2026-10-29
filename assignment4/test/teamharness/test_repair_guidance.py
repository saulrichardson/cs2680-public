"""Portable publication delivery, independent roles and frozen continuation."""
from pathlib import Path
import hashlib
import json
import shutil
import tempfile
import unittest
from unittest.mock import patch

from lab.adapters.teamharness.build import build, freeze_repair_guidance
from src.teamharness.agents import AgentManager
from src.teamharness.prompts import role_names, freeze_guides
from test_main_flow import repository, finish, audit_requests
from test_session import Script


ROOT = Path(__file__).resolve().parents[2]
SOLVER = ROOT / 'research/repair-guidance/solver'


class RepairGuidance(unittest.TestCase):
    def test_historical_frozen_snapshot_is_not_rewritten(self):
        with tempfile.TemporaryDirectory() as tmp:
            frozen = Path(tmp)/'frozen'; frozen.mkdir()
            original = b'Historical frozen operating instructions.\n'
            (frozen/'guide.md').write_bytes(original)
            hashes = {'guide.md': hashlib.sha256(original).hexdigest()}
            manifest = json.dumps({'version': 1, 'files': hashes})
            (frozen/'manifest.json').write_text(manifest)
            with patch('src.teamharness.prompts.GUIDES', Path(tmp)/'unavailable'):
                self.assertEqual(freeze_guides(frozen), hashes)
            self.assertEqual((frozen/'guide.md').read_bytes(), original)
            self.assertEqual((frozen/'manifest.json').read_text(), manifest)

    def test_package_contains_only_exact_solver_bytes_and_rejects_tampering(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            receipt = build(root/'package', solve_seconds=1800, repair_guidance=SOLVER)
            target = root/'package/src/teamharness/guides/portable-repair'
            self.assertTrue(receipt['repair_guidance']['solver_files'])
            self.assertEqual({p.relative_to(target): p.read_bytes() for p in target.rglob('*') if p.is_file()},
                             {p.relative_to(SOLVER): p.read_bytes() for p in SOLVER.rglob('*') if p.is_file()})
            self.assertFalse(any('/evidence/' in p or '/integration/' in p for p in receipt['files']))
            publication = root/'copy'
            shutil.copytree(SOLVER.parent, publication)
            (publication/'solver/SKILLS.md').write_text('altered')
            with self.assertRaises(ValueError):
                freeze_repair_guidance(publication/'solver', root/'rejected')
            self.assertFalse((root/'rejected').exists())

    def test_fresh_roles_receive_layer_and_resume_preserves_exact_frozen_prompt(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = repository(root)
            guides = root/'guides'
            shutil.copytree(ROOT/'src/teamharness/guides', guides)
            freeze_repair_guidance(SOLVER, guides/'portable-repair')
            with patch('src.teamharness.prompts.GUIDES', guides):
                manager = AgentManager(root/'team', lambda _: Script([finish()]))
                manager.automatic_wakes_enabled = False
                parent = manager.create('main', 'Repair the public answer contract.', repo)
                for role in role_names():
                    ident = manager.create(role, 'Independent public contract investigation.', repo,
                                           private=True, parent=parent, inherit_context=False)
                    manager.start(ident)
                    self.assertEqual(manager.wait(ident, 5)['status'], 'completed')
                    d = manager.descriptor(ident)
                    request = audit_requests(Path(d['root'])/'session.sqlite3')[0]
                    system = request['messages'][0]['content']
                    self.assertNotIn((manager.guide_root/'portable-repair/SYSTEM_PROMPT.md').read_text().strip(), system)
                    self.assertIn(str(manager.guide_root/'portable-repair/SYSTEM_PROMPT.md'), system)
                    self.assertIn(str(manager.guide_root/'portable-repair/SKILLS.md'), system)
                    self.assertIn('finish_role', {t['function']['name'] for t in request['tools']})
                    self.assertEqual((repo/'answer.py').read_text(), 'def answer(n):\n    return -1\n')
                manager.shutdown()
                # Changing installed guidance cannot alter a resumed conversation.
                (guides/'portable-repair/SYSTEM_PROMPT.md').write_text('A different future publication.')
                resumed = AgentManager(root/'team', lambda _: None)
                self.assertEqual(manager.role_prompts, resumed.role_prompts)
                self.assertEqual(manager.guide_manifest, resumed.guide_manifest)


if __name__ == '__main__':
    unittest.main()
