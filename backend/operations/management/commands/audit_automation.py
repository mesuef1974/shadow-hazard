import json
from django.core.management.base import BaseCommand
from operations.services import audit_automation


class Command(BaseCommand):
    help = "كشف عوائق الأتمتة وحفظها دون إعادة إرسال المهام أو تحرير الحجوزات."

    def handle(self, *args, **options):
        self.stdout.write(json.dumps(audit_automation(), ensure_ascii=False, default=str))
