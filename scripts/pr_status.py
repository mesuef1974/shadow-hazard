"""مراقب حالة الطلبات المفتوحة (أتمتة جذرية): يُكتب في ملخص GitHub دون أن يفتحها أحد يدويًا.

يصنّف كل طلب إلى: تعارض / ينتظر مراجعًا / فحوص فاشلة / جاهز للدمج، ويقول من عليه الخطوة.
حتمي وبلا توكنز. الاستعمال: python scripts/pr_status.py [owner/repo]
"""
import json
import subprocess
import sys

FIELDS = 'number,title,baseRefName,headRefName,reviewDecision,mergeStateStatus,statusCheckRollup,isDraft'


def failing_checks(rollup):
    return [c.get('name') or c.get('context', '?') for c in rollup or []
            if (c.get('conclusion') or c.get('state') or '').upper() in ('FAILURE', 'ERROR', 'TIMED_OUT', 'CANCELLED')]


def classify(pr):
    """يرجع (الحالة، الإجراء المطلوب)."""
    if pr.get('isDraft'):
        return 'مسودة', 'صاحب الطلب: أكمله ثم أخرجه من المسودة'
    if pr.get('mergeStateStatus') == 'DIRTY':
        return 'تعارض', f"صاحب الطلب: ادمج {pr.get('baseRefName')} في الفرع وحلّ التعارض"
    failed = failing_checks(pr.get('statusCheckRollup'))
    if failed:
        return 'فحوص فاشلة', 'راجع خلاصة الفشل: ' + '، '.join(failed[:3])
    if pr.get('reviewDecision') == 'REVIEW_REQUIRED':
        return 'ينتظر مراجعًا', 'مراجع ثانٍ مطلوب (قاعدة حماية main)'
    if pr.get('mergeStateStatus') in ('CLEAN', 'HAS_HOOKS', 'UNSTABLE'):
        return 'جاهز للدمج', 'المشرف: ادمج'
    return 'قيد الفحص', 'انتظر انتهاء الفحوص'


def report(prs):
    lines = ['# حالة الطلبات المفتوحة', '']
    if not prs:
        return '\n'.join(lines + ['لا طلبات مفتوحة.']) + '\n'
    lines += ['| الطلب | الفرع ← القاعدة | الحالة | المطلوب |', '|---|---|---|---|']
    for pr in sorted(prs, key=lambda p: p['number']):
        state, action = classify(pr)
        title = pr['title'][:50].replace('|', '/')
        lines.append(f"| #{pr['number']} {title} | {pr['headRefName']} ← {pr['baseRefName']} | {state} | {action} |")
    return '\n'.join(lines) + '\n'


def main(argv):
    cmd = ['gh', 'pr', 'list', '--state', 'open', '--json', FIELDS]
    if argv:
        cmd += ['--repo', argv[0]]
    out = subprocess.run(cmd, capture_output=True, text=True, encoding='utf-8')
    if out.returncode != 0:
        print('تعذّر جلب الطلبات: ' + out.stderr.strip()[:200], file=sys.stderr)
        return 1
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    print(report(json.loads(out.stdout)), end='')
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
