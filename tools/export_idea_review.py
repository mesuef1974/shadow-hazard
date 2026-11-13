"""يستخرج حقول استمارة مراجعة الفكرة من المسودة نصًا خامًا يُلصق في نموذج المنصة.

المصدر الوحيد هو المسودة؛ الملف الناتج يولَّد ولا يُحرَّر باليد.
"""
import re
from pathlib import Path

HERE = Path(__file__).resolve().parents[1] / 'docs' / 'competition'
SOURCE = HERE / 'idea-review-draft.md'
TARGET = HERE / 'idea-review-form-v1.txt'
# آخر حقل نصي؛ ما بعده صورة يرفعها الطالبان ثم ملاحظات داخلية لا تدخل الاستمارة.
STOP = 'صورة أو مخطط توضيحي'


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


def main():
    found = list(fields(SOURCE.read_text(encoding='utf-8')))
    TARGET.write_text(''.join(f'=== {title} ===\n{body}\n\n' for title, body in found),
                      encoding='utf-8', newline='\n')
    for title, body in found:
        print(f'{len(body.split()):>4} كلمة  {len(body):>5} حرفًا  {title}')


if __name__ == '__main__':
    main()
