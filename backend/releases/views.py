import json
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError, PermissionDenied, ImproperlyConfigured, ObjectDoesNotExist
from django.db import IntegrityError, OperationalError
from django.http import JsonResponse
from django.views.decorators.http import require_http_methods
from .models import Release
from .services import act, create_release_once, IdempotencyConflict
from django.conf import settings
from .services import deploy
from .models import Deployment
from .services import merge_release
from .factories import transport_factory


@login_required
@require_http_methods(['GET', 'POST'])
def collection(request):
    if request.method == 'GET':
        return JsonResponse({'releases': list(Release.objects.values('id', 'commit', 'environment', 'fingerprint', 'state', 'revision', 'task_id', 'job_id', 'author_id'))})
    try:
        data = json.loads(request.body)
        row, created = create_release_once(actor=request.user, key=request.headers.get('Idempotency-Key'), data=data)
        return JsonResponse({'id': row.pk, 'fingerprint': row.fingerprint}, status=201 if created else 200)
    except PermissionDenied:
        return JsonResponse({'error': 'Forbidden'}, status=403)
    except IdempotencyConflict:
        return JsonResponse({'error': 'Idempotency key conflicts with an earlier request'}, status=409)
    except OperationalError:
        return JsonResponse({'error': 'Database busy; retry with the same Idempotency-Key'}, status=409)
    except (ValueError, TypeError, KeyError, ValidationError, IntegrityError):
        return JsonResponse({'error': 'Invalid release'}, status=400)


@login_required
@require_http_methods(['POST'])
def action(request, pk):
    try:
        data = json.loads(request.body)
        if data.get('action') == 'merge':
            row = merge_release(pk, actor=request.user, key=data['key'])
            return JsonResponse({'id': row.pk, 'state': row.state, 'result_commit': row.result_commit})
        event = act(pk, actor=request.user, **data)
        return JsonResponse({'event_id': event.pk, 'release_id': pk})
    except PermissionDenied:
        return JsonResponse({'error': 'Forbidden'}, status=403)
    except Release.DoesNotExist:
        return JsonResponse({'error': 'Not found'}, status=404)
    except ImproperlyConfigured:
        return JsonResponse({'error': 'Merge repository is not configured'}, status=503)
    except (ValueError, TypeError, KeyError, ValidationError, IntegrityError, OperationalError):
        return JsonResponse({'error': 'Invalid transition or concurrent operation'}, status=409)


@login_required
@require_http_methods(['GET'])
def detail(request, pk):
    try:
        r = Release.objects.get(pk=pk)
    except Release.DoesNotExist:
        return JsonResponse({'error': 'Not found'}, status=404)
    return JsonResponse({'release': {f: getattr(r, f) for f in ['id','task_id','job_id','author_id','commit','environment','manifest','fingerprint','state','revision','repository','source_ref','target_branch','target_commit']},
        'evidence': list(r.evidence.values()), 'reviews': list(r.review_set.values()),
        'approvals': list(r.approval_set.values()), 'events': list(r.events.values()),
        'deployments': list(Deployment.objects.filter(release=r).values()), 'merges': list(r.mergeattempt_set.values())})


@login_required
@require_http_methods(['POST'])
def deployment(request, pk):
    if request.user.role != 'supervisor':
        return JsonResponse({'error': 'Forbidden'}, status=403)
    try:
        data = json.loads(request.body)
        row = deploy(pk, actor=request.user, adapter=transport_factory(), **data)
        return JsonResponse({'id': row.pk, 'state': row.state, 'checks': row.checks})
    except PermissionDenied:
        return JsonResponse({'error': 'Forbidden'}, status=403)
    except ImproperlyConfigured:
        return JsonResponse({'error': 'Deployment transport is not configured'}, status=503)
    except ObjectDoesNotExist:
        return JsonResponse({'error': 'Not found'}, status=404)
    except (ValueError, TypeError, KeyError, ValidationError, IntegrityError, OperationalError):
        return JsonResponse({'error': 'Deployment rejected'}, status=409)


@login_required
@require_http_methods(['POST'])
def merge(request, pk):
    try:
        data = json.loads(request.body)
        if set(data) != {'key'}:
            raise ValidationError('Only key is accepted')
        row = merge_release(pk, actor=request.user, key=data['key'])
        return JsonResponse({'id': row.pk, 'state': row.state, 'result_commit': row.result_commit})
    except PermissionDenied:
        return JsonResponse({'error': 'Forbidden'}, status=403)
    except ImproperlyConfigured:
        return JsonResponse({'error': 'Merge repository is not configured'}, status=503)
    except ObjectDoesNotExist:
        return JsonResponse({'error': 'Not found'}, status=404)
    except (ValueError, TypeError, KeyError, ValidationError, IntegrityError, OperationalError):
        return JsonResponse({'error': 'Merge rejected'}, status=409)
