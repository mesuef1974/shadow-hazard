"""عرض مقروء لنص عربي ملتصق دون المساس بأصله.

سجلات 31 أكتوبر إلى 2 نوفمبر كُتبت بلا مسافات، وملخص السجل مختوم ببصمة فلا يُعدَّل.
لذلك تُضاف المسافات عند العرض فقط، ولا تتغير الحروف، والكتابة الجديدة تُرفض.
"""
import math
import re
from functools import lru_cache
from pathlib import Path

GLUED = re.compile(r'[ء-يـ]{18,}')  # أطول كلمة عربية فعلية أقصر من هذا.
TOKEN = re.compile(r'[\u0621-\u064a\u0640]+')
MIXED = re.compile(r'(?<=[\u0621-\u064a])(?=[A-Za-z0-9])|(?<=[A-Za-z0-9])(?=[\u0621-\u064a])')
PUNCTUATION = re.compile(r'([؛،])(?=\S)')
VOCABULARY = Path(__file__).with_name('arabic_vocabulary.txt')
LONGEST_WORD = 14
SHORTEST_GLUED = 8
UNKNOWN_COST, UNKNOWN_LETTER_COST, WORD_COST = 40, 6, 2
PREFIXES = {'و', 'ب', 'ل', 'ف', 'ك', 'ال', 'لل'}
SUFFIXES = {'ه', 'ا', 'ة', 'ي', 'ها', 'ته', 'هم', 'ات'}


@lru_cache(maxsize=1)
def vocabulary():
    counts = {}
    for line in VOCABULARY.read_text(encoding='utf-8').splitlines():
        word, count = line.split()
        counts[word] = int(count)
    return counts, sum(counts.values())


def join_affixes(words):
    joined = []
    for word in words:
        if joined and (word in SUFFIXES or joined[-1] in PREFIXES):
            joined[-1] += word
        else:
            joined.append(word)
    return joined


def split_words(letters):
    """أقل تكلفة لتقسيم الحروف إلى كلمات شائعة؛ الكلمة المجهولة مكلفة فلا تُختار إلا اضطرارًا."""
    counts, total = vocabulary()
    best = [(0.0, 0)] + [(math.inf, 0)] * len(letters)
    for end in range(1, len(letters) + 1):
        for start in range(max(0, end - LONGEST_WORD), end):
            word = letters[start:end]
            count = counts.get(word)
            cost = (WORD_COST - math.log(count / total) if count
                    else UNKNOWN_COST + UNKNOWN_LETTER_COST * len(word))
            if best[start][0] + cost < best[end][0]:
                best[end] = (best[start][0] + cost, start)
    words, end = [], len(letters)
    while end:
        start = best[end][1]
        words.append(letters[start:end])
        end = start
    return join_affixes(words[::-1])


def has_glued_text(text):
    return bool(GLUED.search(text or ''))


def repair_token(match):
    letters = match.group().replace('\u0640', '')
    if len(letters) < SHORTEST_GLUED or letters in vocabulary()[0]:
        return match.group()
    return ' '.join(split_words(letters))


def readable(text):
    """النص السليم يعود كما هو؛ الكلمة الطويلة النادرة فيه ليست دليل التصاق."""
    if not has_glued_text(text):
        return text
    return TOKEN.sub(repair_token, PUNCTUATION.sub(r'\1 ', MIXED.sub(' ', text)))


def headline(text, limit=90):
    """عنوان قصير من أول جملة؛ الملخص الكامل يظهر تحته."""
    first = re.split(r'[.؛;\n]', text.strip(), maxsplit=1)[0].strip()
    if len(first) <= limit:
        return first
    return first[:limit].rsplit(' ', 1)[0] + '…'
