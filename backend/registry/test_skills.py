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
    def test_support_files_versions_content_and_removal(self):
        from registry.models import SkillFileSnapshot, SkillFileChange, SkillBlob
        script=self.file.parent/'scripts'/'run.py';script.parent.mkdir()
        script.write_bytes(b'original script')
        reference=self.file.parent/'.hidden-reference';reference.write_bytes(b'\x00\xffbinary')
        scan_skills();item=Installation.objects.get();first=item.version
        self.assertEqual(SkillFileSnapshot.objects.filter(version=first).count(),3)
        old=SkillFileSnapshot.objects.get(version=first,path='scripts/run.py')
        script.write_bytes(b'updated script')
        scan_skills();item.refresh_from_db()
        self.assertNotEqual(first.pk,item.version_id)
        self.assertEqual(bytes(old.blob.content),b'original script')
        self.assertTrue(SkillFileChange.objects.filter(path='scripts/run.py',action='modified').exists())
        count=SkillBlob.objects.count();scan_skills();self.assertEqual(count,SkillBlob.objects.count())
        script.unlink();scan_skills()
        self.assertTrue(SkillFileChange.objects.filter(path='scripts/run.py',action='removed').exists())
        self.assertEqual(bytes(SkillFileSnapshot.objects.get(pk=old.pk).blob.content),b'original script')

    def test_bundle_limit_preserves_previous_snapshot(self):
        scan_skills();version=Installation.objects.get().version_id
        with override_settings(SKILL_BUNDLE_MAX_BYTES=1):scan_skills()
        self.assertEqual(Installation.objects.get().version_id,version)
        self.assertFalse(SkillScan.objects.get().success)

    def test_snapshot_export_keeps_historical_binary_content(self):
        import zipfile
        from registry.models import SkillFileSnapshot
        blob=self.file.parent/'binary.dat';blob.write_bytes(b'\x00\xfforiginal')
        scan_skills();version=Installation.objects.get().version_id
        blob.write_bytes(b'new');scan_skills()
        with override_settings(BASE_DIR=self.root/'export-backend'):
            result=StringIO();call_command('export_skill_snapshot',str(version),stdout=result)
            with zipfile.ZipFile(result.getvalue().strip()) as archive:
                self.assertEqual(archive.read('files/binary.dat'),b'\x00\xfforiginal')
                self.assertIn('manifest.json',archive.namelist())

    def test_link_target_is_archived_without_following_it(self):
        from registry.models import SkillFileSnapshot
        outside=self.root/'outside.txt';outside.write_text('private outside content',encoding='utf-8')
        link=self.file.parent/'link.txt'
        try:link.symlink_to(outside)
        except OSError:self.skipTest('OS does not permit creating test symlinks')
        scan_skills()
        snapshot=SkillFileSnapshot.objects.get(path='link.txt')
        self.assertEqual(snapshot.kind,'link')
        self.assertNotIn(b'private outside content',bytes(snapshot.blob.content))
    def test_admin_archive_diff_download_and_permissions(self):
        import io,zipfile
        from accounts.models import User
        from django.urls import reverse
        scan_skills();old=Installation.objects.get().version_id
        self.file.write_text('<script>alert(1)</script>\nnew',encoding='utf-8');scan_skills()
        current=Installation.objects.get().version_id
        admin_user=User.objects.create_user(username='mesuef',role='supervisor',is_staff=True,is_superuser=True)
        self.client.force_login(admin_user)
        url=reverse('admin:skill_archive',args=[current])
        response=self.client.get(url,{'compare':str(old)})
        self.assertEqual(response.status_code,200)
        self.assertContains(response,'&lt;script&gt;')
        self.assertNotContains(response,'<script>alert(1)</script>')
        response=self.client.get(reverse('admin:skill_archive',args=[old]),{'download':'1'})
        self.assertEqual(response.status_code,200)
        with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
            self.assertIn(b'Version one',archive.read('files/SKILL.md'))
        other=Installation.objects.get().version.service.__class__.objects.create(key='unrelated',name='Other',kind='skill')
        from registry.models import ServiceVersion
        unrelated=ServiceVersion.objects.create(service=other,sha256='0'*64,source='test')
        self.assertEqual(self.client.get(url,{'compare':str(unrelated.pk)}).status_code,404)
        self.client.logout();self.assertEqual(self.client.get(url,{'download':'1'}).status_code,302)
        admin_user.is_superuser=False;admin_user.save(update_fields=['is_superuser'])
        self.client.force_login(admin_user);self.assertEqual(self.client.get(url).status_code,403)
