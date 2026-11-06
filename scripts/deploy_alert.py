"""تنبيه المالك عند فشل النشر (ADR 0005): إشعار ويندوز مرة لكل عطل، لا كل خمس دقائق.

الحالة في .local/deploy-alert: سبب العطل المبلَّغ عنه، وفراغها يعني أن النشر سليم.
التنبيه لا يوقف النشر أبدًا. لا يُنشر شيء خارج الجهاز، فالمستودع عام.
"""
import os
import subprocess
from pathlib import Path

STATE = Path('.local') / 'deploy-alert'
TITLE = 'ظل الخطر: فشل النشر'
RECOVERED = 'ظل الخطر: عاد النشر'
NO_WINDOW = getattr(subprocess, 'CREATE_NO_WINDOW', 0)
# النص يمر في متغيرات البيئة لا في الأمر، فلا يُفسَّر شيء منه شيفرة.
TOAST = r"""
$m = [Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType=WindowsRuntime]
$x = $m::GetTemplateContent([Windows.UI.Notifications.ToastTemplateType]::ToastText02)
$t = $x.GetElementsByTagName('text')
[void]$t.Item(0).AppendChild($x.CreateTextNode($env:DEPLOY_ALERT_TITLE))
[void]$t.Item(1).AppendChild($x.CreateTextNode($env:DEPLOY_ALERT_BODY))
$n = [Windows.UI.Notifications.ToastNotification]::new($x)
$m::CreateToastNotifier('{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\WindowsPowerShell\v1.0\powershell.exe').Show($n)
"""  # noqa: E501


def message(previous, reason):
    """يرجع (العنوان، النص) حين تتغير الحالة، وإلا None."""
    if reason == previous:
        return None
    return (TITLE, reason) if reason else (RECOVERED, f'زال العطل: {previous}')


def toast(title, body):
    env = {**os.environ, 'DEPLOY_ALERT_TITLE': title, 'DEPLOY_ALERT_BODY': body}
    return subprocess.run(['powershell', '-NoProfile', '-Command', TOAST], env=env,
                          capture_output=True, creationflags=NO_WINDOW).returncode == 0


def notify(root, reason, show=toast):
    """reason فارغ يعني أن النشر سليم. يرجع True إن عُرض شيء؛ تعذّر العرض يُعاد لاحقًا."""
    try:
        path = Path(root) / STATE
        previous = path.read_text(encoding='utf-8').strip() if path.exists() else ''
        text = message(previous, reason)
        if text and show(*text):
            path.parent.mkdir(exist_ok=True)
            path.write_text(reason, encoding='utf-8')
            return True
    except (OSError, subprocess.SubprocessError):
        pass
    return False
