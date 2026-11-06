"""حارس التباين: رموز الهوية المركزية تحقق WCAG AA في الوضعين (ADR 0006).

المصدر الوحيد للألوان الدلالية docs/brand/platform-theme.css. النص 4.5:1 والحدود 3:1.
حتمي وبلا تبعيات، ويعمل ضمن فحص ما قبل الدفع.
"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
THEME = ROOT / 'docs' / 'brand' / 'platform-theme.css'
BRAND = ROOT / 'docs' / 'brand' / 'approved-v1' / 'tokens.css'
TEXT = ('text', 'link', 'muted', 'muted-strong', 'focus', 'success', 'danger', 'neutral')
SURFACES = ('page-background', 'surface', 'surface-muted')
PAIRS = (('header-foreground', 'header-background', 4.5), ('on-link', 'link', 4.5))
AA_TEXT, AA_UI = 4.5, 3.0
VAR = re.compile(r'--([\w-]+)\s*:\s*([^;]+);')
BLOCK = re.compile(r'(:root(?:\[data-theme="dark"\]|:not\(\[data-theme="light"\]\))?)\s*\{(.*?)\}', re.S)


def luminance(color):
    value = color.lstrip('#')
    if len(value) == 3:
        value = ''.join(c * 2 for c in value)
    channels = [int(value[i:i + 2], 16) / 255 for i in (0, 2, 4)]
    r, g, b = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in channels]
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def ratio(first, second):
    high, low = sorted((luminance(first), luminance(second)), reverse=True)
    return (high + 0.05) / (low + 0.05)


def blocks(css, brand):
    """يرجع {'light': رموز، 'dark': رموز، 'dark-system': رموز} بعد حل var()."""
    names = dict(VAR.findall(brand))
    found = {}
    for selector, body in BLOCK.findall(css):
        key = 'light' if selector == ':root' else 'dark' if 'data-theme="dark"' in selector else 'dark-system'
        values = {}
        for name, raw in VAR.findall(body):
            raw = raw.strip()
            match = re.fullmatch(r'var\(--([\w-]+)\)', raw)
            values[name.removeprefix('platform-')] = names.get(match.group(1), raw).strip() if match else raw
        found[key] = values
    return found


def problems(theme):
    found = []
    for fg in TEXT:
        for bg in SURFACES:
            value = ratio(theme[fg], theme[bg])
            if value < AA_TEXT:
                found.append(f'{fg} على {bg}: {value:.2f} أقل من {AA_TEXT}')
    for bg in SURFACES:
        value = ratio(theme['border'], theme[bg])
        if value < AA_UI:
            found.append(f'border على {bg}: {value:.2f} أقل من {AA_UI}')
    for fg, bg, need in PAIRS:
        value = ratio(theme[fg], theme[bg])
        if value < need:
            found.append(f'{fg} على {bg}: {value:.2f} أقل من {need}')
    return found


def main():
    if sys.stdout:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    themes = blocks(THEME.read_text(encoding='utf-8'), BRAND.read_text(encoding='utf-8'))
    bad = []
    if themes.get('dark') != themes.get('dark-system'):
        bad.append('كتلتا الوضع الليلي غير متطابقتين')
    for name in ('light', 'dark'):
        bad += [f'{name}: {item}' for item in problems(themes[name])]
    print('\n'.join(bad) if bad else 'التباين ناجح في الوضعين')
    return 1 if bad else 0


if __name__ == '__main__':
    sys.exit(main())
