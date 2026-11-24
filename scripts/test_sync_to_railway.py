import base64
import gzip
import json
import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import sync_to_railway as sync  # noqa: E402

DATA = json.dumps([{'model': 'workspace.task', 'pk': 1, 'fields': {'title': 'مهمة'}},
                   {'model': 'workspace.task', 'pk': 2, 'fields': {'title': 'أخرى'}},
                   {'model': 'workspace.decision', 'pk': 1, 'fields': {}}], ensure_ascii=False)


class BuildProgramTests(unittest.TestCase):
    def test_program_compiles_and_embeds_the_payload(self):
        program = sync.build_program(DATA, apply=True, force=False)
        compile(program, 'remote', 'exec')
        payload = re.search(r"^PAYLOAD = '([^']+)'$", program, re.M).group(1)
        self.assertEqual(gzip.decompress(base64.b64decode(payload)).decode('utf-8'), DATA)
        self.assertIn('APPLY = True', program)
        self.assertIn('FORCE = False', program)

    def test_default_is_preview(self):
        self.assertIn('APPLY = False', sync.build_program(DATA))

    def test_no_placeholder_left(self):
        program = sync.build_program(DATA)
        for marker in ('__PAYLOAD__', '__APPLY__', '__FORCE__', '__SLIDING__'):
            self.assertNotIn(marker, program)


class ParseTests(unittest.TestCase):
    def test_result_and_lines(self):
        out = '112 objects imported\nPLAN workspace.task export 2 remote 0 remote_only 0\n' \
              'COUNT workspace.task 2\nRESULT applied\n'
        result, lines = sync.parse_result(out)
        self.assertEqual(result, 'applied')
        self.assertEqual(len(lines), 2)

    def test_missing_result_is_none(self):
        self.assertIsNone(sync.parse_result('Traceback ...')[0])

    def test_local_counts(self):
        self.assertEqual(sync.local_counts(DATA), {'workspace.task': 2, 'workspace.decision': 1})


class LocalProgramTests(unittest.TestCase):
    def test_program_compiles_and_lists_models(self):
        program = sync.local_program()
        compile(program, 'local', 'exec')
        self.assertIn("'workspace.task'", program)
        self.assertIn("'registry.artifact'", program)
        self.assertNotIn('__FULL__', program)
        self.assertTrue(program.isascii())

    def test_referenced_parents_are_not_also_full(self):
        self.assertFalse(set(sync.MODELS) & set(sync.REFERENCED))
        self.assertNotIn('simulation.simulationrun', sync.MODELS)
        self.assertEqual(sync.LATEST, {'simulation.simulationrun': 20})
        self.assertNotIn('__LATEST__', sync.local_program())


class BackendDiscoveryTests(unittest.TestCase):
    def test_empty_worktree_db_is_not_a_host_db(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            local = Path(tmp) / '.local'
            local.mkdir()
            (local / 'registry.sqlite3').write_bytes(b'0' * 4096)
            self.assertFalse(sync.has_host_db(tmp))
            (local / 'registry.sqlite3').write_bytes(b'0' * 1_000_001)
            self.assertTrue(sync.has_host_db(tmp))


if __name__ == '__main__':
    unittest.main()
