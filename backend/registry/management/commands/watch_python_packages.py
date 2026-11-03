import logging
import time
from io import StringIO
from django.core.management import call_command
from django.core.management.base import BaseCommand
from django.db import connections

class Command(BaseCommand):
    help = 'Continuously reconcile project packages and skills every 30 seconds; stop with Ctrl+C.'
    def handle(self, *args, **options):
        while True:
            try:
                for command in ('inventory_python_packages', 'inventory_skills'):
                    try:
                        call_command(command, stdout=StringIO())
                    except Exception:
                        logging.exception('Inventory failed: %s; retrying next cycle', command)
            except KeyboardInterrupt:
                return
            except Exception:
                logging.exception('Python package inventory failed; retrying in 30 seconds')
            finally:
                connections.close_all()
            time.sleep(30)
