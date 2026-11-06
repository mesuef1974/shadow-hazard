import tempfile
import unittest
from pathlib import Path

import deploy_alert


class DeployAlertTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        self.shown = []

    def notify(self, reason, shown=True):
        def show(*text):
            self.shown.append(text)
            return shown
        deploy_alert.notify(self.root, reason, show)

    def test_one_alert_per_failure_not_every_cycle(self):
        self.notify('نسخة متّسخة')
        self.notify('نسخة متّسخة')
        self.assertEqual(self.shown, [(deploy_alert.TITLE, 'نسخة متّسخة')])

    def test_a_different_failure_alerts_again(self):
        self.notify('نسخة متّسخة')
        self.notify('فشل الهجرة')
        self.assertEqual(len(self.shown), 2)

    def test_recovery_alerts_once(self):
        self.notify('نسخة متّسخة')
        self.notify('')
        self.notify('')
        self.assertEqual(len(self.shown), 2)
        self.assertEqual(self.shown[-1][0], deploy_alert.RECOVERED)

    def test_healthy_deploy_is_silent(self):
        self.notify('')
        self.assertEqual(self.shown, [])

    def test_failed_display_retries_next_cycle(self):
        self.notify('نسخة متّسخة', shown=False)
        self.notify('نسخة متّسخة')
        self.assertEqual(len(self.shown), 2)


if __name__ == '__main__':
    unittest.main()
