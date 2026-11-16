"""Shared fixtures. All test data is synthetic and generated on the fly."""
from __future__ import annotations

import os
import shutil
import subprocess
import urllib.request
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
DTD_URL = "https://www.sweethome3d.com/SweetHome3D.dtd"
DEFAULT_JAR = "/usr/share/java/sweethome3d/SweetHome3D.jar"


@pytest.fixture(scope="session")
def dtd_path():
    """SweetHome3D.dtd: $SH3D_DTD, a cached copy, or downloaded once (GPL, not vendored)."""
    env = os.environ.get("SH3D_DTD")
    if env and Path(env).exists():
        return Path(env)
    cache = HERE / ".cache" / "SweetHome3D.dtd"
    if not cache.exists():
        cache.parent.mkdir(exist_ok=True)
        try:
            req = urllib.request.Request(DTD_URL, headers={"User-Agent": "FloorplanDigitizer-tests/0.1"})
            with urllib.request.urlopen(req, timeout=20) as r:
                cache.write_bytes(r.read())
        except Exception as e:  # offline
            pytest.skip(f"SweetHome3D.dtd not available ({e}); set SH3D_DTD")
    return cache


def _jar():
    jar = os.environ.get("SH3D_JAR", DEFAULT_JAR)
    return jar if Path(jar).exists() and shutil.which("java") else None


@pytest.fixture(scope="session")
def sh3d_oracle():
    """Loads a .sh3d with Sweet Home 3D's own reader (headless) - skipped when
    Java or SweetHome3D.jar is missing. Returns a parse function."""
    jar = _jar()
    if not jar:
        pytest.skip("java / SweetHome3D.jar not available (set SH3D_JAR)")

    def run(path):
        out = subprocess.run(["java", "-cp", jar, str(ROOT / "scripts" / "Sh3dCheck.java"), str(path)],
                             capture_output=True, text=True, timeout=180)
        assert out.returncode == 0, out.stderr[-2000:]
        res = {"walls": [], "rooms": [], "levels": [], "bg": None, "dw": []}
        for line in out.stdout.splitlines():
            f = line.split("\t")
            if f[0] == "HOME":
                res["counts"] = tuple(int(v) for v in f[1:4])
            elif f[0] == "LEVEL":
                res["levels"].append((f[1], *map(float, f[2:5])))
            elif f[0] == "WALL":
                res["walls"].append({"xs": float(f[1]), "ys": float(f[2]), "xe": float(f[3]), "ye": float(f[4]),
                                     "t": float(f[5]), "arc": None if f[7] == "-" else float(f[7]),
                                     "at_start": f[8] == "true", "at_end": f[9] == "true",
                                     "length": float(f[10]), "mid": (float(f[11]), float(f[12]))})
            elif f[0] == "ROOM":
                res["rooms"].append((None if f[1] == "-" else f[1], int(f[2]), float(f[3])))
            elif f[0] == "DW":
                res["dw"].append({"name": f[1], "x": float(f[2]), "y": float(f[3]), "width": float(f[4]),
                                  "depth": float(f[5]), "height": float(f[6]), "elevation": float(f[7]),
                                  "angle": float(f[8]), "sashes": int(f[9]), "model": f[10] == "true"})
            elif f[0] == "BG":
                res["bg"] = tuple(map(float, f[1:]))
        return res
    return run
