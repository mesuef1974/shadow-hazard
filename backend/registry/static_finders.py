from pathlib import PurePosixPath
import os
from django.contrib.staticfiles.finders import FileSystemFinder


class BrandAssetsFinder(FileSystemFinder):
    """Expose display assets, never document sources from the shared brand folder."""
    allowed = {'.css', '.svg', '.png', '.jpg', '.jpeg', '.webp', '.ico', '.ttf', '.woff', '.woff2'}

    def find(self, path, find_all=False, **kwargs):
        if PurePosixPath(path).suffix.lower() not in self.allowed:
            return []
        return super().find(os.path.normpath(path), find_all=find_all, **kwargs)

    def list(self, ignore_patterns):
        for path, storage in super().list(ignore_patterns):
            if PurePosixPath(path).suffix.lower() in self.allowed:
                yield path, storage

