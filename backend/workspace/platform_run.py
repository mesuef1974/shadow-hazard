"""يسجل لماذا خرجت المنصة (ADR 0005) في platform-exits.jsonl.

القتل من الخارج لا يمر بشيفرة هنا، فأثر التشغيل يبقى ونبضه يدل على وقت الموت، ويقرؤه البدء التالي.
next_by='deploy' مع نبض حديث يعني إعادة تشغيل النشر، أما نبض قديم فهو توقف فعلي.
الحارس (guard) عملية أم تنتظر الخادم فتسجل رمز خروجه ووقته وشهود اللحظة، ثم تعيده فورًا.
"""
import faulthandler
import json
import os
import signal
import subprocess
import sys
import threading
import time
import traceback
from contextlib import contextmanager
from datetime import UTC, datetime

BEAT_SECONDS = 30
MAX_BYTES = 256_000
REASON_LIMIT = 1500
STARTED_BY = 'PLATFORM_STARTED_BY'
NEXT_START = 'platform-next-start'
LOCK_HELD = 75  # منصة أخرى تعمل: يتكرر مع كل دورة للمهمة، فليس خروجًا يُسجَّل.
NO_WINDOW = getattr(subprocess, 'CREATE_NO_WINDOW', 0)
SIGNAL_NAMES = ('SIGBREAK', 'SIGTERM')
STABLE_SECONDS = 60
RESTART_LIMIT = 3
WITNESS_LIMIT = 40
WITNESS_WIDTH = 220


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
    except OSError as error:
        print(f'{stamp()} تعذّر السجل: {error}', file=sys.stderr, flush=True)


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
    names = [name for name in SIGNAL_NAMES if hasattr(signal, name)]
    return {getattr(signal, name): signal.signal(getattr(signal, name), leave(name))
            for name in names}


def claim_start(local):
    """النشر يطلب البدء من المهمة، فيترك من طلبه في ملف يقرؤه الحارس مرة واحدة."""
    marker = local / NEXT_START
    try:
        os.environ[STARTED_BY] = marker.read_text(encoding='utf-8').strip()
        marker.unlink()
    except OSError:
        pass


def witnesses():
    """من بدأ قبيل الموت وحالة الطاقة: القاتل قد يخرج سريعًا، فتُلتقط اللحظة نفسها."""
    script = ("$since=(Get-Date).AddSeconds(-120);"
              "$new=Get-CimInstance Win32_Process|Where-Object{$_.CreationDate -gt $since}|"
              "ForEach-Object{'{0} {1}<-{2} {3}' -f $_.CreationDate.ToString('HH:mm:ss'),$_.Name,"
              "$_.ParentProcessId,([string]$_.CommandLine).Replace([char]34,[char]39)};"
              "$b=(Get-CimInstance Win32_Battery).BatteryStatus;"
              "@{battery_status=$b;recent=@($new)}|ConvertTo-Json -Compress")
    try:
        found = subprocess.run(['powershell', '-NoProfile', '-Command', script], text=True,
                               capture_output=True, timeout=20, encoding='utf-8',
                               errors='replace', creationflags=NO_WINDOW)
        seen = json.loads(found.stdout)
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        return {'witness_error': str(error)[:200]}
    return {'battery_status': seen.get('battery_status'),
            'recent': [line[:WITNESS_WIDTH] for line in seen.get('recent', [])[:WITNESS_LIMIT]]}


def serve_once(command):
    process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                               stderr=subprocess.DEVNULL, creationflags=NO_WINDOW)
    return process.pid, process.wait()


def guard(command, exits):
    """يعيد الخادم فور موته أيًّا كان السبب، إلا إن طلب النشر إيقافه أو تكرر سقوطه.

    القفل عند الخادم لا هنا: النشر يبدأ خادمًا فور قتل سابقه ولا ينتظر خروج حارسه.
    NO_WINDOW في serve_once: بدونها يفتح الخادم نافذة حين يكون الحارس بلا طرفية.
    """
    numbers = [getattr(signal, name) for name in SIGNAL_NAMES if hasattr(signal, name)]
    previous = {number: signal.signal(number, signal.SIG_IGN) for number in numbers}
    quick_falls = 0
    try:
        while True:
            started, began = stamp(), time.monotonic()
            launcher, code = serve_once(command)
            if code == LOCK_HELD:
                return
            line = dict(kind='server-exit', code=f'{code & 0xFFFFFFFF:#010x}', launcher=launcher,
                        started=started, started_by=os.environ.get(STARTED_BY, 'task'))
            if (exits.parent / NEXT_START).exists():
                note(exits, **line, next_by='deploy')
                return
            quick_falls = quick_falls + 1 if time.monotonic() - began < STABLE_SECONDS else 0
            give_up = quick_falls >= RESTART_LIMIT
            note(exits, **line, **witnesses(), next_by='task' if give_up else 'guard')
            if give_up:  # عطل دائم لا يُكرَّر بلا نهاية، ومهمة المنصة تعيد المحاولة بعد دقائق.
                return
            os.environ[STARTED_BY] = 'guard'
    finally:
        for number, handler in previous.items():
            signal.signal(number, handler)


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
