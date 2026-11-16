"""
export_sh3d.py - Sweet Home 3D file straight from the building model.

* one .sh3d file per floor (default), or one file with a Sweet Home 3D level
  per floor (combined=True; elevations shifted so the lowest level sits at 0);
* walls keep their exact axis, thickness and height;
* every door, window, opening and garage door becomes a Sweet Home 3D
  doorOrWindow piece (generated simple 3D model, door swing as sash) placed in
  its wall, so Sweet Home 3D cuts the wall; replace them with catalogue models
  if you like (Modify furniture / drag a catalogue item onto it);
* rooms become floors with their names;
* optional: the original scan of each level (model `scans:`) as the level's
  background image, scaled and placed with the overlay control points.
Not exported (yet): stairs, roofs.
"""
from __future__ import annotations

import io
import math

import numpy as np
from shapely.geometry import MultiPolygon

from .dxf2sh3d import Seg, join_walls, pair_double
from .export_ifc import wall_extent
from .model import House
from .sh3d import Home


def _rect_axis(w):
    x1, y1, x2, y2 = w["rect"]
    x1, x2 = sorted((x1, x2))
    y1, y2 = sorted((y1, y2))
    if (x2 - x1) >= (y2 - y1):
        t = y2 - y1
        return (x1, (y1 + y2) / 2), (x2, (y1 + y2) / 2), t
    t = x2 - x1
    return ((x1 + x2) / 2, y1), ((x1 + x2) / 2, y2), t


def _poly_walls(poly):
    """Polygon wall -> straight wall pieces by pairing its parallel faces (cm)."""
    pts = [(x / 10, y / 10) for x, y in poly.exterior.coords]
    segs = [Seg(a, b) for a, b in zip(pts, pts[1:])]
    walls, _ = pair_double(segs, 3, 80)
    return join_walls(walls, 1.0)


def write_sh3d(H: House, out_dir, backgrounds=True, combined=False, suffix=""):
    """Writes one .sh3d per floor (default) named after the level's `file`
    entry; with combined=True a single file with one Sweet Home 3D level per
    floor. Returns the written paths."""
    from pathlib import Path
    out_dir = Path(out_dir)
    if combined:
        p = out_dir / f"{H.d['meta'].get('file_stem', 'House')}{suffix}.sh3d"
        _write(H, list(H.levels), p, backgrounds, with_levels=True)
        return [p]
    paths = []
    for lv in H.levels:
        p = out_dir / f"{H.levels[lv].get('file', lv)}{suffix}.sh3d"
        _write(H, [lv], p, backgrounds, with_levels=False)
        paths.append(p)
    return paths


def _write(H: House, keys, path, backgrounds, with_levels):
    levels = {k: H.levels[k] for k in keys}
    base = min(lv["elevation"] for lv in levels.values())
    name = H.d["meta"].get("project", "")
    if not with_levels:
        name = f"{name} – {levels[keys[0]]['name']}"
    home = Home(name=name, wall_height=levels[keys[0]]["clear_height"] / 10)
    lid = {k: None for k in keys}
    if with_levels:
        prev_slab = 0
        for key, lv in sorted(levels.items(), key=lambda kv: kv[1]["elevation"]):
            floor_t = lv.get("floor_thickness", prev_slab or 100)
            lid[key] = home.add_level(f"{key} {lv['name']}", (lv["elevation"] - base) / 10, floor_t / 10,
                                      lv["clear_height"] / 10)
            prev_slab = lv.get("slab_above", 0)

    for w in H.d["walls"]:
        if w["level"] not in levels:
            continue
        z0, h = wall_extent(H, w)
        if "rect" in w:
            a, b, t = _rect_axis(w)
            home.add_wall(a[0] / 10, a[1] / 10, b[0] / 10, b[1] / 10, t / 10, height=(z0 + h) / 10,
                          level=lid[w["level"]])
        else:
            for pw in _poly_walls(H.wall_poly(w)):
                home.add_wall(pw.a[0], pw.a[1], pw.b[0], pw.b[1], round(pw.t, 1), height=(z0 + h) / 10,
                              level=lid[w["level"]])

    for o in H.openings:
        if o["level"] not in levels:
            continue
        _add_opening(H, home, o, lid[o["level"]])

    for r in H.d["rooms"]:
        if r["level"] not in levels:
            continue
        poly = H.room_poly(r)
        for p in (list(poly.geoms) if isinstance(poly, MultiPolygon) else [poly]):
            pts = [(x / 10, y / 10) for x, y in list(p.exterior.coords)[:-1]]
            home.add_room(pts, name=r["name"], level=lid[r["level"]])

    if backgrounds:
        _add_backgrounds(H, home, lid)
    home.save(path)
    return home


KIND_MODEL = {"door": "door", "gate": "door", "window": "window", "garage_door": "garage", "opening": "opening"}
NAMES = {"door": "Door", "gate": "Gate", "window": "Window", "garage_door": "Garage door", "opening": "Opening"}


# --------------------------------------------------------------------------
# Sweet Home 3D catalogue (read from the user's own installation, never vendored)
# --------------------------------------------------------------------------
SH3D_DIRS = ["/usr/share/java/sweethome3d", "/usr/share/sweethome3d", "/opt/SweetHome3D/lib",
             "/Applications/Sweet Home 3D.app/Contents/Java"]


class Catalog:
    """Default door/window models of an installed Sweet Home 3D
    (DefaultFurnitureCatalog.properties + Furniture.jar). Empty if not found."""

    def __init__(self):
        import os
        import zipfile
        self.items, self._jars = {}, []
        dirs = [os.environ["SH3D_HOME"]] if os.environ.get("SH3D_HOME") else SH3D_DIRS
        for d in dirs:
            for name in ("Furniture.jar", "SweetHome3D.jar"):
                p = os.path.join(d, name)
                if os.path.exists(p):
                    self._jars.append(zipfile.ZipFile(p))
        props = self._read("com/eteks/sweethome3d/io/DefaultFurnitureCatalog.properties")
        if not props:
            return
        raw = {}
        for line in props.decode("latin-1").splitlines():
            if "#" in line and "=" in line and not line.startswith("#"):
                k, v = line.split("=", 1)
                key, _, idx = k.partition("#")
                raw.setdefault(idx, {})[key] = v.strip()
        for e in raw.values():
            if e.get("doorOrWindow") == "true" and "id" in e:
                self.items[e["id"]] = e

    def _read(self, path):
        for z in self._jars:
            try:
                return z.read(path.lstrip("/"))
            except KeyError:
                continue
        return None

    def model(self, cid):
        e = self.items.get(cid)
        return self._read(e["model"]) if e else None


_CATALOG = None


def catalog():
    global _CATALOG
    if _CATALOG is None:
        _CATALOG = Catalog()
    return _CATALOG


def pick_catalog_id(H: House, o):
    if o.get("sh3d_model"):
        return o["sh3d_model"]
    kind = o["kind"]
    if kind in ("door", "gate"):
        return "eTeks#frontDoor" if H.walls[o["wall"]]["type"] in ("exterior", "enclosure") else "eTeks#door"
    if kind == "window":
        tall = o["height"] > 1450
        if o["width"] >= 1100:
            return "eTeks#doubleWindow126x163" if tall else "eTeks#doubleWindow126x123"
        return "eTeks#window85x163" if tall else "eTeks#window85x123"
    if kind == "garage_door":
        return "eTeks#garageDoor"
    return "eTeks#doorFrame"     # opening without a door


def _floats(v):
    return [float(x) for x in v.split()] if v else []


def _add_opening(H: House, home: Home, o, level_id):
    cat = catalog()
    cid = pick_catalog_id(H, o)
    data = cat.model(cid) if cat.items else None
    if data is None:
        return _add_generated_opening(H, home, o, level_id)
    e = cat.items[cid]
    w = H.walls[o["wall"]]
    x1, y1, x2, y2 = w["rect"]
    along_x = H.wall_axis(w) == "x"
    wall_t = (abs(y2 - y1) if along_x else abs(x2 - x1)) / 10
    cx, cy = ((o["at"], (y1 + y2) / 2) if along_x else ((x1 + x2) / 2, o["at"]))
    cx, cy = cx / 10, cy / 10
    side = (o.get("swing") or {}).get("side", "+")
    hinge = (o.get("swing") or {}).get("hinge", "start")
    if not o.get("swing"):           # windows etc. open towards the room
        side = "+" if H.interior_side(o) > 0 else "-"
    if along_x:
        angle = 0.0 if side == "+" else math.pi
        hinge_left = (hinge == "start") == (side == "+")
    else:
        angle = math.pi / 2 if side == "+" else -math.pi / 2
        hinge_left = (hinge == "end") == (side == "+")
    angle += math.pi                 # see _add_generated_opening (verified orientation)
    hinge_left = not hinge_left
    # depth: scale the catalogue piece so that its wall part equals the real wall
    d = float(e.get("depth", 10))
    wt = float(e.get("doorOrWindowWallThickness") or d)
    wd = float(e.get("doorOrWindowWallDistance") or 0)
    sc = wall_t / wt
    depth = d * sc
    # centre of the wall part (local y, from the piece centre) must sit on the wall axis
    off = (-d / 2 + wd + wt / 2) * sc
    sx, sy = cx, -cy                                     # Sweet Home 3D plan (y down)
    sx -= -math.sin(angle) * off
    sy -= math.cos(angle) * off
    sashes = []
    xs, ys_, ws = _floats(e.get("doorOrWindowSashXAxis")), _floats(e.get("doorOrWindowSashYAxis")), \
        _floats(e.get("doorOrWindowSashWidth"))
    st, en = _floats(e.get("doorOrWindowSashStartAngle")), _floats(e.get("doorOrWindowSashEndAngle"))
    cw = float(e.get("width", 100))
    for i in range(len(xs)):
        sashes.append((xs[i] / cw, ys_[i] / d, ws[i] / cw, math.radians(st[i]), math.radians(en[i])))
    elev = o.get("sill", 0) if o["kind"] == "window" else 0
    entry = f"models/{cid.split('#')[-1]}.obj"
    home.add_catalog_door_or_window(
        entry, data, f"{e.get('name', cid)} {o['id']} {o['width'] // 10}/{o['height'] // 10}",
        sx, -sy, angle, o["width"] / 10, depth, o["height"] / 10, elev / 10, level=level_id,
        sashes=sashes, mirrored=not hinge_left and bool(sashes), wall_thickness=wt / d,
        wall_distance=wd / d, catalog_id=cid)


def _add_generated_opening(H: House, home: Home, o, level_id):
    """Door/window as a Sweet Home 3D doorOrWindow piece: centred in the wall,
    as deep as the wall (so Sweet Home 3D cuts it), door swing as a sash.
    Verified against Sweet Home 3D's own plan rendering (scripts/Sh3dRender.java)."""
    w = H.walls[o["wall"]]
    x1, y1, x2, y2 = w["rect"]
    along_x = H.wall_axis(w) == "x"
    t = abs(y2 - y1) if along_x else abs(x2 - x1)
    cx, cy = (o["at"], (y1 + y2) / 2) if along_x else ((x1 + x2) / 2, o["at"])
    side = (o.get("swing") or {}).get("side", "+")
    hinge = (o.get("swing") or {}).get("hinge", "start")
    if not o.get("swing"):           # windows etc. open towards the room
        side = "+" if H.interior_side(o) > 0 else "-"
    # angle (SH3D, y down): front must face the swing side
    if along_x:
        angle = 0.0 if side == "+" else math.pi
        hinge_left = (hinge == "start") == (side == "+")
    else:
        angle = math.pi / 2 if side == "+" else -math.pi / 2
        hinge_left = (hinge == "end") == (side == "+")
    # Sweet Home 3D draws the sash on the back side of the piece (+y local):
    # turn the piece by 180 deg, which also swaps left and right
    angle += math.pi
    hinge_left = not hinge_left
    sash = None
    if o["kind"] in ("door", "gate"):
        sash = (0, 0, 1, 0, -math.pi / 2) if hinge_left else (1, 0, 1, math.pi, 1.5 * math.pi)
    elev = o.get("sill", 0) if o["kind"] == "window" else 0
    home.add_door_or_window(KIND_MODEL[o["kind"]], f"{NAMES[o['kind']]} {o['id']} {o['width'] // 10}/{o['height'] // 10}",
                            cx / 10, cy / 10, angle, o["width"] / 10, t / 10, o["height"] / 10, elev / 10,
                            level=level_id, sash=sash)


def _add_backgrounds(H: House, home: Home, lid):
    from PIL import Image

    from .overlay import fit_affine
    for lv, cfg in (H.d.get("scans") or {}).items():
        if lv not in lid:
            continue
        img = Image.open(H.base / cfg["image"]).convert("RGB")
        if cfg.get("rotate"):
            img = img.rotate(-cfg["rotate"], expand=True)
        M, _ = fit_affine(cfg["control_points"])        # model metres -> image pixels
        # two reference points 10 m apart along x give the scale
        p0 = M @ [0, 0, 1]
        p1 = M @ [10, 0, 1]
        # image pixel (0,0) in model metres (inverse affine)
        A = M[:, :2]
        top_left_m = np.linalg.solve(A, -M[:, 2])
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        # Sweet Home 3D draws the image's top-left corner at (-xOrigin, -yOrigin)
        # in its y-down plan; our y is flipped, so y_sh3d = -y_cm
        home.set_background(buf.getvalue(), 1000.0, float(p0[0]), float(p0[1]), float(p1[0]), float(p1[1]),
                            x_origin=-top_left_m[0] * 100, y_origin=top_left_m[1] * 100, level=lid[lv])
