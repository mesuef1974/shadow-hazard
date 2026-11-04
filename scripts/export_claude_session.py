"""Export one Claude session's usage (numbers only) for the platform importer.

Usage: python scripts/export_claude_session.py <transcript.jsonl> <out_dir>
Writes <out_dir>/<transcript name> with assistant usage lines reduced to ids, model, time and the
four token counters; no conversation text. The platform's worker imports it (ADR 0002, exception 6).
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'backend'))
from claude_usage.parser import strip_line  # noqa: E402  (pure module, no Django)


def main(source, out_dir):
    source, out_dir = Path(source), Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    target = out_dir / source.name
    count = 0
    with source.open(encoding='utf-8') as lines, target.open('w', encoding='utf-8', newline='\n') as out:
        for line in lines:
            stripped = strip_line(line, surface='cloud')
            if stripped:
                out.write(stripped + '\n')
                count += 1
    print(f'{count} usage lines → {target}')


if __name__ == '__main__':
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    main(*sys.argv[1:])
