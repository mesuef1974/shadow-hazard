"""Hide the retired agent section (ADR 0002, exception 5 follow-up): its pages redirect.

The code, data and the worker stay; only browsing to these pages is turned away.
"""
from django.conf import settings
from django.shortcuts import redirect

KEEP = ('/sessions/automation-model/',)
TARGETS = (('/usage/', 'claude-usage'), ('/sessions/', 'session-automation-model'))


class HideAgentSectionMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if getattr(settings, 'HIDE_AGENT_SECTION', True) and request.method in ('GET', 'HEAD'):
            path = request.path
            if not path.startswith(KEEP):
                for prefix, target in TARGETS:
                    if path.startswith(prefix):
                        return redirect(target)
        return self.get_response(request)
