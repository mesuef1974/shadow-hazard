import _thread
import threading
import time

from django.contrib.staticfiles.management.commands.runserver import Command as StaticRunserver
from django.core import signals
from django.core.management import call_command


class IdleExit:
    """يوقف الخادم بعد N دقيقة بلا طلبات. للمعاينات والاختبارات، فلا يبقى خادم بعد انتهاء مهمته."""

    def __init__(self, minutes, clock=time.monotonic):
        self.limit = minutes * 60
        self.clock = clock
        self.last = clock()

    def touch(self, **kwargs):
        self.last = self.clock()

    def expired(self):
        return self.limit > 0 and self.clock() - self.last >= self.limit

    def watch(self, stop, interval=15, interrupt=_thread.interrupt_main):
        while not stop.wait(interval):
            if self.expired():
                print(f'لا طلبات منذ {self.limit // 60} دقيقة؛ يُغلق الخادم.', flush=True)
                # KeyboardInterrupt في الخيط الرئيسي: يخرج runserver خروجًا نظيفًا.
                interrupt()
                return


class Command(StaticRunserver):
    def add_arguments(self, parser):
        super().add_arguments(parser)
        parser.add_argument(
            '--idle-exit', type=int, default=0, metavar='MINUTES',
            help='يغلق الخادم بعد هذا العدد من الدقائق بلا طلبات. 0 (الافتراضي) يعطّله.',
        )

    def inner_run(self, *args, **options):
        threading.Thread(
            target=call_command, args=('watch_python_packages',),
            daemon=True, name='package-inventory',
        ).start()
        minutes = options.get('idle_exit') or 0
        stop = threading.Event()
        if minutes > 0:
            idle = IdleExit(minutes)
            signals.request_started.connect(
                idle.touch, weak=False, dispatch_uid='runserver-idle-exit',
            )
            threading.Thread(
                target=idle.watch, args=(stop,), daemon=True, name='idle-exit',
            ).start()
        try:
            return super().inner_run(*args, **options)
        finally:
            stop.set()  # finally يضمن إيقاف المراقب مهما كان سبب الخروج.
