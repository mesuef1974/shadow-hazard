import json
from pathlib import Path
from tempfile import TemporaryDirectory
from django.contrib.auth import get_user_model
from django.core.management import call_command, CommandError
from django.test import TestCase
from django.urls import reverse
from .models import HardwareSnapshot, HardwareRecord


class HardwareInventoryTests(TestCase):
    def envelope(self):
        return {
            'capture_key': 'test-capture-20261031T080000Z',
            'captured_at': '2026-10-31T08:00:00Z',
            'status': 'complete',
            'source_summary': 'Windows CIM and nvidia-smi',
            'records': [{
                'kind': 'specification', 'component': 'graphics', 'key': 'dedicated_memory',
                'value': '8192', 'unit': 'MiB', 'source': 'nvidia-smi memory.total',
                'quality': 'reported', 'estimate_note': '',
            }, {
                'kind': 'specification', 'component': 'graphics', 'key': 'shared_memory',
                'value': '2048', 'unit': 'MiB', 'source': 'Win32_VideoController.AdapterRAM',
                'quality': 'estimated', 'estimate_note': 'Shared allocation; not dedicated VRAM.',
            }],
        }

    def test_command_persists_provenance_kinds_and_estimate_label(self):
        with TemporaryDirectory() as folder:
            envelope = Path(folder) / 'snapshot.json'
            envelope.write_text(json.dumps(self.envelope()), encoding='utf-8')
            call_command('record_hardware_snapshot', str(envelope))
        snapshot = HardwareSnapshot.objects.get(capture_key='test-capture-20261031T080000Z')
        self.assertEqual(snapshot.records.count(), 2)
        estimated = snapshot.records.get(quality='estimated')
        self.assertEqual(estimated.get_quality_display(), 'تقديرية')
        self.assertEqual(estimated.source, 'Win32_VideoController.AdapterRAM')
        self.assertIsNotNone(snapshot.captured_at.utcoffset())

    def test_command_rejects_unapproved_or_identity_fields(self):
        bad = self.envelope()
        bad['records'][0]['key'] = 'serial_number'
        with TemporaryDirectory() as folder:
            envelope = Path(folder) / 'snapshot.json'
            envelope.write_text(json.dumps(bad), encoding='utf-8')
            with self.assertRaises(CommandError):
                call_command('record_hardware_snapshot', str(envelope))
        self.assertFalse(HardwareSnapshot.objects.exists())

    def test_admin_is_searchable_and_read_only(self):
        from django.utils.dateparse import parse_datetime
        snapshot = HardwareSnapshot.objects.create(capture_key='admin-capture',
            captured_at=parse_datetime('2026-10-31T08:00:00Z'), status='complete', source_summary='test source')
        HardwareRecord.objects.create(snapshot=snapshot, kind='measurement', component='memory',
            key='available', value='10.2', unit='GiB', source='Win32_OperatingSystem.FreePhysicalMemory', quality='measured')
        owner = get_user_model().objects.create_user(username='mesuef', role='supervisor', is_staff=True, is_superuser=True)
        self.client.force_login(owner)
        self.assertContains(self.client.get(reverse('admin:registry_hardwaresnapshot_changelist')), 'admin-capture')
        response = self.client.get(reverse('admin:registry_hardwarerecord_changelist'))
        self.assertContains(response, 'Win32_OperatingSystem.FreePhysicalMemory')
        from django.contrib import admin
        from django.test import RequestFactory
        request = RequestFactory().get('/admin/')
        request.user = owner
        model_admin = admin.site._registry[HardwareRecord]
        self.assertTrue(model_admin.has_view_permission(request))
        self.assertFalse(model_admin.has_add_permission(request))
        self.assertFalse(model_admin.has_change_permission(request))
        self.assertFalse(model_admin.has_delete_permission(request))
