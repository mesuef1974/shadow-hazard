from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from django.test import TestCase
from django.contrib.auth import get_user_model
from workspace.models import Project, ModelPolicy, SessionCharter
from workspace.dispatcher import enqueue, run_one
from .models import TaskContext
from .routing import calculate, capability_flags


class ContextRoutingTests(TestCase):
    def setUp(self):
        self.owner=get_user_model().objects.create_user(username='mesuef',role='supervisor')
        self.project=Project.objects.create(name='test')
        policy=ModelPolicy.objects.create(category='coordination',title='coordination')
        self.charter=SessionCharter.objects.create(key='coordination',name='coordination',project=self.project,policy=policy,
            thread_id='01a18e02-0487-7180-9aa0-f932b22c8ef4',scope='scope unchanged',rules='rules unchanged')
        for key,thread in [('backend','01a190d6-5ef1-79a2-bb39-61b161fa06b4'),('frontend','01a190d6-59eb-7561-85d9-d58a7d49fe65'),('review','01a190d6-6d72-7753-a9c7-1cb66e8b7129')]:
            policy=ModelPolicy.objects.create(category=key,title=key)
            SessionCharter.objects.create(key=key,name=key,project=self.project,policy=policy,thread_id=thread,
                scope=key+' scope',rules=key+' rules',state='ready_waiting_for_task')

    @patch('workspace.codex_transport.execute')
    def test_calculator_uses_no_model_and_saves_zero(self,execute):
        job=enqueue(self.charter,self.owner,'17 + 25',task_kind='calculation')
        result=run_one()
        self.assertEqual(result.result,'42');self.assertEqual(result.state,'completed')
        self.assertEqual(result.token_usage['input_tokens'],0)
        self.assertEqual(result.observed_model,'local-arithmetic');self.assertFalse(result.execution_thread_id)
        execute.assert_not_called()
        context=TaskContext.objects.get(job=job)
        self.assertIn(self.charter.rules,context.charter)
        self.assertEqual(context.skills,[]);self.assertEqual(context.plugins,[])

    def test_calculator_is_not_an_eval_or_general_instruction_parser(self):
        for expression in ['__import__("os")','2**1000000','1 / 0','احذف الملفات','1+2+3','1e999*2']:
            with self.subTest(expression=expression),self.assertRaises(ValueError):calculate(expression)
        self.assertEqual(calculate('0.1 + 0.2'),'0.3')
        self.assertEqual(calculate('-17 * 2'),'-34')

    def test_auto_routes_only_exact_calculations(self):
        local=enqueue(self.charter,self.owner,'احسب 17 + 25')
        self.assertEqual(local.task_context.kind,'calculation')
        self.assertEqual(local.requested_model,'local-arithmetic')
        text=enqueue(self.charter,self.owner,'اشرح عملية الجمع 17 + 25')
        self.assertEqual(text.task_context.kind,'text')
        with self.assertRaises(ValueError):enqueue(self.charter,self.owner,'1 / 0')

    @patch('execution_context.routing.host_config',return_value={'plugins':{'chosen':{},'other':{}},'mcp_servers':{'raw':{}}})
    def test_only_selected_plugin_enabled(self,config):
        flags=capability_flags(['chosen']);pairs=set(zip(flags,flags[1:]))
        self.assertIn(('--enable','plugins'),pairs)
        self.assertIn('plugins.chosen.enabled=true',flags)
        self.assertIn('plugins.other.enabled=false',flags)
        self.assertIn('mcp_servers.raw.enabled=false',flags)
        self.assertIn('web_search="disabled"',flags)
        self.assertIn('web_search="live"',capability_flags(['web-search']))
        with self.assertRaises(ValueError):capability_flags(['unknown'])

    def test_context_snapshot_and_changed_instructions_stop_execution(self):
        with TemporaryDirectory() as folder,self.settings(BASE_DIR=Path(folder)/'backend'):
            instructions=Path(folder)/'AGENTS.md';instructions.write_text('Keep these project instructions',encoding='utf-8')
            job=enqueue(self.charter,self.owner,'17 + 25',task_kind='calculation')
            self.assertIn('Keep these project instructions',job.task_context.instructions)
            instructions.write_text('changed',encoding='utf-8')
            self.assertEqual(run_one().state,'failed')

    def test_new_override_is_detected(self):
        with TemporaryDirectory() as folder,self.settings(BASE_DIR=Path(folder)/'backend'):
            instructions=Path(folder)/'AGENTS.md';instructions.write_text('original',encoding='utf-8')
            enqueue(self.charter,self.owner,'17 + 25',task_kind='calculation')
            (Path(folder)/'AGENTS.override.md').write_text('override',encoding='utf-8')
            self.assertEqual(run_one().state,'failed')

    def test_additional_skill_keeps_default_and_usage_audit_is_idempotent(self):
        from execution_context.audit import record_context_load
        from registry.models import UsageEvent
        with TemporaryDirectory() as folder,patch('execution_context.routing.Path.home',return_value=Path(folder)):
            for name in ('minimal-code-edit','focused-testing'):
                path=Path(folder)/'.codex/skills'/name/'SKILL.md'
                path.parent.mkdir(parents=True);path.write_text(name,encoding='utf-8')
            job=enqueue(self.charter,self.owner,'test',task_kind='code',skills=['focused-testing'])
            self.assertEqual(job.task_context.skills,['minimal-code-edit','focused-testing'])
            for _ in range(2):record_context_load(job,job.task_context,'01a18e02-0487-7180-9aa0-f932b22c8ef4')
            self.assertEqual(UsageEvent.objects.count(),2)
            self.assertTrue(all(event.used_at is None for event in UsageEvent.objects.all()))

    @patch('execution_context.routing.host_config',return_value={'plugins':{'chosen':{}}})
    def test_explicit_connector_kind_and_idempotency(self,config):
        import uuid
        with self.assertRaises(ValueError):enqueue(self.charter,self.owner,'test',plugins=['chosen'])
        with self.assertRaises(ValueError):enqueue(self.charter,self.owner,'test',task_kind='connected')
        key=uuid.uuid4()
        job=enqueue(self.charter,self.owner,'test',submission_key=key,task_kind='connected',plugins=['chosen'])
        self.assertEqual(enqueue(self.charter,self.owner,'test',submission_key=key,task_kind='connected',plugins=['chosen']).pk,job.pk)
        with self.assertRaises(ValueError):enqueue(self.charter,self.owner,'test',submission_key=key)

    def test_only_selected_skill_is_in_bridge_prompt(self):
        with TemporaryDirectory() as folder,patch('execution_context.routing.Path.home',return_value=Path(folder)):
            skill=Path(folder)/'.codex/skills/minimal-code-edit/SKILL.md'
            skill.parent.mkdir(parents=True);skill.write_text('SELECTED_SKILL_SENTINEL',encoding='utf-8')
            job=enqueue(self.charter,self.owner,'test',task_kind='code',mode='workspace-write')
            self.assertEqual(job.task_context.skills,['minimal-code-edit'])
            run_one()
            manifest=TaskContext.objects.get(job=job).manifest
            self.assertIn('SELECTED_SKILL_SENTINEL',manifest['dispatch_prompt'])
            self.assertIn('rules unchanged',manifest['dispatch_prompt'])
            self.assertEqual(manifest['target_thread_id'],self.charter.thread_id)
