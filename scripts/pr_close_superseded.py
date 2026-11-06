"""يغلق الطلبات التي وصل عملها إلى main ضمن طلب آخر (ADR 0005).

المعيار قطعي لا تخميني: رأس الطلب المفتوح سلف لرأس طلب مدموج في main، فكل التزاماته داخله
ولو ضغطها squash. طلب فيه عمل فريد لا يُغلق أبدًا هنا؛ مراقب الطلبات يبلّغ عنه.
الاستعمال: python scripts/pr_close_superseded.py <owner/repo>
"""
import json
import subprocess
import sys

MERGED_LIMIT = 30


def gh(*args):
    out = subprocess.run(['gh', *args], capture_output=True, text=True, encoding='utf-8')
    if out.returncode != 0:
        raise RuntimeError(out.stderr.strip()[:200])
    return out.stdout


def is_ancestor(head, other):
    return subprocess.run(['git', 'merge-base', '--is-ancestor', head, other],
                          capture_output=True).returncode == 0


def superseded_by(pr, merged, ancestor=is_ancestor):
    """رقم الطلب المدموج الذي يحوي هذا الطلب، أو None. المسودة تُترك لصاحبها."""
    if pr.get('isDraft'):
        return None
    for item in merged:
        if item['headRefOid'] != pr['headRefOid'] and ancestor(pr['headRefOid'], item['headRefOid']):
            return item['number']
    return None


def main(argv):
    if sys.stdout:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    repo = argv[0]
    prs = json.loads(gh('pr', 'list', '--repo', repo, '--state', 'open',
                        '--json', 'number,headRefOid,isDraft'))
    merged = json.loads(gh('pr', 'list', '--repo', repo, '--state', 'merged', '--base', 'main',
                           '--limit', str(MERGED_LIMIT), '--json', 'number,headRefOid'))
    numbers = [p['number'] for p in prs + merged]
    if not numbers or not merged:
        return 0
    url = f'https://github.com/{repo}.git'
    subprocess.run(['git', 'fetch', '-q', url,
                    *[f'+refs/pull/{n}/head:refs/shz/pull/{n}' for n in numbers]], check=True)
    for pr in prs:
        winner = superseded_by(pr, merged)
        if winner:
            gh('pr', 'close', str(pr['number']), '--repo', repo,
               '--comment', f'أُغلق آليًا: كل التزاماته وصلت إلى main ضمن الطلب {winner}.')
            print(f"أُغلق الطلب {pr['number']}: عمله في الطلب {winner}.")
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
