"""Prefer explicit model transport; safely queue when the desktop owns the writer."""
from .desktop_session_bridge import deliver_one as deliver_owned,BridgeError
from .native_queue_bridge import deliver_one as queue_existing


def deliver_one():
    try:
        result=deliver_owned()
    except BridgeError as exc:
        # Only this explicit pre-turn rejection permits the alternate transport.
        if 'already has an active writer' not in str(exc):raise
        return queue_existing()
    return queue_existing() if result is None else result
