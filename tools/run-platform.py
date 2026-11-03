"""Run the local-only platform on its fixed port, under one OS-owned lock."""
import os
from pathlib import Path
import sys


def main():
    root = Path(__file__).resolve().parent.parent
    backend = root / 'backend'
    local = backend / '.local'
    sys.path.insert(0, str(backend))
    from workspace.worker_lock import single_worker

    with single_worker(local / 'platform-server.lock') as acquired:
        if not acquired:
            return
        log = local / 'platform-server.log'
        if log.exists() and log.stat().st_size > 1_048_576:
            log.replace(local / 'platform-server.previous.log')
        with log.open('a', encoding='utf-8', buffering=1) as output:
            sys.stdout = sys.stderr = output
            os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
            from django.core.management import execute_from_command_line
            # Local development only; serve the approved brand/static files too.
            execute_from_command_line([
                str(backend / 'manage.py'), 'runserver', '127.0.0.1:8011',
                '--insecure', '--noreload',
            ])


if __name__ == '__main__':
    main()
