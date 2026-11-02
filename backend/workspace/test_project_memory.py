from io import StringIO
import json
from django.test import TestCase
from django.core.management import call_command
from django.contrib.auth import get_user_model
from workspace.models import Project,ModelPolicy,SessionCharter,ProjectMemory,ProjectDocumentation
from workspace.memory import document,instructions
from workspace.dispatcher import enqueue,run_one


class ProjectMemoryTests(TestCase):
    def test_compact_batch_inspect_is_bounded_and_verifies_full_records(self):
        from django.core.management.base import CommandError
        rows=[document(self.project,self.charter.key,'compact-'+str(i),'s'*900,
            {'job_id':51,'nested':{'large':'x'*20000},'long':'y'*10000,
             'k'*20000:True,'huge_int':10**200}) for i in range(2)]
        output=StringIO()
        args=['project_memory','inspect','--session',self.charter.key,'--compact',
            '--document-ids',str(rows[0].pk),str(rows[1].pk)]
        call_command(*args,stdout=output)
        data=json.loads(output.getvalue())
        self.assertEqual(len(data),2)
        self.assertLess(len(output.getvalue()),2400)
        for item,row in zip(data,rows):
            self.assertTrue(item['verified']);self.assertTrue(item['omitted'])
            self.assertEqual(item['digest'],row.digest)
            self.assertEqual(item['evidence'],{'job_id':51})
            self.assertEqual(len(item['summary']),600)
        rows[1].evidence['nested']['large']='tampered';rows[1].save()
        failed=StringIO()
        with self.assertRaises(CommandError):call_command(*args,stdout=failed)
        self.assertEqual(failed.getvalue(),'')

    def test_inspect_batch_rejects_foreign_and_conflicting_arguments(self):
        from django.core.management.base import CommandError
        own=document(self.project,self.charter.key,'own','own',{'ok':True})
        other=Project.objects.create(name='foreign')
        foreign=document(other,'foreign','private','private',{'secret':'value'})
        with self.assertRaises(CommandError):
            call_command('project_memory','inspect','--session',self.charter.key,
                '--compact','--document-ids',str(own.pk),str(foreign.pk),stdout=StringIO())
        with self.assertRaises(CommandError):
            call_command('project_memory','inspect','--session',self.charter.key,
                '--document-id',str(own.pk),'--document-ids',str(own.pk),stdout=StringIO())

    def test_document_command_rereads_verified_receipt_without_second_inspect(self):
        output=StringIO()
        call_command('project_memory','document','--session',self.charter.key,
            '--task','operator-analysis:51','--summary','actual analysis',
            '--evidence','{"job_id":51}',stdout=output)
        data=json.loads(output.getvalue())
        self.assertTrue(data['verified']);self.assertTrue(data['reread_verified'])
        self.assertEqual(ProjectDocumentation.objects.get(pk=data['id']).summary,'actual analysis')

    def test_inspect_verifies_receipt_and_rejects_other_project_or_tampering(self):
        from django.core.management.base import CommandError
        row=document(self.project,self.charter.key,'analysis','actual analysis',{'synthetic':True})
        output=StringIO()
        call_command('project_memory','inspect','--session',self.charter.key,'--document-id',str(row.pk),stdout=output)
        self.assertTrue(json.loads(output.getvalue())['verified'])
        row.summary='tampered';row.save()
        with self.assertRaises(CommandError):
            call_command('project_memory','inspect','--session',self.charter.key,'--document-id',str(row.pk),stdout=StringIO())
        other=Project.objects.create(name='other')
        foreign=document(other,'other','private','foreign',{'foreign':True})
        with self.assertRaises(CommandError):
            call_command('project_memory','inspect','--session',self.charter.key,'--document-id',str(foreign.pk),stdout=StringIO())
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

    def test_conditional_memory_is_loaded_only_when_applicable_and_checked_for_drift(self):
        from execution_context.routing import validate_manifest
        code=ProjectMemory.objects.create(project=self.project,key='context.code',instruction='CODE_RULE_SENTINEL')
        ProjectMemory.objects.create(project=self.project,key='context.design',instruction='DESIGN_RULE_SENTINEL')
        text=enqueue(self.charter,self.owner,'short text',task_kind='text').task_context
        programming=enqueue(self.charter,self.owner,'code task',task_kind='code').task_context
        self.assertNotIn('CODE_RULE_SENTINEL',text.instructions)
        self.assertIn('CODE_RULE_SENTINEL',programming.instructions)
        self.assertNotIn('DESIGN_RULE_SENTINEL',programming.instructions)
        self.assertIn('DESIGN_RULE_SENTINEL',instructions(self.project,'design')[0])
        code.instruction='changed code rule';code.save(update_fields=['instruction'])
        validate_manifest(text)
        with self.assertRaises(ValueError):validate_manifest(programming)
        output=StringIO();call_command('project_memory','read','--session','coordination','--kind','code',stdout=output)
        result=json.loads(output.getvalue())
        self.assertIn('changed code rule',result['instructions'])
        self.assertTrue(ProjectDocumentation.objects.filter(pk=result['verified_receipt']).exists())
        with self.assertRaises(ValueError):instructions(self.project,'unknown')
        ProjectMemory.objects.create(project=self.project,key='context.typo',instruction='MUST_NOT_DISAPPEAR')
        with self.assertRaises(ValueError):instructions(self.project,'text')

    def test_documentation_replay_is_verified_and_conflicts_fail(self):
        one=document(self.project,'coordination','task1','done',{'tests':2})
        two=document(self.project,'coordination','task1','done',{'tests':2})
        self.assertEqual(one.pk,two.pk)
        with self.assertRaises(ValueError):document(self.project,'coordination','task1','different',{'tests':2})
        with self.assertRaises(ValueError):document(self.project,'coordination','task2','done',{})

    def test_exact_duplicates_are_removed_but_distinct_restrictions_survive(self):
        from workspace.instruction_text import merge_instructions
        self.assertEqual(merge_instructions(['- keep rule','- keep rule\n- keep rule unless approved']),
                         '- keep rule\n\n- keep rule unless approved')
        ProjectMemory.objects.create(project=self.project,key='context.code',instruction='CODE_ONLY_SENTINEL')
        details=instructions(self.project,'code',include_core=False)[0]
        self.assertIn('CODE_ONLY_SENTINEL',details)
        self.assertNotIn('DATABASE_ONLY_POLICY_SENTINEL',details)
        output=StringIO();call_command('project_memory','read','--session','coordination','--kind','code','--details-only',stdout=output)
        self.assertEqual(json.loads(output.getvalue())['instructions'],details)

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

    def test_bootstrap_is_bounded_idempotent_and_does_not_copy_historical_evidence(self):
        from workspace.bootstrap import prepare_bootstrap
        handover=document(self.project,'coordination','handover','concise handover',{'large_history':'SECRET_HISTORY_SENTINEL'*1000})
        first=prepare_bootstrap(self.charter,handover.pk)
        second=prepare_bootstrap(self.charter,handover.pk)
        self.assertEqual(first,second)
        self.assertNotIn('SECRET_HISTORY_SENTINEL',first['prompt'])
        self.assertIn('DATABASE_ONLY_POLICY_SENTINEL',first['prompt'])
        self.assertLessEqual(first['characters'],6000)
        oversized=document(self.project,'coordination','long-handover','x'*2001,{'source':'test'})
        with self.assertRaises(ValueError):prepare_bootstrap(self.charter,oversized.pk)
        ProjectDocumentation.objects.filter(pk=handover.pk).update(summary='tampered')
        with self.assertRaises(ValueError):prepare_bootstrap(self.charter,handover.pk)
