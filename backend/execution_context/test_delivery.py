from unittest.mock import patch
from django.test import SimpleTestCase
from .delivery import deliver_one
from .desktop_session_bridge import BridgeError


class DeliveryTests(SimpleTestCase):
    @patch('execution_context.delivery.queue_existing',return_value=7)
    @patch('execution_context.delivery.deliver_owned',return_value=None)
    def test_active_owner_is_queued_once(self,owned,queue):
        self.assertEqual(deliver_one(),7);queue.assert_called_once()

    @patch('execution_context.delivery.queue_existing',return_value=7)
    @patch('execution_context.delivery.deliver_owned',side_effect=BridgeError('thread already has an active writer'))
    def test_explicit_pre_turn_rejection_is_queued_once(self,owned,queue):
        self.assertEqual(deliver_one(),7);queue.assert_called_once()

    @patch('execution_context.delivery.queue_existing')
    @patch('execution_context.delivery.deliver_owned',side_effect=BridgeError('response timeout'))
    def test_ambiguous_error_never_uses_second_transport(self,owned,queue):
        with self.assertRaises(BridgeError):deliver_one()
        queue.assert_not_called()

    @patch('execution_context.delivery.queue_existing')
    @patch('execution_context.delivery.deliver_owned',return_value=7)
    def test_success_never_uses_second_transport(self,owned,queue):
        self.assertEqual(deliver_one(),7);queue.assert_not_called()
