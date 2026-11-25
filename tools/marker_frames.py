"""لقطات اصطناعية لتمرين رصد العلامة اللونية، ومدقّق لنتائج برنامج الطالبين.

لا يحتوي هذا الملف برنامج رصد: الرصد يكتبه الطالبان بأيديهما (الخطة الأسبوعية، الأسبوع 1).

  python tools/marker_frames.py make <مجلد>                 يولّد اللقطات وملف الحقيقة truth.csv
  python tools/marker_frames.py check <مجلد> <نتائج.csv>    يقارن نتائج الطالبين بالحقيقة

اللقطة 640×480 كما في بوابة ديسمبر، من كاميرا على مقدمة الحافلة: طريق ورصيف وممر أصفر ومقدمة
الحافلة أسفل الصورة، وطفل على رأسه العلامة. البذرة ثابتة، فكل تشغيل يعطي اللقطات نفسها.
"""
import argparse
import csv
import math
import ntpath
import sys
from pathlib import Path

WIDTH, HEIGHT = 640, 480
# ألوان BGR كما يخزّنها OpenCV. العلامة أخضر فسفوري؛ البرتقالي استُبعد لأن تدرّجه (نحو 14 على سلّم
# OpenCV) يلاصق أصفر الحافلة والممر (نحو 23) فيتداخلان مع أي ميل في لون الإضاءة.
MARKER = (20, 255, 57)
DULL_GREEN = (70, 110, 80)
BODIES = [(200, 90, 40), (40, 40, 200), (230, 230, 230), (180, 130, 240)]
# نمط من 30 لقطة يتكرر: واضحة، معتمة، ساطعة، ضبابية، محجوبة جزئيًّا، فارغة، خادعة بلا علامة.
PATTERN = (['clear'] * 10 + ['dim'] * 4 + ['bright'] * 3 + ['blur'] * 4
           + ['partial'] * 3 + ['empty'] * 3 + ['decoy'] * 3)
ABSENT = {'empty', 'decoy'}
TRUTH = 'truth.csv'
COLUMNS = ['frame', 'kind', 'present', 'x', 'y', 'visible_px']
YES, NO = {'1', 'true', 'yes'}, {'0', 'false', 'no', ''}
# حدّا النجاح المقترحان، ويعدّلهما المشرف: خطأ الموضع بالبكسل، ونسبة اللقطات المرصودة.
TOLERANCE = 5.0
RECALL_MIN = 0.95


def render(kind, rng):
    """(الصورة، قناع بكسلات العلامة الظاهرة) للقطة واحدة من النوع المطلوب."""
    import cv2 as cv
    import numpy as np
    image = np.full((HEIGHT, WIDTH, 3), int(rng.integers(55, 76)), np.uint8)
    cv.rectangle(image, (0, 0), (WIDTH, 90), (150, 152, 148), -1)
    grass = int(rng.integers(20, 500))
    cv.rectangle(image, (grass, 20), (grass + 90, 70), DULL_GREEN, -1)
    for x in range(0, WIDTH, 80):
        cv.rectangle(image, (x, 236), (x + 45, 244), (235, 235, 235), -1)
    crossing = int(rng.integers(60, 500))
    for y in range(110, 400, 36):
        cv.rectangle(image, (crossing, y), (crossing + 70, y + 16), (40, 205, 235), -1)
    hood = HEIGHT - int(rng.integers(45, 75))
    mask = np.zeros((HEIGHT, WIDTH), np.uint8)
    if kind != 'empty':
        radius = int(rng.integers(6, 13))
        x = int(rng.integers(40, WIDTH - 40))
        y = hood + int(rng.integers(-2, 3)) if kind == 'partial' \
            else int(rng.integers(120, hood - 40))
        cv.ellipse(image, (x, y + radius + 12), (9, 14), 0, 0, 360,
                   BODIES[int(rng.integers(len(BODIES)))], -1)
        if kind == 'decoy':
            cv.circle(image, (x + 30, y + 20), 10, DULL_GREEN, -1)
        else:
            shade = tuple(int(np.clip(channel + rng.integers(-10, 11), 0, 255))
                          for channel in MARKER)
            cv.circle(image, (x, y), radius, shade, -1)
            cv.circle(mask, (x, y), radius, 255, -1)
    # مقدمة الحافلة تُرسم أخيرًا لأنها تحجب ما تحتها، ومعها يُحذف المحجوب من القناع.
    cv.rectangle(image, (0, hood), (WIDTH, HEIGHT), (30, 190, 240), -1)
    mask[hood:] = 0
    gain = {'dim': rng.uniform(.45, .6), 'bright': rng.uniform(1.2, 1.35)}.get(
        kind, rng.uniform(.85, 1.1))
    lit = image.astype(np.float32) * gain * rng.uniform(.93, 1.07, 3)
    lit += rng.normal(0, rng.uniform(2, 6), lit.shape)
    image = np.clip(lit, 0, 255).astype(np.uint8)
    size = int(rng.choice([5, 7])) if kind == 'blur' else 3
    return cv.GaussianBlur(image, (size, size), 0), mask


def centre(mask):
    """(س، ص، عدد البكسلات) لمركز ثقل العلامة الظاهرة، أو None إن لم تظهر."""
    ys, xs = mask.nonzero()
    if not len(xs):
        return None
    return float(xs.mean()), float(ys.mean()), len(xs)


def frames(count=60, seed=7):
    """يولّد (الاسم، النوع، الصورة، مركز العلامة) بترتيب ثابت للبذرة نفسها."""
    import numpy as np
    rng = np.random.default_rng(seed)
    # يُخلط النمط كاملًا ثم يؤخذ منه العدد، فلا يخرج العدد الصغير لقطات واضحة فقط.
    kinds = list(PATTERN) * -(-count // len(PATTERN))
    rng.shuffle(kinds)
    kinds = kinds[:count]
    for index, kind in enumerate(kinds):
        image, mask = render(kind, rng)
        yield f'frame_{index:03d}.png', kind, image, centre(mask)


def make(folder, count=60, seed=7):
    import cv2 as cv
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    rows = []
    for name, kind, image, found in frames(count, seed):
        # imwrite يفشل بصمت مع المسارات العربية على ويندوز، فيُرمَّز في الذاكرة ثم يُكتب.
        (folder / name).write_bytes(cv.imencode('.png', image)[1].tobytes())
        x, y, pixels = found or ('', '', 0)
        rows.append({'frame': name, 'kind': kind, 'present': int(bool(found)),
                     'x': x and round(x, 2), 'y': y and round(y, 2), 'visible_px': pixels})
    with open(folder / TRUTH, 'w', encoding='utf-8', newline='') as handle:
        writer = csv.DictWriter(handle, fieldnames=COLUMNS)
        writer.writeheader()
        writer.writerows(rows)
    return rows


def read_rows(path, required):
    try:
        with open(path, encoding='utf-8-sig', newline='') as handle:
            reader = csv.DictReader(handle)
            missing = [column for column in required if column not in (reader.fieldnames or [])]
            if missing:
                sys.exit(f'{Path(path).name}: تنقصه الأعمدة {"، ".join(missing)}')
            return list(reader)
    except (OSError, UnicodeError) as error:
        sys.exit(f'{Path(path).name}: تعذّرت قراءته ({error}). احفظه CSV بترميز UTF-8.')


def is_found(name, value):
    """1 أو 0 فقط. القيمة الغريبة توقف الفحص، لأن عدّها صفرًا يخفي الإنذار الكاذب."""
    value = str(value or '').strip().lower()
    if value in YES or value in NO:
        return value in YES
    try:
        number = float(value)
    except ValueError:
        number = None
    if number not in (0.0, 1.0):
        sys.exit(f'{name}: قيمة found «{value}» غير مفهومة؛ اكتب 1 أو 0.')
    return number == 1.0


def index_detections(detections, known):
    """(النتائج باسم اللقطة، عدد الصفوف التي لا تقابلها لقطة). الاسم يُؤخذ بلا مساره."""
    by_frame, unknown = {}, 0
    for row in detections:
        name = ntpath.basename((row.get('frame') or '').strip())
        if name in by_frame:
            sys.exit(f'{name}: مكرر في ملف النتائج؛ لكل لقطة صف واحد.')
        by_frame[name] = dict(row, found=is_found(name, row.get('found')))
        unknown += name not in known
    return by_frame, unknown


def score(truth, detections):
    """يقارن صفوف النتائج بصفوف الحقيقة. اللقطة الغائبة عن النتائج تُحسب «لم يُرصد»."""
    by_frame, unknown = index_detections(detections, {row['frame'] for row in truth})
    kinds, errors, false_alarms, hits, present = {}, [], 0, 0, 0
    for row in truth:
        tally = kinds.setdefault(row['kind'], {'frames': 0, 'correct': 0})
        tally['frames'] += 1
        answer = by_frame.get(row['frame'])
        found = bool(answer) and answer['found']
        if int(row['present']):
            present += 1
            if not found:
                continue
            try:
                error = math.hypot(float(answer['x']) - float(row['x']),
                                   float(answer['y']) - float(row['y']))
            except (TypeError, ValueError):
                continue
            if error <= TOLERANCE:
                hits += 1
                tally['correct'] += 1
                errors.append(error)
        elif found:
            false_alarms += 1
        else:
            tally['correct'] += 1
    recall = hits / present if present else 1.0
    return {'kinds': kinds, 'recall': recall, 'false_alarms': false_alarms, 'unknown': unknown,
            'mean_error': sum(errors) / len(errors) if errors else None,
            'passed': recall >= RECALL_MIN and false_alarms == 0}


NAMES = {'clear': 'واضحة', 'dim': 'معتمة', 'bright': 'ساطعة', 'blur': 'ضبابية',
         'partial': 'محجوبة جزئيًّا', 'empty': 'فارغة', 'decoy': 'خادعة بلا علامة'}


def report(result):
    lines = [f'{NAMES[kind]}: {tally["correct"]} من {tally["frames"]}'
             for kind, tally in result['kinds'].items()]
    lines.append(f'نسبة الرصد الصحيح: {result["recall"]:.0%} (المطلوب {RECALL_MIN:.0%})')
    lines.append(f'إنذارات كاذبة: {result["false_alarms"]} (المطلوب 0)')
    if result['mean_error'] is not None:
        lines.append(f'متوسط خطأ الموضع: {result["mean_error"]:.1f} بكسل (الحد {TOLERANCE:g})')
    if result['unknown']:
        lines.append(f'تنبيه: {result["unknown"]} من الصفوف لا يطابق اسمها أي لقطة، فلم تُحسب.')
    lines.append('النتيجة: ناجح' if result['passed'] else 'النتيجة: لم ينجح بعد')
    return '\n'.join(lines)


def positive(text):
    value = int(text)
    if value < 1:
        raise argparse.ArgumentTypeError('العدد 1 أو أكثر')
    return value


def main(argv=None):
    sys.stdout.reconfigure(encoding='utf-8')
    parser = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    commands = parser.add_subparsers(dest='command', required=True)
    maker = commands.add_parser('make', help='ولّد اللقطات وملف الحقيقة')
    maker.add_argument('folder')
    maker.add_argument('--count', type=positive, default=60)
    maker.add_argument('--seed', type=int, default=7)
    checker = commands.add_parser('check', help='قارن نتائج البرنامج بالحقيقة')
    checker.add_argument('folder')
    checker.add_argument('detections')
    args = parser.parse_args(argv)
    if args.command == 'make':
        rows = make(args.folder, args.count, args.seed)
        print(f'كُتبت {len(rows)} لقطة و{TRUTH} في {args.folder}')
        return 0
    truth = read_rows(Path(args.folder) / TRUTH, ['frame', 'kind', 'present', 'x', 'y'])
    result = score(truth, read_rows(args.detections, ['frame', 'found', 'x', 'y']))
    print(report(result))
    return 0 if result['passed'] else 1


if __name__ == '__main__':
    sys.exit(main())
