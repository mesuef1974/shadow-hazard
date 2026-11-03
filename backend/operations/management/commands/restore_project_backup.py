import json
from django.core.management.base import BaseCommand, CommandError
from operations.services import restore_backup_to_separate_database


class Command(BaseCommand):
    help = "استعادة نسخة موثقة إلى قاعدة SQLite منفصلة داخل backend/.local/restores."

    def add_arguments(self, parser):
        parser.add_argument("run_id", type=int)

    def handle(self, *args, **options):
        try:
            result = restore_backup_to_separate_database(options["run_id"])
        except Exception as exc:
            raise CommandError(f"تعذرت الاستعادة المنفصلة ({type(exc).__name__}).") from exc
        self.stdout.write(json.dumps({"run_id": result["run"].pk, "source_run_id": result["run"].source_id,
            "path": str(result["path"]), "sha256": result["sha256"], "integrity": result["integrity"],
            "verified": result["verified"], "database_replaced": result["database_replaced"]}, ensure_ascii=False))
