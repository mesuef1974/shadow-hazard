"""Snapshot, prove OS write isolation, then run one owned read-only turn."""
import hashlib
import json
from pathlib import Path
import socket
import sqlite3
import subprocess
import sys
import tempfile
import threading
from django.conf import settings
from workspace.memory import instructions
from .readonly_transport import ReadOnlyServer,IsolationError


def response_only_profile(plan):
    profile=plan.manifest.get('readonly_tool_profile','project-read')
    if profile not in {'project-read','response-only'}:
        raise IsolationError('ملف أدوات القراءة غير معروف')
    if profile=='response-only' and (plan.kind!='text' or plan.skills or plan.plugins):
        raise IsolationError('ملف الرد فقط لا يقبل مهمة تحتاج مهارات أو موصلات أو كود')
    return profile=='response-only'


def snapshot(project,root,instruction_text=None):
    source=settings.BASE_DIR.parent.resolve()
    result=subprocess.run(['git','-c','core.quotePath=false','ls-files','-z','--cached','--others','--exclude-standard'],cwd=source,
        capture_output=True,text=True,encoding='utf-8',check=True)
    allowed={'.py','.html','.css','.js','.md','.toml','.txt'};copied={}
    for name in result.stdout.split('\0'):
        relative=Path(name);path=source/relative
        if relative.is_absolute() or '..' in relative.parts or any(part in {'.git','.local','.venv','.codex','.agents','node_modules','__pycache__'} for part in relative.parts):continue
        if relative.suffix=='.md' and relative.name!='AGENTS.md' and relative.as_posix() not in {'docs/architecture.md','docs/safety-and-privacy.md'}:continue
        if relative.suffix not in allowed or relative.name.startswith('.env'):continue
        if path.is_symlink() or not path.is_file() or path.stat().st_size>500000:continue
        try:path.resolve().relative_to(source)
        except ValueError:continue
        data=path.read_bytes();target=root/relative;target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(data)
        copied[name]=hashlib.sha256(data).hexdigest()
    # Export project data, not the operational database or authentication/session tables.
    from workspace.models import ProjectMemory,Phase,Task,Decision
    from .models import SessionRoutingPolicy
    records={'project':list(type(project).objects.filter(pk=project.pk).values()),
        'memory':list(ProjectMemory.objects.filter(project=project,enabled=True).values('key','instruction')),
        'phases':list(Phase.objects.filter(project=project).values('id','title','objective','order')),
        'tasks':list(Task.objects.filter(phase__project=project).values('id','phase_id','title','description','state')),
        'decisions':list(Decision.objects.filter(project=project).values('title','body','private')),
        'routing':list(SessionRoutingPolicy.objects.values())}
    with sqlite3.connect(root/'project_data.sqlite3') as db:
        db.execute('CREATE TABLE project_records (kind TEXT PRIMARY KEY,payload TEXT NOT NULL)')
        db.executemany('INSERT INTO project_records VALUES (?,?)',[(key,json.dumps(value,ensure_ascii=False,default=str)) for key,value in records.items()])
    db.close()
    memory,digest=instructions(project)
    if instruction_text is None:
        from .routing import instruction_files
        instruction_text='\n\n'.join([memory,*[path.read_text(encoding='utf-8-sig') for path in instruction_files()]])
    (root/'AGENTS.md').write_text(instruction_text+'\nهذه نسخة قراءة معزولة. لا تشغل خدمات أو تحاول تعديل الملفات. أعد النتيجة في الرد فقط؛ يوثقها العامل المضيف في قاعدة المشروع.\n',encoding='utf-8')
    copied['AGENTS.md']=hashlib.sha256((root/'AGENTS.md').read_bytes()).hexdigest()
    return {'files':copied,'project_memory_digest':digest,'instruction_sha256':hashlib.sha256(instruction_text.encode()).hexdigest(),'database':'project_data.sqlite3','database_scope':list(records)}


def probe(server,root):
    sentinel=root/'isolation_canary.txt';sentinel.write_text('UNCHANGED',encoding='utf-8')
    # This sibling is in the denied original project root; contains no real data.
    with tempfile.TemporaryDirectory(prefix='readonly-canary-',dir=settings.LOCAL_DIR) as folder:
        outside=Path(folder)/'outside.txt';outside.write_text('PRIVATE_CANARY',encoding='utf-8')
        listener=socket.socket();listener.bind(('127.0.0.1',0));listener.listen(1)
        port=listener.getsockname()[1];connected=threading.Event();listener.settimeout(5)
        def accept_probe():
            try:
                connection,_=listener.accept();connected.set()
                with connection:connection.sendall(b'HTTP/1.1 200 OK\r\nContent-Length: 2\r\nConnection: close\r\n\r\nOK')
            except OSError:pass
        threading.Thread(target=accept_probe,daemon=True).start()
        script="""import json,pathlib,socket,sqlite3
r={};p=pathlib.Path('isolation_canary.txt');r['read']=p.read_text()=='UNCHANGED'
operations=[('write',lambda:p.write_text('BAD')),('delete',lambda:p.unlink()),('outside_read',lambda:pathlib.Path(OUTSIDE).read_text()),('outside_write',lambda:pathlib.Path(OUTSIDE).write_text('BAD')),('database_write',lambda:sqlite3.connect('project_data.sqlite3').execute('DELETE FROM project_records')),('credentials_read',lambda:pathlib.Path('/run/codex/auth.json').read_text()),('credentials_write',lambda:pathlib.Path('/run/codex/auth.json').write_text('BAD')),('root_write',lambda:pathlib.Path('/etc/shadow_canary').write_text('BAD')),('network',lambda:socket.create_connection(('host.docker.internal',PORT),timeout=2)),('direct_network',lambda:socket.create_connection(('1.1.1.1',443),timeout=2))]
for name,action in operations:
 try:action();r[name]=False
 except (OSError,sqlite3.Error):r[name]=True
print(json.dumps(r))
""".replace('OUTSIDE',repr(str(outside))).replace('PORT',str(port))
        try:
            result=server.command(['/usr/bin/python3','-c',script])
            if result.get('exitCode')!=0:raise IsolationError('فشل تشغيل فحص العزل: '+result.get('stderr','')[:600])
            evidence=json.loads(result['stdout'].strip())
            if connected.is_set():raise IsolationError('فشل منع الشبكة داخل العزل')
            if not all(evidence.get(key) is True for key in ('read','write','delete','outside_read','outside_write','database_write','credentials_read','credentials_write','root_write','network','direct_network')):
                raise IsolationError('لم تثبت جميع حدود العزل؛ لن يبدأ النموذج')
            if not sentinel.exists() or sentinel.read_text()!='UNCHANGED' or outside.read_text()!='PRIVATE_CANARY':raise IsolationError('تغيرت ملفات فحص العزل')
            return evidence
        finally:listener.close()


def execute(job,binary):
    response_only=response_only_profile(job.task_context)
    with tempfile.TemporaryDirectory(prefix='shadow-readonly-task-') as folder:
        root=Path(folder);manifest=snapshot(job.charter.project,root,job.task_context.instructions)
        server=ReadOnlyServer(binary,root,response_only=response_only)
        try:
            isolation=probe(server,root)
            target=job.task_context.manifest.get('readonly_target',{})
            charter=target.get('scope',job.charter.scope)+'\n'+target.get('rules',job.charter.rules)
            prompt=f'مهمة مفوضة ومعتمدة من سفيان، رقم الطلب {job.pk}. ملف AGENTS.md يحمل تعليمات المشروع والذاكرة المحملة من المضيف كاملة. لا تشغل أوامر project_memory أو session_brief داخل النسخة؛ المضيف يتولى التوثيق والتحقق. أدوات القراءة متاحة عند حاجة المهمة فقط؛ لا تفحص الملفات أو قاعدة البيانات دون حاجة.\nميثاق المهمة:\n'+charter+'\nالمهمة للقراءة فقط. أعد الرد فقط؛ لا تنشئ تقريرًا أو ملفًا.\n'+job.prompt+'\n'+job.task_context.skill_text
            if response_only:
                prompt=prompt.replace('أدوات القراءة متاحة عند حاجة المهمة فقط؛ لا تفحص الملفات أو قاعدة البيانات دون حاجة.','وضع رد فقط دون أدوات؛ جميع التعليمات الأساسية محملة، ولا تحتاج المهمة فحص ملفات أو قواعد بيانات.')
            before={str(path.relative_to(root)):hashlib.sha256(path.read_bytes()).hexdigest() for path in root.rglob('*') if path.is_file()}
            response=server.execute(prompt,job.requested_model,job.requested_thinking)
            after={str(path.relative_to(root)):hashlib.sha256(path.read_bytes()).hexdigest() for path in root.rglob('*') if path.is_file()}
            if before!=after:raise IsolationError('تغيرت نسخة القراءة؛ لا اعتماد للنتيجة')
            if response_only and response.get('tool_evidence'):
                raise IsolationError('طلب الرد فقط نفذ أداة؛ لا اعتماد نتيجة مخالفة لملف الأدوات')
            response['evidence']={'tool_profile':'response-only' if response_only else 'project-read','isolation':isolation,'tool_evidence':response.get('tool_evidence',[]),'snapshot':manifest,'transport':'docker-owned-app-server',
                'permissions':'shadow-readonly','image_id':server.runtime.image_id,'network':'none; OpenAI-only unix-socket egress relay','seccomp':'unconfined for nested user namespaces; capabilities dropped, no-new-privileges and nested bwrap enforced','approval_policy':'never',
                'read_scope':'Only project snapshot bind mount; credentials denied to commands by Linux permission profile; no host filesystem mount'}
            return response
        finally:server.close()
