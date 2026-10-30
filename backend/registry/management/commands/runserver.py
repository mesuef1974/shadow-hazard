import threading
from django.contrib.staticfiles.management.commands.runserver import Command as StaticRunserver
from django.core.management import call_command

class Command(StaticRunserver):
    def inner_run(self, *args, **options):
        threading.Thread(target=call_command, args=('watch_python_packages',), daemon=True, name='package-inventory').start()
        return super().inner_run(*args, **options)
