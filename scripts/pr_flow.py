"""مسار الطلب بعد نجاح CI (ADR 0003): يفتح الطلب وحده، ويدمج ما قاعدته غير main.

القاعدة من سطر «PR-Base: <فرع>» في آخر إيداع، والافتراضي main. الدمج إلى main لا يحدث هنا أبدًا:
يبقى بمراجعة بشرية بقرار حماية الفرع. يعمل بعد نجاح كل فحوص CI فقط، لأن قاعدة غير محمية
لا تشترط فحوصًا فكان --auto سيدمج فورًا. الاستعمال: python scripts/pr_flow.py <owner/repo> <branch>
"""
import json
import re
import subprocess
import sys

PROTECTED = 'main'
TRAILER = re.compile(r'^PR-Base:\s*(\S+)\s*$', re.M)


def base_from_message(message):
    match = TRAILER.search(message or '')
    return match.group(1) if match else PROTECTED


def decide(branch, pr):
    """يرجع 'skip' أو 'open' أو 'merge' أو 'human'."""
    if branch == PROTECTED:
        return 'skip'
    if pr is None:
        return 'open'
    if pr.get('isDraft'):
        return 'skip'
    return 'human' if pr.get('baseRefName') == PROTECTED else 'merge'


def gh(*args):
    return subprocess.run(['gh', *args], capture_output=True, text=True, encoding='utf-8')


def find_pr(repo, branch):
    out = gh('pr', 'list', '--repo', repo, '--head', branch, '--state', 'open',
             '--json', 'number,baseRefName,isDraft')
    if out.returncode != 0:
        raise RuntimeError(out.stderr.strip()[:200])
    found = json.loads(out.stdout)
    return found[0] if found else None


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
        out = gh('pr', 'merge', str(pr['number']), '--repo', repo, '--squash')
        print(f"دُمج الطلب {pr['number']}." if out.returncode == 0
              else f"تعذّر الدمج: {out.stderr.strip()[:200]}")
        return out.returncode
    if action == 'human':
        print(f"الطلب {pr['number']} أخضر وقاعدته main: ينتظر مراجعة بشرية.")
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
