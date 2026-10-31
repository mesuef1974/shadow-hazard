from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from django.test import TestCase
from workspace.models import Project
from .readonly_worker import snapshot
from .readonly_transport import IsolationError
from .docker_runtime import Runtime


class ReadOnlyRuntimeTests(TestCase):
    def test_snapshot_never_copies_local_database_or_env(self):
        project=Project.objects.create(name='test')
        with TemporaryDirectory() as source,TemporaryDirectory() as destination:
            root=Path(source);backend=root/'backend';backend.mkdir()
            (root/'safe.py').write_text('print(1)')
            (root/'.env').write_text('SECRET')
            (root/'.local').mkdir();(root/'.local/private.py').write_text('SECRET')
            with self.settings(BASE_DIR=backend),patch('execution_context.readonly_worker.subprocess.run') as run:
                run.return_value.stdout='safe.py\0.env\0.local/private.py\0'
                manifest=snapshot(project,Path(destination))
            self.assertEqual(set(manifest['files']),{'safe.py','AGENTS.md'})
            self.assertFalse((Path(destination)/'.env').exists())
            self.assertFalse((Path(destination)/'.local').exists())
            self.assertTrue((Path(destination)/'project_data.sqlite3').exists())

    def test_runtime_has_no_host_network_or_writable_input_mounts(self):
        with TemporaryDirectory() as folder,patch('execution_context.docker_runtime.Path.home',return_value=Path(folder)),patch('execution_context.docker_runtime.Runtime.docker'),patch('execution_context.docker_runtime.subprocess.Popen') as popen:
            home=Path(folder)/'.codex';home.mkdir();(home/'auth.json').write_text('{}')
            runtime=Runtime(folder);runtime.start(['codex','app-server'])
            args=popen.call_args.args[0]
            self.assertEqual(args[args.index('--network')+1],'none')
            self.assertIn('--read-only',args);self.assertIn('no-new-privileges',args)
            self.assertNotIn('--privileged',args)
            for mount in [args[i+1] for i,value in enumerate(args) if value=='--mount']:
                self.assertTrue(mount.endswith(',readonly'))
                self.assertNotIn('docker.sock',mount)
