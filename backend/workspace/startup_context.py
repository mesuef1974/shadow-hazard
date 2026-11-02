"""Aggregate local startup diagnostics without retaining prompt contents."""
import hashlib
import json
import subprocess
import os
from pathlib import Path
import tomllib


def fingerprint(text):
    return {'characters': len(text), 'utf8_bytes': len(text.encode('utf-8')),
            'sha256': hashlib.sha256(text.encode('utf-8')).hexdigest()}


def inventory(items):
    layers = []
    for index, item in enumerate(items):
        layer = {'index': index, 'type': item.get('type'), 'role': item.get('role')}
        if item.get('type') == 'additional_tools':
            layer['tools'] = [{'namespace': tool.get('name'),
                'names': [child.get('name') for child in tool.get('tools', [])],
                **fingerprint(json.dumps(tool, ensure_ascii=False, sort_keys=True))}
                for tool in item.get('tools', [])]
        elif item.get('type') == 'message':
            text = '\n'.join(block.get('text', '') for block in item.get('content', [])
                             if isinstance(block, dict))
            layer['text'] = fingerprint(text)
        layers.append(layer)
    return {'rendered_input': fingerprint(json.dumps(items, ensure_ascii=False, sort_keys=True)),
            'layers': layers, 'coverage': 'debug prompt-input; not a complete desktop request',
            'token_count': None}


def agent_options(agents_enabled, profile=None):
    if profile and not agents_enabled:
        return ['--profile', profile]
    return ['-c', f'agents.enabled={str(agents_enabled).lower()}']


def validate_profile(profile):
    if not profile or not all(c.isalnum() or c in '-_' for c in profile):
        raise ValueError('A named minimal startup profile is required')
    path = Path(os.environ.get('CODEX_HOME', Path.home() / '.codex')) / (profile + '.config.toml')
    data = tomllib.loads(path.read_text(encoding='utf-8-sig'))
    if data != {'agents': {'enabled': False}}:
        raise ValueError('Comparison profile must change only agents.enabled=false')
    return fingerprint(path.read_text(encoding='utf-8-sig'))


def run_process(*args, **kwargs):
    try:
        return subprocess.run(*args, **kwargs)
    except (subprocess.SubprocessError, OSError):
        raise ValueError('Startup subprocess failed; command and raw diagnostics withheld') from None


def validate_usage(usage):
    if not isinstance(usage, dict) or not {'input_tokens', 'output_tokens'} <= usage.keys():
        raise ValueError('Missing startup usage counters')
    for name in ['input_tokens', 'output_tokens', 'cached_input_tokens',
                 'cache_write_input_tokens', 'reasoning_output_tokens', 'total_tokens']:
        if name in usage and (type(usage[name]) is not int or usage[name] < 0):
            raise ValueError('Invalid startup usage counter')
    if (usage['input_tokens'] <= 0 or usage.get('cached_input_tokens', 0) > usage['input_tokens']
            or usage.get('reasoning_output_tokens', 0) > usage['output_tokens']
            or ('total_tokens' in usage and usage['total_tokens'] != usage['input_tokens'] + usage['output_tokens'])):
        raise ValueError('Inconsistent startup usage counters')


def render(binary, cwd, prompt, *, agents_enabled, profile=None):
    result = run_process([str(binary), *agent_options(agents_enabled, profile),
        'debug', 'prompt-input', prompt], cwd=cwd, capture_output=True, text=True,
        encoding='utf-8', timeout=60, check=False)
    if result.returncode:
        raise ValueError(f'Startup rendering failed (exit {result.returncode}); raw diagnostics withheld')
    return inventory(json.loads(result.stdout))


def measure(binary, cwd, prompt, *, agents_enabled, model, profile=None):
    # A short, explicitly requested measurement. Ephemeral sessions never enter
    # the desktop sidebar or replace any charter/thread binding.
    result = run_process([str(binary), '-a', 'never', '-c', 'notify=[]',
        *agent_options(agents_enabled, profile),
        '-c', 'model_reasoning_effort="low"', 'exec', '--ephemeral', '--json',
        '--sandbox', 'read-only', '--model', model, '-'], input=prompt, cwd=cwd,
        capture_output=True, text=True, encoding='utf-8', timeout=120, check=False)
    usage = None
    tool_calls = 0
    item_types = {}
    response = []
    completed_turns = 0
    errors = []
    attempt_id = None
    unknown_items = 0
    for line in result.stdout.splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if event.get('type') == 'thread.started':
            attempt_id = event.get('thread_id')
        if event.get('type') == 'turn.completed':
            usage = event.get('usage')
            completed_turns += 1
        if event.get('type') == 'item.completed':
            item = event.get('item', {})
            item_type = item.get('type', 'unknown')
            item_types[item_type] = item_types.get(item_type, 0) + 1
            if item.get('type') == 'agent_message':
                response.append(item.get('text', ''))
            elif item_type == 'error':
                message = item.get('message', '')
                errors.append({'fingerprint': fingerprint(message),
                    'categories': [word for word in ['mcp', 'plugin', 'config', 'sandbox', 'network', 'skill']
                                   if word in message.lower()]})
            elif item_type in {'command_execution', 'file_change', 'mcp_tool_call', 'web_search', 'todo_list'}:
                tool_calls += 1
            elif item_type != 'reasoning':
                unknown_items += 1
    valid_usage = True
    try:
        validate_usage(usage)
    except ValueError:
        valid_usage = False
    usage = ({key: value for key, value in usage.items() if key in {
        'input_tokens', 'output_tokens', 'cached_input_tokens', 'cache_write_input_tokens',
        'reasoning_output_tokens', 'total_tokens'}} if valid_usage else None)
    accepted = (result.returncode == 0 and completed_turns == 1 and valid_usage
                and not tool_calls and not errors and not unknown_items
                and '\n'.join(response).strip() == 'CONTEXT_READY')
    return {'usage': usage, 'tool_calls': tool_calls,
            'ready_only': '\n'.join(response).strip() == 'CONTEXT_READY',
            'attempt_id': attempt_id, 'unknown_items': unknown_items,
            'accepted': accepted, 'completed_turns': completed_turns, 'startup_errors': errors,
            'usage_valid': valid_usage, 'item_types': item_types,
            'model_requested': model, 'thinking': 'low', 'ephemeral': True,
            'profile': profile if not agents_enabled else None,
            'coverage': 'matched CLI startup only; not desktop-host savings'}
