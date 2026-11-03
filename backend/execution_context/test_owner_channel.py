import json
from pathlib import Path
import tempfile
from unittest.mock import patch
from django.test import SimpleTestCase
from .owner_tools import OwnerToolsError
from .owner_channel import ensure_channel, _probe, PIPE_PATTERN


class OwnerChannelTests(SimpleTestCase):
    def setUp(self):
        self.trust={'image':'signed-app','sha256':'a'*64,'sid':'same-user'}
        self.config={'pipe':'old','owner_identity':self.trust,'caller_thread_id':'caller',
                     'project_id':1,'authorizing_user_id':1,'server_sha256':'b'*64}
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.path=Path(self.temp.name)/'config.json'
        self.path.write_text(json.dumps(self.config))

    def renew(self, identities, candidates=('new',), probe_error=None, current=None):
        def identity(pipe):
            value=identities[pipe]
            if isinstance(value,Exception):raise value
            return value
        with patch('execution_context.owner_channel.pipe_identity',side_effect=identity), \
             patch('execution_context.owner_channel.candidate_pipes',return_value=candidates), \
             patch('execution_context.owner_channel._probe',side_effect=probe_error) as probe, \
             patch('execution_context.owner_channel.load_config',return_value=current or self.config), \
             patch('execution_context.owner_tools.config_path',return_value=self.path), \
             patch('execution_context.owner_tools.load_config',return_value=current or self.config):
            result=ensure_channel(self.config)
            return result,probe.call_count

    def test_live_trusted_endpoint_does_not_probe_or_rewrite(self):
        result,count=self.renew({'old':self.trust})
        self.assertEqual(result,self.config);self.assertEqual(count,0)

    def test_stale_endpoint_renews_only_pipe_after_read_proof(self):
        result,count=self.renew({'old':OwnerToolsError('gone'),'new':self.trust})
        self.assertEqual(result,{**self.config,'pipe':'new'});self.assertEqual(count,1)
        self.assertEqual(json.loads(self.path.read_text()),result)
        self.assertEqual(list(self.path.parent.glob('*.tmp')),[])

    def test_missing_owner_never_rewrites(self):
        with self.assertRaises(OwnerToolsError):
            self.renew({'old':OwnerToolsError('gone')},candidates=[])
        self.assertEqual(json.loads(self.path.read_text()),self.config)

    def test_wrong_user_image_or_binary_never_adopted(self):
        for field in ('sid','image','sha256'):
            with self.subTest(field=field),self.assertRaises(OwnerToolsError):
                self.renew({'old':OwnerToolsError('gone'),'new':{**self.trust,field:'wrong'}})
        self.assertEqual(json.loads(self.path.read_text()),self.config)

    def test_ambiguous_owner_no_probe_or_update(self):
        with self.assertRaises(OwnerToolsError):
            self.renew({'old':OwnerToolsError('gone'),'new':self.trust,'second':self.trust},('new','second'))

    def test_bad_live_thread_proof_no_update(self):
        with self.assertRaises(OwnerToolsError):
            self.renew({'old':OwnerToolsError('gone'),'new':self.trust},probe_error=OwnerToolsError('wrong thread'))
        self.assertEqual(json.loads(self.path.read_text()),self.config)

    def test_authority_changed_during_probe_no_overwrite(self):
        with self.assertRaises(OwnerToolsError):
            self.renew({'old':OwnerToolsError('gone'),'new':self.trust},current={**self.config,'authorizing_user_id':2})
        self.assertEqual(json.loads(self.path.read_text()),self.config)

    def test_channel_replaced_during_probe_no_update(self):
        with patch('execution_context.owner_channel.pipe_identity',side_effect=[OwnerToolsError('gone'),self.trust,{}]), \
             patch('execution_context.owner_channel.candidate_pipes',return_value=['new']), \
             patch('execution_context.owner_channel._probe'),self.assertRaises(OwnerToolsError):
            ensure_channel(self.config)

    def test_concurrent_authority_update_under_write_lock_is_preserved(self):
        changed={**self.config,'caller_thread_id':'different'}
        self.path.write_text(json.dumps(changed))
        with patch('execution_context.owner_channel.pipe_identity',side_effect=[OwnerToolsError('gone'),self.trust,self.trust]), \
             patch('execution_context.owner_channel.candidate_pipes',return_value=['new']), \
             patch('execution_context.owner_channel._probe'), \
             patch('execution_context.owner_channel.load_config',return_value=self.config), \
             patch('execution_context.owner_tools.load_config',return_value=changed), \
             patch('execution_context.owner_tools.config_path',return_value=self.path),self.assertRaises(OwnerToolsError):
            ensure_channel(self.config)
        self.assertEqual(json.loads(self.path.read_text()),changed)

    def test_legacy_config_cannot_bootstrap_untrusted_owner(self):
        with self.assertRaises(OwnerToolsError):ensure_channel({'pipe':'old'})

    def test_busy_config_lock_preserves_config(self):
        from workspace.worker_lock import single_worker
        from .owner_tools import save_config
        with single_worker(self.path.with_suffix('.lock')) as acquired:
            self.assertTrue(acquired)
            with patch('execution_context.owner_tools.config_path',return_value=self.path),self.assertRaises(OwnerToolsError):
                save_config({**self.config,'pipe':'new'},expected=self.config)
        self.assertEqual(json.loads(self.path.read_text()),self.config)

    def test_explicit_runtime_repair_compares_raw_config_before_replacement(self):
        import hashlib
        from .owner_tools import save_config
        fingerprint=hashlib.sha256(self.path.read_bytes()).hexdigest()
        with patch('execution_context.owner_tools.config_path',return_value=self.path), \
                patch('execution_context.owner_tools.load_config',side_effect=OwnerToolsError('old runtime missing')):
            fresh={**self.config,'server_sha256':'new-runtime'}
            save_config(fresh,expected_sha256=fingerprint)
            with self.assertRaises(OwnerToolsError):
                save_config(self.config,expected_sha256=fingerprint)
        self.assertEqual(json.loads(self.path.read_text()),fresh)

    def test_discovery_namespace_is_strict(self):
        self.assertTrue(PIPE_PATTERN.fullmatch('\\\\.\\pipe\\codex-app-tools-'+'a'*36))
        for pipe in ('remote','\\\\remote\\pipe\\codex-app-tools-'+'a'*36,'\\\\.\\pipe\\unrelated','\\\\.\\pipe\\codex-app-tools-x'):
            self.assertIsNone(PIPE_PATTERN.fullmatch(pipe))

    def test_probe_checks_exact_local_project_identity(self):
        with patch('execution_context.owner_channel.OwnerTools') as factory:
            client=factory.return_value.__enter__.return_value
            client.call.return_value={'thread':{'id':'caller','kind':'codex','hostId':'remote','cwd':'wrong'}}
            with self.assertRaises(OwnerToolsError):_probe(self.config)
            self.assertEqual(client.call.call_args.args[0],'read_thread')
