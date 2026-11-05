from django.conf import settings
from django.test.runner import DiscoverRunner


class Runner(DiscoverRunner):
    """The retired agent section's code is still tested as written; its redirect has its own test."""

    def setup_test_environment(self, **kwargs):
        super().setup_test_environment(**kwargs)
        settings.HIDE_AGENT_SECTION = False
        settings.CODEX_RETIRED = False
