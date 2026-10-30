import sys
import platform
from importlib.metadata import distributions
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone
from registry.models import PythonPackage, PythonPackageEvent, Actor, RegistryTask

class Command(BaseCommand):
    help = 'Inventory distributions in the active project virtual environment.'
    def add_arguments(self, parser):
        parser.add_argument('--actor')
        parser.add_argument('--task')
        parser.add_argument('--reason')
        parser.add_argument('--use', help='Record a declared use of an installed package')
    @transaction.atomic
    def handle(self, *args, **options):
        if sys.prefix == sys.base_prefix:
            raise CommandError('Run with the project virtual environment Python.')
        from django.conf import settings
        from pathlib import Path
        if Path(sys.prefix).resolve() != (settings.BASE_DIR.parent / '.venv').resolve():
            raise CommandError('This command inventories only the project .venv.')
        attribution = {}
        supplied = [options.get(k) for k in ('actor','task','reason')]
        if any(supplied) and not all(supplied):
            raise CommandError('Supply actor, task and reason together.')
        if all(supplied):
            attribution = dict(actor=Actor.objects.get(key=options['actor']), task=RegistryTask.objects.get(key=options['task']), reason=options['reason'])
        descriptions = {
            'django':'إطار بناء المنصة: الحسابات وقاعدة البيانات ولوحة الإدارة.',
            'asgiref':'يربط التنفيذ المتزامن وغير المتزامن الذي يحتاجه Django.',
            'pip':'مدير تثبيت حزم Python وتحديثها وحذفها.',
            'psycopg':'موصل Python للتعامل مع قاعدة PostgreSQL.',
            'psycopg-binary':'المكونات الثنائية الجاهزة لتشغيل موصل PostgreSQL.',
            'sqlparse':'يحلل نصوص SQL وينسقها، ويستخدمه Django داخليًا.',
            'typing-extensions':'يوفر ميزات إضافية لتعريف أنواع البيانات في Python.',
            'tzdata':'بيانات المناطق الزمنية لعرض الأوقات وتحويلها بدقة.',
        }
        observed = timezone.now()
        names = []
        for dist in distributions():
            name = dist.metadata.get('Name')
            if not name: continue
            name = name.lower().replace('_', '-').replace('.', '-')
            names.append(name)
            previous = PythonPackage.objects.filter(name=name).first()
            package, created = PythonPackage.objects.update_or_create(name=name, defaults={
                'explanation': descriptions.get(name, previous.explanation if previous and previous.explanation else 'مكتبة مكتشفة حديثًا؛ شرحها المبسط يحتاج مراجعة.'),
                'version':dist.version, 'summary':dist.metadata.get('Summary',''),
                'requires':dist.requires or [], 'location':str(dist.locate_file('')),
                'python_version':platform.python_version(), 'environment':sys.prefix,
                'is_installed':True, 'observed_at':observed,
            })
            action = 'installed' if created or not previous.is_installed else ('updated' if previous.version != package.version else None)
            if action:
                PythonPackageEvent.objects.create(package=package, action=action, old_version=previous.version if previous else '', new_version=package.version, **attribution)
        for package in PythonPackage.objects.exclude(name__in=names).filter(is_installed=True):
            package.is_installed=False
            package.observed_at=observed
            package.save(update_fields=['is_installed','observed_at'])
            PythonPackageEvent.objects.create(package=package, action='removed', old_version=package.version, **attribution)
        if options.get('use'):
            if not attribution: raise CommandError('Documented use requires actor, task and reason.')
            package=PythonPackage.objects.get(name=options['use'],is_installed=True)
            PythonPackageEvent.objects.create(package=package, action='used', new_version=package.version, **attribution)
        self.stdout.write(f'Inventoried {len(set(names))} installed distributions; no packages installed or removed.')
