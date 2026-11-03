"""Explicit adoption of an existing candidate. No create/send/unarchive capability."""
import hashlib
import json
import math
import uuid
from datetime import timedelta
from pathlib import Path

from django.db import transaction
from django.utils import timezone
from execution_context.owner_tools import OwnerTools, load_config, OwnerToolsError
from execution_context.renewal_owner_tools import trusted_config, checksum
from execution_context.renewal_rollout import snapshot, retired_source_snapshot, _assistant_text
from execution_context.renewal_history_evidence import historical_bootstrap_evidence
from .models import (SessionRenewal, SessionCharter, SessionRenewalEvent,
                     ProjectDocumentation, AutoRenewalLatch)
from .memory import document
from .renewal import _lock, _stored_evidence, _quiet, _context, _verified, RenewalError


def context(row):
    return {**_context(row.charter), 'allowed_model_profiles':row.charter.allowed_model_profiles}


def _channel(row, config):
    trusted_config(config)
    if (config['project_id'] != row.charter.project_id or config['authorizing_user_id'] != row.owner_id
            or config['caller_thread_id'] in {row.source_thread_id,row.target_thread_id}
            or SessionCharter.objects.filter(project_id=row.charter.project_id,
                thread_id=config['caller_thread_id'], policy__enabled=True,
                state__in=['active','prepared','ready_waiting_for_task']).count()!=1):
        raise RenewalError('Independent owner channel required for adoption')


def _candidate(row):
    _stored_evidence(row)
    if (row.state not in {'created','creation_uncertain','verified'} or not row.create_attempted
            or row.charter.thread_id != row.source_thread_id or not row.target_thread_id
            or row.source_thread_id == row.target_thread_id
            or SessionCharter.objects.filter(thread_id=row.target_thread_id).exists()):
        raise RenewalError('Recorded unbound candidate required for adoption')
    _quiet(row)


@transaction.atomic
def authorize_adoption(request_id, actor, *, user_instruction, accept_historical_gaps=False):
    """Supervisor records an explicit request; never called by automatic renewal."""
    if not user_instruction.strip():
        raise RenewalError('Explicit user instruction must be recorded')
    row=_lock(request_id,actor)
    _candidate(row)
    config=load_config(); _channel(row,config)
    proof=historical_bootstrap_evidence(row,row.target_thread_id)
    source=retired_source_snapshot(row.source_thread_id)
    if Path(source['meta'].get('cwd','')).resolve()!=Path(row.context['cwd']).resolve():
        raise RenewalError('Retired source project mismatch')
    nonce=str(uuid.uuid4())
    current=context(row)
    digest=hashlib.sha256(json.dumps(current,sort_keys=True,ensure_ascii=False).encode()).hexdigest()
    history={}
    if accept_historical_gaps:
        from execution_context.adoption_snapshot import history_scope
        history=history_scope(row.target_thread_id)
    return document(row.charter.project,row.charter.key,'manual-adoption-authorization:'+nonce,
        'تفويض محدود لاعتماد المحادثة القائمة؛ ينتظر إقرار السياق والخمول ولا ينشئ محادثة.',
        {'status':'manual_adoption_authorized','request_id':row.pk,'renewal_key':str(row.key),
         'owner_id':actor.pk,'source':row.source_thread_id,'target':row.target_thread_id,
         'current_context':current,'context_sha256':digest,'nonce':nonce,
         'acknowledgement':f'ADOPTION_READY:{nonce}:{digest}',
         'expires_at':(timezone.now()+timedelta(hours=24)).isoformat(),
         'config_sha256':checksum(config),'historical_prefix_sha256':proof['prefix_sha256'],
         'source_sha256':source['sha256'],'source_archived':source['archived'],
         'source_terminal_type':source['terminal_type'],'user_instruction':user_instruction,
         'accept_historical_gaps':accept_historical_gaps,'history_scope':history,
         'allow_create':False,'allow_send':False,'allow_unarchive':False,'allow_adopt':True,
         'report_title':'اعتماد المحادثة القائمة — تفويض مقيد ينتظر التحقق',
         'report_sections':[
             {'title':'النطاق','body':f'الطلب {row.pk} والمحادثة المرشحة المسجلة فقط. لا إنشاء أو إرسال أو فك أرشفة. هذا تفويض وليس إعلان اكتمال الربط.'},
             {'title':'شروط التنفيذ','body':'إقرار نهائي حديث بالمرجع والسياق المحددين، وخمول الهدف، وثبات تاريخ المصدر، والتحقق من قناة المالك والعمل المعلق والسياق مباشرة قبل تبديل الرابط.'},
         ]})


def _approval(row, receipt, config):
    from datetime import datetime
    if not _verified(receipt) or receipt.project_id!=row.charter.project_id or receipt.session_key!=row.charter.key:
        raise RenewalError('Adoption authority receipt invalid')
    e=receipt.evidence
    expected={'status':'manual_adoption_authorized','request_id':row.pk,'renewal_key':str(row.key),
              'owner_id':row.owner_id,'source':row.source_thread_id,'target':row.target_thread_id,
              'current_context':context(row),'config_sha256':checksum(config),
              'allow_create':False,'allow_send':False,'allow_unarchive':False,'allow_adopt':True}
    if any(e.get(k)!=v for k,v in expected.items()):
        raise RenewalError('Adoption scope, context or owner changed')
    try:
        expires=datetime.fromisoformat(e['expires_at'])
        if not receipt.created_at <= timezone.now() < expires <= receipt.created_at+timedelta(hours=25):
            raise ValueError()
    except (KeyError,TypeError,ValueError):
        raise RenewalError('Adoption authority expired or malformed') from None
    _channel(row,config)


def _inspect(client, row, thread_id, *, source=False, limited_history=False):
    from execution_context.adoption_snapshot import latest_adoption_snapshot
    reader=retired_source_snapshot if source else (latest_adoption_snapshot if limited_history else snapshot)
    before=reader(thread_id)
    data=client.call('read_thread',{'threadId':thread_id,'hostId':'local','turnLimit':1,
        'includeOutputs':False,'maxOutputCharsPerItem':40},call_id=str(uuid.uuid4()),context_id=str(uuid.uuid4()))
    after=reader(thread_id)
    thread=data.get('thread',{}); turns=data.get('turns',[]); state=thread.get('status',{})
    terminal='interrupted' if after['terminal_type']=='turn_aborted' else 'completed'
    if (before['sha256']!=after['sha256'] or thread.get('id')!=thread_id
            or thread.get('kind')!='codex' or thread.get('hostId')!='local'
            or not isinstance(thread.get('cwd'),str)
            or Path(thread['cwd']).resolve()!=Path(row.context['cwd']).resolve()
            or Path(after['meta'].get('cwd','')).resolve()!=Path(row.context['cwd']).resolve()
            or state.get('type') not in {'idle','notLoaded'} or state.get('activeFlags')
            or not turns or turns[0].get('id')!=after['latest_turn'] or turns[0].get('status')!=terminal):
        raise RenewalError('Independent Desktop and rollout closure do not agree')
    return after


def _acknowledged(data, receipt):
    start_line,start=data['starts'][-1]; end_line,end=data['completed']
    started=start.get('started_at'); completed=end.get('completed_at')
    # A running turn may prepare authority, but its completion must follow it.
    if (any(type(v) not in (int,float) or not math.isfinite(v) for v in [started,completed])
            or not 0 <= started <= completed <= timezone.now().timestamp()
            or completed < receipt.created_at.timestamp()):
        raise RenewalError('Adoption acknowledgement has invalid time bounds')
    text=end.get('last_agent_message','')
    marker=receipt.evidence['acknowledgement']
    if not isinstance(text,str) or marker not in text.splitlines():
        raise RenewalError('Latest completed turn must acknowledge the exact recovery context')
    host=[]; responses=[]
    for line,r in data['records']:
        if not start_line<line<end_line:continue
        p=r['payload']
        if r.get('type')=='event_msg' and p.get('type')=='item_completed':
            item=p.get('item',{})
            if item.get('type')=='AgentMessage' and item.get('phase')=='final_answer':
                if p.get('thread_id')!=receipt.evidence['target'] or p.get('turn_id')!=data['latest_turn']:
                    raise RenewalError('Acknowledgement host attribution differs')
                host.append((line,item))
        if r.get('type')=='response_item' and p.get('type')=='message' and p.get('role')=='assistant' and p.get('phase')=='final_answer':
            responses.append((line,p))
    # Structured questions may emit earlier final-phase host items. Only the
    # terminal paired answer can acknowledge adoption; duplicate IDs still fail.
    if not host or not responses:
        raise RenewalError('Paired final acknowledgement required')
    final_host=host[-1];final_response=responses[-1];final_id=final_host[1].get('id')
    if (not final_id or final_response[0]<=final_host[0]
            or final_response[1].get('id')!=final_id
            or sum(item.get('id')==final_id for _,item in host)!=1
            or sum(item.get('id')==final_id for _,item in responses)!=1
            or final_response[1].get('status')!='completed'
            or _assistant_text(final_host[1].get('content'),'Text')!=text
            or _assistant_text(final_response[1].get('content'),'output_text')!=text):
        raise RenewalError('Paired final acknowledgement required')


@transaction.atomic
def commit_adoption(request_id, actor, *, approval_id):
    row=_lock(request_id,actor)
    receipt=ProjectDocumentation.objects.get(pk=approval_id)
    if row.state=='manually_adopted':
        if (row.charter.thread_id==row.target_thread_id and row.binding_receipt_id
                and _verified(row.binding_receipt)
                and row.binding_receipt.evidence.get('approval_id')==approval_id):
            return row.binding_receipt
        raise RenewalError('Adoption already resolved differently')
    _candidate(row)
    config=load_config(); _approval(row,receipt,config)
    proof=historical_bootstrap_evidence(row,row.target_thread_id)
    if proof['prefix_sha256']!=receipt.evidence['historical_prefix_sha256']:
        raise RenewalError('Original delivery proof changed')
    with OwnerTools(config) as client:
        source=_inspect(client,row,row.source_thread_id,source=True)
        if source['sha256']!=receipt.evidence['source_sha256'] or source['archived']!=receipt.evidence['source_archived']:
            raise RenewalError('Retired source changed after authorization')
        limited=receipt.evidence.get('accept_historical_gaps') is True
        target=_inspect(client,row,row.target_thread_id,limited_history=limited)
        if limited and receipt.evidence.get('history_scope')!={'ack_turn_id':target['latest_turn'],
                'historical_gaps':target['historical_gaps'],'historical_overlaps':target['historical_overlaps']}:
            raise RenewalError('Historical scope or acknowledgement turn changed')
        _acknowledged(target,receipt)
        _approval(row,receipt,config); _candidate(row)
        # Recheck both files and Desktop states at the final boundary, not only before acknowledgement.
        if (_inspect(client,row,row.source_thread_id,source=True)['sha256']!=source['sha256']
                or _inspect(client,row,row.target_thread_id,limited_history=limited)['sha256']!=target['sha256']):
            raise RenewalError('Activity changed at adoption boundary')
        _approval(row,receipt,config)
        if SessionCharter.objects.filter(pk=row.charter_id,thread_id=row.source_thread_id).update(thread_id=row.target_thread_id)!=1:
            raise RenewalError('Source binding CAS failed')
    from token_usage.models import TrackedSession
    tracked,_=TrackedSession.objects.get_or_create(external_id=row.target_thread_id,
        defaults={'project':row.charter.project,'name':row.charter.name})
    if tracked.project_id!=row.charter.project_id or not tracked.enabled:
        raise RenewalError('Candidate tracking conflicts with project')
    result=document(row.charter.project,row.charter.key,f'manual-adoption-result:{receipt.pk}',
        'اعتماد يدوي موثق للمحادثة القائمة بعد تحقق السياق والخمول؛ ليس تجديدًا آليًا جديدًا.',
        {'status':'manual_adoption_completed','approval_id':receipt.pk,'request_id':row.pk,
         'source':row.source_thread_id,'target':row.target_thread_id,'context':receipt.evidence['current_context'],
         'target_turn':target['latest_turn'],'target_sha256':target['sha256'],
         'source_sha256':source['sha256'],'created_thread':False,'sent_message':False,
         'history_scope':receipt.evidence.get('history_scope',{}),
         'report_title':'اكتمل اعتماد محادثة التنسيق القائمة',
         'report_sections':[
             {'title':'النتيجة','body':f'تغير رابط الميثاق من المصدر المسجل إلى المرشح نفسه في الطلب {row.pk}، بعد تحقق مستقل من إقرار السياق والخمول وثبات الأدلة.'},
             {'title':'حدود العملية','body':'اعتماد يدوي موثق للمحادثة القائمة، وليس تجديدًا آليًا جديدًا. لم تُنشأ محادثة ولم تُرسل رسالة ولم يُفك أرشيف المصدر. بقي سياق الإنشاء وإيصال التسليم التشغيلي الأصلي محفوظين.'},
             {'title':'حدود الدليل التاريخي','body':('اعتمد سفيان إثبات الحالة الحالية مع حفظ عيب التاريخ. التداخل القديم موثق في الأدلة ولم يُعتبر محلولًا؛ إثبات الخمول والإقرار يخص الدور الأخير فقط.' if limited else 'استُخدم فحص السجل الصارم للهدف.')},
             {'title':'مرجع التفويض','body':f'التفويض {receipt.pk} مقيد بالطلب والسياق والوجهتين. أعيد فحص قناة المالك وحالة الطرفين قبل التبديل، وحُسم حجر الطلب بأثر اعتماد يدوي.'},
         ]})
    row.state='manually_adopted';row.binding_receipt=result
    row.save(update_fields=['state','binding_receipt','updated_at'])
    SessionRenewalEvent.objects.create(renewal=row,state='manually_adopted',receipt=result)
    AutoRenewalLatch.objects.filter(renewal=row).update(state='switched',last_error='resolved_by_manual_adoption')
    return result
