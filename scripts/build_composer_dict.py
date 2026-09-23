#!/usr/bin/env python3
"""Build static/dict/en-US.bin.gz from vendored SCOWL size 60 sources."""
from __future__ import annotations

import gzip
import re
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCOWL = ROOT / "third_party" / "scowl"
FINAL = SCOWL / "final"
OUT_DIR = ROOT / "static" / "dict"
OUT = OUT_DIR / "en-US.bin.gz"
LICENSE_OUT = OUT_DIR / "LICENSE"

# SCOWL 60: english + US spelling variants. Size 60 of english-words is the
# last attribution-permissive band; skip 70+.
SIZES = (10, 20, 35, 40, 50, 55, 60)
PREFIXES = (
    "english-words",
    "english-contractions",
    "american-words",
    "american-contractions",
)
WORD_RE = re.compile(r"^[A-Za-z]+(?:'[A-Za-z]+)?$")


def collect() -> set[str]:
    words: set[str] = set()
    for prefix in PREFIXES:
        for size in SIZES:
            path = FINAL / f"{prefix}.{size}"
            if not path.is_file():
                continue
            for raw in path.read_text(encoding="utf-8", errors="ignore").splitlines():
                word = raw.strip().lower()
                if WORD_RE.match(word) and 2 <= len(word) <= 32:
                    words.add(word)
    return words


def main() -> None:
    words = collect()
    # Size 60 english+american is typically ~90–110k unique lowercase forms.
    if not (35000 <= len(words) <= 120000):
        raise SystemExit(f"unexpected word count {len(words)}; expected 35000-120000")
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    payload = ("\n".join(sorted(words)) + "\n").encode("utf-8")
    with gzip.open(OUT, "wb", compresslevel=9) as fh:
        fh.write(payload)
    src_license = SCOWL / "Copyright"
    if src_license.is_file():
        shutil.copyfile(src_license, LICENSE_OUT)
    print(f"words={len(words)} gzip={OUT.stat().st_size} path={OUT}")


if __name__ == "__main__":
    main()
