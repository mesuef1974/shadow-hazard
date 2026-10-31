"""Owned App Server runtime; no access to desktop-owned threads or writable tools."""
import json
import os
from pathlib import Path
import queue
import subprocess
import sys
import threading
import time
from .routing import capability_flags


class IsolationError(RuntimeError):
    pass


PROFILE='shadow-readonly'


class ReadOnlyServer:
    def __init__(self,binary,root):
        self.root=Path(root).resolve();self.events=[];self.sequence=0;self.inbox=queue.Queue()
        from .docker_runtime import Runtime
        self.runtime=Runtime(self.root)
        filesystem={':minimal':'read','/workspace':'read','/run/codex':'deny','/proxy':'deny'}
        table='{'+','.join(json.dumps(key)+'='+json.dumps(value) for key,value in filesystem.items())+'}'
        options=['--disable','apps','--disable','plugins','--disable','hooks','--disable','in_app_browser',
            '--disable','memories','-c','notify=[]','-c','web_search="disabled"',
            '-c',f'default_permissions="{PROFILE}"','-c',f'permissions.{PROFILE}.filesystem={table}',
            '-c',f'permissions.{PROFILE}.network.enabled=false',
            '-c','shell_environment_policy.inherit="none"',
            '-c','shell_environment_policy.set.PATH="/usr/local/bin:/usr/bin:/bin"',
            '-c','approval_policy="never"']
        try:self.process=self.runtime.start(['codex',*options,'app-server','--listen','stdio://'])
        except Exception:self.runtime.close();raise
        self.cwd='/workspace';self.errors=[]
        def errors():
            for line in self.process.stderr:self.errors.append(line.strip()[:300])
        threading.Thread(target=errors,daemon=True).start()
        def reader():
            try:
                for line in self.process.stdout:
                    try:self.inbox.put(json.loads(line))
                    except ValueError:continue
            finally:self.inbox.put(None)
        self.reader=threading.Thread(target=reader,daemon=True);self.reader.start()
        try:
            self.rpc('initialize',{'clientInfo':{'name':'shadow_readonly','version':'1'},'capabilities':{'experimentalApi':True}})
            self.send({'method':'initialized'})
        except Exception:
            self.close();raise

    def send(self,value):
        self.process.stdin.write(json.dumps(value)+'\n');self.process.stdin.flush()

    def receive(self,timeout):
        try:event=self.inbox.get(timeout=timeout)
        except queue.Empty:raise IsolationError('انتهت مهلة خادم القراءة المعزول')
        if event is None:raise IsolationError('توقف خادم القراءة المعزول؛ راجع إعداد الصورة والعزل')
        if 'method' in event and 'id' in event:
            self.send({'id':event['id'],'error':{'code':-32601,'message':'No approval or external tool service is available'}})
            raise IsolationError('رفض طلب صلاحية أو أداة خارجية من العامل')
        return event

    def rpc(self,method,params,timeout=60):
        self.sequence+=1;request_id=self.sequence
        self.send({'id':request_id,'method':method,'params':params})
        deadline=time.monotonic()+timeout
        while True:
            event=self.receive(max(.01,deadline-time.monotonic()))
            if event.get('id')==request_id:
                if 'error' in event:raise IsolationError(str(event['error'].get('message','App Server error'))[:500])
                return event.get('result',{})
            self.events.append(event)

    def command(self,argv):
        return self.rpc('command/exec',{'command':argv,'cwd':self.cwd,'permissionProfile':PROFILE,'timeoutMs':20000},timeout=30)

    def close(self):
        self.runtime.close()
        try:self.process.wait(timeout=15)
        except subprocess.TimeoutExpired:self.process.kill();self.process.wait(timeout=5)

    def execute(self,prompt,model,thinking,timeout=600):
        thread=self.rpc('thread/start',{'model':model,'cwd':self.cwd,'approvalPolicy':'never',
            'permissions':PROFILE,'ephemeral':True})
        if thread.get('model')!=model:raise IsolationError('اختلاف نموذج عامل القراءة الفعلي')
        thread_id=thread['thread']['id']
        result=self.rpc('turn/start',{'threadId':thread_id,'input':[{'type':'text','text':prompt}],
            'model':model,'effort':thinking,'approvalPolicy':'never','permissions':PROFILE})
        turn_id=result['turn']['id'];deadline=time.monotonic()+timeout;usage=None;messages=[];tools=[]
        while time.monotonic()<deadline:
            event=self.events.pop(0) if self.events else self.receive(max(.01,deadline-time.monotonic()))
            method=event.get('method');params=event.get('params',{})
            if params.get('threadId')!=thread_id:continue
            if method=='thread/tokenUsage/updated' and params.get('turnId')==turn_id:usage=params.get('tokenUsage',{}).get('total')
            if method=='item/completed' and params.get('turnId')==turn_id:
                item=params.get('item',{})
                if item.get('type')=='agentMessage':messages.append(item.get('text',''))
                elif item.get('type')=='commandExecution':
                    import hashlib
                    tools.append({'item_id':item.get('id'),'exit_code':item.get('exitCode'),'status':item.get('status'),
                        'command_sha256':hashlib.sha256(str(item.get('command','')).encode()).hexdigest()})
            if method=='turn/completed' and params.get('turn',{}).get('id')==turn_id:
                if params['turn'].get('status')!='completed':raise IsolationError('لم يكتمل دور القراءة المعزول')
                fields={'input_tokens':'inputTokens','cached_input_tokens':'cachedInputTokens','output_tokens':'outputTokens','reasoning_output_tokens':'reasoningOutputTokens','total_tokens':'totalTokens'}
                if not usage or any(type(usage.get(key)) is not int for key in fields.values()):raise IsolationError('غياب دليل استهلاك الدور المعزول')
                counts={key:usage[value] for key,value in fields.items()}
                if counts['total_tokens']!=counts['input_tokens']+counts['output_tokens']:raise IsolationError('عدادات الدور غير متسقة')
                return {'thread_id':thread_id,'turn_id':turn_id,'text':'\n'.join(messages),'usage':counts,'model':thread['model'],'thinking':thinking,'tool_evidence':tools}
        raise IsolationError('مهلة الدور؛ لا إعادة تلقائية')
