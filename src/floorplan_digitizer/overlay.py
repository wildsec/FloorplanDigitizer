"""
overlay.py - validation step: draws the generated model over the original scans
so that deviations become visible.

The model needs a `scans:` section (paths relative to the model file):

    scans:
      EG:
        image: ../Originals/ground_floor.png
        control_points:            # [x_m, y_m, pixel_x, pixel_y]
          - [0, 0, 138.5, 834.5]
          - [12, 0, 560.8, 834.5]
          - [12, 10, 560.8, 485.4]

At least three points (outer corners work best). An affine transform is fitted
by least squares; the residuals per point are reported (they show scan distortion).

Red = walls, blue = openings, green = stair treads, orange = chimneys.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageOps
from shapely.geometry import MultiPolygon

from .model import House

UP = 3  # upscale factor for readability


def fit_affine(points):
    A, b = [], []
    for x, y, u, v in points:
        A.append([x, y, 1, 0, 0, 0]); b.append(u)
        A.append([0, 0, 0, x, y, 1]); b.append(v)
    p, *_ = np.linalg.lstsq(np.array(A, float), np.array(b, float), rcond=None)
    M = p.reshape(2, 3)
    res = [float(np.hypot(*(M @ [x, y, 1] - [u, v]))) for x, y, u, v in points]
    return M, res


def _polys(g):
    return list(g.geoms) if isinstance(g, MultiPolygon) else [g]


def write_overlays(H: House, out_dir: Path, file_names: dict | None = None) -> list[dict]:
    """Writes Overlay_<level>.png for every level listed under `scans:`.
    Returns a report per level (px/m and residuals in cm)."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    report = []
    for lv, cfg in (H.d.get("scans") or {}).items():
        pts = cfg["control_points"]
        if len(pts) < 3:
            raise ValueError(f"scans.{lv}: need at least 3 control points")
        M, res = fit_affine(pts)
        px_per_m = float(np.hypot(*M[:, 0]))
        img = Image.open(H.base / cfg["image"]).convert("L")
        if cfg.get("rotate"):
            img = img.rotate(-cfg["rotate"], expand=True)  # clockwise degrees
        img = ImageOps.autocontrast(img).point(lambda v: 150 + v * 105 // 255)  # fade
        img = img.resize((img.width * UP, img.height * UP), Image.LANCZOS).convert("RGBA")
        over = Image.new("RGBA", img.size, (0, 0, 0, 0))
        d = ImageDraw.Draw(over)

        def T(x, y):
            return tuple(UP * (M @ [x / 1000, y / 1000, 1]))

        def draw(geom, fill, outline, width=2):
            for p in _polys(geom):
                if not p.is_empty:
                    d.polygon([T(x, y) for x, y in p.exterior.coords], fill=fill, outline=outline, width=width)

        draw(H.level_walls_union(lv, cut=True), (220, 0, 0, 70), (220, 0, 0, 255))
        for o in H.openings_on(lv):
            draw(H.opening_box(o), (0, 90, 255, 90), (0, 90, 255, 255))
        for s in H.d.get("stairs", []):
            if s.get("draw_on", {}).get(lv) in ("full", "cut"):
                for t in H.stair_treads(s)[0]:
                    draw(t, None, (0, 150, 0, 255), 1)
        for c in H.d.get("chimneys", []):
            if lv in c["levels"]:
                draw(H.rect_or_poly(c), (255, 160, 0, 120), (200, 100, 0, 255))
        name = (file_names or {}).get(lv, H.levels[lv].get("file", lv))
        path = out_dir / f"Overlay_{name}.png"
        Image.alpha_composite(img, over).convert("RGB").save(path)
        report.append({"level": lv, "px_per_m": px_per_m,
                       "residuals_cm": [r / px_per_m * 100 for r in res], "file": str(path)})
    return report
