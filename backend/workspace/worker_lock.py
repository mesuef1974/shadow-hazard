"""OS-owned lock: released on exit/crash, shared by manual and startup launchers."""
import os
from contextlib import contextmanager


@contextmanager
def single_worker(path):
    path.parent.mkdir(parents=True,exist_ok=True)
    stream=path.open('a+b')
    stream.seek(0,2)
    if not stream.tell():stream.write(b'0');stream.flush()
    stream.seek(0)
    acquired=False
    try:
        try:
            if os.name=='nt':
                import msvcrt
                msvcrt.locking(stream.fileno(),msvcrt.LK_NBLCK,1)
            else:
                import fcntl
                fcntl.flock(stream,fcntl.LOCK_EX|fcntl.LOCK_NB)
            acquired=True
        except OSError:pass
        yield acquired
    finally:
        if acquired:
            stream.seek(0)
            if os.name=='nt':msvcrt.locking(stream.fileno(),msvcrt.LK_UNLCK,1)
            else:fcntl.flock(stream,fcntl.LOCK_UN)
        stream.close()
