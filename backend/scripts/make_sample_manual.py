"""Write the fictional sample step stool manual to manuals/sample_step_stool.pdf.

    python backend/scripts/make_sample_manual.py
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from apprentice.sample import build_pdf  # noqa: E402

if __name__ == "__main__":
    out = build_pdf(ROOT / "manuals" / "sample_step_stool.pdf")
    print(f"Wrote {out}")
