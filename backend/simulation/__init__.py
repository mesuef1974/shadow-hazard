"""Local simulation integration; shared domain remains in src/."""
from pathlib import Path
import sys

source = str(Path(__file__).resolve().parents[2] / 'src')
if source not in sys.path:
    sys.path.insert(0, source)
