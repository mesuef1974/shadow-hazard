from django.core.management.base import BaseCommand
from registry.skill_inventory import scan_skills
class Command(BaseCommand):
    help = 'Reconcile local SKILL.md definitions without executing them.'
    def handle(self, *args, **options):
        self.stdout.write(str(scan_skills()))
