"""سجل حالة النشر (ADR 0005): سبب العطل مرة لكل عطل، لا كل خمس دقائق.

الحالة في .local/deploy-alert: سبب العطل المبلَّغ عنه، وفراغها يعني أن النشر سليم.
لا يصدر هنا أي إشعار ويندوز (قرار المالك 2026-11-07)؛ الحالة تُقرأ من الملف ومن سجل الأتمتة.
التسجيل لا يوقف النشر أبدًا، ولا يُنشر شيء خارج الجهاز.
"""
from pathlib import Path

STATE = Path('.local') / 'deploy-alert'


def changed(previous, reason):
    """هل تغيّرت الحالة عمّا سُجّل؟ النشر السليم الأول لا يُعدّ تغيّرًا."""
    return reason != previous


def notify(root, reason):
    """reason فارغ يعني أن النشر سليم. يرجع True حين تتغير الحالة المسجلة."""
    try:
        path = Path(root) / STATE
        previous = path.read_text(encoding='utf-8').strip() if path.exists() else ''
        if not changed(previous, reason):
            return False
        path.parent.mkdir(exist_ok=True)
        path.write_text(reason, encoding='utf-8')
        return True
    except OSError:
        return False
