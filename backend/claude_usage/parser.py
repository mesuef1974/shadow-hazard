"""Pure parsing of Claude Code transcript lines (no Django). Keeps numbers only."""
import json
from dataclasses import dataclass
from datetime import datetime

FIELDS = ('input_tokens', 'cache_creation_input_tokens', 'cache_read_input_tokens', 'output_tokens')


@dataclass(frozen=True)
class Reply:
    message_id: str
    session_id: str
    model: str
    at: datetime
    cwd: str
    git_branch: str
    is_subagent: bool
    surface: str
    usage: dict


def parse_time(value):
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace('Z', '+00:00'))
    except ValueError:
        return None


def parse_line(line):
    """Return a Reply for an assistant line with usage, else None (unknown shapes are skipped)."""
    try:
        data = json.loads(line)
    except (ValueError, TypeError):
        return None
    if not isinstance(data, dict) or data.get('type') != 'assistant':
        return None
    message = data.get('message')
    if not isinstance(message, dict):
        return None
    usage, message_id, model = message.get('usage'), message.get('id'), message.get('model')
    at = parse_time(data.get('timestamp'))
    if not isinstance(usage, dict) or not message_id or not model or at is None or model == '<synthetic>':
        return None
    numbers = {}
    for name in FIELDS:
        value = usage.get(name) or 0
        if not isinstance(value, int) or value < 0:
            return None
        numbers[name] = value
    return Reply(str(message_id), str(data.get('sessionId') or ''), str(model), at,
                 str(data.get('cwd') or ''), str(data.get('gitBranch') or ''),
                 bool(data.get('isSidechain')), 'cloud' if data.get('surface') == 'cloud' else 'local', numbers)


def strip_line(line, surface='cloud'):
    """A transcript line reduced to the fields the importer reads; None when not a usage line."""
    reply = parse_line(line)
    if reply is None:
        return None
    return json.dumps({'type': 'assistant', 'sessionId': reply.session_id, 'timestamp': reply.at.isoformat(),
                       'cwd': reply.cwd, 'gitBranch': reply.git_branch, 'isSidechain': reply.is_subagent,
                       'surface': surface, 'message': {'id': reply.message_id, 'model': reply.model, 'usage': reply.usage}},
                      ensure_ascii=False)
