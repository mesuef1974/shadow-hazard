from tempfile import TemporaryDirectory
from pathlib import Path
from io import StringIO
from unittest.mock import patch
from django.test import TestCase, override_settings
from django.core.management import call_command
from django.core.management.base import CommandError
from registry.skill_inventory import scan_skills
from registry.models import Installation, SkillChange, SkillScan, Actor, ExecutionSession, RegistryTask, UsageEvent

class SkillInventoryTests(TestCase):
    def setUp(self):
        self.temp=TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        self.file=self.root/'sample'/'SKILL.md'
        self.file.parent.mkdir()
        self.file.write_text('---\nname: sample\ndescription: A sample skill\n---\nVersion one',encoding='utf-8')
        self.config=override_settings(SKILL_INVENTORY_ROOTS={'test':self.root})
        self.config.enable();self.addCleanup(self.config.disable)

    def test_lifecycle_and_idempotency(self):
        scan_skills(); installation=Installation.objects.get()
        first=installation.version_id
        scan_skills(); self.assertEqual(SkillChange.objects.count(),1)
        self.file.write_text('---\nname: sample\n---\nVersion two',encoding='utf-8')
        scan_skills();installation.refresh_from_db()
        self.assertNotEqual(first,installation.version_id)
        self.assertEqual(SkillChange.objects.get(action='updated').old_version_id,first)
        self.file.unlink();scan_skills();scan_skills()
        self.assertEqual(SkillChange.objects.filter(action='removed').count(),1)
        installation.refresh_from_db();self.assertEqual(installation.presence,'removed')
        self.file.write_text('restored',encoding='utf-8');scan_skills()
        self.assertEqual(SkillChange.objects.filter(action='restored').count(),1)

    def test_unreadable_root_does_not_report_deletions(self):
        scan_skills()
        with patch('registry.skill_inventory.collect',side_effect=PermissionError('denied')):scan_skills()
        self.assertEqual(Installation.objects.get().presence,'file_verified')
        self.assertFalse(SkillScan.objects.get().success)
        self.assertFalse(SkillChange.objects.filter(action='removed').exists())

    def test_usage_preserves_version_and_requires_matching_actor(self):
        scan_skills();installation=Installation.objects.get()
        actor=Actor.objects.create(key='test-agent',name='Test',kind='agent')
        session=ExecutionSession.objects.create(external_key='test-session',name='Test',actor=actor,client='tests',model='test')
        task=RegistryTask.objects.create(key='test-task',title='Test',purpose='Test')
        args=dict(event='test-use',installation=installation.external_key,session=session.external_key,actor=actor.key,task=task.key,reason='Testing',method='Read skill',result='Verified',stdout=StringIO())
        call_command('record_skill_use',**args);original=UsageEvent.objects.get().version_id
        self.file.write_text('new version',encoding='utf-8');scan_skills()
        call_command('record_skill_use',**args)
        self.assertEqual(UsageEvent.objects.count(),1)
        self.assertEqual(UsageEvent.objects.get().version_id,original)
        with self.assertRaises(CommandError):call_command('record_skill_use',**dict(args,event='bad',actor='someone-else'))
