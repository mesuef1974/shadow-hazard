import copy
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import uuid
from unittest.mock import patch
from contextlib import ExitStack
from django.test import TestCase
from django.utils import timezone
from workspace.test_renewal import RenewalFixture
from workspace.models import SessionCharter, SessionRenewal, ProjectDocumentation
from workspace.memory import document
from workspace.renewal import create_candidate, verify_candidate, commit_binding, rollback, RenewalError
from workspace.renewal_desktop import DesktopRenewalAdapter, probe_desktop_support
from execution_context.owner_tools import OwnerTools, OwnerToolsError
from execution_context.renewal_owner_tools import RenewalOwnerTools, schemas, checksum
from execution_context.renewal_rollout import bootstrap_evidence, snapshot, RolloutEvidenceError


def tool_schemas():
    return {
        'create_thread': {'inputSchema': {'required': ['prompt', 'target'], 'properties': {
            'prompt': {'type': 'string'}, 'target': {'anyOf': [{'required': ['type', 'projectId', 'environment'],
                'properties': {'type': {'enum': ['project']}, 'projectId': {'type': 'string'},
                    'environment': {'anyOf': [{'properties': {'type': {'enum': ['local']}}}]}}}]}}}},
        'read_thread': {'inputSchema': {'required': ['threadId'], 'properties': {k: {} for k in
            ['threadId', 'hostId', 'turnLimit', 'includeOutputs', 'maxOutputCharsPerItem']}}},
        'list_projects': {'inputSchema': {'properties': {}}},
    }


class DesktopRenewalTests(RenewalFixture, TestCase):
    def setUp(self):
        self.stack = ExitStack();self.addCleanup(self.stack.close)
        self.folder = TemporaryDirectory();self.addCleanup(self.folder.cleanup)
        self.stack.enter_context(patch.dict(os.environ, {'CODEX_HOME': self.folder.name}))
        super().setUp()
        self.broker = str(uuid.uuid4());self.target = str(uuid.uuid4());self.app_project = str(uuid.uuid4())
        SessionCharter.objects.create(project=self.project, policy=self.policy, key='owner-broker',
            name='owner', scope='owner scope', rules='rules', thread_id=self.broker)
        self.config = {'project_id': self.project.pk, 'authorizing_user_id': self.owner.pk,
            'caller_thread_id': self.broker, 'server_sha256': 'a' * 64,
            'owner_identity': {'sha256': 'b' * 64}, 'pipe': 'fake-attested-pipe'}
        self.tools = tool_schemas();self.rpc_calls = [];self.states = {};self.extra_create_result = None
        self.project_rows = [{'projectId': self.app_project, 'projectKind': 'local',
            'hostId': 'local', 'path': self.row.context['cwd']}]
        self.latest = {self.old: str(uuid.uuid4()), self.broker: str(uuid.uuid4())}
        self.write_old()
        for module in ['workspace.renewal_desktop', 'execution_context.renewal_owner_tools']:
            self.stack.enter_context(patch(module + '.load_config', side_effect=lambda: copy.deepcopy(self.config)))
        self.stack.enter_context(patch('execution_context.renewal_owner_tools.pipe_identity',
            side_effect=lambda pipe: copy.deepcopy(self.config['owner_identity'])))
        def initialize(client, config=None, timeout=30):
            client.config=copy.deepcopy(config or self.config);client.tools=copy.deepcopy(self.tools)
        self.stack.enter_context(patch.object(OwnerTools, '__init__', new=initialize))
        self.stack.enter_context(patch.object(OwnerTools, 'close'))
        self.rpc_mock=self.stack.enter_context(patch.object(OwnerTools, 'rpc', autospec=True, side_effect=self.rpc))
        self.capability = probe_desktop_support(self.charter, self.owner)
        self.approval = document(self.project,self.charter.key,'scoped approval','synthetic scoped execution',{
            **self.approval.evidence,'desktop_default_inheritance_authorized':True,
            'expected_desktop_default':{'model':'gpt-6-luna','thinking':'low'},
            'owner_channel':{'caller_thread_id':self.broker,
                'server_sha256':'a'*64,'owner_image_sha256':'b'*64}})

    def path(self, thread):
        folder=Path(self.folder.name)/'sessions'/'fixture';folder.mkdir(parents=True,exist_ok=True)
        return folder/f'rollout-{thread}.jsonl'

    def save(self, thread, records):
        self.path(thread).write_text('\n'.join(json.dumps(r,ensure_ascii=False) for r in records)+'\n',encoding='utf-8')

    def write_old(self):
        turn=self.latest[self.old];stamp=timezone.now().timestamp()-20
        self.save(self.old,[{'type':'session_meta','payload':{'session_id':self.old,'cwd':self.row.context['cwd']}},
            {'type':'event_msg','payload':{'type':'task_started','turn_id':turn,'started_at':stamp}},
            {'type':'event_msg','payload':{'type':'task_complete','turn_id':turn,'completed_at':stamp+1}}])

    def write_bootstrap(self, prompt, root):
        parent=root;turn=str(uuid.uuid4());root=turn
        self.latest[self.target]=turn;stamp=timezone.now().timestamp()
        envelope=f'<codex_delegation>\n  <source_thread_id>{self.broker}</source_thread_id>\n  <input>{prompt}</input>\n</codex_delegation>'
        item_id='fco-'+str(uuid.uuid4())
        counts={'input_tokens':100,'cached_input_tokens':40,'cache_write_input_tokens':0,
                'output_tokens':7,'reasoning_output_tokens':0,'total_tokens':107}
        records=[{'type':'session_meta','payload':{'session_id':self.target,'cwd':self.row.context['cwd']}},
            {'type':'event_msg','payload':{'type':'task_started','turn_id':turn,'root_turn_id':root,'started_at':stamp,
                'turn_attribution':{'turn_id':turn,'root_turn_id':root,
                    'turn_trigger':'app_tool_create_thread','parent_turn_id':parent}}},
            {'type':'turn_context','payload':{'turn_id':turn,'root_turn_id':root,'model':'gpt-6-luna','effort':'low'}},
            {'type':'response_item','payload':{'type':'function_call_output','id':item_id,
                'namespace':'codex_app','name':'create_thread','output':envelope}},
            {'type':'event_msg','payload':{'type':'item_completed','thread_id':self.target,'turn_id':turn,
                'item':{'type':'FunctionCallOutput','id':item_id,'namespace':'codex_app','name':'create_thread','output':envelope}}},
            {'type':'token_usage_record','payload':{'thread_id':self.target,'turn_id':turn,'root_turn_id':root,
                'response_id':'response1','usage':counts,'turn_token_usage':counts}},
            {'type':'event_msg','payload':{'type':'item_completed','thread_id':self.target,'turn_id':turn,
                'item':{'type':'AgentMessage','id':'final1','phase':'final_answer',
                    'content':[{'type':'Text','text':'CONTEXT_READY'}]}}},
            {'type':'response_item','payload':{'type':'message','id':'final1','role':'assistant',
                'status':'completed','phase':'final_answer','content':[{'type':'output_text','text':'CONTEXT_READY'}]}},
            {'type':'event_msg','payload':{'type':'task_complete','turn_id':turn,'root_turn_id':root,
                'started_at':stamp,'completed_at':timezone.now().timestamp(),'last_agent_message':'CONTEXT_READY'}}]
        self.save(self.target,records)

    def rpc(self, client, method, params):
        self.rpc_calls.append((method,copy.deepcopy(params)))
        name=params['name'];args=params['arguments']
        if name=='list_projects':result={'projects':copy.deepcopy(self.project_rows)}
        elif name=='read_thread':
            target=args['threadId']
            result={'thread':{'id':target,'kind':'codex','hostId':'local','cwd':self.row.context['cwd'],
                'status':{'type':self.states.get(target,'idle')}},
                'turns':[{'id':self.latest[target],'status':'completed'}]}
        elif name=='create_thread':
            self.write_bootstrap(args['prompt'],params['_meta']['codexTurnId'])
            if self.extra_create_result:raise self.extra_create_result
            result={'threadId':self.target,'hostId':'local'}
        else:raise AssertionError('Unexpected RPC')
        return {'content':[{'type':'text','text':json.dumps(result)}]}

    def create_real(self):
        self.row=create_candidate(self.row.pk,self.owner,approval_id=self.approval.pk)
        return self.row

    def count_creates(self):
        return sum(p.get('name')=='create_thread' for _,p in self.rpc_calls)

    def test_capability_probe_is_read_only_and_constructor_does_not_connect(self):
        self.assertTrue(self.capability.evidence['capability_supported'])
        self.assertFalse(self.capability.evidence['operationally_verified'])
        count=len(self.rpc_calls)
        adapter=DesktopRenewalAdapter(self.row.pk,self.owner,self.approval.pk)
        self.assertTrue(adapter.supported);self.assertEqual(len(self.rpc_calls),count)
        self.assertEqual(self.count_creates(),0)

    def test_narrow_owner_rpc_default_create_and_independent_receipt_cycle(self):
        self.assertEqual(self.create_real().state,'created')
        self.row=verify_candidate(self.row.pk,self.owner)
        self.assertEqual(self.row.state,'verified')
        self.row=commit_binding(self.row.pk,self.owner)
        self.charter.refresh_from_db();self.assertEqual(self.charter.thread_id,self.target)
        self.assertEqual(commit_binding(self.row.pk,self.owner).state,'switched')
        self.assertEqual(self.count_creates(),1)
        call=next(p for _,p in self.rpc_calls if p['name']=='create_thread')
        self.assertEqual(set(call['arguments']),{'prompt','title','target'})
        self.assertEqual(call['arguments']['target']['environment'],{'type':'local'})
        from token_usage.models import TokenSample,TrackedSession
        self.assertEqual(TokenSample.objects.count(),0)
        self.assertEqual(TrackedSession.objects.get(external_id=self.target).project_id,self.project.pk)
        self.assertEqual(rollback(self.row.pk,self.owner).state,'rolled_back')
        self.assertTrue(TrackedSession.objects.filter(external_id=self.target).exists())

    def test_creation_timeout_preserves_no_replay_intent_and_recovers_from_rollout(self):
        self.extra_create_result=TimeoutError('ambiguous owner reply')
        self.assertEqual(self.create_real().state,'creation_uncertain')
        self.create_real();self.assertEqual(self.count_creates(),1)
        self.extra_create_result=None
        self.row=verify_candidate(self.row.pk,self.owner,candidate_thread_id=self.target)
        self.assertEqual(self.row.state,'verified')

    def test_worker_owner_tools_still_cannot_create_or_list_projects(self):
        with OwnerTools(self.config) as worker:
            for name in ['create_thread','list_projects']:
                with self.assertRaises(OwnerToolsError):
                    worker.call(name,{},call_id=str(uuid.uuid4()),context_id=str(uuid.uuid4()))
        self.assertEqual(self.count_creates(),0)

    def test_schema_drift_or_scope_revocation_prevents_create(self):
        self.tools['create_thread']['inputSchema']['required'].append('unknown')
        with self.assertRaises(OwnerToolsError):self.create_real()
        self.assertEqual(self.count_creates(),0)

    def test_owner_caller_self_renewal_is_blocked_before_creation(self):
        self.config['caller_thread_id']=self.old
        receipt=probe_desktop_support(self.charter,self.owner)
        self.assertFalse(receipt.evidence['capability_supported'])
        self.assertEqual(self.create_real().state,'desktop_blocked')
        self.assertEqual(self.count_creates(),0)

    def test_changed_config_after_create_quarantines_and_never_recreates(self):
        original=self.rpc
        def changed(client,method,params):
            result=original(client,method,params)
            if params['name']=='create_thread':self.config['caller_thread_id']=self.old
            return result
        self.rpc_mock.side_effect=changed
        self.assertEqual(self.create_real().state,'creation_uncertain')
        self.create_real();self.assertEqual(self.count_creates(),1)

    def test_changed_config_before_commit_keeps_original_binding(self):
        self.create_real();self.row=verify_candidate(self.row.pk,self.owner)
        self.config['server_sha256']='c'*64
        with self.assertRaises(RenewalError):commit_binding(self.row.pk,self.owner)
        self.charter.refresh_from_db();self.assertEqual(self.charter.thread_id,self.old)

    def test_unapproved_channel_or_wrong_project_mapping_cannot_create(self):
        self.project_rows[0]['path']=str(Path(self.folder.name)/'wrong-project')
        self.assertEqual(self.create_real().state,'cancelled')
        self.assertEqual(self.count_creates(),0)

    def test_scoped_rpc_cannot_send_messages_or_read_arbitrary_thread(self):
        adapter=DesktopRenewalAdapter(self.row.pk,self.owner,self.approval.pk)
        with adapter._client() as client:
            with self.assertRaises(OwnerToolsError):
                client.call('send_message_to_thread',{},call_id='call',context_id='context')
            with self.assertRaises(OwnerToolsError):
                client.call('read_thread',{'threadId':str(uuid.uuid4())},call_id='call',context_id='context')

    def test_rollout_partial_duplicate_file_and_wrong_prompt_are_fail_closed(self):
        self.create_real()
        path=self.path(self.target);original=path.read_bytes()
        path.write_bytes(original[:-1])
        with self.assertRaises(RolloutEvidenceError):bootstrap_evidence(self.row,self.target)
        path.write_bytes(original)
        duplicate=path.with_name('duplicate-'+self.target+'.jsonl');duplicate.write_bytes(original)
        with self.assertRaises(RolloutEvidenceError):bootstrap_evidence(self.row,self.target)
        duplicate.unlink()
        records=[json.loads(line) for line in original.decode().splitlines()]
        records[3]['payload']['output']=records[3]['payload']['output'].replace(self.row.bootstrap_prompt,'quoted other prompt')
        self.save(self.target,records)
        with self.assertRaises(RolloutEvidenceError):bootstrap_evidence(self.row,self.target)

    def test_arbitrary_tool_output_with_same_bootstrap_is_not_delivery(self):
        self.create_real()
        records=[json.loads(line) for line in self.path(self.target).read_text(encoding='utf-8').splitlines()]
        records[3]['payload']['namespace']='untrusted'
        records[4]['payload']['item']['namespace']='untrusted'
        self.save(self.target,records)
        with self.assertRaises(RolloutEvidenceError):bootstrap_evidence(self.row,self.target)

    def test_schema_requires_local_environment(self):
        changed=tool_schemas();changed['create_thread']['inputSchema']['properties']['target']['anyOf']=[]
        with self.assertRaises(OwnerToolsError):schemas(changed)

    def test_earlier_unclosed_or_overlapping_or_duplicated_turn_cannot_be_hidden(self):
        original=self.path(self.old).read_text(encoding='utf-8')
        records=[json.loads(line) for line in original.splitlines()]
        bad_start={'type':'event_msg','payload':{'type':'task_started','turn_id':str(uuid.uuid4()),'started_at':0}}
        for altered in [[records[0],bad_start,*records[1:]],
                [*records[:2],records[1],records[2]],[*records,records[-1]]]:
            self.save(self.old,altered)
            with self.assertRaises(RolloutEvidenceError):self.create_real()
            self.assertEqual(self.count_creates(),0)

    def aborted_prior_records(self):
        original=[json.loads(line) for line in self.path(self.old).read_text(encoding='utf-8').splitlines()]
        turn=str(uuid.uuid4());root=str(uuid.uuid4());stamp=timezone.now().timestamp()-40
        start={'type':'event_msg','payload':{'type':'task_started','turn_id':turn,
            'root_turn_id':root,'started_at':stamp}}
        abort={'type':'event_msg','payload':{'type':'turn_aborted','turn_id':turn,
            'root_turn_id':root,'started_at':stamp,'completed_at':stamp+1}}
        return [original[0],start,abort,*original[1:]]

    def test_valid_historical_abort_allows_later_completed_source(self):
        records=self.aborted_prior_records();self.save(self.old,records)
        self.assertEqual(snapshot(self.old)['latest_turn'], self.latest[self.old])
        self.create_real()
        self.assertEqual(self.count_creates(),1)

    def test_invalid_or_latest_abort_cannot_establish_source_idle(self):
        for variation in ['unmatched','duplicate','missing_start','missing_end','mismatched_start',
                          'reverse_time','future_time','nan','bool','root','invalid_uuid',
                          'next_before_abort','latest_abort','completion_after_abort']:
            with self.subTest(variation=variation):
                records=self.aborted_prior_records();p=records[2]['payload']
                if variation=='unmatched':p['turn_id']=str(uuid.uuid4())
                elif variation=='duplicate':records.insert(3,copy.deepcopy(records[2]))
                elif variation=='missing_start':p.pop('started_at')
                elif variation=='missing_end':p.pop('completed_at')
                elif variation=='mismatched_start':p['started_at']+=0.5
                elif variation=='reverse_time':p['completed_at']=p['started_at']-1
                elif variation=='future_time':p['completed_at']=timezone.now().timestamp()+100
                elif variation=='nan':p['completed_at']=float('nan')
                elif variation=='bool':p['completed_at']=True
                elif variation=='root':p['root_turn_id']=str(uuid.uuid4())
                elif variation=='invalid_uuid':
                    records[1]['payload']['turn_id']=p['turn_id']='invalid'
                elif variation=='next_before_abort':records[3]['payload']['started_at']=p['completed_at']-1
                elif variation=='latest_abort':records=records[:3]
                elif variation=='completion_after_abort':
                    records.insert(3,{'type':'event_msg','payload':{'type':'task_complete',
                        'turn_id':p['turn_id'],'completed_at':p['completed_at']}})
                self.save(self.old,records)
                with self.assertRaises(RolloutEvidenceError):snapshot(self.old)
        self.assertEqual(self.count_creates(),0)

    def test_actual_model_or_effort_mismatch_never_allows_binding(self):
        self.create_real()
        original=[json.loads(line) for line in self.path(self.target).read_text(encoding='utf-8').splitlines()]
        for field,value in [('model','gpt-6-astra'),('effort','high')]:
            records=copy.deepcopy(original);records[2]['payload'][field]=value;self.save(self.target,records)
            with self.assertRaises(RolloutEvidenceError):verify_candidate(self.row.pk,self.owner)
            self.charter.refresh_from_db();self.assertEqual(self.charter.thread_id,self.old)

    def test_revoked_actor_or_changed_pipe_identity_prevents_any_create(self):
        self.owner.is_active=False;self.owner.save(update_fields=['is_active'])
        with self.assertRaises(RenewalError):self.create_real()
        self.owner.is_active=True;self.owner.save(update_fields=['is_active'])
        with patch('execution_context.renewal_owner_tools.pipe_identity',return_value={'sha256':'wrong'}):
            with self.assertRaises(OwnerToolsError):self.create_real()
        self.assertEqual(self.count_creates(),0)

    def test_changed_caller_to_source_before_commit_refuses_binding(self):
        self.create_real();self.row=verify_candidate(self.row.pk,self.owner)
        self.config['caller_thread_id']=self.old
        with self.assertRaises(RenewalError):commit_binding(self.row.pk,self.owner)
        self.charter.refresh_from_db();self.assertEqual(self.charter.thread_id,self.old)

    def test_corrupt_response_usage_and_root_or_unpaired_tool_output_are_rejected(self):
        self.create_real()
        original=[json.loads(line) for line in self.path(self.target).read_text(encoding='utf-8').splitlines()]
        variants=[]
        changed=copy.deepcopy(original);changed[5]['payload']['usage']['cached_input_tokens']=101;variants.append(changed)
        changed=copy.deepcopy(original);changed[5]['payload']['response_id']='';variants.append(changed)
        changed=copy.deepcopy(original);changed[1]['payload']['root_turn_id']=str(uuid.uuid4());variants.append(changed)
        changed=copy.deepcopy(original);changed[4]['payload']['item']['id']='unpaired';variants.append(changed)
        for records in variants:
            self.save(self.target,records)
            with self.assertRaises(RolloutEvidenceError):bootstrap_evidence(self.row,self.target)

    def test_delivery_receipt_is_stable_across_repeated_reads(self):
        self.create_real();adapter=DesktopRenewalAdapter(self.row.pk,self.owner)
        first=adapter.delivery_receipt_id(self.row);second=adapter.delivery_receipt_id(self.row)
        self.assertEqual(first,second)

    def test_real_create_attribution_accepts_arabic_completion_without_magic_ack(self):
        self.create_real()
        records=[json.loads(line) for line in self.path(self.target).read_text(encoding='utf-8').splitlines()]
        text='تمت تهيئة الجلسة. جاهز للمهمة التالية.'
        records[6]['payload']['item']['content'][0]['text']=text
        records[7]['payload']['content'][0]['text']=text
        records[-1]['payload']['last_agent_message']=text
        records.insert(2,{'type':'world_state','payload':{'full':True,'state':{}}})
        records.insert(3,{'type':'event_msg','payload':{'type':'thread_settings_applied'}})
        self.save(self.target,records)
        evidence=bootstrap_evidence(self.row,self.target)
        self.assertFalse(evidence['ack_marker_exact'])
        self.assertFalse(evidence['semantic_instruction_compliance_proven'])
        self.assertEqual(evidence['execution_tool_calls'],0)
        self.assertEqual(evidence['root_turn_id'],self.latest[self.target])
        self.assertEqual(evidence['usage']['total_tokens'],107)

    def test_wrong_or_missing_create_parent_trigger_and_roots_are_rejected(self):
        self.create_real()
        original=[json.loads(line) for line in self.path(self.target).read_text(encoding='utf-8').splitlines()]
        for variation in ['no_attribution','no_parent','wrong_parent','trigger','turn','start_root',
                          'attribution_root','context_root','missing_context_root','end_root','usage_root']:
            with self.subTest(variation=variation):
                records=copy.deepcopy(original);start=records[1]['payload'];attr=start['turn_attribution']
                if variation=='no_attribution':start.pop('turn_attribution')
                elif variation=='no_parent':attr.pop('parent_turn_id')
                elif variation=='wrong_parent':attr['parent_turn_id']=str(uuid.uuid4())
                elif variation=='trigger':attr['turn_trigger']='app_tool_send_message'
                elif variation=='turn':attr['turn_id']=str(uuid.uuid4())
                elif variation=='start_root':start['root_turn_id']=str(uuid.uuid4())
                elif variation=='attribution_root':attr['root_turn_id']=str(uuid.uuid4())
                elif variation=='context_root':records[2]['payload']['root_turn_id']=str(uuid.uuid4())
                elif variation=='missing_context_root':records[2]['payload'].pop('root_turn_id')
                elif variation=='end_root':records[-1]['payload']['root_turn_id']=str(uuid.uuid4())
                elif variation=='usage_root':records[5]['payload']['root_turn_id']=str(uuid.uuid4())
                self.save(self.target,records)
                with self.assertRaises(RolloutEvidenceError):bootstrap_evidence(self.row,self.target)

    def test_empty_unpaired_or_conflicting_final_is_rejected(self):
        self.create_real()
        original=[json.loads(line) for line in self.path(self.target).read_text(encoding='utf-8').splitlines()]
        for variation in ['empty','host_text','response_text','end_text','missing_host',
                          'missing_response','id','phase','status','duplicate','wrong_scope']:
            with self.subTest(variation=variation):
                records=copy.deepcopy(original);host=records[6]['payload'];response=records[7]['payload']
                if variation=='empty':
                    host['item']['content'][0]['text']=response['content'][0]['text']=' '
                    records[-1]['payload']['last_agent_message']=' '
                elif variation=='host_text':host['item']['content'][0]['text']='different'
                elif variation=='response_text':response['content'][0]['text']='different'
                elif variation=='end_text':records[-1]['payload']['last_agent_message']='different'
                elif variation=='missing_host':records.pop(6)
                elif variation=='missing_response':records.pop(7)
                elif variation=='id':response['id']='unpaired'
                elif variation=='phase':host['item']['phase']='commentary'
                elif variation=='status':response['status']='in_progress'
                elif variation=='duplicate':records.insert(7,copy.deepcopy(records[6]))
                elif variation=='wrong_scope':host['turn_id']=str(uuid.uuid4())
                self.save(self.target,records)
                with self.assertRaises(RolloutEvidenceError):bootstrap_evidence(self.row,self.target)

    def test_any_execution_or_unknown_bootstrap_activity_is_rejected(self):
        self.create_real()
        original=[json.loads(line) for line in self.path(self.target).read_text(encoding='utf-8').splitlines()]
        unsafe=[{'type':'response_item','payload':{'type':kind}} for kind in
            ['function_call','custom_tool_call','local_shell_call','web_search_call','computer_call']]
        unsafe += [{'type':'event_msg','payload':{'type':'item_completed','thread_id':self.target,
            'turn_id':self.latest[self.target],'item':{'type':kind}}} for kind in
            ['FunctionCall','CommandExecution','McpToolCall','CustomToolCall']]
        unsafe += [{'type':'event_msg','payload':{'type':'exec_command_begin'}},
            {'type':'unknown_remote_execution','payload':{}},
            {'type':'response_item','payload':{'type':'function_call_output','name':'exec','namespace':'functions'}}]
        for activity in unsafe:
            with self.subTest(activity=activity):
                records=copy.deepcopy(original);records.insert(6,activity);self.save(self.target,records)
                with self.assertRaises(RolloutEvidenceError):bootstrap_evidence(self.row,self.target)

    def test_source_becomes_busy_after_project_lookup_no_host_create_and_lane_released(self):
        original=self.rpc
        def changed(client,method,params):
            result=original(client,method,params)
            if params['name']=='list_projects':self.states[self.old]='active'
            return result
        self.rpc_mock.side_effect=changed
        self.assertEqual(self.create_real().state,'cancelled')
        self.assertEqual(self.count_creates(),0)
        self.row.refresh_from_db();self.assertFalse(self.row.create_attempted)

    def test_queued_client_id_is_not_a_thread_and_never_retried(self):
        original=self.rpc
        def queued(client,method,params):
            result=original(client,method,params)
            if params['name']=='create_thread':
                return {'content':[{'type':'text','text':json.dumps({'clientThreadId':str(uuid.uuid4())})}]}
            return result
        self.rpc_mock.side_effect=queued
        self.assertEqual(self.create_real().state,'creation_uncertain')
        self.create_real();self.assertEqual(self.count_creates(),1)

    def test_read_only_live_inspection_does_not_claim_operational_verification(self):
        self.create_real();self.row=verify_candidate(self.row.pk,self.owner)
        self.assertFalse(ProjectDocumentation.objects.filter(task_key__startswith='desktop-renewal-natural-cycle:').exists())
        self.row=commit_binding(self.row.pk,self.owner)
        receipt=ProjectDocumentation.objects.get(task_key='desktop-renewal-natural-cycle:'+str(self.row.key))
        self.assertTrue(receipt.evidence['operationally_verified'])
        self.assertFalse(receipt.evidence['automatic_renewal'])

    def test_missing_project_path_cannot_resolve_to_process_current_directory(self):
        self.project_rows[0].pop('path')
        self.assertEqual(self.create_real().state,'cancelled')
        self.assertEqual(self.count_creates(),0)

    def test_missing_thread_cwd_never_uses_process_current_directory(self):
        original=self.rpc
        def missing(client,method,params):
            result=original(client,method,params)
            if params['name']=='read_thread':
                data=json.loads(result['content'][0]['text']);data['thread'].pop('cwd')
                result['content'][0]['text']=json.dumps(data)
            return result
        self.rpc_mock.side_effect=missing
        with self.assertRaises(OwnerToolsError):self.create_real()
        self.assertEqual(self.count_creates(),0)

    def test_source_activity_after_delivery_parse_is_rechecked_at_binding_boundary(self):
        self.create_real();self.row=verify_candidate(self.row.pk,self.owner)
        original=DesktopRenewalAdapter.assert_binding_authority
        def becomes_active(adapter):
            self.states[self.old]='active'
            return original(adapter)
        with patch.object(DesktopRenewalAdapter,'assert_binding_authority',new=becomes_active):
            with self.assertRaises(OwnerToolsError):commit_binding(self.row.pk,self.owner)
        self.charter.refresh_from_db();self.assertEqual(self.charter.thread_id,self.old)

    def test_historical_bootstrap_accepts_later_activity_without_relaxing_live_proof(self):
        from execution_context.renewal_history_evidence import historical_bootstrap_evidence
        self.create_real()
        records=[json.loads(line) for line in self.path(self.target).read_text(encoding='utf-8').splitlines()]
        records.append({'type':'event_msg','payload':{'type':'task_started','turn_id':str(uuid.uuid4())}})
        self.save(self.target,records)
        proof=historical_bootstrap_evidence(self.row,self.target)
        self.assertFalse(proof['live_idle_proven'])
        self.assertNotIn('sha256',proof)
        self.assertEqual(len(proof['prefix_sha256']),64)
        with self.assertRaises(RolloutEvidenceError):bootstrap_evidence(self.row,self.target)

    def test_recovery_preparation_preserves_request_and_is_not_binding_permission(self):
        from workspace.renewal_recovery import prepare_recovery
        from workspace.models import ProjectMemory
        self.create_real()
        original=copy.deepcopy(self.row.context)
        ProjectMemory.objects.create(project=self.project,key='recovery-test',instruction='new rules')
        receipt=prepare_recovery(self.row.pk,self.owner)
        self.assertEqual(prepare_recovery(self.row.pk,self.owner).pk,receipt.pk)
        self.assertIn('memory_digest',receipt.evidence['changed_context_keys'])
        self.row.refresh_from_db()
        self.assertEqual(self.row.context,original)
        self.assertEqual(self.row.state,'created')
        self.assertIsNone(self.row.delivery_receipt_id)
        with self.assertRaises(RenewalError):commit_binding(self.row.pk,self.owner)
        self.charter.refresh_from_db();self.assertEqual(self.charter.thread_id,self.old)

    def test_historical_prefix_rejects_partial_and_changed_data(self):
        from execution_context.renewal_history_evidence import historical_bootstrap_evidence, _prefix
        self.create_real()
        path=self.path(self.target);original=path.read_bytes()
        path.write_bytes(original[:-1])
        with self.assertRaises(RolloutEvidenceError):historical_bootstrap_evidence(self.row,self.target)
        path.write_bytes(original)
        raw,records=_prefix(path)
        with patch('execution_context.renewal_history_evidence._prefix',side_effect=[(raw,records),(raw+b'changed',records)]):
            with self.assertRaises(RolloutEvidenceError):historical_bootstrap_evidence(self.row,self.target)

    def test_historical_evidence_rejects_tampered_original_and_wrong_target(self):
        from execution_context.renewal_history_evidence import historical_bootstrap_evidence
        self.create_real()
        with self.assertRaises(RolloutEvidenceError):historical_bootstrap_evidence(self.row,self.old)
        records=[json.loads(line) for line in self.path(self.target).read_text(encoding='utf-8').splitlines()]
        records[3]['payload']['output']='untrusted prompt'
        self.save(self.target,records)
        with self.assertRaises(RolloutEvidenceError):historical_bootstrap_evidence(self.row,self.target)

    def test_recovery_rejects_corrupt_original_receipts_and_prompt_digest(self):
        from workspace.renewal_recovery import prepare_recovery
        self.create_real()
        saved=self.row.bootstrap_digest
        SessionRenewal.objects.filter(pk=self.row.pk).update(bootstrap_digest='0'*64)
        with self.assertRaises(RenewalError):prepare_recovery(self.row.pk,self.owner)
        SessionRenewal.objects.filter(pk=self.row.pk).update(bootstrap_digest=saved)
        for receipt in [self.row.handover,self.row.bootstrap]:
            digest=receipt.digest
            ProjectDocumentation.objects.filter(pk=receipt.pk).update(digest='0'*64)
            with self.assertRaises(RenewalError):prepare_recovery(self.row.pk,self.owner)
            ProjectDocumentation.objects.filter(pk=receipt.pk).update(digest=digest)
        SessionRenewal.objects.filter(pk=self.row.pk).update(handover_digest='0'*64)
        with self.assertRaises(RenewalError):prepare_recovery(self.row.pk,self.owner)
        self.assertFalse(ProjectDocumentation.objects.filter(task_key__startswith='renewal-recovery-preparation:').exists())

    def test_recovery_rejects_revoked_approval_without_receipt_or_rpc(self):
        from workspace.renewal_recovery import prepare_recovery
        self.create_real()
        count=self.rpc_mock.call_count
        ProjectDocumentation.objects.filter(pk=self.approval.pk).update(digest='0'*64)
        with self.assertRaises(RenewalError):prepare_recovery(self.row.pk,self.owner)
        self.assertEqual(self.rpc_mock.call_count,count)
        self.assertFalse(ProjectDocumentation.objects.filter(task_key__startswith='renewal-recovery-preparation:').exists())

    def adoption_setup(self, *, archive_source=False):
        from workspace.renewal_adoption import authorize_adoption
        self.create_real()
        self.stack.enter_context(patch('workspace.renewal_adoption.load_config',return_value=self.config))
        if archive_source:
            destination=Path(self.folder.name)/'archived_sessions'/self.path(self.old).name
            destination.parent.mkdir(parents=True,exist_ok=True)
            self.path(self.old).rename(destination)
        return authorize_adoption(self.row.pk,self.owner,user_instruction='Explicit synthetic adoption request')

    def write_adoption_ack(self, approval, *, marker=None, existing_turn=False):
        records=[json.loads(line) for line in self.path(self.target).read_text(encoding='utf-8').splitlines()]
        turn=records.pop()['payload']['turn_id'] if existing_turn else str(uuid.uuid4())
        self.latest[self.target]=turn;stamp=timezone.now().timestamp()
        text=marker or approval.evidence['acknowledgement']; item_id='ack-'+turn
        records.extend([
            {'type':'event_msg','payload':{'type':'task_started','turn_id':turn,'started_at':stamp}},
            {'type':'event_msg','payload':{'type':'item_completed','thread_id':self.target,'turn_id':turn,
                'item':{'type':'AgentMessage','id':item_id,'phase':'final_answer','content':[{'type':'Text','text':text}]}}},
            {'type':'response_item','payload':{'type':'message','role':'assistant','id':item_id,'phase':'final_answer',
                'status':'completed','content':[{'type':'output_text','text':text}]}},
            {'type':'event_msg','payload':{'type':'task_complete','turn_id':turn,'completed_at':stamp,'last_agent_message':text}},
        ])
        self.save(self.target,records)

    def test_manual_adoption_of_archived_source_and_existing_candidate_is_idempotent(self):
        from workspace.renewal_adoption import commit_adoption
        approval=self.adoption_setup(archive_source=True);self.write_adoption_ack(approval)
        with self.assertRaises(RolloutEvidenceError):snapshot(self.old)
        result=commit_adoption(self.row.pk,self.owner,approval_id=approval.pk)
        self.assertEqual(commit_adoption(self.row.pk,self.owner,approval_id=approval.pk).pk,result.pk)
        self.charter.refresh_from_db();self.assertEqual(self.charter.thread_id,self.target)
        self.row.refresh_from_db();self.assertEqual(self.row.state,'manually_adopted')
        self.assertIsNone(self.row.delivery_receipt_id)
        self.assertEqual(self.count_creates(),1)
        self.assertFalse(any(p['name']=='send_message_to_thread' for _,p in self.rpc_calls))
        self.client.force_login(self.owner)
        page=self.client.get('/exhibition/')
        self.assertNotContains(page,'اكتمل اعتماد محادثة التنسيق القائمة')
        self.charter.thread_id=self.old;self.charter.save(update_fields=['thread_id'])
        self.assertNotContains(self.client.get('/exhibition/'),'اكتمل اعتماد محادثة التنسيق القائمة')

    def test_manual_adoption_rejects_missing_wrong_and_unpaired_ack(self):
        from workspace.renewal_adoption import commit_adoption
        approval=self.adoption_setup()
        with self.assertRaises(RenewalError):commit_adoption(self.row.pk,self.owner,approval_id=approval.pk)
        original=self.path(self.target).read_bytes()
        self.write_adoption_ack(approval,marker='ADOPTION_READY:wrong')
        with self.assertRaises(RenewalError):commit_adoption(self.row.pk,self.owner,approval_id=approval.pk)
        self.path(self.target).write_bytes(original);self.write_adoption_ack(approval)
        records=[json.loads(line) for line in self.path(self.target).read_text(encoding='utf-8').splitlines()]
        records[-2]['payload']['id']='different-response'
        self.save(self.target,records)
        with self.assertRaises(RenewalError):commit_adoption(self.row.pk,self.owner,approval_id=approval.pk)
        self.charter.refresh_from_db();self.assertEqual(self.charter.thread_id,self.old)

    def test_manual_adoption_rejects_active_target_and_changed_context(self):
        from workspace.renewal_adoption import commit_adoption
        from workspace.models import ProjectMemory
        approval=self.adoption_setup();self.write_adoption_ack(approval)
        self.states[self.target]='active'
        with self.assertRaises(RenewalError):commit_adoption(self.row.pk,self.owner,approval_id=approval.pk)
        self.states[self.target]='idle'
        ProjectMemory.objects.create(project=self.project,key='changed-after-approval',instruction='new context')
        with self.assertRaises(RenewalError):commit_adoption(self.row.pk,self.owner,approval_id=approval.pk)

    def test_manual_adoption_rechecks_activity_at_final_boundary(self):
        from workspace.renewal_adoption import commit_adoption, _inspect
        approval=self.adoption_setup();self.write_adoption_ack(approval)
        calls=0
        def changed(client,row,thread_id,**kwargs):
            nonlocal calls
            calls+=1
            if calls==4:self.states[self.target]='active'
            return _inspect(client,row,thread_id,**kwargs)
        with patch('workspace.renewal_adoption._inspect',side_effect=changed):
            with self.assertRaises(RenewalError):commit_adoption(self.row.pk,self.owner,approval_id=approval.pk)
        self.charter.refresh_from_db();self.assertEqual(self.charter.thread_id,self.old)

    def test_manual_adoption_rejects_corrupt_authority_and_ceiling_change(self):
        from workspace.renewal_adoption import commit_adoption
        approval=self.adoption_setup();self.write_adoption_ack(approval)
        digest=approval.digest
        ProjectDocumentation.objects.filter(pk=approval.pk).update(digest='0'*64)
        with self.assertRaises(RenewalError):commit_adoption(self.row.pk,self.owner,approval_id=approval.pk)
        ProjectDocumentation.objects.filter(pk=approval.pk).update(digest=digest)
        self.charter.allowed_model_profiles=[];self.charter.save(update_fields=['allowed_model_profiles'])
        with self.assertRaises(RenewalError):commit_adoption(self.row.pk,self.owner,approval_id=approval.pk)

    def test_manual_adoption_rejects_invalid_acknowledgement_chronology(self):
        from workspace.renewal_adoption import _acknowledged
        approval=self.adoption_setup();self.write_adoption_ack(approval)
        data=snapshot(self.target)
        stamp=approval.created_at.timestamp()
        for start,end in [(stamp+10,stamp-10),(stamp-20,stamp-10),
                          (stamp,stamp+3600),(True,stamp),(stamp,float('nan'))]:
            with self.subTest(start=start,end=end):
                altered=copy.deepcopy(data)
                altered['starts'][-1][1]['started_at']=start
                altered['completed'][1]['completed_at']=end
                with self.assertRaises(RenewalError):_acknowledged(altered,approval)

    def test_explicit_limited_history_adoption_preserves_and_reports_old_gap(self):
        from workspace.renewal_adoption import authorize_adoption,commit_adoption
        self.adoption_setup()
        original=[json.loads(line) for line in self.path(self.target).read_text(encoding='utf-8').splitlines()]
        gap={'type':'event_msg','payload':{'type':'task_started','turn_id':str(uuid.uuid4()),'started_at':timezone.now().timestamp()}}
        self.save(self.target,[*original,gap])
        self.write_adoption_ack(self.approval,marker='earlier completed work')
        records=[json.loads(line) for line in self.path(self.target).read_text(encoding='utf-8').splitlines()]
        turn=str(uuid.uuid4());self.latest[self.target]=turn
        records.append({'type':'event_msg','payload':{'type':'task_started','turn_id':turn,'started_at':timezone.now().timestamp()}})
        self.save(self.target,records)
        limited=authorize_adoption(self.row.pk,self.owner,user_instruction='Explicit acceptance of declared historical gaps',accept_historical_gaps=True)
        self.assertEqual(len(limited.evidence['history_scope']['historical_gaps']),1)
        self.write_adoption_ack(limited,existing_turn=True)
        with self.assertRaises(RolloutEvidenceError):snapshot(self.target)
        result=commit_adoption(self.row.pk,self.owner,approval_id=limited.pk)
        self.assertEqual(result.evidence['history_scope'],limited.evidence['history_scope'])

    def test_limited_reader_rejects_gap_adjacent_to_acknowledgement(self):
        from execution_context.adoption_snapshot import history_scope
        self.create_real()
        records=[json.loads(line) for line in self.path(self.target).read_text(encoding='utf-8').splitlines()]
        for _ in range(2):
            records.append({'type':'event_msg','payload':{'type':'task_started','turn_id':str(uuid.uuid4())}})
        self.save(self.target,records)
        with self.assertRaises(RolloutEvidenceError):history_scope(self.target)

    def test_limited_reader_rejects_activity_after_completion(self):
        from execution_context.adoption_snapshot import latest_adoption_snapshot
        approval=self.adoption_setup();self.write_adoption_ack(approval)
        records=[json.loads(line) for line in self.path(self.target).read_text(encoding='utf-8').splitlines()]
        records.append({'type':'response_item','payload':{'type':'function_call','name':'unexpected'}})
        self.save(self.target,records)
        with self.assertRaises(RolloutEvidenceError):latest_adoption_snapshot(self.target)

    def test_limited_reader_records_overlaps_even_when_both_old_turns_completed(self):
        from execution_context.adoption_snapshot import history_scope
        self.create_real()
        records=[json.loads(line) for line in self.path(self.target).read_text(encoding='utf-8').splitlines()]
        a,b,c=[str(uuid.uuid4()) for _ in range(3)]
        for kind,turn in [('task_started',a),('task_started',b),('task_complete',a),('task_complete',b),('task_started',c)]:
            records.append({'type':'event_msg','payload':{'type':kind,'turn_id':turn}})
        self.save(self.target,records)
        scope=history_scope(self.target)
        self.assertEqual(scope['historical_gaps'],[])
        self.assertEqual(scope['historical_overlaps'][0]['turn_id'],b)
        self.assertEqual(scope['historical_overlaps'][0]['earlier'][0]['turn_id'],a)

    def test_adoption_ack_allows_prior_question_but_rejects_duplicate_or_later_host_final(self):
        from workspace.renewal_adoption import _acknowledged
        approval=self.adoption_setup();self.write_adoption_ack(approval)
        data=snapshot(self.target)
        final_line=next(i for i,r in data['records'] if r.get('type')=='event_msg'
            and r['payload'].get('item',{}).get('id','').startswith('ack-'))
        question={'type':'event_msg','payload':{'type':'item_completed','thread_id':self.target,
            'turn_id':data['latest_turn'],'item':{'type':'AgentMessage','id':'call-question',
                'phase':'final_answer','content':[{'type':'Text','text':'Choose an option'}]}}}
        earlier=copy.deepcopy(data)
        index=next(n for n,(line,_) in enumerate(earlier['records']) if line==final_line)
        earlier['records'].insert(index,(final_line-0.5,question))
        _acknowledged(earlier,approval)
        duplicate=copy.deepcopy(earlier)
        duplicate['records'].insert(index+1,duplicate['records'][index+1])
        with self.assertRaises(RenewalError):_acknowledged(duplicate,approval)
        later=copy.deepcopy(data)
        later['records'].insert(-1,(data['completed'][0]-0.5,question))
        with self.assertRaises(RenewalError):_acknowledged(later,approval)
