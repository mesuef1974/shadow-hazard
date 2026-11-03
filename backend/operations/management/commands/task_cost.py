import json
from pathlib import Path
from django.core.management.base import BaseCommand, CommandError
from workspace.models import SessionCharter, ProjectDocumentation
from workspace.memory import document
from token_usage.capacity import verified_document
from operations.task_cost import collect


class Command(BaseCommand):
    help = 'Record explicit task-stage costs; unknown stages remain unknown.'

    def add_arguments(self, parser):
        parser.add_argument('--session', required=True)
        parser.add_argument('--key', required=True)
        parser.add_argument('--spec', type=Path, required=True)

    def handle(self, *args, **options):
        charter = SessionCharter.objects.get(key=options['session'])
        spec = json.loads(options['spec'].read_text(encoding='utf-8-sig'))
        result = collect(charter.project, spec['stages'], spec['acceptance_receipt'])
        sections = [{'title': 'نطاق السجل', 'body': spec['title']},
            {'title': 'التغطية', 'body': 'الأدوار المعلنة متحققة؛ لا يثبت ذلك عدم وجود أدوار غير معلنة.' if result['total_task_usage'] is not None else 'التكلفة الكلية غير مكتملة؛ الأجزاء غير المتحققة مجهولة وليست صفرًا.'},
            {'title': 'المجموع المتحقق فقط', 'body': f"إجمالي التوكنز المتحقق {result['verified_subtotal']['total_tokens']:,}. ليس تكلفة مالية ولا ميزانية معتمدة. مرجع قبول المخرج: الإيصال {result['acceptance_receipt']}؛ السجل لا يعيد تقييم الجودة تلقائيًا."}]
        labels = {'execution':'التنفيذ','review':'المراجعة','correction':'التصحيح','coordination':'التنسيق'}
        for row in result['stages']:
            body = f"متحقق: {row['usage']['total_tokens']:,} توكنًا؛ زمن الدور {row['turn_seconds']:g} ثانية." if row['status']=='verified' else 'غير متحقق: '+row.get('unknown_reason', 'تعذر التحقق من دليل الدور المرتبط.')
            sections.append({'title':labels[row['role']]+' — '+row['id'], 'body':body})
        receipt = document(charter.project, charter.key, 'task-cost:'+options['key'],
            'سجل تكلفة المهمة مع فصل المجموع المتحقق عن الأجزاء المجهولة.',
            {'report_title':spec['title'], 'report_sections':sections, 'task_cost':result})
        if not verified_document(ProjectDocumentation.objects.get(pk=receipt.pk)):
            raise CommandError('Receipt verification failed')
        self.stdout.write(json.dumps({'receipt':receipt.pk,'coverage':result['coverage']},ensure_ascii=False))
