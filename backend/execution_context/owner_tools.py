"""MCP client for the installed, official Codex app-tools server (stdio)."""
import hashlib
import json
import os
from pathlib import Path
import queue
import subprocess
import threading
import time
import uuid
from django.conf import settings


class OwnerToolsError(RuntimeError):
    pass


def config_path():
    return settings.BASE_DIR / '.local' / 'owner-connector.json'


def load_config():
    try:
        value=json.loads(config_path().read_text(encoding='utf8'))
        server=Path(value['server'])
        if not value.get('enabled') or not Path(value['node']).is_file() or not server.is_file():
            raise ValueError()
        if hashlib.sha256(server.read_bytes()).hexdigest()!=value['server_sha256']:
            raise ValueError()
        if not value['pipe'].startswith('\\\\.\\pipe\\'):
            raise ValueError()
        uuid.UUID(value['caller_thread_id'])
        if type(value['project_id']) is not int or type(value['authorizing_user_id']) is not int:
            raise ValueError()
        return value
    except (OSError,KeyError,TypeError,ValueError) as exc:
        raise OwnerToolsError('إعداد موصل المالك غير متاح أو تغير إصدار الخادم؛ لا إرسال') from exc


def save_config(value, *, expected=None):
    """Serialize initialization and compare-and-replace renewal across processes."""
    from workspace.worker_lock import single_worker
    path=config_path()
    temporary=path.with_name(path.name+'.'+str(uuid.uuid4())+'.tmp')
    try:
        with single_worker(path.with_suffix('.lock')) as acquired:
            if not acquired:raise OwnerToolsError('إعداد المالك محجوز؛ لا استبدال')
            if expected is not None and load_config()!=expected:
                raise OwnerToolsError('تغير إعداد المالك أثناء تجديد القناة')
            temporary.write_text(json.dumps(value),encoding='utf8')
            temporary.replace(path)
    except OSError as exc:
        raise OwnerToolsError('تعذر حفظ قناة المالك بأمان') from exc
    finally:
        temporary.unlink(missing_ok=True)


class OwnerTools:
    def __init__(self, config=None, timeout=30):
        self.config=config or load_config()
        self.timeout=timeout;self.sequence=0;self.inbox=queue.Queue();self.tools={}
        env={key:os.environ[key] for key in ('PATH','SystemRoot','WINDIR','USERPROFILE','APPDATA','LOCALAPPDATA','TEMP','TMP') if key in os.environ}
        env['CODEX_APP_TOOLS_PIPE_PATH']=self.config['pipe']
        self.process=subprocess.Popen([self.config['node'],self.config['server']],stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,text=True,encoding='utf8',bufsize=1,env=env,shell=False)
        threading.Thread(target=self._read,daemon=True).start()
        try:
            self.rpc('initialize',{'protocolVersion':'2024-11-05','capabilities':{},
                'clientInfo':{'name':'shadow-owner-connector','version':'1.0.0'}})
            self._send({'jsonrpc':'2.0','method':'notifications/initialized'})
            self.tools={tool['name']:tool for tool in self.rpc('tools/list',{}).get('tools',[])}
            schema=self.tools.get('send_message_to_thread',{}).get('inputSchema',{}).get('properties',{})
            if not {'threadId','prompt','model','thinking'}<=set(schema):
                raise OwnerToolsError('أداة المالك لا تعرض التحكم المطلوب بالنموذج والتفكير')
        except Exception:
            self.close();raise

    def _read(self):
        try:
            for line in self.process.stdout:
                try:self.inbox.put(json.loads(line))
                except ValueError:self.inbox.put({'invalid':True})
        finally:self.inbox.put(None)

    def _send(self, payload):
        if self.process.poll() is not None:raise OwnerToolsError('توقف خادم موصل المالك')
        self.process.stdin.write(json.dumps(payload,ensure_ascii=False)+'\n');self.process.stdin.flush()

    def rpc(self,method,params):
        self.sequence+=1;request_id=self.sequence
        self._send({'jsonrpc':'2.0','id':request_id,'method':method,'params':params})
        deadline=time.monotonic()+self.timeout
        while time.monotonic()<deadline:
            try:event=self.inbox.get(timeout=max(.01,deadline-time.monotonic()))
            except queue.Empty as exc:raise OwnerToolsError('مهلة موصل المالك؛ لا إعادة إرسال تلقائية') from exc
            if event is None or event.get('invalid'):raise OwnerToolsError('استجابة موصل المالك غير صالحة')
            if event.get('id')!=request_id:continue
            if 'error' in event:raise OwnerToolsError('رفض المضيف استدعاء موصل المالك')
            return event.get('result',{})
        raise OwnerToolsError('مهلة موصل المالك')

    def call(self,name,arguments,*,call_id,context_id):
        if name not in {'read_thread','send_message_to_thread'} or name not in self.tools:
            raise OwnerToolsError('أداة غير مسموحة في موصل العامل')
        result=self.rpc('tools/call',{'name':name,'arguments':arguments,'_meta':{
            'codexThreadId':self.config['caller_thread_id'],'codexTurnId':context_id,'codexCallId':call_id}})
        if result.get('isError'):raise OwnerToolsError('رفض اتصال المالك العملية؛ لا إعادة إرسال تلقائية')
        try:
            parts=[part['text'] for part in result['content'] if part.get('type')=='text']
            if len(parts)!=1:raise ValueError()
            return json.loads(parts[0])
        except (ValueError,TypeError,KeyError) as exc:raise OwnerToolsError('إيصال المالك غير صالح') from exc

    def close(self):
        if self.process.poll() is None:
            self.process.terminate()
            try:self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:self.process.kill();self.process.wait(timeout=5)
        for stream in (self.process.stdin,self.process.stdout):
            try:stream.close()
            except OSError:pass

    def __enter__(self):return self
    def __exit__(self,*args):self.close()
