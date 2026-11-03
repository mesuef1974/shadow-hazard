import json
import subprocess
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from django.test import SimpleTestCase
from workspace.startup_context import agent_options, inventory, measure, render, validate_usage, validate_profile


class StartupContextTests(SimpleTestCase):
    def test_light_profile_is_opt_in_only_and_baseline_keeps_subagents(self):
        self.assertEqual(agent_options(False, 'shadow-coordination-light'),
            ['--profile', 'shadow-coordination-light'])
        self.assertEqual(agent_options(True, 'shadow-coordination-light'),
            ['-c', 'agents.enabled=true'])

    def test_inventory_retains_metrics_and_names_without_prompt_or_schema_contents(self):
        data = inventory([{'type': 'message', 'role': 'developer',
            'content': [{'text': 'PRIVATE_RULE_SENTINEL'}]},
            {'type': 'additional_tools', 'tools': [{'name': 'required',
                'tools': [{'name': 'read', 'description': 'PRIVATE_SCHEMA_SENTINEL'}]}]}])
        serialized = json.dumps(data)
        self.assertNotIn('PRIVATE_RULE_SENTINEL', serialized)
        self.assertNotIn('PRIVATE_SCHEMA_SENTINEL', serialized)
        self.assertIn('required', serialized)
        self.assertEqual(data['layers'][0]['text']['characters'], 21)
        self.assertIsNone(data['token_count'])

    @patch('workspace.startup_context.subprocess.run')
    def test_measure_preserves_actual_usage_and_uses_ephemeral_read_only_startup(self, run):
        events = [{'type': 'item.completed', 'item': {'type': 'reasoning', 'text': 'not a tool'}},
            {'type': 'item.completed', 'item': {'type': 'agent_message', 'text': 'CONTEXT_READY'}},
            {'type': 'turn.completed', 'usage': {'input_tokens': 100, 'cached_input_tokens': 80,
                'output_tokens': 7, 'unrelated': 'PRIVATE_USAGE_SENTINEL'}}]
        run.return_value = SimpleNamespace(returncode=0, stdout='\n'.join(map(json.dumps, events)))
        result = measure('codex', '.', 'same bootstrap', agents_enabled=False, model='gpt-6-luna')
        self.assertEqual(result['usage']['input_tokens'], 100)
        self.assertTrue(result['accepted'])
        self.assertNotIn('PRIVATE_USAGE_SENTINEL', json.dumps(result))
        command = run.call_args.args[0]
        self.assertIn('--ephemeral', command)
        self.assertEqual(command[command.index('--sandbox') + 1], 'read-only')
        self.assertIn('agents.enabled=false', command)

    @patch('workspace.startup_context.subprocess.run')
    def test_tool_execution_or_missing_usage_never_counts_as_success_and_does_not_retry(self, run):
        for events in [[], [{'type': 'item.completed', 'item': {'type': 'command_execution'}},
                {'type': 'turn.completed', 'usage': {'input_tokens': 100}}]]:
            run.reset_mock()
            run.return_value = SimpleNamespace(returncode=0, stdout='\n'.join(map(json.dumps, events)))
            self.assertFalse(measure('codex', '.', 'same', agents_enabled=True, model='gpt-6-luna')['accepted'])
            run.assert_called_once()

    @patch('workspace.startup_context.subprocess.run')
    def test_timeout_never_exposes_prompt_or_command(self, run):
        run.side_effect = subprocess.TimeoutExpired(['codex', 'PRIVATE_BOOTSTRAP_SENTINEL'], 1,
            output='PRIVATE_OUTPUT_SENTINEL')
        with self.assertRaises(ValueError) as raised:
            render('codex', '.', 'PRIVATE_BOOTSTRAP_SENTINEL', agents_enabled=True)
        self.assertNotIn('PRIVATE', str(raised.exception))

    def test_usage_rejects_invalid_counters_without_requiring_optional_fields(self):
        validate_usage({'input_tokens': 100, 'output_tokens': 7})
        for values in [{'cached_input_tokens': 101}, {'reasoning_output_tokens': 8},
                {'total_tokens': 108}, {'input_tokens': True}, {'output_tokens': -1}]:
            with self.assertRaises(ValueError):
                validate_usage({'input_tokens': 100, 'output_tokens': 7, **values})

    def test_profile_cannot_change_provider_or_instructions(self):
        with tempfile.TemporaryDirectory() as folder, patch.dict('os.environ', {'CODEX_HOME': folder}):
            path = Path(folder) / 'test.config.toml'
            path.write_text('[agents]\nenabled=false\n', encoding='utf-8')
            self.assertTrue(validate_profile('test')['sha256'])
            path.write_text('model_instructions_file="other"\n[agents]\nenabled=false\n', encoding='utf-8')
            with self.assertRaises(ValueError):validate_profile('test')

    @patch('workspace.startup_context.subprocess.run')
    def test_errors_and_multiple_completed_turns_reject_comparison_but_preserve_usage(self, run):
        usage = {'input_tokens': 100, 'output_tokens': 7}
        events = [{'type': 'item.completed', 'item': {'type': 'agent_message', 'text': 'CONTEXT_READY'}},
            {'type': 'turn.completed', 'usage': usage}]
        for extra in [{'type': 'item.completed', 'item': {'type': 'error', 'message': 'PRIVATE_ERROR'}},
                {'type': 'turn.completed', 'usage': usage}]:
            run.return_value = SimpleNamespace(returncode=0, stdout='\n'.join(map(json.dumps, events + [extra])))
            result = measure('codex', '.', 'same', agents_enabled=True, model='gpt-6-luna')
            self.assertFalse(result['accepted'])
            self.assertEqual(result['usage'], usage)
            self.assertNotIn('PRIVATE_ERROR', json.dumps(result))


from django.test import TestCase
from workspace.test_project_memory import ProjectMemoryTests


class StartupPartialReceiptTests(TestCase):
    setUp = ProjectMemoryTests.setUp

    def test_successful_a_is_persisted_when_b_fails_and_unknown_usage_is_not_zero(self):
        import sys
        from io import StringIO
        from contextlib import ExitStack
        from django.core.management import call_command
        from workspace.memory import document
        from workspace.models import ProjectDocumentation
        handover = document(self.project, self.charter.key, 'test-startup-handover', 'small summary', {'test': True})
        first = {'accepted': True, 'usage': {'input_tokens': 100, 'output_tokens': 7}}
        output = StringIO()
        prefix = 'workspace.management.commands.audit_startup_context.'
        with ExitStack() as stack:
            stack.enter_context(patch(prefix + 'render', side_effect=[{}, {}]))
            stack.enter_context(patch(prefix + 'instruction_files', return_value=[]))
            measured = stack.enter_context(patch(prefix + 'measure', side_effect=[first, ValueError('failed')]))
            call_command('audit_startup_context', binary=sys.executable, session=self.charter.key,
                handover=handover.pk, measure=True, stdout=output)
        data = json.loads(output.getvalue())
        self.assertEqual(measured.call_count, 2)
        self.assertEqual(data['before']['actual']['usage']['input_tokens'], 100)
        self.assertIsNone(data['after']['actual']['usage'])
        saved = ProjectDocumentation.objects.get(pk=data['before']['actual_receipt'])
        self.assertEqual(saved.evidence['actual']['usage']['input_tokens'], 100)
        self.assertNotIn('cli_input_tokens_saved', data)

    def test_rejected_a_never_starts_paid_b(self):
        import sys
        from io import StringIO
        from django.core.management import call_command
        from workspace.memory import document
        handover = document(self.project, self.charter.key, 'rejected-startup', 'small summary', {'test': True})
        prefix = 'workspace.management.commands.audit_startup_context.'
        output = StringIO()
        with patch(prefix + 'render', side_effect=[{}, {}]), patch(prefix + 'instruction_files', return_value=[]), \
                patch(prefix + 'measure', return_value={'accepted': False, 'usage': None}) as measured:
            call_command('audit_startup_context', binary=sys.executable, session=self.charter.key,
                handover=handover.pk, measure=True, stdout=output)
        measured.assert_called_once()
        self.assertTrue(json.loads(output.getvalue())['after']['actual']['not_run'])
