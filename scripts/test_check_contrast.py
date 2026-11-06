import re
import unittest

import check_contrast as c

LIGHT = {'text': '#000000', 'link': '#000000', 'muted': '#000000', 'muted-strong': '#000000',
         'focus': '#000000', 'success': '#000000', 'danger': '#000000', 'neutral': '#000000',
         'page-background': '#ffffff', 'surface': '#ffffff', 'surface-muted': '#ffffff',
         'border': '#767676', 'header-foreground': '#ffffff', 'header-background': '#000000',
         'on-link': '#ffffff'}


class ContrastTests(unittest.TestCase):
    def test_known_ratios(self):
        self.assertAlmostEqual(c.ratio('#000', '#fff'), 21, places=1)
        self.assertAlmostEqual(c.ratio('#767676', '#ffffff'), 4.54, places=2)

    def test_weak_token_is_reported_by_name(self):
        self.assertEqual(c.problems(LIGHT), [])
        found = c.problems({**LIGHT, 'danger': '#cccccc'})
        self.assertTrue(found and all(item.startswith('danger') for item in found))

    def test_real_theme_passes_in_both_modes(self):
        self.assertEqual(c.main(), 0)

    def test_flow_page_has_no_private_colors(self):
        css = (c.ROOT / 'backend/automation_log/static/automation_log/flow.css').read_text(encoding='utf-8')
        self.assertEqual(re.findall(r'#[0-9a-fA-F]{3,8}\b(?![\w-])', re.sub(r'#flow\b', '', css)), [])


if __name__ == '__main__':
    unittest.main()
