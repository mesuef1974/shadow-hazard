"""Only server settings select providers; request data never names executable code."""
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.utils.module_loading import import_string
from .adapters import RailwayCLI
from .git import LocalGit


def provider(value):
    return import_string(value) if isinstance(value, str) else value


def git_factory(release):
    paths = getattr(settings, 'RELEASE_GIT_REPOSITORIES', {})
    if release.repository not in paths:
        raise ImproperlyConfigured('Release repository alias is not configured')
    return LocalGit(paths[release.repository])


def transport_factory():
    override = getattr(settings, 'RELEASE_TRANSPORT_FACTORY', None)
    if override:
        return provider(override)()
    config = getattr(settings, 'RELEASE_RAILWAY', {})
    required = {'project', 'service', 'artifact_provider', 'validation_provider', 'probe_provider'}
    if not required.issubset(config) or any(not config[k] for k in required):
        raise ImproperlyConfigured('Railway release providers are not configured')
    return RailwayCLI(project=config['project'], service=config['service'],
        artifact_provider=provider(config['artifact_provider']), preflight=provider(config['validation_provider']),
        probes=provider(config['probe_provider']))
