import json
import sqlite3
from django.core.management.base import BaseCommand, CommandError
from operations.services import backup_database


class Command(BaseCommand):
    help = "إنشاء نسخة SQLite متحققة داخل backend/.local/backups فقط."

    def add_arguments(self, parser):
        parser.add_argument("--before-migration", action="store_true")

    def handle(self, *args, **options):
        try:
            result = backup_database(kind="pre_migration" if options["before_migration"] else "manual")
        except (OSError, sqlite3.Error, ValueError, RuntimeError) as exc:
            raise CommandError(f"تعذر إنشاء النسخة ({type(exc).__name__}).") from exc
        self.stdout.write(json.dumps({"run_id": result["run"].pk if result["run"] else None,
            "path": str(result["path"]), "sha256": result["sha256"], "size_bytes": result["size_bytes"],
            "integrity": result["integrity"], "verified": result["verified"], "pruned": result["pruned"]}, ensure_ascii=False))
