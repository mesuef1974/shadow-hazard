"""Project-owned disposable containers. No Docker socket or host network in the worker."""
from pathlib import Path
import os
import subprocess
import uuid
from .readonly_transport import IsolationError

IMAGE='shadow-hazard-readonly:codex-0.166.0'


class Runtime:
    def __init__(self,root):
        token=uuid.uuid4().hex
        self.worker='shadow-readonly-worker-'+token;self.proxy='shadow-readonly-egress-'+token
        self.volume='shadow-readonly-proxy-'+token
        self.root=Path(root).resolve()
    def docker(self,*args):
        result=subprocess.run(['docker',*args],capture_output=True,text=True,encoding='utf-8',timeout=60)
        if result.returncode:raise IsolationError('فشل إعداد حاوية القراءة: '+result.stderr[:250])
        return result.stdout.strip()
    def start(self,command):
        auth=Path(os.environ.get('CODEX_HOME',Path.home()/'.codex'))/'auth.json'
        if not auth.is_file():raise IsolationError('تسجيل دخول Codex المحلي مطلوب')
        self.image_id=self.docker('image','inspect',IMAGE,'--format','{{.Id}}')
        self.docker('volume','create',self.volume)
        self.docker('run','--detach','--name',self.proxy,'--user','0:0','--read-only','--cap-drop','ALL',
            '--security-opt','no-new-privileges','--memory','128m','--pids-limit','32',
            '--mount',f'type=volume,src={self.volume},dst=/proxy','--entrypoint','node',self.image_id,'/opt/shadow/proxy.js')
        args=['docker','run','--rm','--interactive','--name',self.worker,'--network','none','--read-only',
            '--cap-drop','ALL','--security-opt','no-new-privileges','--security-opt','seccomp=unconfined','--pids-limit','128','--memory','1g','--cpus','2',
            '--tmpfs','/tmp:rw,nosuid,nodev,size=128m','--tmpfs','/run/codex:rw,nosuid,nodev,uid=1000,gid=1000,size=64m',
            '--mount',f'type=bind,src={self.root},dst=/workspace,readonly',
            '--mount',f'type=bind,src={auth.resolve()},dst=/run/codex/auth.json,readonly',
            '--mount',f'type=volume,src={self.volume},dst=/proxy,readonly',self.image_id,*command]
        return subprocess.Popen(args,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,
            text=True,encoding='utf-8',shell=False)
    def close(self):
        for name in (self.worker,self.proxy):
            subprocess.run(['docker','rm','--force',name],capture_output=True,timeout=30)
        subprocess.run(['docker','volume','rm',self.volume],capture_output=True,timeout=30)
