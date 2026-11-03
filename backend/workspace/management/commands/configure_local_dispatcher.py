from django.core.management.base import BaseCommand, CommandError
from workspace.models import DispatcherHeartbeat, ModelPolicy, SessionCharter, Project


class Command(BaseCommand):
    help='Record startup installation and ensure the main coordinator charter.'
    def add_arguments(self,parser):
        parser.add_argument('--startup-method',choices=['Windows — عند تسجيل الدخول','Windows — مجلد بدء التشغيل'],required=True)
    def handle(self,*args,**options):
        project=Project.objects.order_by('pk').first()
        if not project:raise CommandError('Project must exist first.')
        policy,_=ModelPolicy.objects.get_or_create(category='coordination',defaults={
            'title':'التنسيق العام','model':'gpt-6-luna','thinking':'low','max_attempts':1})
        SessionCharter.objects.get_or_create(key='coordination',defaults={
            'name':'ظل الخطر — التنسيق العام','project':project,'policy':policy,
            'scope':'تنسيق رحلة المشروع ومتابعة القرارات والمهام والتوثيق، وتجميع نتائج الجلسات المتخصصة.',
            'rules':'القرارات النهائية لسفيان. لا نشر ولا دفع بعيد ولا تفويض خارج المهمة. استند إلى أدلة المشروع؛ لا تفترض ذاكرة المحادثة الأم. العمل الافتراضي قراءة ومراجعة.',
            'thread_id':'01a18e02-0487-7180-9aa0-f932b22c8ef4','state':'ready_waiting_for_task'})
        DispatcherHeartbeat.objects.update_or_create(key='local',defaults={'startup_method':options['startup_method']})
        self.stdout.write('Startup method recorded; coordination charter ready.')
