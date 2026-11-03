"""Low-cost cross-process wakeup for the local Windows dispatcher."""

import ctypes
import os


_EVENT_NAME = "Local\\ShadowDangerModelJobWake"
_EVENT_MODIFY_STATE = 0x0002
_INFINITE = 0xFFFFFFFF


def _kernel32():
    if os.name != "nt":
        return None
    library = ctypes.WinDLL("kernel32", use_last_error=True)
    library.OpenEventW.argtypes = [ctypes.c_ulong, ctypes.c_int, ctypes.c_wchar_p]
    library.OpenEventW.restype = ctypes.c_void_p
    library.CreateEventW.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int, ctypes.c_wchar_p]
    library.CreateEventW.restype = ctypes.c_void_p
    library.SetEvent.argtypes = [ctypes.c_void_p]
    library.SetEvent.restype = ctypes.c_int
    library.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
    library.WaitForSingleObject.restype = ctypes.c_ulong
    library.CloseHandle.argtypes = [ctypes.c_void_p]
    library.CloseHandle.restype = ctypes.c_int
    return library


def signal_dispatcher():
    """Wake a same-user dispatcher process; a missing listener is harmless."""
    library = _kernel32()
    if library is None:
        return False
    handle = library.OpenEventW(_EVENT_MODIFY_STATE, False, _EVENT_NAME)
    if not handle:
        return False
    try:
        return bool(library.SetEvent(handle))
    finally:
        library.CloseHandle(handle)


class DispatcherWakeup:
    """Auto-reset Windows event with a bounded fallback wake interval."""

    def __init__(self):
        self.library = _kernel32()
        self.handle = None
        if self.library:
            self.handle = self.library.CreateEventW(None, False, False, _EVENT_NAME)

    def wait(self, seconds=30):
        if not self.handle:
            import time
            time.sleep(seconds)
            return
        milliseconds = max(1, min(int(seconds * 1000), 0xFFFFFFFE))
        self.library.WaitForSingleObject(self.handle, milliseconds)

    def close(self):
        if self.handle:
            self.library.CloseHandle(self.handle)
            self.handle = None
