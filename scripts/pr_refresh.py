"""يحدّث الطلبات المتأخرة عن main بهوية المالك من مهمة النشر المحلية (ADR 0005).

تحديث الفرع من CI لا يصلح: دفع GITHUB_TOKEN يترك تشغيل pull_request معلّقًا، وفحوص
workflow_dispatch لا تُحتسب للطلب، فلا يُدمج أبدًا. دفع المالك يشغّل CI طبيعيًا فيدمج pr_flow.
"""
import json
import subprocess

from automation_events import record

REPO = 'mesuef1974/shadow-hazard'
NO_WINDOW = getattr(subprocess, 'CREATE_NO_WINDOW', 0)


def behind(prs):
    return [pr['number'] for pr in prs
            if pr.get('mergeStateStatus') == 'BEHIND' and not pr.get('isDraft')]


def gh(*args):
    return subprocess.run(['gh', *args], capture_output=True, text=True, encoding='utf-8',
                          errors='replace', creationflags=NO_WINDOW)


def refresh(root, run=gh):
    """لا يرفع أبدًا: تعذّر التحديث لا يوقف النشر، ويُعاد بعد خمس دقائق."""
    try:
        out = run('pr', 'list', '--repo', REPO, '--base', 'main', '--state', 'open',
                  '--json', 'number,isDraft,mergeStateStatus')
        if out.returncode != 0:
            raise RuntimeError(out.stderr.strip()[:150])
        for number in behind(json.loads(out.stdout)):
            done = run('pr', 'update-branch', str(number), '--repo', REPO)
            record('pr-update', 'pass' if done.returncode == 0 else 'fail',
                   f'تحديث الطلب {number}: {done.stderr.strip()[:120]}', root)
    except (OSError, RuntimeError, ValueError) as error:
        record('pr-update', 'fail', f'قراءة الطلبات: {error}', root)
