"""Railway transport requires trusted artifact/preflight/probe providers; no default network execution."""
import subprocess
import json
import os
import re


class RailwayCLI:
    def __init__(self, *, project, service, artifact_provider, preflight, probes, runner=subprocess.run):
        self.project, self.service = project, service
        self.artifact_provider, self.preflight_provider = artifact_provider, preflight
        self.probes, self.runner = probes, runner

    def preflight(self, release, backup):
        return self.preflight_provider(release, backup)

    def publish(self, release, key):
        # Provider must materialize and verify an isolated artifact against the pinned
        # commit AND manifest digest; never upload the shared mutable checkout.
        artifact = self.artifact_provider(release)
        if artifact['sha256'] != release.manifest['artifact_sha256'] or artifact['commit'] != release.commit:
            raise ValueError('Artifact mismatch')
        if not os.environ.get('RAILWAY_TOKEN'):
            raise ValueError('Railway token is unavailable')
        result = self.runner(['railway', 'up', str(artifact['path']), '--path-as-root', '--json',
            '--project', self.project, '--service', self.service, '--environment', release.environment,
            '--message', key], check=True, capture_output=True, text=True, timeout=900)
        # CLI streams NDJSON. Persist only the deployment ID, never raw output.
        deployment_id = None
        for line in result.stdout.splitlines():
            try:
                value = json.loads(line)
            except ValueError:
                continue
            candidate = value.get('deploymentId') if isinstance(value, dict) else None
            if isinstance(candidate, str) and re.fullmatch(r'[a-zA-Z0-9_-]{1,200}', candidate):
                deployment_id = candidate
        if not deployment_id:
            raise RuntimeError('Deployment identifier unresolved')
        return deployment_id

    def check(self, external_id):
        return self.probes(external_id)


class SimulatedTransport:
    """Explicit test double; never contacts Railway."""
    def __init__(self, *, preflight_ok=True, healthy=True, ambiguous=False):
        self.preflight_ok, self.healthy, self.ambiguous = preflight_ok, healthy, ambiguous
        self.calls = 0

    def preflight(self, release, backup):
        return dict.fromkeys(['backup', 'migrations', 'settings'], self.preflight_ok)

    def publish(self, release, key):
        self.calls += 1
        if self.ambiguous:
            raise TimeoutError()
        return 'simulated:' + key

    def check(self, external_id):
        return dict.fromkeys(['health', 'pages', 'logs'], self.healthy)
