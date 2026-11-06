"""Run the local-only platform on its fixed port, under one OS-owned lock.

Without --serve this process only guards the server and records its exit code.
"""
import os
from pathlib import Path
import sys

SERVE = '--serve'


def serve(backend, local):
    from workspace.platform_run import LOCK_HELD, tracked_run
    from workspace.worker_lock import single_worker

    with single_worker(local / 'platform-server.lock') as acquired:
        if not acquired:
            return LOCK_HELD
        log = local / 'platform-server.log'
        if log.exists() and log.stat().st_size > 1_048_576:
            log.replace(local / 'platform-server.previous.log')
        with log.open('a', encoding='utf-8', buffering=1) as output:
            sys.stdout = sys.stderr = output
            os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
            from django.core.management import execute_from_command_line
            # Local development only; serve the approved brand/static files too.
            with tracked_run(local, output):
                execute_from_command_line([
                    str(backend / 'manage.py'), 'runserver', '127.0.0.1:8011',
                    '--insecure', '--noreload',
                ])
    return 0


def main():
    root = Path(__file__).resolve().parent.parent
    backend = root / 'backend'
    local = backend / '.local'
    sys.path.insert(0, str(backend))
    if SERVE in sys.argv:
        return serve(backend, local)
    from workspace.platform_run import claim_start, guard
    local.mkdir(exist_ok=True)
    claim_start(local)
    # الحارس بلا طرفية: خطؤه يضيع دون هذا الملف، ويضيع معه رمز الخروج.
    sys.stderr = (local / 'platform-guard.log').open('a', encoding='utf-8', buffering=1)
    guard([sys.executable, str(Path(__file__).resolve()), SERVE], local / 'platform-exits.jsonl')
    return 0


if __name__ == '__main__':
    sys.exit(main())
