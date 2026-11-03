"""Process-wide local locks shared by manual synchronization and the watcher."""
from contextlib import contextmanager
from pathlib import Path
import os
from django.conf import settings


@contextmanager
def local_lock(name):
    root = Path(settings.LOCAL_DIR)
    root.mkdir(parents=True, exist_ok=True)
    with (root / name).open('a+b') as handle:
        handle.seek(0, 2)
        if handle.tell() == 0:
            handle.write(b'0')
            handle.flush()
        handle.seek(0)
        acquired = False
        try:
            try:
                if os.name == 'nt':
                    import msvcrt
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                acquired = True
            except OSError as error:
                if error.errno not in {11, 13, 35, 36}:
                    raise
            yield acquired
        finally:
            if acquired:
                handle.seek(0)
                if os.name == 'nt':
                    import msvcrt
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
