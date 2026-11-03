"""The single send entry point; observation belongs to the dispatcher cycle."""
from .owner_delivery import deliver_one as deliver_owned


def deliver_one():
    """Attempt one owner-MCP delivery; never select a fallback transport."""
    return deliver_owned()
