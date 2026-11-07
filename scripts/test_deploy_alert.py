import tempfile
import unittest
from pathlib import Path

import deploy_alert


class DeployStateTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)

    def state(self):
        return (self.root / deploy_alert.STATE).read_text(encoding='utf-8')

    def test_one_record_per_failure_not_every_cycle(self):
        self.assertTrue(deploy_alert.notify(self.root, 'نسخة متّسخة'))
        self.assertFalse(deploy_alert.notify(self.root, 'نسخة متّسخة'))

    def test_a_different_failure_is_recorded_again(self):
        deploy_alert.notify(self.root, 'نسخة متّسخة')
        self.assertTrue(deploy_alert.notify(self.root, 'فشل الهجرة'))
        self.assertEqual(self.state(), 'فشل الهجرة')

    def test_recovery_is_recorded_once_and_clears_the_state(self):
        deploy_alert.notify(self.root, 'نسخة متّسخة')
        self.assertTrue(deploy_alert.notify(self.root, ''))
        self.assertFalse(deploy_alert.notify(self.root, ''))
        self.assertEqual(self.state(), '')

    def test_healthy_deploy_changes_nothing(self):
        self.assertFalse(deploy_alert.notify(self.root, ''))
        self.assertFalse((self.root / deploy_alert.STATE).exists())

    def test_no_windows_notification_code_remains(self):
        source = Path(deploy_alert.__file__).read_text(encoding='utf-8')
        for word in ('Toast', 'powershell', 'subprocess', 'Notifications'):
            self.assertNotIn(word, source)


if __name__ == '__main__':
    unittest.main()
