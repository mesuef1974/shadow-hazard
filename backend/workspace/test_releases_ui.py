"""UI-only checks: no release records or deployment transport is created."""
import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

from django.template.loader import render_to_string
from django.test import RequestFactory, SimpleTestCase
from django.urls import resolve


class ReleasesUiTests(SimpleTestCase):
    def test_current_backend_route_renders_the_owned_template_without_database_writes(self):
        request = RequestFactory().get('/sessions/releases/')
        request.user = SimpleNamespace(role='supervisor', pk=7, is_authenticated=True)
        match = resolve('/sessions/releases/')
        response = match.func(request, **match.kwargs)
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'release-app', response.content)
        self.assertIn(b'data-api-root="/releases/"', response.content)
        self.assertEqual(response['Cache-Control'], 'private, no-cache')

    def render(self, role='supervisor', authenticated=True):
        user = SimpleNamespace(role=role, pk=7, is_authenticated=authenticated)
        request = RequestFactory().get('/release-board/')
        request.user = user
        return render_to_string('workspace/releases.html', {
            'user': user, 'request': request, 'csrf_token': 'ui-test-token',
            'release_api_root': '/releases/',
        })

    def test_central_shell_accessibility_and_safe_defaults(self):
        html = self.render()
        for marker in ['dir="rtl"', 'registry/releases.css', 'registry/releases.js',
                       'id="theme-toggle"', 'تميمة ظل الخطر', 'role="status"',
                       'aria-live="polite"', 'id="release-unknown"',
                       'id="release-stages"', 'csrf-token', 'البيانات لا تُستعاد تلقائيًا']:
            self.assertIn(marker, html)
        self.assertEqual(html.count('<h1'), 1)
        self.assertEqual(html.count('<main'), 1)
        self.assertNotIn('<details open', html)
        self.assertNotIn('type="password"', html)
        self.assertIn('pattern="[0-9a-f]{64}"', html)
        self.assertIn('data-api-root="/releases/"', html)

    def test_student_and_guest_do_not_receive_supervisor_controls(self):
        student = self.render('student')
        for marker in ['id="release-deploy"', 'id="release-approve"',
                       'id="release-add-evidence"', 'data-action="merge"']:
            self.assertNotIn(marker, student)
            self.assertIn(marker, self.render())
        guest = self.render('student', False)
        self.assertNotIn('id="release-create"', guest)
        self.assertNotIn('id="release-controls"', guest)

    def test_node_gates_payloads_and_network_contract(self):
        script = Path(__file__).resolve().parents[1] / 'registry/static/registry/releases.js'
        code = r'''
const assert=require('node:assert/strict');
const ui=require(SCRIPT);
const r={id:2,author_id:7,commit:'a'.repeat(40),fingerprint:'b'.repeat(64),environment:'test',state:'pinned'};
const detail={release:r,evidence:['tests','migration','settings'].map((kind,id)=>({kind,id,fingerprint:r.fingerprint,accepted:true})),reviews:[{id:1,fingerprint:r.fingerprint,reviewer_id:9,accepted:true}],merges:[],approvals:[]};
assert.equal(ui.gates(detail,7,'student').merge,false);
assert.equal(ui.gates(detail,9,'student').reviewer,true);
assert.equal(ui.gates(detail,7,'supervisor').merge,true);
detail.reviews.push({id:2,fingerprint:r.fingerprint,reviewer_id:9,accepted:false});
assert.equal(ui.gates(detail,7,'supervisor').merge,false);
detail.reviews.pop(); detail.evidence.push({id:99,kind:'tests',fingerprint:r.fingerprint,accepted:false});
assert.equal(ui.gates(detail,7,'supervisor').merge,false);
detail.evidence.pop(); r.state='unknown';
assert.equal(ui.gates(detail,7,'supervisor').blocked,true);
r.state='unexpected-new-state'; assert.equal(ui.gates(detail,7,'supervisor').blocked,true);
r.state='draft'; assert.equal(ui.gates(detail,7,'student').author,true);
assert.equal(ui.gates(detail,9,'student').author,false);
r.state='merged'; detail.merges=[{state:'merged',result_commit:r.commit}];
assert.equal(ui.gates(detail,7,'supervisor').deploy,true);
assert.equal(ui.gates({...detail,merges:[]},7,'supervisor').deploy,false);
assert.equal(ui.hash('secret-value'),'غير متاح');
assert.equal(ui.hash('a'.repeat(40)),'a'.repeat(40));
const values={commit:r.commit,target_commit:r.commit,repository:'test',environment:'test',source_ref:'refs/heads/feature',target_branch:'refs/heads/main',source_type:'task_id',source_id:'3',artifact_sha256:r.fingerprint,migration_plan_sha256:r.fingerprint,settings_sha256:r.fingerprint};
const payload=ui.createPayload(values);assert.equal(payload.task_id,3);
assert.deepEqual(Object.keys(payload.manifest).sort(),['artifact_sha256','migration_plan_sha256','settings_sha256']);
(async()=>{
 let calls=0;
 const result=await ui.request('/releases/2/actions/',{body:{action:'pin',key:'unique'},key:'unique',csrf:'csrf',fetcher:async(url,options)=>{
   calls++;assert.equal(options.method,'POST');assert.equal(options.headers['X-CSRFToken'],'csrf');assert.equal(options.headers['Idempotency-Key'],'unique');assert.equal(JSON.parse(options.body).key,'unique');
   return {ok:true,json:async()=>({event_id:1})};
 }});assert.equal(result.event_id,1);assert.equal(calls,1);
 await ui.request('/releases/',{body:payload,key:'draft-unique',csrf:'csrf',fetcher:async(url,options)=>{
   assert.equal(options.headers['Idempotency-Key'],'draft-unique');
   assert.equal(JSON.parse(options.body).task_id,3);
   assert.equal(Object.hasOwn(JSON.parse(options.body),'key'),false);
   return {ok:true,json:async()=>({id:3})};
 }});
 await assert.rejects(ui.request('/releases/2/actions/',{body:{action:'pin'},fetcher:async()=>{calls++;throw Error('network');}}),e=>e.unknown===true);
 assert.equal(calls,2); // no automatic resend
 await assert.rejects(ui.request('/releases/',{fetcher:async()=>{throw Error('network');}}),e=>e.unknown===false);
 await assert.rejects(ui.request('/releases/2/actions/',{body:{},fetcher:async()=>({ok:false,status:403})}),e=>e.unknown===false);
 await assert.rejects(ui.request('/releases/2/actions/',{body:{},fetcher:async()=>({ok:false,status:500})}),e=>e.unknown===true);
 let timeouts=0;
 await assert.rejects(ui.request('/releases/2/actions/',{body:{},timeout:5,fetcher:async(url,{signal})=>{timeouts++;return new Promise((resolve,reject)=>signal.addEventListener('abort',()=>reject(Error('timeout'))));}}),e=>e.unknown===true);
 assert.equal(timeouts,1);
 console.log('release UI gates and single-send network contract: PASS');
})().catch(e=>{console.error(e);process.exitCode=1;});
'''.replace('SCRIPT', json.dumps(str(script)))
        result = subprocess.run(['node', '-e', code], capture_output=True, text=True,
                                encoding='utf-8', timeout=20)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_scoped_responsive_styles_and_allowlisted_rendering(self):
        root = Path(__file__).resolve().parents[1] / 'registry/static/registry'
        css = (root / 'releases.css').read_text(encoding='utf-8')
        js = (root / 'releases.js').read_text(encoding='utf-8')
        self.assertIn('var(--platform-text)', css)
        self.assertIn('max-width:700px', css)
        self.assertIn('minmax(0,1fr)', css)
        self.assertNotIn('innerHTML', js)
        self.assertNotIn('row.external_id', js)
        self.assertNotIn('row.details', js)
        self.assertIn('sessionStorage.setItem', js)
        self.assertIn('result.state===\'unknown\'', js)
