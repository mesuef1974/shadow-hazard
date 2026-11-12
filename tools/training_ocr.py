"""قراءة ضوئية محلية لصور الشرائح بـ PaddleOCR؛ بلا Django وبلا أي خدمة خارجية.

    python training_ocr.py <manifest.jsonl> <results.jsonl> [--lang ar] [--no-mkldnn]

البيئة معزولة عن المنصة، وتُختار ببايثونها؛ الأداة نفسها لا تتغير بين المعالج والكرت:
    المعالج:  pip install paddlepaddle==3.2.2 paddleocr==3.5.0 pillow
    الكرت:    pip install paddlepaddle-gpu==3.2.2 -i <فهرس paddle لـ cu126، في المستند أدناه>
              pip install paddleocr==3.5.0 pillow
نسخة الكرت تستعمله تلقائيًا. القياس على RTX 4060 (2026-11-12): ربع ثانية للشريحة على
الكرت مقابل 17 ثانية على المعالج. التفصيل: docs/competition/training-content.md.

كل سطر في manifest: {"sha256": ..., "path": ...}. يُكتب لكل صورة سطر في results فور
قراءتها، والصور المقروءة سابقًا تُتخطى، فإعادة التشغيل تكمل من حيث توقف.
"""
import argparse
import json
import sys
from pathlib import Path


def ordered_text(texts, boxes, right_to_left=True):
    """يرتب الأسطر من أعلى إلى أسفل، وداخل السطر من اليمين إلى اليسار للعربية."""
    items = []
    for text, box in zip(texts, boxes, strict=True):
        left, top, right, bottom = (float(value) for value in box)
        items.append((top, bottom, left, right, text))
    items.sort(key=lambda item: (item[0] + item[1]) / 2)
    lines, current, limit = [], [], None
    for item in items:
        middle = (item[0] + item[1]) / 2
        if current and middle > limit:
            lines.append(current)
            current = []
        if not current:
            limit = item[1]  # ما يبدأ منتصفه قبل أسفل أول عنصر فهو في السطر نفسه
        current.append(item)
    if current:
        lines.append(current)
    out = []
    for line in lines:
        line.sort(key=lambda item: item[2], reverse=right_to_left)
        out.append(" ".join(item[4].strip() for item in line if item[4].strip()))
    return "\n".join(part for part in out if part)


ENGINE_SUFFIXES = frozenset(
    "bmp dib jpeg jpg png webp pbm pgm ppm pnm sr ras tiff tif pdf".split())


def first_frame(path):
    """الإطار الأول من الصورة مصفوفةَ BGR كما يتوقعها المحرك."""
    import numpy
    from PIL import Image
    with Image.open(path) as image:
        return numpy.asarray(image.convert("RGB"))[:, :, ::-1].copy()


def engine_input(path, decode=first_frame):
    """المحرك يرفض الملف بلاحقته (GIF مثلًا)؛ ما لا يقبله يُسلَّم له إطاره الأول مفكوكًا."""
    if Path(path).suffix.lower().lstrip(".") in ENGINE_SUFFIXES:
        return path
    return decode(path)


def done_hashes(path):
    seen = set()
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                row = json.loads(line)
                if not row.get("error"):
                    seen.add(row["sha256"])
    return seen


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("manifest", type=Path)
    parser.add_argument("results", type=Path)
    parser.add_argument("--lang", default="ar")
    # oneDNN يسرّع المعالج نحو 14 ضعفًا (7 ثوانٍ للشريحة بدل 100)، لكنه يتعطل في
    # paddlepaddle 3.3.0 (ConvertPirAttribute)؛ البيئة تُثبَّت على 3.2.2 أو يُعطَّل بهذا الخيار.
    parser.add_argument("--no-mkldnn", action="store_true")
    args = parser.parse_args(argv)
    rows = [json.loads(line) for line in args.manifest.read_text(encoding="utf-8").splitlines()
            if line.strip()]
    seen = done_hashes(args.results)
    todo = [row for row in rows if row["sha256"] not in seen]
    if not todo:
        return 0
    import paddleocr  # استيراد متأخر: الاختبارات لا تحتاج المحرك
    engine = f"paddleocr-{paddleocr.__version__}-{args.lang}"
    reader = paddleocr.PaddleOCR(
        lang=args.lang, use_doc_orientation_classify=False, use_doc_unwarping=False,
        use_textline_orientation=False, enable_mkldnn=not args.no_mkldnn)
    failed = 0
    with args.results.open("a", encoding="utf-8") as out:
        for row in todo:
            record = {"sha256": row["sha256"], "engine": engine}
            try:
                page = reader.predict(engine_input(row["path"]))[0]
                record["text"] = ordered_text(page["rec_texts"], page["rec_boxes"])
            except Exception as exc:  # صورة تالفة لا توقف الباقي
                failed += 1
                record["error"] = f"{type(exc).__name__}: {exc}"[:300]
            out.write(json.dumps(record, ensure_ascii=False) + "\n")
            out.flush()
    print(json.dumps({"read": len(todo) - failed, "failed": failed}), file=sys.stderr)
    return 1 if failed == len(todo) else 0  # فشل كل الصور عطل في المحرك لا في الصور


if __name__ == "__main__":
    raise SystemExit(main())
