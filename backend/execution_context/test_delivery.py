from unittest.mock import patch
from django.test import SimpleTestCase
from .delivery import deliver_one
from .desktop_session_bridge import BridgeError


class DeliveryTests(SimpleTestCase):
    @patch('execution_context.delivery.deliver_owned', return_value=None)
    def test_busy_session_has_one_attempt(self, sender):
        self.assertIsNone(deliver_one())
        sender.assert_called_once_with()

    @patch('execution_context.delivery.deliver_owned', return_value=7)
    def test_success_has_one_attempt(self, sender):
        self.assertEqual(deliver_one(), 7)
        sender.assert_called_once_with()

    def test_every_failure_propagates_without_retry_or_fallback(self):
        for reason in ['response timeout', 'already has an active writer', 'channel unavailable']:
            with self.subTest(reason=reason), patch('execution_context.delivery.deliver_owned',
                    side_effect=BridgeError(reason)) as sender:
                with self.assertRaises(BridgeError):
                    deliver_one()
                sender.assert_called_once_with()

    def test_retired_sender_cannot_launch_or_claim(self):
        from .desktop_session_bridge import deliver_one as retired
        with patch('execution_context.desktop_session_bridge.AppServer') as process, patch(
                'execution_context.desktop_session_bridge.call_command') as command:
            with self.assertRaises(BridgeError):
                retired()
            process.assert_not_called()
            command.assert_not_called()
