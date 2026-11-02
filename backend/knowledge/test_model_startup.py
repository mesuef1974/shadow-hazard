import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch
from .model_startup import ensure_model, MODEL


class ModelStartupTests(TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.lms = Path(self.temp.name) / 'lms.exe'
        self.lms.touch()
        self.loaded = [{'identifier': MODEL, 'type': 'llm', 'contextLength': 8192}]

    @patch('knowledge.model_startup.command')
    def test_ready_runtime_is_not_reloaded(self, cmd):
        cmd.side_effect = [{'status': 'running', 'pid': 1}, {'running': True, 'port': 1234},
            self.loaded, self.loaded, {'running': True, 'port': 1234}]
        self.assertTrue(ensure_model(self.lms)['native_runtime'])
        self.assertFalse(any('load' in call.args for call in cmd.call_args_list))

    @patch('knowledge.model_startup.command')
    def test_starts_service_and_installed_model_without_download(self, cmd):
        cmd.side_effect = [{'status': 'stopped'}, {'status': 'running'}, {'running': False},
            None, [], [{'modelKey': MODEL}], None, self.loaded, {'running': True, 'port': 1234}]
        self.assertEqual(ensure_model(self.lms)['context_length'], 8192)
        commands = [c.args for c in cmd.call_args_list]
        self.assertTrue(any('up' in c for c in commands))
        self.assertTrue(any('127.0.0.1' in c for c in commands))
        self.assertTrue(any('load' in c for c in commands))
        self.assertFalse(any('get' in c or 'update' in c for c in commands))

    @patch('knowledge.model_startup.command')
    def test_missing_model_is_not_downloaded(self, cmd):
        cmd.side_effect = [{'status': 'running'}, {'running': True, 'port': 1234}, [], []]
        with self.assertRaisesRegex(RuntimeError, 'download disabled'):
            ensure_model(self.lms)

    @patch('knowledge.model_startup.command')
    def test_other_server_port_is_not_stopped(self, cmd):
        cmd.side_effect = [{'status': 'running'}, {'running': True, 'port': 9999}]
        with self.assertRaisesRegex(RuntimeError, 'another port'):
            ensure_model(self.lms)
        self.assertFalse(any('stop' in c.args for c in cmd.call_args_list))
