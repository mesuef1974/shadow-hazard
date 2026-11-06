"""جمع أحداث الأتمتة في قاعدة المنصة. حتمي، بلا توكنز، وإعادته لا تكرر شيئًا."""
import hashlib
import json
import re
import shutil
import time
from datetime import datetime, timedelta

from django.utils import timezone
from django.utils.dateparse import parse_datetime

from workspace.automation_live import EVENTS, _run

from .models import Card, Event

REPO_SLUG = 'mesuef1974/shadow-hazard'
SYNC_SECONDS = 300
LIMIT = '300'
PR_FIELDS = 'number,title,headRefName,baseRefName,state,createdAt,mergedAt,closedAt,url,mergeCommit'
RUN_FIELDS = 'databaseId,headBranch,headSha,event,status,conclusion,createdAt,updatedAt,url'
SHA = re.compile(r'\b[0-9a-f]{7,40}\b')
LOCAL_STAGE = {'deploy': 'deploy', 'push': 'guard', 'commit': 'guard', 'pr-update': 'pr_opened'}
LOCAL_RESULT = {'pass': 'pass', 'fail': 'fail', 'reject': 'fail'}
CI_RESULT = {'success': 'pass', 'failure': 'fail'}
_last = {'at': 0.0}


def _event(external_id, **fields):
    return Event.objects.get_or_create(external_id=external_id, defaults=fields)[1]


def ingest_prs(rows):
    added = 0
    for row in rows:
        merged = parse_datetime(row['mergedAt'] or '')
        closed = parse_datetime(row['closedAt'] or '')
        created = parse_datetime(row['createdAt'])
        merge_sha = (row.get('mergeCommit') or {}).get('oid', '')
        card, _ = Card.objects.update_or_create(branch=row['headRefName'], defaults={
            'title': row['title'][:300], 'pr_number': row['number'], 'pr_url': row['url'],
            'base': row['baseRefName'], 'state': row['state'].lower(), 'merge_sha': merge_sha,
            'created_at': created, 'merged_at': merged, 'closed_at': closed})
        common = {'source': 'github', 'card': card, 'url': row['url']}
        number = row['number']
        added += _event(f'pr-{number}-opened', stage='pr_opened', result='info', occurred_at=created,
                        detail=f"الطلب {number} إلى {row['baseRefName']}", **common)
        if merged:
            added += _event(f'pr-{number}-merged', stage='merged', result='pass', sha=merge_sha,
                            occurred_at=merged, detail=f'دُمج الطلب {number}', **common)
        elif closed:
            added += _event(f'pr-{number}-closed', stage='pr_closed', result='info',
                            occurred_at=closed, detail=f'أُغلق الطلب {number} بلا دمج', **common)
    return added


def ingest_runs(rows):
    added = 0
    for row in rows:
        if row['status'] != 'completed' or not row['headBranch']:
            continue
        card, _ = Card.objects.get_or_create(
            branch=row['headBranch'], defaults={'created_at': parse_datetime(row['createdAt'])})
        conclusion = row['conclusion'] or 'unknown'
        added += _event(f"run-{row['databaseId']}", stage='ci', source='github', card=card,
                        result=CI_RESULT.get(conclusion, 'info'), sha=row['headSha'],
                        occurred_at=parse_datetime(row['updatedAt']), url=row['url'],
                        detail=f"{row['event']}: {conclusion}")
    return added


def ingest_local(lines):
    added = 0
    for line in lines:
        try:
            row = json.loads(line)
            at = datetime.fromisoformat(row['at'])
        except (ValueError, KeyError, TypeError):
            continue
        stage = LOCAL_STAGE.get(row.get('kind'))
        if not stage:
            continue
        detail = str(row.get('detail', ''))[:300]
        match = SHA.search(detail) if stage == 'deploy' else None
        sha = match.group() if match else ''
        card = Card.objects.filter(merge_sha__startswith=sha).first() if sha else None
        key = 'local-' + hashlib.sha256(line.encode('utf-8')).hexdigest()[:40]
        added += _event(key, stage=stage, source='local', card=card, sha=sha, occurred_at=at,
                        result=LOCAL_RESULT.get(row.get('result'), 'info'), detail=detail)
    return added


def sync():
    """يجلب كل المصادر؛ يرجع عدد الأحداث الجديدة. فشل GitHub يُرفع ليظهر في نبض العامل."""
    gh = shutil.which('gh')
    if not gh:
        raise RuntimeError('gh غير مثبت')
    options = ['--repo', REPO_SLUG, '--limit', LIMIT, '--json']
    added = ingest_prs(json.loads(_run([gh, 'pr', 'list', '--state', 'all', *options, PR_FIELDS], 30)))
    added += ingest_runs(json.loads(_run([gh, 'run', 'list', '--workflow', 'CI', *options, RUN_FIELDS], 30)))
    # بعد الطلبات كي يرتبط النشر ببطاقته.
    if EVENTS.exists():
        added += ingest_local(EVENTS.read_text(encoding='utf-8').splitlines())
    return added


def sync_due():
    """خطوة العامل: يُستدعى كل 30 ثانية، ولا يلمس GitHub إلا كل 5 دقائق."""
    if _last['at'] and time.monotonic() - _last['at'] < SYNC_SECONDS:
        return 0
    _last['at'] = time.monotonic()
    return sync()


def card_stage(card, deploy_times):
    """أبعد مرحلة بلغتها البطاقة. النشر يشمل كل ما دُمج قبله."""
    if card.merged_at:
        return 'deployed' if any(at >= card.merged_at for at in deploy_times) else 'merged'
    if card.state == 'closed':
        return 'closed'
    return 'pr' if card.pr_number else 'ci'


def filtered_events(params):
    events = Event.objects.select_related('card')
    if params.get('q'):
        q = params['q']
        events = (events.filter(detail__icontains=q) | events.filter(card__branch__icontains=q)
                  | events.filter(card__title__icontains=q) | events.filter(sha__startswith=q))
    for name in ('stage', 'result', 'source'):
        if params.get(name):
            events = events.filter(**{name: params[name]})
    if str(params.get('card', '')).isdigit():
        events = events.filter(card_id=params['card'])
    for name, lookup in (('from', 'occurred_at__date__gte'), ('to', 'occurred_at__date__lte')):
        if params.get(name):
            try:
                events = events.filter(**{lookup: datetime.strptime(params[name], '%Y-%m-%d').date()})
            except ValueError:
                pass
    return events


def event_row(event):
    return {'id': event.pk, 'at': timezone.localtime(event.occurred_at).strftime('%Y-%m-%d %H:%M'),
            'stage': event.stage, 'stage_label': event.get_stage_display(), 'result': event.result,
            'result_label': event.get_result_display(), 'source': event.get_source_display(),
            'branch': event.card.branch if event.card else '', 'card': event.card_id,
            'sha': event.sha[:7], 'detail': event.detail, 'url': event.url}


def snapshot(params, days=30, limit=200):
    deploys = list(Event.objects.filter(stage='deploy', result='pass')
                   .values_list('occurred_at', flat=True))
    cards = []
    counts = {'ci': 0, 'pr': 0, 'merged': 0, 'deployed': 0, 'closed': 0}
    for card in Card.objects.all():
        stage = card_stage(card, deploys)
        counts[stage] += 1
        cards.append({'id': card.pk, 'branch': card.branch, 'title': card.title, 'stage': stage,
                      'pr': card.pr_number, 'url': card.pr_url})
    events = filtered_events(params)
    narrowed = {key: value for key, value in params.items() if value and key != 'card'}
    if narrowed:
        matched = set(filtered_events(narrowed).exclude(card=None).values_list('card_id', flat=True))
        cards = [card for card in cards if card['id'] in matched]
    today = timezone.localdate()
    since = today - timedelta(days=days - 1)
    daily = {(since + timedelta(days=i)).isoformat(): {'pass': 0, 'fail': 0, 'info': 0}
             for i in range(days)}
    for at, result in events.filter(occurred_at__date__gte=since).values_list('occurred_at', 'result'):
        day = timezone.localtime(at).date().isoformat()
        if day in daily:
            daily[day][result] += 1
    last = Event.objects.order_by('-recorded_at').values_list('recorded_at', flat=True).first()
    return {'counts': {**counts, 'all': len(cards)}, 'cards': cards[:limit], 'total': events.count(),
            'events': [event_row(e) for e in events[:limit]],
            'daily': [{'day': day, **values} for day, values in daily.items()],
            'synced': timezone.localtime(last).strftime('%Y-%m-%d %H:%M') if last else ''}
