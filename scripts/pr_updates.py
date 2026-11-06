"""يدمج ترقيات Dependabot الصغيرة من مهمة النشر المحلية بهوية المالك (ADR 0005).

فروع Dependabot لا تشغّل pr_flow لأن CI لا يعمل عليها عند الدفع، وتوكنها لا يملك الدمج.
الترقية الصغيرة: pip بلا تغيّر الرقم الأكبر، وأي ترقية لـActions لأن CI نفسه يشغّلها فيختبرها.
مجموعة pip-small في dependabot.yml تجمع الصغرى في طلب واحد، فلا تتعارض أسطر ملف واحد متجاورة.
طلب واحد في كل دورة، فيُنشر وحده ويُعرف سبب أي فشل، والترقية الكبرى تنتظر المالك.
"""
import json
import re
import subprocess

from automation_events import record
from pr_refresh import REPO, gh

BOT = 'app/dependabot'
GROUP = 'pip-small'
BUMP = re.compile(r'\bfrom v?(\d+)\S* to v?(\d+)')


def small(pr):
    if '/github_actions/' in pr['headRefName'] or GROUP in pr['headRefName']:
        return True
    match = BUMP.search(pr['title'])
    return bool(match) and match.group(1) == match.group(2)


def pick(prs):
    """أقدم ترقية صغيرة نجحت فحوصها، أو None."""
    ready = [pr['number'] for pr in prs
             if pr['author']['login'] == BOT and pr.get('mergeStateStatus') == 'CLEAN'
             and not pr.get('isDraft') and small(pr)]
    return min(ready, default=None)


def merge_small_update(root, run=gh):
    """لا يرفع أبدًا: تعذّر الدمج لا يوقف النشر، ويُعاد بعد خمس دقائق."""
    try:
        out = run('pr', 'list', '--repo', REPO, '--base', 'main', '--state', 'open', '--json',
                  'number,title,author,headRefName,isDraft,mergeStateStatus')
        if out.returncode != 0:
            raise RuntimeError(out.stderr.strip()[:150])
        number = pick(json.loads(out.stdout))
        if number is None:
            return
        done = run('pr', 'merge', str(number), '--repo', REPO, '--squash')
        reason = done.stderr.strip()[:120]
        detail = f'دمج ترقية الطلب {number}'
        record('pr-update', 'pass' if done.returncode == 0 else 'fail',
               f'{detail}: {reason}' if reason else detail, root)
    except (OSError, RuntimeError, ValueError, KeyError, TypeError,
            subprocess.SubprocessError) as error:
        record('pr-update', 'fail', f'دمج الترقيات: {error}', root)
