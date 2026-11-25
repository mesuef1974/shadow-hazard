"""يولّد نسخة المورد PDF من ورقة طلب العروض.

المصدر الوحيد `docs/procurement/quote-request-groups-a-b-ar.md`. نسخة المورد تحذف ما يخص الفريق
(الاقتباس الافتتاحي وقسم «بعد وصول العروض»)، وتستبدل بأعمدة العروض الثلاثة أعمدة يملؤها المورد.
الطباعة بمتصفح Edge بلا واجهة، لأنه يشكّل العربية ويضبط الاتجاه.
"""
import html
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'docs' / 'procurement' / 'quote-request-groups-a-b-ar.md'
TARGET = SOURCE.with_suffix('.pdf')
FONTS = ROOT / 'docs' / 'brand' / 'fonts'
INTERNAL = 'بعد وصول العروض'
OFFERS = ['عرض 1', 'عرض 2', 'عرض 3']
SUPPLIER = ['الموديل المقترح', 'السعر (ر.ق)', 'موعد التوريد']
EDGE = [r'C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe',
        r'C:\Program Files\Microsoft\Edge\Application\msedge.exe']

STYLE = '''
@font-face { font-family: Plex; src: url("%(regular)s"); }
@font-face { font-family: Plex; font-weight: 600; src: url("%(bold)s"); }
@page { size: A4 landscape; margin: 14mm; }
body { font-family: Plex, sans-serif; font-size: 10.5pt; line-height: 1.6; color: #111; }
h1 { font-size: 17pt; margin: 0 0 6pt; }
h2 { font-size: 12.5pt; margin: 14pt 0 5pt; break-after: avoid; }
table { border-collapse: collapse; width: 100%%; }
th, td { border: 0.6pt solid #666; padding: 4pt 6pt; text-align: start; vertical-align: top; }
th { background: #eee; font-weight: 600; }
tr { break-inside: avoid; }
td:nth-child(1), td:nth-child(3) { white-space: nowrap; }
td:nth-child(5) { width: 17%%; } td:nth-child(6), td:nth-child(7) { width: 10%%; }
ul { margin: 0; padding-inline-start: 16pt; }
'''


def inline(text):
    return re.sub(r'`([^`]+)`', r'<span dir="ltr">\1</span>', html.escape(text))


def cells(line):
    return [cell.strip() for cell in line.strip().strip('|').split('|')]


def supplier_blocks(markdown):
    """(النوع، المحتوى) لكل فقرة تبقى في نسخة المورد."""
    blocks, skipping, internal = [], False, 0
    for chunk in re.split(r'\n\s*\n', markdown.strip()):
        lines = chunk.splitlines()
        first = lines[0]
        if first.startswith('## '):
            skipping = INTERNAL in first
            internal += skipping
        if skipping or first.startswith('>'):
            continue
        if first.startswith('#'):
            level = len(first) - len(first.lstrip('#'))
            blocks.append((f'h{level}', first.lstrip('# ').strip()))
            lines = lines[1:]
            if not lines:
                continue
            first = lines[0]
        if first.startswith('|'):
            header = cells(first)
            # الفشل مغلق: جدول بلا أعمدة العروض الثلاثة لا يخرج للمورد كما هو.
            if [cell for cell in header if cell in OFFERS] != OFFERS:
                sys.exit(f'جدول بلا أعمدة العروض الثلاثة: {" | ".join(header)}')
            header = [SUPPLIER[OFFERS.index(cell)] if cell in OFFERS else cell for cell in header]
            blocks.append(('table', [header] + [cells(line) for line in lines[2:]]))
        elif first.startswith('- '):
            blocks.append(('ul', [line[2:] for line in lines]))
        else:
            blocks.append(('p', ' '.join(lines)))
    if internal != 1:
        sys.exit(f'قسم «{INTERNAL}» وُجد {internal} مرة في المصدر والمنتظر مرة واحدة؛ '
                 'لن أولّد نسخة المورد حتى لا يدخلها ما يخص الفريق.')
    return blocks


def render(blocks):
    out = []
    for kind, body in blocks:
        if kind == 'table':
            head = ''.join(f'<th>{inline(cell)}</th>' for cell in body[0])
            rows = ''.join('<tr>' + ''.join(f'<td>{inline(cell)}</td>' for cell in row) + '</tr>'
                           for row in body[1:])
            out.append(f'<table><thead><tr>{head}</tr></thead><tbody>{rows}</tbody></table>')
        elif kind == 'ul':
            out.append('<ul>' + ''.join(f'<li>{inline(item)}</li>' for item in body) + '</ul>')
        else:
            out.append(f'<{kind}>{inline(body)}</{kind}>')
    style = STYLE % {
        'regular': (FONTS / 'ibmplexsansarabic-IBMPlexSansArabic-Regular.ttf').as_uri(),
        'bold': (FONTS / 'ibmplexsansarabic-IBMPlexSansArabic-SemiBold.ttf').as_uri()}
    return ('<!doctype html><html lang="ar" dir="rtl"><head><meta charset="utf-8">'
            f'<title>طلب عروض أسعار</title><style>{style}</style></head>'
            f'<body>{"".join(out)}</body></html>')


def edge():
    found = shutil.which('msedge') or next((path for path in EDGE if Path(path).exists()), None)
    if not found:
        sys.exit('لم أجد متصفح Edge؛ الطباعة إلى PDF تحتاجه.')
    return found


def main():
    page = render(supplier_blocks(SOURCE.read_text(encoding='utf-8')))
    # مجلد مؤقت بمسار لاتيني: Edge لا يقبل مسارًا عربيًّا في --print-to-pdf على كل الأجهزة.
    with tempfile.TemporaryDirectory() as folder:
        source, target = Path(folder) / 'quote.html', Path(folder) / 'quote.pdf'
        source.write_text(page, encoding='utf-8')
        subprocess.run([edge(), '--headless=new', '--disable-gpu', '--no-pdf-header-footer',
                        f'--user-data-dir={Path(folder) / "profile"}',
                        f'--print-to-pdf={target}', source.as_uri()],
                       check=True, capture_output=True, timeout=120)
        shutil.copyfile(target, TARGET)
    print(f'كُتب {TARGET.relative_to(ROOT)} ({TARGET.stat().st_size // 1024} ك.ب)')


if __name__ == '__main__':
    main()
