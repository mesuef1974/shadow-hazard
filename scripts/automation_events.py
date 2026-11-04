"""سجل أحداث الأتمتة للعرض الحي في صفحة الأتمتة (ADR 0003). صفر توكنز.

كل حارس يضيف سطر JSON واحدًا إلى .local/automation-events.jsonl في جذر المستودع.
الفشل هنا لا يوقف أي حارس أبدًا، ولا يُكتب شيء في CI لأن بيئته مؤقتة.
"""
import json
import os
from datetime import datetime, timezone
from pathlib import Path

FILE = Path('.local') / 'automation-events.jsonl'
MAX_BYTES = 256_000
KEEP_LINES = 500


def record(kind, result, detail='', root=None):
    if os.environ.get('GITHUB_ACTIONS') == 'true':
        return
    try:
        path = Path(root or Path.cwd()) / FILE
        path.parent.mkdir(exist_ok=True)
        if path.exists() and path.stat().st_size > MAX_BYTES:
            kept = path.read_text(encoding='utf-8').splitlines()[-KEEP_LINES:]
            path.write_text('\n'.join(kept) + '\n', encoding='utf-8')
        event = {'at': datetime.now(timezone.utc).isoformat(timespec='seconds'), 'kind': kind,
                 'result': result, 'detail': str(detail)[:200]}
        with path.open('a', encoding='utf-8') as handle:
            handle.write(json.dumps(event, ensure_ascii=False) + '\n')
    except Exception:  # السجل للعرض فقط؛ لا يعطّل الحارس.
        pass
