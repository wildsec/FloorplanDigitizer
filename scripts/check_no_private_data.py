#!/usr/bin/env python3
"""
check_no_private_data.py - guard against committing real floor plans.

Fails when a file that is (about to be) committed looks like plan data:
  * drawings, scans, renders, CAD/BIM/Sweet Home 3D files, archives, office files
  * building models (YAML) outside examples/ (real models belong in the user's folder)
  * files larger than 1 MB

  python scripts/check_no_private_data.py --staged   # pre-commit hook
  python scripts/check_no_private_data.py --all      # CI: every tracked file
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

BLOCKED_EXT = {
    ".dxf", ".dwg", ".dwf", ".sh3d", ".sh3f", ".ifc", ".ifczip", ".rvt", ".skp", ".3dm", ".fcstd", ".blend",
    ".obj", ".stl", ".glb", ".gltf",
    ".pdf", ".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".gif", ".webp", ".heic", ".svg",
    ".zip", ".7z", ".rar", ".tar", ".gz",
    ".doc", ".docx", ".xls", ".xlsx", ".odt", ".ods",
}
ALLOWED_YAML_DIRS = ("examples/", ".github/")
MAX_BYTES = 1_000_000


def files(staged: bool):
    if staged:
        out = subprocess.run(["git", "diff", "--cached", "--name-only", "--diff-filter=ACMR"],
                             capture_output=True, text=True, check=True).stdout
    else:
        out = subprocess.run(["git", "ls-files"], capture_output=True, text=True, check=True).stdout
    return [f for f in out.splitlines() if f]


def main():
    staged = "--staged" in sys.argv
    bad = []
    for f in files(staged):
        p = Path(f)
        ext = p.suffix.lower()
        if ext in BLOCKED_EXT:
            bad.append(f"{f}: file type {ext} is not allowed (plans, scans, CAD/BIM/3D outputs stay private)")
        elif ext in (".yaml", ".yml") and not f.startswith(ALLOWED_YAML_DIRS):
            bad.append(f"{f}: building models belong in your own project folder, not in this repo")
        elif p.exists() and p.stat().st_size > MAX_BYTES:
            bad.append(f"{f}: larger than 1 MB")
    if bad:
        print("Refusing to commit possible private plan data:\n  " + "\n  ".join(bad))
        print("If a file is truly generic (e.g. a synthetic example), discuss it first and adjust this guard.")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
