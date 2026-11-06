"""مسار الطلب بعد نجاح CI (ADR 0005): يفتح الطلب وحده ويدمجه، حتى إلى main.

القاعدة من سطر «PR-Base: <فرع>» في آخر إيداع، والافتراضي main. المسودة لا تُدمج، فهي طريق إبقاء
عين بشرية على تغيير بعينه. يعمل بعد نجاح كل الفحوص فقط، لأن قاعدة غير محمية لا تشترطها.
الاستعمال: python scripts/pr_flow.py <owner/repo> <branch>
"""
import json
import re
import subprocess
import sys
import time

PROTECTED = 'main'
MERGE_TRIES = 12
MERGE_WAIT = 30
WAITING = re.compile(r'base branch policy prohibits|status checks?\b.*\b(?:is|are) expected', re.I)
OUTDATED = 'not up to date'
TRAILER = re.compile(r'^PR-Base:\s*(\S+)\s*$', re.M)


def base_from_message(message):
    match = TRAILER.search(message or '')
    return match.group(1) if match else PROTECTED


def decide(branch, pr):
    """يرجع 'skip' أو 'open' أو 'update' أو 'merge'."""
    if branch == PROTECTED:
        return 'skip'
    if pr is None:
        return 'open'
    if pr.get('isDraft'):
        return 'skip'
    return 'update' if pr.get('mergeStateStatus') == 'BEHIND' else 'merge'


def gh(*args):
    return subprocess.run(['gh', *args], capture_output=True, text=True, encoding='utf-8')


def merge_when_allowed(merge, wait=time.sleep):
    """الحماية قد ترفض لحظة فتح الطلب حتى تُحتسب فحوصه؛ الرفض لغير هذا السبب يُعاد لصاحبه."""
    for attempt in range(MERGE_TRIES):
        out = merge()
        if out.returncode == 0 or not WAITING.search(out.stderr) or attempt == MERGE_TRIES - 1:
            return out
        wait(MERGE_WAIT)


def find_pr(repo, branch):
    out = gh('pr', 'list', '--repo', repo, '--head', branch, '--state', 'open',
             '--json', 'number,baseRefName,isDraft,mergeStateStatus')
    if out.returncode != 0:
        raise RuntimeError(out.stderr.strip()[:200])
    found = json.loads(out.stdout)
    return found[0] if found else None


def leave_for_refresh(number):
    """التحديث لا يصلح من هنا (انظر pr_refresh)؛ مهمة النشر المحلية تحدّثه فتعود الفحوص وتدمجه."""
    print(f'الطلب {number} متأخر عن main؛ تحدّثه مهمة النشر المحلية خلال خمس دقائق.')
    return 0


def main(argv):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    repo, branch = argv
    pr = find_pr(repo, branch)
    action = decide(branch, pr)
    if action == 'open':
        message = subprocess.run(['git', 'log', '-1', '--format=%B'], capture_output=True,
                                 text=True, encoding='utf-8').stdout
        title = message.splitlines()[0] if message else branch
        out = gh('pr', 'create', '--repo', repo, '--head', branch, '--title', title,
                 '--base', base_from_message(message), '--body', message)
        if out.returncode != 0:
            print(f'تعذّر فتح الطلب: {out.stderr.strip()[:200]}')
            print(f'افتحه يدويًا: https://github.com/{repo}/pull/new/{branch}')
            return 1
        print(f'فُتح الطلب: {out.stdout.strip()}')
        pr = find_pr(repo, branch)
        action = decide(branch, pr) if pr else 'skip'
    if action == 'merge':
        # المستودع يقبل squash وحده.
        out = merge_when_allowed(lambda: gh('pr', 'merge', str(pr['number']), '--repo', repo, '--squash'))
        if out.returncode == 0:
            print(f"دُمج الطلب {pr['number']}.")
            return 0
        if OUTDATED not in out.stderr:
            print(f"تعذّر الدمج: {out.stderr.strip()[:200]}")
            return out.returncode
        # حالة طلب فُتح لتوّه لم تُحسب بعد، فلا يكشف تأخره إلا رفض الدمج.
        action = 'update'
    if action == 'update':
        return leave_for_refresh(pr['number'])
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
