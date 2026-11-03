from django.conf import settings
from django.db import connections
from django.db.models.signals import post_migrate, pre_migrate
from django.dispatch import receiver
from pathlib import Path


_pending_migration_backup = {}


def _is_runtime_database(using):
    connection = connections[using]
    configured = settings.DATABASES.get(using, {}).get("NAME")
    actual = connection.settings_dict.get("NAME")
    if connection.vendor != "sqlite" or not configured or not actual:
        return False
    if str(actual).startswith("file:") or str(actual) == ":memory:":
        return False
    return Path(actual).resolve() == Path(configured).resolve()


@receiver(pre_migrate, dispatch_uid="operations.backup_before_schema_migration")
def backup_before_operations_migration(sender, app_config, plan, using, **kwargs):
    if not plan or _pending_migration_backup.get(using,{}).get('plan') is plan:
        return
    if not _is_runtime_database(using):
        return
    from .models import BackupPolicy, BackupRun
    from .services import backup_database

    tables = set(connections[using].introspection.table_names())
    policy = "operations_backuppolicy" in tables and BackupPolicy.objects.using(using).filter(key="default", enabled=True, before_migration=True).exists()
    if "operations_backuppolicy" in tables and not policy:
        return
    result = backup_database(kind="pre_migration", using=using, register="operations_backuprun" in tables)
    _pending_migration_backup[using] = {
            'plan':plan,'registered':result['run'] is not None,
            "path": result["path"], "sha256": result["sha256"], "size_bytes": result["size_bytes"],
            "integrity": result["integrity"],
        }


@receiver(post_migrate, dispatch_uid="operations.record_pre_schema_migration_backup")
def record_pre_operations_migration_backup(sender, app_config, using, **kwargs):
    if app_config.label != "operations" or using not in _pending_migration_backup:
        return
    from .models import BackupRun

    evidence = _pending_migration_backup.pop(using)
    if evidence['registered']:
        return
    BackupRun.objects.using(using).create(
        kind="pre_migration", state="success", relative_path=f"backups/{evidence['path'].name}",
        sha256=evidence["sha256"], size_bytes=evidence["size_bytes"], integrity_result=evidence["integrity"],
        details={"created_before_operations_migration": True},
    )
