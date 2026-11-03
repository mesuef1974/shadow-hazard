"""Run a dedicated CLI execution, never reopen a desktop-owned thread."""
import json
import os
from pathlib import Path
import queue
import subprocess
import threading
import time
from datetime import datetime, timezone, timedelta
from execution_context.routing import capability_flags

class Unavailable(Exception):
    pass

class Uncertain(Exception):
    pass


def observed_model(thread_id):
    """Read only matching execution metadata; never return conversation content."""
    root=Path(os.environ.get('CODEX_HOME', Path.home()/'.codex'))/'sessions'
    today=datetime.now(timezone.utc)
    for date in (today,today-timedelta(days=1)):
        folder=root/date.strftime('%Y/%m/%d')
        for path in folder.glob(f'*{thread_id}.jsonl'):
            with path.open(encoding='utf-8') as stream:
                for line in stream:
                    try: event=json.loads(line)
                    except ValueError: continue
                    if event.get('type')=='turn_context':
                        return event.get('payload',{}).get('model','')
    return ''


def execute(binary, thread_id, model, effort, prompt, cwd, mode, timeout=600, on_started=None,selected_plugins=()):
    # thread_id identifies the parent charter only; it is deliberately NOT resumed.
    # Keep default instructions, AGENTS, rules and sandbox intact. Exclude general
    # host memory/catalogs and app connectors from this local execution only.
    command=[binary,'-a','never','exec','--sandbox',mode,'--model',model,
             *capability_flags(selected_plugins),
             '-c',f'model_reasoning_effort="{effort}"',
             '-c','sandbox_workspace_write.network_access=false','--json','-']
    try:
        process=subprocess.Popen(command,stdin=subprocess.PIPE,stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,text=True,encoding='utf-8',cwd=cwd,shell=False)
    except OSError as error: raise Unavailable() from error
    inbox=queue.Queue()
    deadline=time.monotonic()+timeout
    execution_id='';messages=[];completed=False;usage={}
    def reader():
        try:
            for line in process.stdout:
                try: inbox.put(json.loads(line))
                except ValueError: continue
        finally: inbox.put(None)
    reader_thread=threading.Thread(target=reader,daemon=True)
    reader_thread.start()
    try:
        process.stdin.write(prompt);process.stdin.close()
        while True:
            if time.monotonic()>=deadline:raise Uncertain()
            try: event=inbox.get(timeout=max(.01,deadline-time.monotonic()))
            except queue.Empty: raise Uncertain()
            if event is None: break
            if event.get('type')=='thread.started':
                import uuid
                execution_id=str(uuid.UUID(event['thread_id']))
                if on_started: on_started(execution_id)
            item=event.get('item',{})
            if event.get('type')=='item.completed' and item.get('type')=='agent_message':
                messages.append(item.get('text',''))
            if event.get('type')=='turn.completed':
                completed=True;usage=event.get('usage',{})
        code=process.wait(timeout=max(.01,deadline-time.monotonic()))
        if not execution_id: raise Unavailable()
        return {'completed':completed and code==0,'text':'\n\n'.join(messages)[-20000:],
                'thread_id':execution_id,'model':observed_model(execution_id),'usage':usage}
    except (OSError,subprocess.TimeoutExpired) as error:
        raise Uncertain() from error
    finally:
        if process.poll() is None:
            if os.name=='nt':
                try:
                    subprocess.run(['taskkill','/PID',str(process.pid),'/T','/F'],
                        stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=10,check=False)
                except (OSError,subprocess.TimeoutExpired):
                    process.kill()
            else:process.terminate()
        try: process.wait(timeout=5)
        except subprocess.TimeoutExpired: process.kill();process.wait(timeout=5)
        if not process.stdin.closed: process.stdin.close()
        reader_thread.join(timeout=.2)
        # A descendant holding the pipe must not hang the dispatcher cleanup.
        if not reader_thread.is_alive():process.stdout.close()
