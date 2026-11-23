"""تفريغ تسجيل لقاء تدريبي إلى نص بتوقيت: faster-whisper (large-v3-turbo) على الكرت، وإلا المعالج.

الاستعمال (ببايثون فيه faster-whisper وtorch، لا بيئة المنصة):
    python tools/transcribe_recording.py <صوت أو فيديو> <ملف التفريغ> "<وصف اللقاء>"

الناتج سطر لكل مقطع بصيغة [س:د:ث] نص، وسطره الأول يصف اللقاء ويذكر أنه غير مراجَع؛ وهي الصيغة التي
يقبلها `manage.py training_content transcript`. الصوت يُفك بـ ffmpeg (يلزم في PATH) لا بـ PyAV،
لأن إصدار PyAV 19 يرفض وسيطًا تمرّره faster-whisper. مكتبات CUDA (cublas وcudnn) تُؤخذ من مجلد
torch/lib إن وُجد. استخراج الصوت من رابط ثم المسار كاملًا في docs/competition/training-content.md.
"""
import importlib.util
import os
import subprocess
import sys
import time
from pathlib import Path

import numpy as np

MODEL = "mobiuslabsgmbh/faster-whisper-large-v3-turbo"
RATE = 16000


def add_cuda_libs():
    """يضيف torch/lib إلى مسار المكتبات ليجد ctranslate2 ملفات cublas وcudnn."""
    spec = importlib.util.find_spec("torch")
    if not spec or not spec.submodule_search_locations:
        return
    lib = Path(next(iter(spec.submodule_search_locations))) / "lib"
    if lib.is_dir():
        os.environ["PATH"] = f"{lib}{os.pathsep}{os.environ['PATH']}"
        if hasattr(os, "add_dll_directory"):
            os.add_dll_directory(str(lib))


def decode(src):
    """صوت أحادي 16 كيلوهرتز بصيغة float32 من أي ملف يفهمه ffmpeg."""
    raw = subprocess.run(
        ["ffmpeg", "-nostdin", "-v", "error", "-i", src, "-f", "f32le", "-ac", "1",
         "-ar", str(RATE), "-"], capture_output=True, check=True).stdout
    return np.frombuffer(raw, dtype=np.float32)


def stamp(seconds):
    s = int(seconds)
    return f"{s // 3600:02d}:{s % 3600 // 60:02d}:{s % 60:02d}"


def load_model():
    from faster_whisper import WhisperModel
    try:
        return WhisperModel(MODEL, device="cuda", compute_type="float16"), "cuda"
    except Exception as error:  # الكرت غائب أو مكتباته ناقصة: نكمل على المعالج
        print("cuda failed:", error, flush=True)
        return WhisperModel(MODEL, device="cpu", compute_type="int8"), "cpu"


def main(src, out, title):
    add_cuda_libs()
    model, device = load_model()
    print("device", device, flush=True)
    audio = decode(src)
    segments, info = model.transcribe(
        audio, language="ar", vad_filter=True, beam_size=5, condition_on_previous_text=False)
    started, last = time.time(), 0
    with open(out, "w", encoding="utf-8") as f:
        f.write(f"تفريغ آلي (Whisper large-v3-turbo) لتسجيل {title}. غير مراجَع بشريًا.\n\n")
        for segment in segments:
            f.write(f"[{stamp(segment.start)}] {segment.text.strip()}\n")
            f.flush()
            last = segment.end
    print("done", stamp(last), "of", stamp(info.duration),
          "elapsed", int(time.time() - started), flush=True)


if __name__ == "__main__":
    if len(sys.argv) != 4:
        sys.exit(__doc__)
    main(*sys.argv[1:])
