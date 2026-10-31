import json
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from django.test import SimpleTestCase
from .rollout_usage import read_turn_evidence, RolloutEvidenceError


class RolloutUsageTests(SimpleTestCase):
    def test_repeated_identical_context_is_valid_but_model_switch_is_not(self):
        for changed in (False,True):
            with self.subTest(changed=changed),tempfile.TemporaryDirectory() as folder:
                path=self.write_rollout(folder)
                rows=[json.loads(line) for line in path.read_text(encoding='utf-8').splitlines()]
                context=json.loads(json.dumps(rows[3]))
                if changed:context['payload']['model']='gpt-6-astra'
                rows.insert(4,context)
                path.write_text('\n'.join(json.dumps(row) for row in rows)+'\n',encoding='utf-8')
                kwargs=dict(thread_id='thread-123',claimed_at=datetime.fromtimestamp(200,tz=timezone.utc),acknowledged_at=datetime.fromtimestamp(201,tz=timezone.utc),expected_model='gpt-6-luna',expected_thinking='low')
                if changed:
                    with self.assertRaises(RolloutEvidenceError):read_turn_evidence(path,**kwargs)
                else:self.assertEqual(read_turn_evidence(path,**kwargs)['usage']['total_tokens'],15)

    def test_partial_usage_before_context_does_not_truncate_turn_total(self):
        with tempfile.TemporaryDirectory() as folder:
            path=self.write_rollout(folder)
            rows=[json.loads(line) for line in path.read_text(encoding='utf-8').splitlines()]
            partial={'input_tokens':5,'cached_input_tokens':2,'cache_write_input_tokens':0,'output_tokens':1,'reasoning_output_tokens':0,'total_tokens':6}
            rows.insert(3,{'type':'token_usage_record','payload':{'thread_id':'thread-123','turn_id':'turn-123','turn_token_usage':partial}})
            rows.insert(4,{'type':'event_msg','payload':{'type':'token_count','info':{'total_token_usage':{'input_tokens':905,'cached_input_tokens':502,'cache_write_input_tokens':0,'output_tokens':101,'reasoning_output_tokens':0,'total_tokens':1006}}}})
            path.write_text('\n'.join(json.dumps(row) for row in rows)+'\n',encoding='utf-8')
            evidence=read_turn_evidence(path,thread_id='thread-123',claimed_at=datetime.fromtimestamp(200,tz=timezone.utc),acknowledged_at=datetime.fromtimestamp(201,tz=timezone.utc),expected_model='gpt-6-luna',expected_thinking='low')
            self.assertEqual(evidence['usage']['total_tokens'],15)

    def test_whole_second_start_matches_subsecond_claim_only_in_same_second(self):
        with tempfile.TemporaryDirectory() as folder:
            path=self.write_rollout(folder)
            rows=[json.loads(line) for line in path.read_text(encoding='utf-8').splitlines()]
            rows[2]['payload']['started_at']=200
            path.write_text('\n'.join(json.dumps(row) for row in rows)+'\n',encoding='utf-8')
            kwargs=dict(thread_id='thread-123',acknowledged_at=datetime.fromtimestamp(202,tz=timezone.utc),expected_model='gpt-6-luna',expected_thinking='low')
            result=read_turn_evidence(path,claimed_at=datetime.fromtimestamp(200.5,tz=timezone.utc),**kwargs)
            self.assertEqual(result['usage']['total_tokens'],15)
            with self.assertRaises(RolloutEvidenceError):
                read_turn_evidence(path,claimed_at=datetime.fromtimestamp(201.01,tz=timezone.utc),**kwargs)

    def test_historical_counter_offset_requires_matching_independent_delta(self):
        for mismatch in (False, True):
            with self.subTest(mismatch=mismatch), tempfile.TemporaryDirectory() as folder:
                path=self.write_rollout(folder)
                rows=[json.loads(line) for line in path.read_text(encoding='utf-8').splitlines()]
                baseline=dict(rows[1]['payload']['info']['total_token_usage'])
                baseline['input_tokens']+=100; baseline['total_tokens']+=100
                rows.insert(2,{'type':'token_usage_record','payload':{'thread_id':'thread-123','turn_id':'previous','thread_token_usage':baseline}})
                for row in rows:
                    if row.get('type')=='token_usage_record' and row['payload'].get('turn_id')=='turn-123':
                        counts=row['payload']['thread_token_usage']
                        counts['input_tokens']+=100+int(mismatch); counts['total_tokens']+=100+int(mismatch)
                path.write_text('\n'.join(json.dumps(row) for row in rows)+'\n',encoding='utf-8')
                kwargs=dict(thread_id='thread-123',claimed_at=datetime.fromtimestamp(200,tz=timezone.utc),acknowledged_at=datetime.fromtimestamp(201,tz=timezone.utc),expected_model='gpt-6-luna',expected_thinking='low')
                if mismatch:
                    with self.assertRaises(RolloutEvidenceError):read_turn_evidence(path,**kwargs)
                else:self.assertEqual(read_turn_evidence(path,**kwargs)['usage']['total_tokens'],15)

    def write_rollout(self, folder, *, reset=False, overlap=False, complete=True):
        path=Path(folder)/'rollout.jsonl'
        turn='turn-123';thread='thread-123'
        start={'type':'event_msg','payload':{'type':'task_started','turn_id':turn,'started_at':200.1}}
        ctx={'type':'turn_context','payload':{'turn_id':turn,'model':'gpt-6-luna','effort':'low'}}
        end_counts=803 if reset else 1015
        delta={'input_tokens':12,'cached_input_tokens':6,'cache_write_input_tokens':0,
            'output_tokens':3,'reasoning_output_tokens':0,'total_tokens':15}
        end={'type':'event_msg','payload':{'type':'task_complete','turn_id':turn,'started_at':200.1,'completed_at':200.4,'last_agent_message':'SESSION_BRIDGE_OK 42'}}
        rows=[
            {'type':'session_meta','payload':{'session_id':thread}},
            {'type':'event_msg','payload':{'type':'token_count','info':{'total_token_usage':{'input_tokens':900,'cached_input_tokens':500,'cache_write_input_tokens':0,'output_tokens':100,'reasoning_output_tokens':0,'total_tokens':1000}}}},
            start,ctx,
        ]
        if overlap:rows.append({'type':'event_msg','payload':{'type':'task_started','turn_id':'other','started_at':200.2}})
        rows.extend([
            {'type':'token_usage_record','payload':{'thread_id':thread,'turn_id':turn,'turn_token_usage':delta,'thread_token_usage':{'input_tokens':912 if not reset else 700,'cached_input_tokens':506,'cache_write_input_tokens':0,'output_tokens':103,'reasoning_output_tokens':0,'total_tokens':end_counts}}},
            {'type':'event_msg','payload':{'type':'token_count','info':{'total_token_usage':{'input_tokens':912 if not reset else 700,'cached_input_tokens':506,'cache_write_input_tokens':0,'output_tokens':103,'reasoning_output_tokens':0,'total_tokens':end_counts}}}},
        ])
        if complete:rows.append(end)
        path.write_text('\n'.join(json.dumps(row) for row in rows)+'\n',encoding='utf-8')
        return path

    def test_turn_delta_matches_direct_turn_record(self):
        with tempfile.TemporaryDirectory() as folder:
            path=self.write_rollout(folder)
            result=read_turn_evidence(path,thread_id='thread-123',
                claimed_at=datetime.fromtimestamp(200,tz=timezone.utc),
                acknowledged_at=datetime.fromtimestamp(201,tz=timezone.utc),
                expected_model='gpt-6-luna',expected_thinking='low')
        self.assertEqual(result['usage']['total_tokens'],15)
        self.assertEqual(result['result'],'SESSION_BRIDGE_OK 42')
        self.assertEqual(result['turn_id'],'turn-123')

    def test_rejects_counter_reset_overlap_and_missing_completion(self):
        for flags in ({'reset':True},{'overlap':True},{'complete':False}):
            with self.subTest(flags=flags),tempfile.TemporaryDirectory() as folder:
                path=self.write_rollout(folder,**flags)
                with self.assertRaises(RolloutEvidenceError):
                    read_turn_evidence(path,thread_id='thread-123',
                        claimed_at=datetime.fromtimestamp(200,tz=timezone.utc),
                        acknowledged_at=datetime.fromtimestamp(201,tz=timezone.utc),
                        expected_model='gpt-6-luna',expected_thinking='low')

    def test_accepts_multiple_usage_records_in_one_turn(self):
        with tempfile.TemporaryDirectory() as folder:
            path=self.write_rollout(folder)
            rows=[json.loads(line) for line in path.read_text(encoding='utf-8').splitlines()]
            intermediate={'input_tokens':5,'cached_input_tokens':2,'cache_write_input_tokens':0,'output_tokens':1,'reasoning_output_tokens':0,'total_tokens':6}
            rows.insert(4,{'type':'token_usage_record','payload':{'thread_id':'thread-123','turn_id':'turn-123','turn_token_usage':intermediate}})
            path.write_text('\n'.join(json.dumps(row) for row in rows)+'\n',encoding='utf-8')
            result=read_turn_evidence(path,thread_id='thread-123',claimed_at=datetime.fromtimestamp(200,tz=timezone.utc),acknowledged_at=datetime.fromtimestamp(201,tz=timezone.utc),expected_model='gpt-6-luna',expected_thinking='low')
            self.assertEqual(result['usage']['total_tokens'],15)
