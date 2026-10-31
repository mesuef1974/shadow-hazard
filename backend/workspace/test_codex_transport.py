import io
import json
import subprocess
from unittest import TestCase
from unittest.mock import Mock, patch
from .codex_transport import execute, Unavailable, Uncertain


class TransportTests(TestCase):
    def process(self, events, code=0):
        process=Mock()
        process.stdin=Mock(closed=False)
        process.stdout=io.StringIO(''.join(json.dumps(e)+'\n' for e in events))
        process.wait.return_value=code
        process.poll.return_value=code
        return process

    @patch('workspace.codex_transport.observed_model',return_value='gpt-6-luna')
    @patch('workspace.codex_transport.subprocess.Popen')
    def test_dedicated_execution_and_model_evidence(self,popen,observed):
        tid='01a190e3-e60a-7f82-8f5c-5b1f54a1ec03'
        popen.return_value=self.process([
            {'type':'thread.started','thread_id':tid},
            {'type':'item.completed','item':{'type':'agent_message','text':'OK'}},
            {'type':'turn.completed','usage':{'input_tokens':12,'output_tokens':1}}])
        started=Mock()
        result=execute('codex.exe','desktop-parent','gpt-6-luna','low','test','.', 'read-only',on_started=started)
        command=popen.call_args.args[0]
        self.assertNotIn('resume',command);self.assertNotIn('desktop-parent',command)
        self.assertIn('gpt-6-luna',command);self.assertIn('never',command)
        started.assert_called_once_with(tid)
        self.assertTrue(result['completed']);self.assertEqual(result['model'],'gpt-6-luna')
        self.assertEqual(result['usage']['input_tokens'],12)

    @patch('workspace.codex_transport.subprocess.Popen')
    def test_start_failure(self,popen):
        popen.side_effect=OSError()
        with self.assertRaises(Unavailable):execute('missing','','gpt-6-luna','low','test','.','read-only')

    @patch('workspace.codex_transport.time.monotonic',side_effect=[0,100])
    @patch('workspace.codex_transport.subprocess.Popen')
    def test_deadline_checked_even_when_events_are_ready(self,popen,clock):
        popen.return_value=self.process([{'type':'turn.completed'}])
        with self.assertRaises(Uncertain):execute('codex','','gpt-6-luna','low','test','.','read-only',timeout=1)

    @patch('workspace.codex_transport.os.name','nt')
    @patch('workspace.codex_transport.subprocess.run',side_effect=subprocess.TimeoutExpired('taskkill',10))
    @patch('workspace.codex_transport.time.monotonic',side_effect=[0,100])
    @patch('workspace.codex_transport.subprocess.Popen')
    def test_taskkill_timeout_preserves_uncertain_and_kills_parent(self,popen,clock,killtree):
        process=self.process([]);process.poll.return_value=None;popen.return_value=process
        with self.assertRaises(Uncertain):execute('codex','','gpt-6-luna','low','test','.','read-only',timeout=1)
        process.kill.assert_called_once()

    @patch('workspace.codex_transport.observed_model',return_value='')
    @patch('workspace.codex_transport.subprocess.Popen')
    def test_incomplete_turn_not_success(self,popen,observed):
        popen.return_value=self.process([{'type':'thread.started','thread_id':'01a190e3-e60a-7f82-8f5c-5b1f54a1ec03'}, {'type':'turn.failed'}],1)
        self.assertFalse(execute('codex','','gpt-6-luna','low','test','.','read-only')['completed'])
