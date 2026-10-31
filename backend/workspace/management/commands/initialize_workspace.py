from django.core.management.base import BaseCommand
from django.db import transaction
from django.contrib.auth import get_user_model
from workspace.models import Project, Phase, Decision, DocumentAccess


class Command(BaseCommand):
    help = 'Initialize the agreed project structure without inventing tasks or completion.'

    @transaction.atomic
    def handle(self, *args, **options):
        project, _ = Project.objects.get_or_create(name='ظل الخطر', defaults={'summary': 'نموذج تجريبي لرصد المشاة المحجوبين والتنبيه على عمود الكاميرات. المشروع في مرحلة التصميم وإثبات الفكرة.'})
        phases = [
            ('الفكرة ودراسة المشكلة', 'تعريف المشكلة والمستفيدين وحدود النطاق.'),
            ('دراسة السوق والبحث', 'مقارنة البدائل والأبحاث وتوثيق فرضيات التميز والحقوق.'),
            ('المتطلبات وإثبات الفكرة', 'تثبيت معايير النجاح ومحاكاة منطق الرصد والتنبيه.'),
            ('التصميم الهندسي', 'اعتماد الأبعاد والمواضع والتوصيلات وقائمة المواد.'),
            ('التجهيز والبناء', 'تجهيز القطع وتوثيق تركيب الوحدات واختبارها منفردة.'),
            ('التكامل والتجارب', 'دمج الرصد والتنبيه وتوثيق القياسات وحالات الفشل.'),
            ('التقييم والتحسين', 'مقارنة النتائج وتحليل الأخطاء وتحسين النسخة.'),
            ('العرض والتسليم والتطوير', 'تثبيت النسخة ودليل التشغيل وعرض المسابقة وخطة التطوير.'),
        ]
        for order, (title, objective) in enumerate(phases, 1):
            Phase.objects.get_or_create(project=project, order=order, defaults={'title':title,'objective':objective})
        owner = get_user_model().objects.filter(username='mesuef').first()
        if owner:
            Decision.objects.get_or_create(project=project, title='اعتماد نطاق المنصة وصلاحيات الفريق', defaults={'author':owner,'body':'اعتمد سفيان في المحادثة: مشروع واحد مع بنية قابلة للتوسع؛ يرى الطالبان المهام والوثائق العامة مع حصر التعديل في أعمالهما؛ الوثائق الحساسة بصلاحيات خاصة؛ عرض المسابقة يشغله المشرف من حسابه.'})
        for path in ['blueprint/prototype-first-experiment-ar.md','roadside-warning-decision-ar.md']:
            DocumentAccess.objects.get_or_create(path=path, defaults={'team_visible':True})
        self.stdout.write('Project, phases, approval and public document permissions initialized.')
