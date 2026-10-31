import json
from types import SimpleNamespace
from unittest.mock import patch
from django.test import SimpleTestCase
from .native_queue_bridge import deliver_one
from .desktop_session_bridge import BridgeError


class NativeQueueTests(SimpleTestCase):
    def invoke(self, result=None, error=None):
        payload={'dispatch_id':7,'claim_token':'token','thread_id':'existing-thread','prompt':'task-marker'}
        def claim(*args, **kwargs):
            self.assertTrue(kwargs['enqueue_only'])
            kwargs['stdout'].write(json.dumps(payload))
        with patch('execution_context.native_queue_bridge.reconcile'), \
             patch('execution_context.native_queue_bridge.SessionDispatch') as model, \
             patch('execution_context.native_queue_bridge.call_command',side_effect=claim), \
             patch('execution_context.native_queue_bridge._binary',return_value='codex'), \
             patch('execution_context.native_queue_bridge.subprocess.run',return_value=result,side_effect=error) as run, \
             patch('execution_context.native_queue_bridge._command') as transition:
            model.objects.filter.return_value.exists.return_value=False
            if error or result.returncode:
                with self.assertRaises(BridgeError):deliver_one()
                self.assertEqual(transition.call_args.args[0],'uncertain')
            else:
                self.assertEqual(deliver_one(),7)
                self.assertEqual(transition.call_args.args[0],'sent')
                self.assertEqual(run.call_args.args[0],['codex','queue','--thread','existing-thread','--message','task-marker'])
            self.assertEqual(run.call_count,1)

    def test_acceptance_is_sent_not_completed(self):
        self.invoke(SimpleNamespace(returncode=0,stdout='Queued message receipt',stderr=''))

    def test_rejection_is_uncertain_without_retry(self):
        self.invoke(SimpleNamespace(returncode=1,stdout='',stderr='connection lost'))

    def test_timeout_is_uncertain_without_retry(self):
        self.invoke(error=TimeoutError('ambiguous timeout'))
