from io import StringIO
import json
from django.test import TestCase
from django.core.management import call_command
from django.contrib.auth import get_user_model
from workspace.models import Project,ModelPolicy,SessionCharter,ProjectMemory,ProjectDocumentation
from workspace.memory import document,instructions
from workspace.dispatcher import enqueue,run_one


class ProjectMemoryTests(TestCase):
    def setUp(self):
        self.project=Project.objects.create(name='test')
        self.owner=get_user_model().objects.create_user(username='mesuef',role='supervisor')
        policy=ModelPolicy.objects.create(category='coordination',title='coordination')
        self.charter=SessionCharter.objects.create(key='coordination',name='coordination',project=self.project,policy=policy,
            thread_id='01a18e02-0487-7180-9aa0-f932b22c8ef4')
        ProjectMemory.objects.create(project=self.project,key='documentation',instruction='DATABASE_ONLY_POLICY_SENTINEL')

    def test_memory_is_in_task_context_and_read_has_verified_receipt(self):
        job=enqueue(self.charter,self.owner,'hello',task_kind='text')
        self.assertIn('DATABASE_ONLY_POLICY_SENTINEL',job.task_context.instructions)
        output=StringIO();call_command('project_memory','read','--session','coordination',stdout=output)
        value=json.loads(output.getvalue());row=ProjectDocumentation.objects.get(pk=value['verified_receipt'])
        self.assertEqual(row.evidence['memory_digest'],instructions(self.project)[1])

    def test_documentation_replay_is_verified_and_conflicts_fail(self):
        one=document(self.project,'coordination','task1','done',{'tests':2})
        two=document(self.project,'coordination','task1','done',{'tests':2})
        self.assertEqual(one.pk,two.pk)
        with self.assertRaises(ValueError):document(self.project,'coordination','task1','different',{'tests':2})
        with self.assertRaises(ValueError):document(self.project,'coordination','task2','done',{})

    def test_read_only_preparation_never_creates_desktop_outbox(self):
        job=enqueue(self.charter,self.owner,'hello',task_kind='text',mode='read-only')
        result=run_one();self.assertEqual(result.state,'waiting_readonly')
        job.refresh_from_db();self.assertIsNone(job.finished_at)
        self.assertFalse(hasattr(job,'session_dispatch'))

    def test_read_only_completion_requires_verified_documentation(self):
        from unittest.mock import patch
        from execution_context.readonly_service import run_pending
        job=enqueue(self.charter,self.owner,'hello',task_kind='text',mode='read-only');run_one()
        response={'thread_id':'owned-thread','turn_id':'owned-turn','text':'READ_OK','usage':{'input_tokens':1,'cached_input_tokens':0,'output_tokens':1,'reasoning_output_tokens':0,'total_tokens':2},'model':'gpt-6-luna','evidence':{'image_id':'sha256:'+'a'*64,'isolation':{'write':True}}}
        with patch('execution_context.readonly_service.execute',return_value=response):
            result=run_pending(job.pk)
        self.assertEqual(result.state,'completed')
        receipt=ProjectDocumentation.objects.get(task_key=f'readonly-job:{job.pk}')
        self.assertEqual(receipt.evidence['turn_id'],'owned-turn')
        self.assertTrue(receipt.digest)

    def test_corrupted_documentation_is_not_reported_as_verified(self):
        row=document(self.project,'coordination','corrupt','done',{'tests':1})
        ProjectDocumentation.objects.filter(pk=row.pk).update(summary='tampered')
        with self.assertRaises(ValueError):document(self.project,'coordination','corrupt','done',{'tests':1})
