"""Idempotent native LM Studio startup; never installs or downloads models."""
import json
import os
import subprocess
from pathlib import Path

MODEL = 'qwen3-vl-4b-instruct'
API_PORT = 1234


def command(lms, *args, as_json=False):
    result = subprocess.run([str(lms), *args], capture_output=True, text=True,
        encoding='utf-8', errors='replace', timeout=180,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
    if result.returncode:
        raise RuntimeError(f'LM Studio command failed: {args[0]} ({result.returncode})')
    return json.loads(result.stdout) if as_json else None


def ensure_model(lms=None):
    lms = Path(lms) if lms else Path.home() / '.lmstudio' / 'bin' / 'lms.exe'
    if not lms.is_file():
        raise RuntimeError('Installed LM Studio CLI not found')
    daemon = command(lms, 'daemon', 'status', '--json', as_json=True)
    if daemon.get('status') != 'running':
        command(lms, 'daemon', 'up', '--json', as_json=True)
    server = command(lms, 'server', 'status', '--json', as_json=True)
    if not server.get('running'):
        command(lms, 'server', 'start', '--port', str(API_PORT), '--bind', '127.0.0.1')
    elif server.get('port') != API_PORT:
        raise RuntimeError('LM Studio is serving another port; existing service preserved')
    loaded = command(lms, 'ps', '--json', as_json=True)
    if not any(row.get('identifier') == MODEL and row.get('type') == 'llm' for row in loaded):
        installed = command(lms, 'ls', '--json', as_json=True)
        if not any(row.get('modelKey') == MODEL for row in installed):
            raise RuntimeError('Required model is not installed; automatic download disabled')
        command(lms, 'load', MODEL, '--identifier', MODEL,
            '--context-length', '8192', '--parallel', '1', '--yes')
    loaded = command(lms, 'ps', '--json', as_json=True)
    model = next((row for row in loaded if row.get('identifier') == MODEL and row.get('type') == 'llm'), None)
    if not model:
        raise RuntimeError('Model load could not be verified')
    server = command(lms, 'server', 'status', '--json', as_json=True)
    if not server.get('running') or server.get('port') != API_PORT:
        raise RuntimeError('Local model server is not ready')
    return dict(model=MODEL, model_api='http://127.0.0.1:1234',
        platform='http://127.0.0.1:8011', context_length=model.get('contextLength'),
        daemon_pid=daemon.get('pid'), native_runtime=True)
