#!/usr/bin/env python3
"""Convenience entry point: python dxf2sh3d.py input.dxf -o output.sh3d [options]
(same as the installed `dxf2sh3d` command)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))
from floorplan_digitizer.dxf2sh3d import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
