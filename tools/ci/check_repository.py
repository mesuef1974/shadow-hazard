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
    if ('.local' in path.parts or Path(name).parts[0] in {'.codex', 'sessions'}
            or path.suffix in {'.sqlite3', '.key', '.pem'} or path.name == '.env'):
        errors.append(f'Private runtime file tracked: {name}')
    if path.is_file() and path.suffix in {'.py', '.js', '.mjs', '.json', '.yml', '.yaml', '.md', '.txt', '.html', '.css'}:
        if any(pattern.search(path.read_bytes()) for pattern in patterns):
            errors.append(f'Potential credential in {name}; value suppressed')
manifest = json.loads((ROOT / 'docs/brand/approved-v1/manifest.json').read_text(encoding='utf-8'))
for item in manifest['assets']:
    path = (ROOT / item['path']).resolve()
    if not path.is_relative_to(ROOT) or hashlib.sha256(path.read_bytes()).hexdigest() != item['sha256']:
        errors.append(f'Approved brand asset mismatch: {item["path"]}')
fonts = json.loads((ROOT / 'docs/brand/font-sources.json').read_text(encoding='utf-8'))
for font in fonts:
    relative = 'docs/brand/fonts/' + font['filename']
    path = (ROOT / relative).resolve()
    if not path.is_relative_to(ROOT / 'docs/brand/fonts'):
        errors.append(f'Font path escapes its directory: {relative}')
        continue
    # Check both working bytes and Git's stored bytes, catching EOL conversion
    # before a Windows-only success becomes a clean-checkout failure on Linux.
    stored = subprocess.check_output(['git', 'show', ':' + relative], cwd=ROOT)
    if any(hashlib.sha256(data).hexdigest() != font['sha256'] for data in (path.read_bytes(), stored)):
        errors.append(f'Font or license fingerprint mismatch: {relative}')
workflow = (ROOT / '.github/workflows/ci.yml').read_text(encoding='utf-8')
for action in re.findall(r'uses:\s*([^\s]+)', workflow):
    if not re.fullmatch(r'[\w./-]+@[0-9a-f]{40}', action):
        errors.append(f'Action must use a full commit SHA: {action}')
if errors:
    raise SystemExit('\n'.join(errors))
print('Tracked runtime files, credential patterns, brand hashes and Action pins: OK')
