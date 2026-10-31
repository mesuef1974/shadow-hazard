from unittest.mock import patch
import uuid
from django.db import IntegrityError, transaction
from django.test import TestCase
from django.contrib.auth import get_user_model
from .models import Project, ModelPolicy, SessionCharter, ModelJob
from .dispatcher import enqueue, run_one
from execution_context.models import SessionDispatch


def write_test_rollout(path,thread_id,model,thinking,turn_id,started):
    import json
    before={'input_tokens':900,'cached_input_tokens':500,'cache_write_input_tokens':0,'output_tokens':100,'reasoning_output_tokens':0,'total_tokens':1000}
    usage={'input_tokens':12,'cached_input_tokens':6,'cache_write_input_tokens':0,'output_tokens':3,'reasoning_output_tokens':0,'total_tokens':15}
    after={key:before[key]+usage[key] for key in before}
    rows=[
        {'type':'session_meta','payload':{'session_id':thread_id}},
        {'type':'event_msg','payload':{'type':'token_count','info':{'total_token_usage':before}}},
        {'type':'event_msg','payload':{'type':'task_started','turn_id':turn_id,'started_at':started}},
        {'type':'turn_context','payload':{'turn_id':turn_id,'model':model,'effort':thinking}},
        {'type':'token_usage_record','payload':{'thread_id':thread_id,'turn_id':turn_id,'turn_token_usage':usage,'thread_token_usage':after}},
        {'type':'event_msg','payload':{'type':'token_count','info':{'total_token_usage':after,'last_token_usage':usage}}},
        {'type':'event_msg','payload':{'type':'task_complete','turn_id':turn_id,'started_at':started,'completed_at':started+0.01,'last_agent_message':'SESSION_BRIDGE_OK 42'}},
    ]
    dispatch=SessionDispatch.objects.filter(target_thread_id=thread_id,state__in=['claimed','acknowledged']).order_by('-pk').first()
    if dispatch:
        rows.insert(4,{'type':'response_item','payload':{'type':'message','role':'user','content':[{'type':'input_text','text':dispatch.job.task_context.manifest.get('task_marker','')}]}})
        rows.insert(5,{'type':'event_msg','payload':{'type':'item_completed','thread_id':thread_id,'turn_id':turn_id,
            'item':{'type':'UserMessage','content':[{'type':'text','text':dispatch.job.task_context.manifest.get('task_marker','')}]}}})
    path.write_text('\n'.join(json.dumps(row) for row in rows)+'\n',encoding='utf-8')
    return path


class DispatcherTests(TestCase):
    def setUp(self):
        self.owner=get_user_model().objects.create_user(username='mesuef',role='supervisor')
        self.student=get_user_model().objects.create_user(username='saud',role='student')
        self.project=Project.objects.create(name='test')
        self.policy=ModelPolicy.objects.create(category='review',title='review',model='gpt-6-luna',thinking='low')
        self.charter=SessionCharter.objects.create(key='review',name='review',project=self.project,policy=self.policy,thread_id='01a190d6-6d72-7753-a9c7-1cb66e8b7129')
        for key,thread in [('coordination','01a18e02-0487-7180-9aa0-f932b22c8ef4'),('backend','01a190d6-5ef1-79a2-bb39-61b161fa06b4'),('frontend','01a190d6-59eb-7561-85d9-d58a7d49fe65')]:
            policy=ModelPolicy.objects.create(category=key,title=key,model='gpt-6-luna',thinking='low')
            SessionCharter.objects.create(key=key,name=key,project=self.project,policy=policy,thread_id=thread,state='ready_waiting_for_task')

    def test_authorization_and_snapshot(self):
        with self.assertRaises(ValueError):enqueue(self.charter,self.student,'test')
        job=enqueue(self.charter,self.owner,'test')
        self.policy.model='gpt-6-astra';self.policy.save()
        job.refresh_from_db();self.assertEqual(job.requested_model,'gpt-6-luna')
        self.client.force_login(self.student)
        self.assertEqual(self.client.get('/sessions/jobs/').status_code,403)
        self.client.force_login(self.owner)
        self.assertEqual(self.client.get('/sessions/jobs/').status_code,200)

    def test_idempotency_and_uncertain_database_lock(self):
        key=uuid.uuid4()
        first=enqueue(self.charter,self.owner,'test',submission_key=key)
        self.assertEqual(enqueue(self.charter,self.owner,'test',submission_key=key).pk,first.pk)
        with self.assertRaises(ValueError):enqueue(self.charter,self.owner,'different',submission_key=key)
        first.state='uncertain';first.save()
        second=enqueue(self.charter,self.owner,'second')
        with self.assertRaises(IntegrityError), transaction.atomic():
            ModelJob.objects.filter(pk=second.pk).update(state='running')

    def test_outbox_created_once_without_independent_execution(self):
        enqueue(self.charter,self.owner,'review the schema',mode='workspace-write')
        self.assertEqual(run_one().state,'waiting_delivery')
        self.assertIsNone(run_one())
        self.assertEqual(SessionDispatch.objects.count(),1)
        dispatch=SessionDispatch.objects.get()
        self.assertEqual(dispatch.target_thread_id,self.charter.thread_id)
        self.assertEqual(dispatch.requested_model,'gpt-6-luna')

    def test_dispatch_prompt_includes_stable_reference(self):
        from django.core.management import call_command
        from io import StringIO
        enqueue(self.charter,self.owner,'review the backend schema',mode='workspace-write')
        run_one()
        output=StringIO();call_command('session_outbox','claim',stdout=output)
        payload=__import__('json').loads(output.getvalue())
        self.assertEqual(payload['thread_id'],self.charter.thread_id)
        self.assertIn('review the backend schema',payload['prompt'])
        self.assertEqual(SessionDispatch.objects.get().state,'claimed')

    def test_review_policy_escalates_once_on_evidenced_stuck_turn(self):
        import json
        import os
        from io import StringIO
        from pathlib import Path
        from tempfile import TemporaryDirectory
        from unittest.mock import patch
        from django.core.management import call_command
        enqueue(self.charter,self.owner,'review the hidden edge case',mode='workspace-write')
        run_one()
        first=StringIO();call_command('session_outbox','claim',stdout=first)
        payload=json.loads(first.getvalue())
        dispatch=SessionDispatch.objects.get(pk=payload['dispatch_id'])
        temp=TemporaryDirectory();root=Path(temp.name);sessions=root/'sessions';sessions.mkdir()
        rollout=write_test_rollout(sessions/f'rollout-{dispatch.target_thread_id}.jsonl',dispatch.target_thread_id,
            dispatch.requested_model,dispatch.requested_thinking,'review-turn-1',dispatch.claimed_at.timestamp())
        def mark(action,*extra):
            call_command('session_outbox',action,'--dispatch',str(payload['dispatch_id']),
                '--claim-token',payload['claim_token'],*extra,stdout=StringIO())
        mark('sent');mark('acknowledged')
        rows=[json.loads(line) for line in rollout.read_text(encoding='utf-8').splitlines()]
        rows[-1]['payload']['last_agent_message']='ESCALATION_REQUEST: الحالة الحدية غير محسومة'
        rollout.write_text('\n'.join(json.dumps(row) for row in rows)+'\n',encoding='utf-8')
        with patch.dict(os.environ,{'CODEX_HOME':str(root)}):
            mark('stuck','--turn-id','review-turn-1','--rollout',str(rollout),
                '--detail',json.dumps({'reason':'المراجعة لم تحسم الحالة الحدية'}))
        dispatch=SessionDispatch.objects.get()
        self.assertEqual((dispatch.state,dispatch.attempt_no,dispatch.escalation_count),('queued',2,1))
        second=StringIO();call_command('session_outbox','claim',stdout=second)
        raised=json.loads(second.getvalue())
        self.assertEqual((raised['model'],raised['thinking']),('gpt-6.1-sol','high'))
        self.assertIn('الحالة الحدية',raised['prompt'])
        temp.cleanup()

    def test_completion_checks_model_and_prevents_global_turn_reuse(self):
        import json
        import os
        from io import StringIO
        from pathlib import Path
        from tempfile import TemporaryDirectory
        from unittest.mock import patch
        from django.core.management import call_command, CommandError
        from execution_context.models import SessionDispatchTurn
        temp=TemporaryDirectory();root=Path(temp.name)
        sessions=root/'sessions'/'2026'/'10'/'31';sessions.mkdir(parents=True)
        def create_and_ack(prompt):
            enqueue(self.charter,self.owner,prompt,mode='workspace-write');run_one()
            output=StringIO();call_command('session_outbox','claim',stdout=output)
            payload=json.loads(output.getvalue())
            for action in ('sent','acknowledged'):
                call_command('session_outbox',action,'--dispatch',str(payload['dispatch_id']),
                    '--claim-token',payload['claim_token'],stdout=StringIO())
            return payload
        first=create_and_ack('review the schema')
        first_dispatch=SessionDispatch.objects.get(pk=first['dispatch_id'])
        first_path=write_test_rollout(sessions/f'rollout-first-{first_dispatch.target_thread_id}.jsonl',first_dispatch.target_thread_id,
            first_dispatch.requested_model,first_dispatch.requested_thinking,'stable-turn',first_dispatch.claimed_at.timestamp())
        # The wrong-model case uses a turn context that conflicts with the request.
        rows=[json.loads(line) for line in first_path.read_text(encoding='utf-8').splitlines()]
        rows[3]['payload']['model']='gpt-6.1-sol'
        first_path.write_text('\n'.join(json.dumps(row) for row in rows)+'\n',encoding='utf-8')
        with self.assertRaises(CommandError),patch.dict(os.environ,{'CODEX_HOME':str(root)}):
            call_command('session_outbox','complete-from-rollout','--dispatch',str(first['dispatch_id']),
                '--claim-token',first['claim_token'],'--turn-id','stable-turn','--rollout',str(first_path),stdout=StringIO())
        rows[3]['payload']['model']='gpt-6-luna'
        first_path.write_text('\n'.join(json.dumps(row) for row in rows)+'\n',encoding='utf-8')
        with patch.dict(os.environ,{'CODEX_HOME':str(root)}):
            call_command('session_outbox','complete-from-rollout','--dispatch',str(first['dispatch_id']),
                '--claim-token',first['claim_token'],'--turn-id','stable-turn','--rollout',str(first_path),stdout=StringIO())
        self.assertEqual(SessionDispatchTurn.objects.get().thinking,'low')
        self.assertEqual(SessionDispatchTurn.objects.get().usage['total_tokens'],15)
        second=create_and_ack('review another schema')
        with self.assertRaises(IntegrityError):
            second_dispatch=SessionDispatch.objects.get(pk=second['dispatch_id'])
            second_path=write_test_rollout(sessions/f'rollout-second-{second_dispatch.target_thread_id}.jsonl',second_dispatch.target_thread_id,
                second_dispatch.requested_model,second_dispatch.requested_thinking,'stable-turn',second_dispatch.claimed_at.timestamp())
            with patch.dict(os.environ,{'CODEX_HOME':str(root)}):
                call_command('session_outbox','complete-from-rollout','--dispatch',str(second['dispatch_id']),
                    '--claim-token',second['claim_token'],'--turn-id','stable-turn','--rollout',str(second_path),stdout=StringIO())
        self.assertEqual(SessionDispatchTurn.objects.count(),1)
        temp.cleanup()

    def test_claim_rejects_revoked_owner_and_changed_destination(self):
        from django.core.management import call_command, CommandError
        from io import StringIO
        enqueue(self.charter,self.owner,'review schema',mode='workspace-write');run_one()
        job=SessionDispatch.objects.get().job
        self.assertIsNone(job.finished_at)
        self.owner.is_active=False;self.owner.save()
        with self.assertRaises(CommandError):call_command('session_outbox','claim',stdout=StringIO())
        self.assertEqual(SessionDispatch.objects.get().state,'queued')
        self.owner.is_active=True;self.owner.save()
        self.charter.rules='changed rules';self.charter.save()
        with self.assertRaises(CommandError):call_command('session_outbox','claim',stdout=StringIO())
        self.assertEqual(SessionDispatch.objects.get().state,'queued')

    def test_read_only_task_is_never_claimed_by_desktop_bridge(self):
        import json
        from io import StringIO
        from django.core.management import call_command
        job=enqueue(self.charter,self.owner,'review schema',mode='read-only')
        run_one()
        output=StringIO();call_command('session_outbox','claim',stdout=output)
        self.assertEqual(json.loads(output.getvalue()),{})
        job.refresh_from_db()
        self.assertEqual(job.state,'waiting_readonly')
        self.assertFalse(SessionDispatch.objects.filter(job=job).exists())
