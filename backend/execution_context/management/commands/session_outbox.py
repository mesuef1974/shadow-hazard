"""Codex bridge contract: atomically claim and record desktop-thread delivery."""
import json
import uuid
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone
from execution_context.models import SessionDispatch, SessionDispatchEvent, SessionDispatchTurn


class Command(BaseCommand):
    help = 'Claim or transition one durable session-dispatch record for the Codex bridge.'

    def add_arguments(self, parser):
        parser.add_argument('action', choices=['claim','sent','acknowledged','complete-from-rollout','stuck','uncertain'])
        parser.add_argument('--dispatch', type=int)
        parser.add_argument('--consumer', default='codex-agent')
        parser.add_argument('--enqueue-only', action='store_true')
        parser.add_argument('--claim-token')
        parser.add_argument('--turn-id', default='')
        parser.add_argument('--rollout', default='')
        parser.add_argument('--detail', default='{}')

    def handle(self, *args, **opts):
        if opts['action'] == 'claim':
            return self.claim(opts)
        if not opts['dispatch'] or not opts['claim_token']:
            raise CommandError('--dispatch و --claim-token مطلوبان')
        try:
            claim_token=uuid.UUID(opts['claim_token'])
            detail=json.loads(opts['detail'])
            if not isinstance(detail,dict):raise ValueError()
        except (ValueError,TypeError):raise CommandError('صيغة JSON أو claim-token غير صالحة')
        with transaction.atomic():
            dispatch=SessionDispatch.objects.select_for_update().select_related('job','target_charter','routing_policy').get(pk=opts['dispatch'])
            if dispatch.claim_token!=claim_token:raise CommandError('claim-token لا يطابق السجل')
            action=opts['action']
            if action=='sent' and dispatch.state=='claimed' and dispatch.job.mode=='read-only':
                raise CommandError('الجسر لا يوفر عزل القراءة فقط؛ حُظر التسليم')
            evidence=None
            if action in {'stuck','complete-from-rollout'}:
                from execution_context.rollout_usage import find_rollout, read_turn_evidence, RolloutEvidenceError
                from execution_context.owner_delivery import owner_origin_for
                try:
                    source=find_rollout(dispatch.target_thread_id,opts['rollout'] or None)
                    evidence=read_turn_evidence(source,thread_id=dispatch.target_thread_id,claimed_at=dispatch.claimed_at,
                        acknowledged_at=dispatch.acknowledged_at,expected_model=dispatch.requested_model,
                        expected_thinking=dispatch.requested_thinking,turn_id=opts['turn_id'],expected_marker=dispatch.job.task_context.manifest.get('task_marker',''),
                        expected_prompt=dispatch.job.prompt.strip(),task_id=dispatch.job_id,
                        expected_owner_origin=owner_origin_for(dispatch))
                except RolloutEvidenceError as exc:raise CommandError(str(exc))
            if action=='stuck':
                if dispatch.state!='acknowledged':raise CommandError('تعثر المهمة لا يسجل قبل إقرار وصولها')
                reason=detail.get('reason','').strip()
                if not reason or 'ESCALATION_REQUEST:' not in evidence['result']:raise CommandError('التصعيد يتطلب طلب تعثر صريحًا في رد الدور المثبت')
                SessionDispatchTurn.objects.create(dispatch=dispatch,target_thread_id=dispatch.target_thread_id,
                    turn_key=evidence['turn_id'],model=evidence['model'],thinking=evidence['thinking'],
                    usage=evidence['usage'],source_evidence=evidence['evidence'])
                policy=dispatch.routing_policy
                can_escalate=(policy and policy.enabled and dispatch.attempt_no<min(dispatch.max_attempts,policy.max_attempts)
                    and dispatch.escalation_count<policy.max_escalations and policy.stuck_model=='gpt-6.1-sol'
                    and policy.stuck_thinking=='high')
                SessionDispatchEvent.objects.create(dispatch=dispatch,state='stuck',detail={'reason':reason,'turn_id':evidence['turn_id'],'model':evidence['model'],'evidence':evidence['evidence']})
                if can_escalate:
                    dispatch.attempt_no+=1;dispatch.escalation_count+=1
                    dispatch.requested_model=policy.stuck_model;dispatch.requested_thinking=policy.stuck_thinking
                    dispatch.state='queued';dispatch.claim_token=None;dispatch.claimed_by='';dispatch.claimed_at=None
                    dispatch.sent_at=None;dispatch.acknowledged_at=None
                    plan=dispatch.job.task_context
                    plan.manifest={**plan.manifest,'task_marker':f'[SHADOW_TASK:{dispatch.job_id}:{dispatch.dispatch_key}:{dispatch.attempt_no}]'}
                    plan.save(update_fields=['manifest'])
                    dispatch.job.state='waiting_delivery';dispatch.job.requested_model=dispatch.requested_model
                    dispatch.job.requested_thinking=dispatch.requested_thinking;dispatch.job.started_at=None
                    dispatch.job.outcome_note='رُصد تعثر موثق في الدور؛ أُنشئت محاولة تصعيد تلقائية ضمن حد السياسة المحفوظة.'
                else:
                    dispatch.state='uncertain';dispatch.job.state='needs_review'
                    dispatch.job.outcome_note='المهمة تعثرت ووصلت إلى حد المحاولات/التصعيد؛ تحتاج مراجعة بشرية ولا تعاد تلقائيًا.'
                dispatch.save()
                dispatch.job.save(update_fields=['state','requested_model','requested_thinking','started_at','outcome_note'])
                if not can_escalate:
                    from execution_context.exhausted_delivery import record_exhausted
                    record_exhausted(dispatch, evidence)
                if can_escalate:SessionDispatchEvent.objects.create(dispatch=dispatch,state='queued',detail={'attempt':dispatch.attempt_no,'model':dispatch.requested_model,'thinking':dispatch.requested_thinking,'reason':reason})
                self.stdout.write(json.dumps({'dispatch_id':dispatch.pk,'state':dispatch.state,'attempt':dispatch.attempt_no,'model':dispatch.requested_model,'thinking':dispatch.requested_thinking},ensure_ascii=False));return
            transitions={'sent':'claimed','acknowledged':'sent','complete-from-rollout':'acknowledged','uncertain':['claimed','sent','acknowledged']}
            expected=transitions[action]
            terminal='completed' if action=='complete-from-rollout' else action
            if dispatch.state==terminal:
                if action=='complete-from-rollout' and dispatch.turn_key!=evidence['turn_id']:raise CommandError('الدور المكتمل مختلف عن إعادة الطلب')
                self.stdout.write(json.dumps({'dispatch_id':dispatch.pk,'state':action,'idempotent':True},ensure_ascii=False));return
            if dispatch.state not in (expected if isinstance(expected,list) else [expected]):
                raise CommandError(f'انتقال غير مسموح من {dispatch.state} إلى {action}')
            now=timezone.now()
            if action=='complete-from-rollout':
                SessionDispatchTurn.objects.create(dispatch=dispatch,target_thread_id=dispatch.target_thread_id,
                    turn_key=evidence['turn_id'],model=evidence['model'],thinking=evidence['thinking'],
                    usage=evidence['usage'],source_evidence=evidence['evidence'])
                dispatch.turn_key=evidence['turn_id']
                dispatch.completed_at=now
                from workspace.memory import document
                receipt=document(dispatch.target_charter.project,dispatch.target_charter.key,
                    f'dispatch:{dispatch.pk}:attempt:{dispatch.attempt_no}',evidence['result'],
                    {'turn_id':evidence['turn_id'],'source':evidence['evidence'],'usage':evidence['usage']})
                SessionDispatchEvent.objects.create(dispatch=dispatch,state='documentation_verified',detail={'receipt':receipt.pk,'digest':receipt.digest})
                dispatch.job.state='completed';dispatch.job.finished_at=now
                dispatch.job.observed_model=evidence['model'];dispatch.job.token_usage=evidence['usage']
                dispatch.job.result=evidence['result']
                dispatch.job.outcome_note='اكتمل الدور بدليل rollout؛ الاستهلاك محفوظ للدور مع مصدره، ولا يضاف إلى إجمالي عينات الجلسة التراكمية.'
                dispatch.job.save(update_fields=['state','finished_at','observed_model','token_usage','result','outcome_note'])
            elif action=='sent':dispatch.sent_at=now
            elif action=='acknowledged':dispatch.acknowledged_at=now
            elif action=='uncertain':dispatch.job.state='uncertain';dispatch.job.outcome_note='نتيجة تسليم الجسر غير مؤكدة؛ لا إعادة تلقائية.';dispatch.job.save(update_fields=['state','outcome_note'])
            dispatch.state=terminal;dispatch.save()
            SessionDispatchEvent.objects.create(dispatch=dispatch,state=action,detail=detail)
        self.stdout.write(self.style.SUCCESS(json.dumps({'dispatch_id':dispatch.pk,'state':action},ensure_ascii=False)))

    def claim(self, opts):
        with transaction.atomic():
            from workspace.job_lanes import available_dispatches, without_renewing_dispatches
            from workspace.models import SessionCharter
            from execution_context.models import SessionWriterRecovery
            recovering_threads=SessionWriterRecovery.objects.filter(state__in=['archiving','restoring','uncertain']).values('thread_id')
            candidates=SessionDispatch.objects.filter(state='queued',job__mode='workspace-write').exclude(target_thread_id__in=recovering_threads)
            if opts.get('dispatch'):
                candidates=candidates.filter(pk=opts['dispatch'])
            if not opts.get('enqueue_only'):
                candidates=available_dispatches(candidates)
            candidate=candidates.order_by('created_at','pk').values_list('pk',flat=True).first()
            if not candidate:
                self.stdout.write('{}');return
            identities=SessionDispatch.objects.filter(pk=candidate).values_list('target_charter_id','job__charter_id').get()
            list(SessionCharter.objects.select_for_update().filter(pk__in=identities).order_by('pk'))
            token=uuid.uuid4();claimed_at=timezone.now()
            claim_query=SessionDispatch.objects.filter(pk=candidate,state='queued').exclude(target_thread_id__in=recovering_threads)
            claim_query=without_renewing_dispatches(claim_query)
            if not opts.get('enqueue_only'):
                claim_query=available_dispatches(claim_query)
            changed=claim_query.update(state='claimed',claim_token=token,
                claimed_by=opts['consumer'][:100],claimed_at=claimed_at,updated_at=claimed_at)
            if not changed:
                self.stdout.write('{}');return
            dispatch=SessionDispatch.objects.select_related('job','target_charter').get(pk=candidate,claim_token=token)
            plan=dispatch.job.task_context
            from execution_context.routing import validate_manifest
            validate_manifest(plan)
            target=dispatch.target_charter
            if (not dispatch.job.owner.is_active or dispatch.job.owner.role!='supervisor' or not target.policy.enabled
                or target.state not in {'active','prepared','ready_waiting_for_task'}
                or target.project_id!=dispatch.job.charter.project_id or target.thread_id!=dispatch.target_thread_id
                or not dispatch.routing_policy or not dispatch.routing_policy.enabled):
                raise CommandError('تفويض الإرسال أو ميثاق الوجهة غير صالح حاليًا')
            snap=plan.manifest
            if not all(snap.get(key) for key in ('target_snapshot','policy_snapshot','job_snapshot','task_marker')):
                raise CommandError('طلب قديم بلا لقطة تفويض مكتملة؛ يلزم طلب جديد')
            policy_values=snap['policy_snapshot']
            expected_pair=(policy_values['model'],policy_values['thinking']) if dispatch.attempt_no==1 else (policy_values['stuck_model'],policy_values['stuck_thinking'])
            if (dispatch.requested_model,dispatch.requested_thinking)!=expected_pair or not 1<=dispatch.attempt_no<=dispatch.max_attempts:
                raise CommandError('نموذج المحاولة أو حدها لا يطابق السياسة المثبتة')
            if snap.get('target_snapshot') and snap['target_snapshot']!={'thread_id':target.thread_id,'scope':target.scope,'rules':target.rules}:
                raise CommandError('تغير ميثاق الوجهة؛ يلزم طلب جديد')
            if snap.get('job_snapshot') and snap['job_snapshot']!={'prompt':dispatch.job.prompt,'mode':dispatch.job.mode}:
                raise CommandError('تغير محتوى المهمة أو صلاحيتها')
            if snap.get('policy_snapshot') and snap['policy_snapshot']!={name:getattr(dispatch.routing_policy,name) for name in snap['policy_snapshot']}:
                raise CommandError('تغيرت سياسة التوجيه؛ يلزم طلب جديد')
            SessionDispatchEvent.objects.create(dispatch=dispatch,state='claimed',detail={'consumer':dispatch.claimed_by})
            dispatch.job.state='waiting_delivery' if opts.get('enqueue_only') else 'running'
            dispatch.job.started_at=None if opts.get('enqueue_only') else dispatch.claimed_at
            dispatch.job.save(update_fields=['state','started_at'])
            plan=dispatch.job.task_context
            payload={'dispatch_id':dispatch.pk,'dispatch_key':str(dispatch.dispatch_key),'claim_token':str(token),
                'thread_id':dispatch.target_thread_id,'charter':dispatch.target_charter.key,
                'model':dispatch.requested_model,'thinking':dispatch.requested_thinking,
                'mode':dispatch.job.mode,'read_only_enforcement':'unsupported_read_only_delivery_blocked',
                'prompt':plan.manifest.get('task_marker','')+'\n'+plan.manifest.get('dispatch_prompt',''),'route_reason':dispatch.route_reason,
                'attempt_no':dispatch.attempt_no,'max_attempts':dispatch.max_attempts}
            last_stuck=dispatch.events.filter(state='stuck').order_by('-created_at').first()
            if last_stuck:payload['prompt']+=f"\n\nمحاولة تصعيد رقم {dispatch.attempt_no} بعد التعثر الموثق: {last_stuck.detail['reason']}"
        self.stdout.write(json.dumps(payload,ensure_ascii=False))
