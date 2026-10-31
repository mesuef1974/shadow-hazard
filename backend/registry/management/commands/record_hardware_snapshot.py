import json
import re
from pathlib import Path
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from registry.models import HardwareSnapshot, HardwareRecord


FIELDS = {'kind', 'component', 'key', 'value', 'unit', 'source', 'quality', 'estimate_note'}
ENVELOPE_FIELDS = {'capture_key', 'captured_at', 'status', 'source_summary', 'records'}
SAFE_KEYS = {
    'processor': {'model', 'physical_cores', 'logical_processors', 'max_clock', 'current_utilization'},
    'memory': {'installed', 'module_count', 'rated_speed', 'configured_speed', 'available', 'total_visible'},
    'graphics': {'integrated_model', 'discrete_model', 'dedicated_memory', 'shared_memory', 'memory_in_use', 'driver_version'},
    'storage': {'total_capacity', 'available_capacity'},
}


class Command(BaseCommand):
    help = 'Record a reviewed, privacy-filtered hardware snapshot from a JSON envelope.'

    def add_arguments(self, parser):
        parser.add_argument('envelope')

    @transaction.atomic
    def handle(self, *args, **options):
        try:
            data = json.loads(Path(options['envelope']).read_text(encoding='utf-8-sig'))
            if not isinstance(data, dict) or set(data) != ENVELOPE_FIELDS:
                raise ValueError('Envelope fields are not approved')
            captured_at = parse_datetime(data['captured_at'])
            records = data['records']
            if not captured_at or timezone.is_naive(captured_at):
                raise ValueError('captured_at must be timezone-aware')
            if not re.fullmatch(r'[A-Za-z0-9._-]{1,80}', data['capture_key']):
                raise ValueError('capture_key contains unsupported characters')
            if data['status'] not in {'complete', 'partial'} or not isinstance(data['source_summary'], str) or len(data['source_summary']) > 300:
                raise ValueError('Snapshot status or source summary is invalid')
            if not isinstance(records, list) or not records:
                raise ValueError('records must be a non-empty list')
            if any(not isinstance(row, dict) or set(row) != FIELDS for row in records):
                raise ValueError('Every record must use the approved field set')
            for row in records:
                if row['kind'] not in {'specification', 'measurement'} or row['component'] not in SAFE_KEYS:
                    raise ValueError('Record kind or component is invalid')
                if row['key'] not in SAFE_KEYS[row['component']]:
                    raise ValueError('Record key is not in the privacy-reviewed allowlist')
                if row['quality'] not in {'reported', 'measured', 'estimated'}:
                    raise ValueError('Record quality is invalid')
                for key, limit in [('key', 80), ('value', 180), ('unit', 30), ('source', 220), ('estimate_note', 300)]:
                    if not isinstance(row[key], str) or len(row[key]) > limit:
                        raise ValueError(f'{key} is invalid')
                if not row['value'] or not row['source']:
                    raise ValueError('Record value and source are required')
                if row['quality'] == 'estimated' and not row['estimate_note'].strip():
                    raise ValueError('Estimated values require an explanation')
            snapshot = HardwareSnapshot.objects.create(
                capture_key=data['capture_key'], captured_at=captured_at,
                status=data['status'], source_summary=data['source_summary'])
            HardwareRecord.objects.bulk_create([HardwareRecord(snapshot=snapshot, **row) for row in records])
        except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
            raise CommandError('تعذر تسجيل لقطة العتاد: تحقق من الظرف وحقوله ومصادره.') from exc
        self.stdout.write(f'Recorded snapshot {snapshot.capture_key} with {len(records)} values.')
