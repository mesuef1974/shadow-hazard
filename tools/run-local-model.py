"""Windowless scheduled LM Studio check, protected against duplicate loading."""
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(root / 'backend'))
from workspace.worker_lock import single_worker
from knowledge.model_startup import ensure_model


def main():
    local = root / 'backend' / '.local'
    with single_worker(local / 'local-model-startup.lock') as acquired:
        if not acquired:
            return
        now = datetime.now(timezone.utc).isoformat()
        status = {'checked_at': now, 'ready': False}
        try:
            status.update(ensure_model(), ready=True)
        except Exception as exc:
            status['error_type'] = type(exc).__name__
            status['error'] = str(exc)[:300] if isinstance(exc, RuntimeError) else 'Local model startup check failed'
            raise
        finally:
            status['checked_at'] = datetime.now(timezone.utc).isoformat()
            temporary = local / 'local-model-health.tmp'
            temporary.write_text(json.dumps(status, ensure_ascii=False), encoding='utf-8')
            temporary.replace(local / 'local-model-health.json')


if __name__ == '__main__':
    main()
