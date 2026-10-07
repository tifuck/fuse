#!/usr/bin/env python3
"""Report oversized handwritten source files; never rewrites or splits them."""
from __future__ import annotations

import argparse
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]
EXTENSIONS = {".py", ".js", ".cjs", ".css", ".html", ".c", ".h", ".go", ".sh"}
EXCLUDED = {"third_party", "vendor", "node_modules", "fixtures", "dict"}


def source_sizes(root: Path):
    paths = subprocess.check_output(
        ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"], cwd=root
    ).decode().split("\0")
    for name in sorted(set(filter(None, paths))):
        path = Path(name)
        if path.suffix not in EXTENSIONS or EXCLUDED.intersection(path.parts):
            continue
        if ".min." in path.name:
            continue
        full = root / path
        if full.is_symlink() or not full.is_file():
            continue
        yield len(full.read_bytes().splitlines()), name


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--threshold", type=int, default=600)
    args = parser.parse_args()
    if args.threshold < 1:
        parser.error("--threshold must be positive")
    rows = sorted(source_sizes(ROOT), reverse=True)
    oversized = [(lines, name) for lines, name in rows if lines > args.threshold]
    print(f"{'LINES':>7}  FILE")
    for lines, name in oversized:
        print(f"{lines:7}  {name}")
    print(f"\n{len(oversized)} of {len(rows)} source files exceed {args.threshold} lines.")
    print("Review responsibilities before splitting; this report is advisory.")


if __name__ == "__main__":
    main()
