"""Exercise real SQLite writer contention on the isolated Django test database."""
import threading
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import skipUnless

from django.db import connection, connections, transaction
from django.test import TransactionTestCase, override_settings

from .models import Installation, SkillChange
from .skill_inventory import scan_skills


@skipUnless(connection.vendor == 'sqlite', 'SQLite-specific concurrency regression')
class SkillContentionTests(TransactionTestCase):
    def test_scan_recovers_after_competing_writer_releases_lock(self):
        locked = threading.Event()
        release = threading.Event()
        errors = []

        def writer():
            try:
                with transaction.atomic():
                    with connections['default'].cursor() as cursor:
                        cursor.execute('UPDATE registry_skillscan SET root_alias = root_alias WHERE 1 = 0')
                    locked.set()
                    release.wait(5)
            except Exception as exc:
                errors.append(exc)
            finally:
                connections['default'].close()

        with TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'SKILL.md').write_text('---\nname: synthetic\n---\nSynthetic skill', encoding='utf-8')
            thread = threading.Thread(target=writer)
            thread.start()
            try:
                self.assertTrue(locked.wait(5))
                # Release only after the scanner has actually encountered a lock.
                from . import skill_inventory
                real_sleep = skill_inventory.time.sleep
                from unittest.mock import patch
                def release_writer(delay):
                    release.set()
                    thread.join(5)
                    real_sleep(delay)
                with override_settings(SKILL_INVENTORY_ROOTS={'synthetic': root}), patch(
                    'registry.skill_inventory.time.sleep', side_effect=release_writer,
                ) as retry:
                    self.assertEqual(scan_skills()['synthetic'], 1)
                    self.assertGreaterEqual(retry.call_count, 1)
            finally:
                release.set()
                thread.join(5)
            self.assertFalse(thread.is_alive())
            self.assertEqual(errors, [])
            self.assertEqual(Installation.objects.count(), 1)
            self.assertEqual(SkillChange.objects.count(), 1)
