import sys
import tempfile
import unittest
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import marker_frames as frames  # noqa: E402

# نطاق تدرّج العلامة الخضراء في HSV كما يخزّنه OpenCV (0 إلى 179).
HUE = (45, 65)


def marker_like(image):
    """بكسلات مشبعة ومضيئة في نطاق تدرّج العلامة: ما يحتاج البرنامج أن يميّزه."""
    hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
    return cv2.inRange(hsv, (HUE[0], 150, 80), (HUE[1], 255, 255)) > 0


class GenerationTests(unittest.TestCase):
    def test_same_seed_gives_the_same_frames(self):
        first = [(name, kind, found, image.tobytes()) for name, kind, image, found
                 in frames.frames(count=8)]
        second = [(name, kind, found, image.tobytes()) for name, kind, image, found
                  in frames.frames(count=8)]
        self.assertEqual(first, second)

    def test_truth_matches_the_kind(self):
        kinds = set()
        for _, kind, image, found in frames.frames(count=60):
            kinds.add(kind)
            self.assertEqual(image.shape, (frames.HEIGHT, frames.WIDTH, 3))
            self.assertEqual(found is None, kind in frames.ABSENT, kind)
        self.assertEqual(kinds, set(frames.PATTERN))

    def test_small_count_still_mixes_kinds(self):
        kinds = {kind for _, kind, _, _ in frames.frames(count=10)}
        self.assertGreater(len(kinds), 2)

    def test_missing_results_file_gives_a_message(self):
        with self.assertRaises(SystemExit) as caught:
            frames.read_rows('no-such-file.csv', ['frame'])
        self.assertIn('تعذّرت قراءته', str(caught.exception))

    def test_partial_marker_is_cut_by_the_bus(self):
        rng = np.random.default_rng(3)
        for _ in range(20):
            _, mask = frames.render('partial', rng)
            _, _, pixels = frames.centre(mask)
            columns = mask.any(axis=0).sum()
            self.assertLess(pixels, 0.8 * np.pi * (columns / 2) ** 2)
            self.assertGreater(pixels, 20)

    def test_every_frame_is_solvable_by_colour(self):
        """العلامة تتميّز بلونها في كل نوع، ولا شيء غيرها يشبهها: التمرين له حل."""
        rng = np.random.default_rng(11)
        for kind in frames.PATTERN * 2:
            image, mask = frames.render(kind, rng)
            alike = marker_like(image)
            outside = alike & (cv2.dilate(mask, np.ones((9, 9), np.uint8)) == 0)
            self.assertLess(outside.sum(), 10, kind)
            if kind not in frames.ABSENT:
                inside = (alike & (mask > 0)).sum() / (mask > 0).sum()
                self.assertGreater(inside, 0.6, kind)

    def test_make_writes_frames_under_an_arabic_folder(self):
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / 'لقطات'
            rows = frames.make(target, count=6)
            self.assertEqual(len(list(target.glob('frame_*.png'))), 6)
            truth = frames.read_rows(target / frames.TRUTH, ['frame', 'kind', 'present', 'x', 'y'])
            self.assertEqual([row['frame'] for row in truth], [row['frame'] for row in rows])
            image = cv2.imdecode(np.fromfile(target / 'frame_000.png', np.uint8), cv2.IMREAD_COLOR)
            self.assertEqual(image.shape, (frames.HEIGHT, frames.WIDTH, 3))


class ScoreTests(unittest.TestCase):
    TRUTH = [
        {'frame': 'a.png', 'kind': 'clear', 'present': '1', 'x': '100', 'y': '200'},
        {'frame': 'b.png', 'kind': 'dim', 'present': '1', 'x': '300', 'y': '150'},
        {'frame': 'c.png', 'kind': 'empty', 'present': '0', 'x': '', 'y': ''},
    ]

    def answers(self, **changes):
        rows = {'a.png': {'frame': 'a.png', 'found': '1', 'x': '101', 'y': '199'},
                'b.png': {'frame': 'b.png', 'found': '1', 'x': '300', 'y': '150'},
                'c.png': {'frame': 'c.png', 'found': '0', 'x': '', 'y': ''}}
        rows.update(changes)
        return [row for row in rows.values() if row]

    def test_correct_answers_pass(self):
        result = frames.score(self.TRUTH, self.answers())
        self.assertTrue(result['passed'])
        self.assertEqual(result['recall'], 1.0)
        self.assertEqual(result['false_alarms'], 0)

    def test_position_beyond_tolerance_is_a_miss(self):
        far = {'frame': 'a.png', 'found': '1', 'x': '106', 'y': '200'}
        result = frames.score(self.TRUTH, self.answers(**{'a.png': far}))
        self.assertEqual(result['recall'], 0.5)
        self.assertFalse(result['passed'])

    def test_missing_row_counts_as_not_found(self):
        result = frames.score(self.TRUTH, self.answers(**{'b.png': None}))
        self.assertEqual(result['recall'], 0.5)

    def test_false_alarm_on_empty_frame_fails(self):
        alarm = {'frame': 'c.png', 'found': '1', 'x': '5', 'y': '5'}
        result = frames.score(self.TRUTH, self.answers(**{'c.png': alarm}))
        self.assertEqual(result['false_alarms'], 1)
        self.assertFalse(result['passed'])

    def test_frame_name_with_folder_still_matches(self):
        full = {'frame': 'C:\\frames\\a.png', 'found': '1', 'x': '100', 'y': '200'}
        result = frames.score(self.TRUTH, self.answers(**{'a.png': full}))
        self.assertTrue(result['passed'])
        self.assertEqual(result['unknown'], 0)

    def test_row_for_no_frame_is_reported(self):
        stray = {'frame': 'zzz.png', 'found': '1', 'x': '1', 'y': '1'}
        result = frames.score(self.TRUTH, self.answers(stray=stray))
        self.assertEqual(result['unknown'], 1)

    def test_duplicate_row_stops_the_check(self):
        rows = self.answers() + [{'frame': 'c.png', 'found': '1', 'x': '5', 'y': '5'}]
        with self.assertRaises(SystemExit):
            frames.score(self.TRUTH, rows)

    def test_unknown_found_value_stops_the_check(self):
        odd = {'frame': 'c.png', 'found': 'نعم', 'x': '', 'y': ''}
        with self.assertRaises(SystemExit):
            frames.score(self.TRUTH, self.answers(**{'c.png': odd}))

    def test_found_written_as_decimal_counts(self):
        decimal = {'frame': 'c.png', 'found': '1.0', 'x': '5', 'y': '5'}
        result = frames.score(self.TRUTH, self.answers(**{'c.png': decimal}))
        self.assertEqual(result['false_alarms'], 1)

    def test_unreadable_position_is_a_miss(self):
        broken = {'frame': 'a.png', 'found': '1', 'x': '', 'y': ''}
        result = frames.score(self.TRUTH, self.answers(**{'a.png': broken}))
        self.assertEqual(result['recall'], 0.5)


if __name__ == '__main__':
    unittest.main()
