"""يسجل لماذا خرجت المنصة (ADR 0005) في platform-exits.jsonl.

القتل من الخارج لا يمر بشيفرة هنا، فأثر التشغيل يبقى ونبضه يدل على وقت الموت، ويقرؤه البدء التالي.
next_by='deploy' مع نبض حديث يعني إعادة تشغيل النشر، أما نبض قديم فهو توقف فعلي.
"""
import faulthandler
import json
import os
import signal
import threading
import traceback
from contextlib import contextmanager
from datetime import UTC, datetime

BEAT_SECONDS = 30
MAX_BYTES = 256_000
REASON_LIMIT = 1500
STARTED_BY = 'PLATFORM_STARTED_BY'


def stamp(seconds=None):
    moment = datetime.fromtimestamp(seconds, UTC) if seconds else datetime.now(UTC)
    return moment.isoformat(timespec='seconds')


def note(path, **fields):
    """لا يرفع أبدًا: تعذّر السجل لا يوقف المنصة."""
    try:
        if path.exists() and path.stat().st_size > MAX_BYTES:
            os.replace(path, path.with_suffix('.previous.jsonl'))
        with path.open('a', encoding='utf-8') as stream:
            stream.write(json.dumps({'at': stamp(), **fields}, ensure_ascii=False) + '\n')
    except OSError:
        pass


def report_no_trace(marker, exits):
    try:
        saved = json.loads(marker.read_text(encoding='utf-8'))
        last_beat = marker.stat().st_mtime
    except (OSError, ValueError):
        return
    note(exits, kind='no-trace', pid=saved.get('pid'), started=saved.get('started'),
         last_beat=stamp(last_beat), next_by=os.environ.get(STARTED_BY, 'task'))


def beat(marker, stop):
    while not stop.wait(BEAT_SECONDS):
        try:
            os.utime(marker)
        except OSError:
            pass


def leave(name):
    def handler(_number, _frame):
        raise SystemExit(f'إشارة {name}')
    return handler


def catch_signals():
    """إغلاق الطرفية أو الجلسة يصل SIGBREAK على ويندوز؛ يرجع المعالجات السابقة لتُعاد."""
    names = [name for name in ('SIGBREAK', 'SIGTERM') if hasattr(signal, name)]
    return {getattr(signal, name): signal.signal(getattr(signal, name), leave(name))
            for name in names}


@contextmanager
def tracked_run(local, log):
    marker, exits = local / 'platform-run.json', local / 'platform-exits.jsonl'
    report_no_trace(marker, exits)
    started = stamp()
    marker.write_text(json.dumps({'pid': os.getpid(), 'started': started}), encoding='utf-8')
    stop = threading.Event()
    threading.Thread(target=beat, args=(marker, stop), daemon=True).start()
    faulthandler.enable(file=log)  # انهيار مكتبة أصلية لا يبلغ except.
    previous = catch_signals()
    reason = 'انتهى التشغيل'
    try:
        yield
    except SystemExit as error:
        reason = f'SystemExit: {error.code}'
        raise
    except BaseException:
        reason = traceback.format_exc()[-REASON_LIMIT:]
        raise
    finally:
        for number, handler in previous.items():
            signal.signal(number, handler)
        faulthandler.disable()
        stop.set()
        note(exits, kind='exit', pid=os.getpid(), started=started, reason=reason)
        try:
            marker.unlink()
        except OSError:
            pass
