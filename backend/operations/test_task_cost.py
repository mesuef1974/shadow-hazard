import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch
from django.test import SimpleTestCase
from .task_cost import collect


class TaskCostTests(SimpleTestCase):
    def setUp(self):
        self.temp=TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.path=Path(self.temp.name)/'source.jsonl'
        self.path.write_text('\n'.join(json.dumps({'payload':x}) for x in [
            {'started_at':10},{'completed_at':13,'last_agent_message':'done'}]),encoding='utf-8')
        self.data={'thread_id':'thread','turn_id':'turn','usage':{'input_tokens':100,'cached_input_tokens':60,'output_tokens':10,'total_tokens':110},
            'evidence':{'path':str(self.path),'sha256':hashlib.sha256(self.path.read_bytes()).hexdigest(),'start_line':1,'complete_line':2}}
        for target,value in [('ProjectDocumentation.objects.get',SimpleNamespace(pk=9,digest='digest')),
                             ('ModelJob.objects.get',SimpleNamespace(result='done')),
                             ('verified_document',True),('metrics',self.data)]:
            p=patch('operations.task_cost.'+target,return_value=value);p.start();self.addCleanup(p.stop)
        self.stages=[{'id':'execution','role':'execution','job_id':1}]+[
            {'id':role,'role':role,'unknown_reason':'No verified source'} for role in ['review','correction','coordination']]

    def test_partial_cost_is_not_complete_total(self):
        result=collect('project',self.stages,9)
        self.assertIsNone(result['total_task_usage'])
        self.assertEqual(result['verified_subtotal']['total_tokens'],110)
        self.assertEqual(result['verified_subtotal']['noncached_input_tokens'],40)
        self.assertEqual(result['sum_verified_turn_seconds'],3)

    def test_duplicate_job_across_roles_is_rejected(self):
        self.stages[1]={'id':'review','role':'review','job_id':1}
        with self.assertRaises(ValueError):collect('project',self.stages,9)

    def test_duplicate_turn_across_jobs_is_rejected(self):
        self.stages[1]={'id':'review','role':'review','job_id':2}
        with self.assertRaisesRegex(ValueError,'two stages'):collect('project',self.stages,9)

    def test_missing_role_and_duplicate_stage_are_rejected(self):
        with self.assertRaises(ValueError):collect('project',self.stages[:-1],9)
        self.stages[1]['id']='execution'
        with self.assertRaises(ValueError):collect('project',self.stages,9)

    def test_changed_source_does_not_become_zero_cost(self):
        self.path.write_text('{}',encoding='utf-8')
        result=collect('project',self.stages,9)
        self.assertEqual(result['stages'][0]['status'],'unknown')
        self.assertIsNone(result['stages'][0]['usage'])
        self.assertIsNone(result['total_task_usage'])

    def test_output_mismatch_is_unknown(self):
        with patch('operations.task_cost.ModelJob.objects.get',return_value=SimpleNamespace(result='other')):
            self.assertIsNone(collect('project',self.stages,9)['total_task_usage'])

    def test_unverified_acceptance_reference_is_rejected(self):
        with patch('operations.task_cost.verified_document',return_value=False):
            with self.assertRaises(ValueError):collect('project',self.stages,9)

    def test_complete_declared_stages_count_unique_turns_once(self):
        stages=[{'id':r,'role':r,'job_id':n} for n,r in enumerate(['execution','review','correction','coordination'],1)]
        data=[{**self.data,'turn_id':str(n)} for n in range(4)]
        with patch('operations.task_cost.metrics',side_effect=data):
            result=collect('project',stages,9)
        self.assertEqual(result['total_task_usage']['total_tokens'],440)
        self.assertEqual(result['coverage'],'complete_declared_stages')
