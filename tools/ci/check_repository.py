"""Small, dependency-free guards; not a comprehensive secret/security audit."""
import hashlib
import json
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
paths = subprocess.check_output(['git', 'ls-files', '-z'], cwd=ROOT).decode().split('\0')
errors = []
patterns = [
    re.compile(rb'gh[pousr]_[A-Za-z0-9]{36,}'),
    re.compile(rb'github_pat_[A-Za-z0-9_]{50,}'),
    re.compile(rb'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----'),
]
for name in filter(None, paths):
    path = ROOT / name
    if '.local' in path.parts or path.suffix in {'.sqlite3', '.key', '.pem'} or path.name == '.env':
        errors.append(f'Private runtime file tracked: {name}')
    if path.is_file() and path.suffix in {'.py', '.js', '.mjs', '.json', '.yml', '.yaml', '.md', '.txt', '.html', '.css'}:
        if any(pattern.search(path.read_bytes()) for pattern in patterns):
            errors.append(f'Potential credential in {name}; value suppressed')
manifest = json.loads((ROOT / 'docs/brand/approved-v1/manifest.json').read_text(encoding='utf-8'))
for item in manifest['assets']:
    path = (ROOT / item['path']).resolve()
    if not path.is_relative_to(ROOT) or hashlib.sha256(path.read_bytes()).hexdigest() != item['sha256']:
        errors.append(f'Approved brand asset mismatch: {item["path"]}')
workflow = (ROOT / '.github/workflows/ci.yml').read_text(encoding='utf-8')
for action in re.findall(r'uses:\s*([^\s]+)', workflow):
    if not re.fullmatch(r'[\w./-]+@[0-9a-f]{40}', action):
        errors.append(f'Action must use a full commit SHA: {action}')
if errors:
    raise SystemExit('\n'.join(errors))
print('Tracked runtime files, credential patterns, brand hashes and Action pins: OK')
