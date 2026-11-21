"""يستخرج حقول استمارة مراجعة الفكرة من المسودة نصًا خامًا يُلصق في نموذج المنصة.

المصدر الوحيد هو المسودة؛ الملف الناتج يولَّد ولا يُحرَّر باليد.
يرفض التوليد إن تجاوز حقل حدّ النموذج، لأن المنصة تقصّ الزائد عند اللصق.
"""
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parents[1] / 'docs' / 'competition'
SOURCE = HERE / 'idea-review-draft.md'
TARGET = HERE / 'idea-review-form-v1.txt'
# آخر حقل نصي؛ ما بعده صورة يرفعها الطالبان ثم ملاحظات داخلية لا تدخل الاستمارة.
STOP = 'صورة أو مخطط توضيحي'
# حدود نموذج المنصة كما قُرئت منه في 22 نوفمبر 2026.
NAME = 'اسم المشروع'
NAME_LIMIT = 100
FIELD_LIMIT = 1000


def plain(text):
    """يزيل ترميز Markdown لأن حقول المنصة نص خام."""
    text = re.sub(r'\[([^\]]+)\]\(([^)]+)\)', r'\1: \2', text)
    return text.replace('**', '').replace('`', '')


def fields(markdown):
    parts = re.split(r'^## (.+)$', markdown, flags=re.M)[1:]
    for title, body in zip(parts[::2], parts[1::2], strict=True):
        if title.strip() == STOP:
            return
        yield title.strip(), plain(body).strip()
    raise ValueError(f'لم يوجد الحقل الفاصل: {STOP}')


def form_length(body):
    """الطول بأشدّ عدٍّ محتمل: المتصفح يرسل السطر الجديد حرفين (CR LF)."""
    return len(body) + body.count('\n')


def limit_of(title):
    return NAME_LIMIT if title == NAME else FIELD_LIMIT


def over_limit(found):
    """الحقول المتجاوزة: (العنوان، الطول، الحد)."""
    return [(title, form_length(body), limit_of(title)) for title, body in found
            if form_length(body) > limit_of(title)]


def render(found):
    return ''.join(f'=== {title} ===\n{body}\n\n' for title, body in found)


def main():
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    found = list(fields(SOURCE.read_text(encoding='utf-8')))
    for title, body in found:
        print(f'{len(body.split()):>4} كلمة  {form_length(body):>5} من {limit_of(title):>4} حرفًا'
              f'  {title}')
    exceeded = over_limit(found)
    if exceeded:
        for title, length, limit in exceeded:
            print(f'تجاوز الحد: «{title}» {length} حرفًا والحد {limit} (زائد {length - limit})')
        print(f'لم يُكتب {TARGET.name}: اختصر الحقل في المسودة ثم أعد التوليد.')
        return 1
    TARGET.write_text(render(found), encoding='utf-8', newline='\n')
    return 0


if __name__ == '__main__':
    sys.exit(main())
