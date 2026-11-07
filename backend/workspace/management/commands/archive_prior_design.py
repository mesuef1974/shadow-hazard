"""يسجّل ADR 0007 ويعلّم مهام التصميم السابق بتحديثات جديدة ويُخفي ما لا يخص الطالبين؛ لا يحذف شيئًا، ويُعاد بأمان."""
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.db import transaction

from workspace.models import Decision, DocumentAccess, Project, Task, TaskUpdate
from workspace.prior_design import HIDDEN_FROM_STUDENTS

DECISION_TITLE = 'اعتماد الرصد والتحذير من الحافلة (ADR 0007)'
DECISION_BODY = (
    'اعتمد سفيان في 7 نوفمبر 2026 نقل الرصد والتحذير من جانب الطريق إلى الحافلة المتوقفة، وتبقى نواة المشروع: '
    'نحسب ظل رؤية السائق، ولا نحذّر إلا من طفل داخله. يعدّل هذا ADR 0002 في موضع الرصد والتحذير. '
    'نتائج التصميم السابق محفوظة في الأرشيف، ولا تُنقل أرقامها قبل إعادة بروتوكول 731.'
)
RESULTS = 'مراجعة التوافق مع ADR 0007: هذه النتائج تخص التصميم السابق، وهي محفوظة في الأرشيف، وتُعاد بروتوكول 731 على تصميم الحافلة.'
UPDATES = {
    'الفكرة الأساسية': 'مراجعة التوافق مع ADR 0007: انتقل الرصد والتحذير إلى الحافلة المتوقفة، وبقي الوصف الأصلي محفوظًا كما كُتب.',
    'المحاكاة ولوحة التحكم ضمن النطاق الحالي': 'مراجعة التوافق مع ADR 0007: هذا الاعتماد يخص المختبر السابق، وهو في الأرشيف. النموذج الحالي أداة شرح بافتراضات.',
    'تثبيت بروتوكول تقييم المحاكاة': RESULTS,
    'تنفيذ التجارب وتحليل حساسية المعلمات': RESULTS,
    'تشخيص التداخل المستحدث وقصور التنبيه المبكر': RESULTS,
    'دراسة وتصميم المجسم المصغر': 'مراجعة التوافق مع ADR 0007: يُبنى التصميم على مواضع الكاميرات والتحذير على الحافلة.',
    'مراجعة عرض المسابقة وحزمة التسليم': 'مراجعة التوافق مع ADR 0007: حُدّث العرض إلى تصميم الحافلة، والعرض السابق محفوظ في الأرشيف.',
}


class Command(BaseCommand):
    help = 'أرشفة التصميم السابق في سجلات المشروع بلا حذف.'

    @transaction.atomic
    def handle(self, *args, **options):
        author = get_user_model().objects.get(username='mesuef')
        project = Project.objects.order_by('pk').first()
        _, created = Decision.objects.get_or_create(project=project, title=DECISION_TITLE, defaults={'body': DECISION_BODY, 'author': author})
        added = 0
        for title, body in UPDATES.items():
            task = Task.objects.filter(phase__project=project, title=title).first()
            if task and not task.updates.filter(body=body).exists():
                TaskUpdate.objects.create(task=task, author=author, body=body, state=task.state)
                added += 1
        hidden = DocumentAccess.objects.filter(
            path__in=HIDDEN_FROM_STUDENTS, team_visible=True).update(team_visible=False)
        self.stdout.write(f'decision_created={created} updates_added={added} students_hidden={hidden}')
