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
        for marker in ('__PAYLOAD__', '__APPLY__', '__FORCE__'):
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


if __name__ == '__main__':
    unittest.main()
