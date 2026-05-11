from __future__ import annotations

import sys
from pathlib import Path

# Ensure `src/` is importable in tests (repo is not packaged).
ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

