from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from django.db import connection
from django.test import SimpleTestCase
from .signals import backup_before_operations_migration,record_pre_operations_migration_backup,_pending_migration_backup


class MigrationBackupTests(SimpleTestCase):
    def tearDown(self):
        _pending_migration_backup.clear()

    def test_one_backup_for_all_apps_and_new_backup_for_next_migration_plan(self):
        result={'run':SimpleNamespace(pk=1),'path':Path('backup.sqlite3'),'sha256':'hash','size_bytes':10,'integrity':'ok'}
        with patch('operations.signals._is_runtime_database',return_value=True), \
             patch.object(connection.introspection,'table_names',return_value=['operations_backuppolicy','operations_backuprun']), \
             patch('operations.models.BackupPolicy.objects') as policies, \
             patch('operations.services.backup_database',return_value=result) as backup:
            policies.using.return_value.filter.return_value.exists.return_value=True
            plan=[('workspace','new-migration')]
            for label in ['accounts','workspace','operations']:
                app=SimpleNamespace(label=label)
                backup_before_operations_migration(app,app,plan,'default')
            self.assertEqual(backup.call_count,1)
            app=SimpleNamespace(label='operations')
            record_pre_operations_migration_backup(app,app,'default')
            self.assertNotIn('default',_pending_migration_backup)
            backup_before_operations_migration(app,app,list(plan),'default')
            self.assertEqual(backup.call_count,2)

    def test_failed_previous_migration_does_not_suppress_a_new_plan(self):
        _pending_migration_backup['default']={'plan':[],'registered':True}
        result={'run':None,'path':Path('backup.sqlite3'),'sha256':'hash','size_bytes':10,'integrity':'ok'}
        with patch('operations.signals._is_runtime_database',return_value=True), \
             patch.object(connection.introspection,'table_names',return_value=[]), \
             patch('operations.models.BackupRun.objects') as runs, \
             patch('operations.services.backup_database',return_value=result) as backup:
            app=SimpleNamespace(label='operations');plan=[('workspace','next')]
            backup_before_operations_migration(app,app,plan,'default')
            backup.assert_called_once_with(kind='pre_migration',using='default',register=False)
            record_pre_operations_migration_backup(app,app,'default')
            runs.using.return_value.create.assert_called_once()
