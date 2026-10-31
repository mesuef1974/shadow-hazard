"""Bounded stdio connection to the existing local Codex daemon, never a new writer."""
import json
import queue
import subprocess
import threading
import time


class Unavailable(Exception):
    pass


class Uncertain(Exception):
    pass


def execute(binary, thread_id, model, effort, prompt, cwd, mode, timeout=600):
    process = subprocess.Popen([binary, 'app-server', 'proxy'], stdin=subprocess.PIPE,
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, encoding='utf-8',
        cwd=cwd, shell=False)
    inbox = queue.Queue()
    submitted = False
    deadline = time.monotonic() + timeout

    def reader():
        for line in process.stdout:
            try:
                inbox.put(json.loads(line))
            except json.JSONDecodeError:
                continue
        inbox.put(None)

    threading.Thread(target=reader, daemon=True).start()

    def send(message):
        process.stdin.write(json.dumps(message) + '\n')
        process.stdin.flush()

    def receive(limit=30):
        try:
            event = inbox.get(timeout=max(.01, min(limit, deadline-time.monotonic())))
        except queue.Empty:
            raise Uncertain() if submitted else Unavailable()
        if event is None:
            raise Uncertain() if submitted else Unavailable()
        # Never grant approvals or answer user questions automatically.
        if 'method' in event and 'id' in event:
            raise Uncertain()
        return event

    def request(number, method, params):
        send({'id': number, 'method': method, 'params': params})
        while True:
            event = receive()
            if event.get('id') == number:
                if 'error' in event:
                    raise Uncertain() if submitted else Unavailable()
                return event.get('result', {})

    try:
        request(1, 'initialize', {'clientInfo': {'name': 'shadow_dispatcher', 'version': '0.1'}})
        send({'method': 'initialized', 'params': {}})
        info = request(2, 'thread/resume', {'threadId': thread_id, 'excludeTurns': True})
        if info.get('thread', {}).get('status', {}).get('type') != 'idle':
            raise Unavailable()
        sandbox = {'type': 'readOnly', 'networkAccess': False}
        if mode == 'workspace-write':
            sandbox = {'type': 'workspaceWrite', 'networkAccess': False, 'writableRoots': [str(cwd)]}
        submitted = True
        result = request(3, 'turn/start', {'threadId': thread_id, 'model': model,
            'effort': effort, 'cwd': str(cwd), 'approvalPolicy': 'on-request',
            'sandboxPolicy': sandbox, 'input': [{'type': 'text', 'text': prompt}]})
        turn_id = result.get('turn', {}).get('id')
        if not turn_id:
            raise Uncertain()
        messages = []
        while time.monotonic() < deadline:
            event = receive(limit=timeout)
            params = event.get('params', {})
            if params.get('threadId') != thread_id:
                continue
            if event.get('method') == 'item/completed' and params.get('turnId') == turn_id:
                item = params.get('item', {})
                if item.get('type') == 'agentMessage':
                    messages.append(item.get('text', ''))
            if event.get('method') == 'turn/completed' and params.get('turn', {}).get('id') == turn_id:
                return {'completed': params['turn'].get('status') == 'completed',
                        'text': '\n\n'.join(messages)[-20000:], 'turn_id': turn_id}
        raise Uncertain()
    except (OSError, BrokenPipeError):
        raise Uncertain() if submitted else Unavailable()
    finally:
        if process.poll() is None:
            process.terminate()
        process.wait(timeout=5)
        for stream in (process.stdin, process.stdout):
            stream.close()
