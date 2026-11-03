import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch, MagicMock
from django.test import TestCase
from django.contrib.auth import get_user_model
from workspace.models import Project, ProjectDocumentation, ModelPolicy, SessionCharter, ModelJob
from workspace.memory import document
from token_usage.models import TokenSample
from token_usage.capacity import verified_document
from .operator_usage import KEY, arm, collect, snapshot, metrics


class OperatorUsageTests(TestCase):
    def setUp(self):
        self.project=Project.objects.create(name='usage comparison')
        self.owner=get_user_model().objects.create(username='mesuef',role='supervisor')
        self.policy=ModelPolicy.objects.create(category='operator')
        self.charter=SessionCharter.objects.create(project=self.project,key='session_operator',policy=self.policy)
        self.baseline=self.job('completed')
        self.before={'job_id':self.baseline.pk,'usage':{'total_tokens':100,'input_tokens':90,'cached_input_tokens':60,'output_tokens':10},
            'tool_calls':5,'responses':6,'model':'gpt-6-luna','thinking':'low'}
        with patch('operations.operator_usage.metrics',return_value=self.before):self.armed=arm(self.project,self.baseline.pk)

    def job(self,state='queued'):
        return ModelJob.objects.create(charter=self.charter,owner=self.owner,prompt='case',state=state)

    def natural(self,job):
        return document(self.project,'session_operator',f'operator-natural-batch:{job.pk}',
            'natural',{'job_id':job.pk,'incident_ids':[1],'policy_id':1})

    def test_waits_without_creating_jobs_or_samples_and_ignores_manual_jobs(self):
        self.job('completed')
        before=(ModelJob.objects.count(),TokenSample.objects.count(),ProjectDocumentation.objects.count())
        self.assertIsNone(collect(self.project))
        self.assertEqual(snapshot(self.project)['state'],'waiting')
        self.assertEqual(before,(ModelJob.objects.count(),TokenSample.objects.count(),ProjectDocumentation.objects.count()))

    def test_first_natural_is_pinned_waits_for_analysis_and_result_is_idempotent(self):
        first=self.job();self.natural(first)
        self.assertIsNone(collect(self.project))
        later=self.job('completed');self.natural(later)
        self.assertEqual(snapshot(self.project)['selected_job'],first.pk)
        first.state='completed';first.save()
        self.assertIsNone(collect(self.project))
        analysis=document(self.project,'session_operator',f'operator-analysis:{first.pk}','analysis',{'job_id':first.pk})
        after={**self.before,'job_id':first.pk,'usage':{**self.before['usage'],'total_tokens':60,'input_tokens':55,'output_tokens':5,'cached_input_tokens':40},'tool_calls':2}
        counts=(ModelJob.objects.count(),TokenSample.objects.count())
        with patch('operations.operator_usage.metrics',return_value=after) as read:
            one=collect(self.project);two=collect(self.project)
        self.assertEqual(read.call_count,1)
        self.assertEqual(one.pk,two.pk);self.assertTrue(verified_document(one))
        self.assertEqual(one.evidence['analysis_receipt'],analysis.pk)
        self.assertEqual(one.evidence['total_delta'],-40)
        self.assertEqual(one.evidence['tools_delta'],-3)
        self.assertFalse(one.evidence['billing_writes'])
        self.assertEqual(counts,(ModelJob.objects.count(),TokenSample.objects.count()))
        self.assertEqual(snapshot(self.project)['state'],'completed')

    def test_natural_receipt_with_same_clock_timestamp_is_selected_by_insert_order(self):
        first=self.job()
        with patch('django.utils.timezone.now',return_value=self.armed.created_at):
            natural=self.natural(first)
        self.assertEqual(natural.created_at,self.armed.created_at)
        self.assertGreater(natural.pk,self.armed.pk)
        self.assertIsNone(collect(self.project))
        self.assertEqual(snapshot(self.project)['selected_job'],first.pk)

    def test_bad_analysis_or_foreign_receipt_does_not_complete(self):
        job=self.job('completed');self.natural(job)
        other=Project.objects.create(name='other')
        document(other,'session_operator',f'operator-analysis:{job.pk}','foreign',{'job_id':job.pk})
        self.assertIsNone(collect(self.project))
        analysis=document(self.project,'session_operator',f'operator-analysis:{job.pk}','analysis',{'job_id':job.pk})
        analysis.summary='tampered';analysis.save()
        self.assertIsNone(collect(self.project))
        self.assertFalse(ProjectDocumentation.objects.filter(task_key=KEY+':result').exists())

    def test_arm_replay_and_tampered_selection_fail_closed(self):
        self.assertEqual(arm(self.project,self.baseline.pk).pk,self.armed.pk)
        job=self.job();self.natural(job);collect(self.project)
        row=ProjectDocumentation.objects.get(task_key=KEY+':selected');row.evidence={};row.save()
        with self.assertRaises(ValueError):collect(self.project)
        self.assertEqual(snapshot(self.project)['state'],'untrusted')

    def test_comparison_ui_is_supervisor_only_in_both_pages(self):
        for path in ['/usage/','/sessions/automation/']:
            self.assertEqual(self.client.get(path).status_code,302)
        self.client.force_login(self.owner)
        for path in ['/usage/','/sessions/automation/']:
            response=self.client.get(path)
            self.assertContains(response,'id="operator-comparison"')
            self.assertContains(response,'بانتظار أول تنبيه طبيعي')
            self.assertContains(response,'استدعاءات الأدوات المسجلة')
        student=get_user_model().objects.create(username='saud',role='student')
        self.client.force_login(student)
        for path in ['/usage/','/sessions/automation/']:self.assertEqual(self.client.get(path).status_code,403)

    def test_recorded_tool_calls_count_once_and_reject_duplicate_ids_or_usage_mismatch(self):
        job=MagicMock();job.state='completed';job.pk=77;job.prompt='case';job.token_usage=self.before['usage']
        dispatch=job.session_dispatch;dispatch.state='completed';dispatch.turn_key='turn';dispatch.target_thread_id='thread'
        turn=MagicMock();turn.turn_key='turn';turn.target_thread_id='thread';turn.usage=job.token_usage;turn.source_evidence={}
        dispatch.turns.all.return_value=[turn];dispatch.events.filter.return_value.order_by.return_value.first.return_value=None
        records=[{'type':'event_msg','payload':{}},
            {'type':'response_item','payload':{'type':'custom_tool_call','call_id':'a','name':'exec'}},
            {'type':'response_item','payload':{'type':'custom_tool_call_output','call_id':'a'}},
            {'type':'response_item','payload':{'type':'function_call','call_id':'b','name':'tool'}},
            {'type':'event_msg','payload':{}}]
        proof={'usage':job.token_usage,'evidence':{'start_line':1,'complete_line':5,'response_count':3}}
        with TemporaryDirectory() as folder:
            path=Path(folder)/'rollout.jsonl'
            path.write_text('\n'.join(json.dumps(x) for x in records),encoding='utf-8')
            with patch('operations.operator_usage.find_rollout',return_value=path),patch('operations.operator_usage.read_turn_evidence',return_value=proof):
                self.assertEqual(metrics(job)['tool_calls'],2)
                records[3]['payload']['call_id']='a'
                path.write_text('\n'.join(json.dumps(x) for x in records),encoding='utf-8')
                with self.assertRaises(ValueError):metrics(job)
                proof['usage']={**job.token_usage,'total_tokens':999}
                with self.assertRaises(ValueError):metrics(job)
