"""Local fast-forward integration, restricted to a configured isolated checkout."""
from pathlib import Path
import subprocess
from django.core.exceptions import ValidationError


class GitMergeFailure(Exception):
    """Known failure before target ref mutation."""


class LocalGit:
    def __init__(self, path, *, runner=subprocess.run):
        self.path = Path(path).resolve()
        self.runner = runner
        from django.conf import settings
        shared = Path(settings.BASE_DIR).parent.resolve()
        if self.path == shared or shared in self.path.parents:
            raise ValidationError('Shared workspace cannot be a merge checkout')

    def git(self, *args, check=True):
        return self.runner(['git', '-c', 'core.hooksPath=', '-C', str(self.path), *args],
            check=check, capture_output=True, text=True, timeout=60)

    def merge(self, release):
        # No working-tree modification. Atomic update-ref is a fast-forward merge;
        # divergent histories require a new reviewed integration commit.
        try:
            if self.git('rev-parse', '--is-bare-repository').stdout.strip() != 'true':
                raise GitMergeFailure('Isolated bare repository required')
            source = self.git('rev-parse', '--verify', release.source_ref + '^{commit}').stdout.strip()
            target = self.git('rev-parse', '--verify', release.target_branch + '^{commit}').stdout.strip()
            if source != release.commit or target != release.target_commit:
                raise GitMergeFailure('Pinned reference mismatch')
            if self.git('merge-base', '--is-ancestor', target, source, check=False).returncode != 0:
                raise GitMergeFailure('Non fast-forward integration rejected')
        except subprocess.CalledProcessError as exc:
            raise GitMergeFailure('Invalid git reference') from exc
        # Once mutation starts, timeout/errors are ambiguous; do not retry.
        self.git('update-ref', release.target_branch, release.commit, release.target_commit)
        if self.git('rev-parse', '--verify', release.target_branch).stdout.strip() != release.commit:
            raise RuntimeError('Merge result unresolved')
        return release.commit
