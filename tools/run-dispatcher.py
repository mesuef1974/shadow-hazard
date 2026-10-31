"""Windowless entrypoint with a bounded startup log for Windows Task Scheduler."""
import os
from pathlib import Path
import sys

root=Path(__file__).resolve().parent.parent
backend=root/'backend'
local=backend/'.local'
local.mkdir(exist_ok=True)
log=local/'dispatcher-startup.log'
if log.exists() and log.stat().st_size>1_048_576:
    log.replace(local/'dispatcher-startup.previous.log')
with log.open('a',encoding='utf-8',buffering=1) as output:
    sys.stdout=sys.stderr=output
    sys.path.insert(0,str(backend))
    os.environ.setdefault('DJANGO_SETTINGS_MODULE','config.settings')
    from django.core.management import execute_from_command_line
    try:
        execute_from_command_line([str(backend/'manage.py'),'run_model_dispatcher','--watch'])
    except Exception:
        import traceback
        traceback.print_exc(file=output)
        output.flush()
        raise
