"""Windowless entrypoint for the once-per-minute local usage watcher."""
import os
from pathlib import Path
import sys

root = Path(__file__).resolve().parent.parent
backend = root / 'backend'
local = backend / '.local'
local.mkdir(exist_ok=True)
log = local / 'token-sync-startup.log'
if log.exists() and log.stat().st_size > 1_048_576:
    log.replace(local / 'token-sync-startup.previous.log')
with log.open('a', encoding='utf-8', buffering=1) as output:
    sys.stdout = sys.stderr = output
    sys.path.insert(0, str(backend))
    os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
    from django.core.management import execute_from_command_line
    execute_from_command_line([str(backend / 'manage.py'), 'sync_token_usage', '--watch', '--interval', '60'])
