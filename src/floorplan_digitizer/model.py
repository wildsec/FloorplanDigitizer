"""
model.py - core of FloorplanDigitizer.

Loads a building model (YAML, see skills/digitize-floorplan/references/model-schema.md),
builds exact geometry with shapely and turns it into a neutral list of drawing
primitives per level. The SVG, DXF, IFC and Sweet Home 3D writers all consume
this module, so every output is generated from the same numbers.

Units: model = millimetres; paper sizes (line weights, text) = mm at 1:100.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path

import yaml
from shapely import affinity
from shapely.geometry import LineString, MultiPolygon, Point, Polygon, box
from shapely.ops import polylabel, unary_union

SCALE = 100  # 1:100

LAYERS = {
    "L01": ("LAYER_01_EXTERIOR_WALLS", 7, 50),
    "L02": ("LAYER_02_INTERIOR_WALLS", 7, 35),
    "L03": ("LAYER_03_DOORS", 3, 25),
    "L04": ("LAYER_04_WINDOWS", 5, 25),
    "L05": ("LAYER_05_STAIRS", 6, 25),
    "L06": ("LAYER_06_ROOMS", 8, 13),
    "L07": ("LAYER_07_LABELS", 7, 18),
    "L08": ("LAYER_08_DIMENSIONS", 1, 18),
    "L09": ("LAYER_09_PLUMBING", 4, 18),
    "L10": ("LAYER_10_OTHER_ELEMENTS", 2, 18),
    "L11": ("LAYER_11_UNCERTAIN", 1, 18),
}

WALL_LAYER = {"exterior": "L01", "garage": "L01", "enclosure": "L01",
              "interior": "L02", "knee": "L02",
              "retaining": "L10", "foundation": "L10"}
WALL_FILL = {"L01": "#3c3c3c", "L02": "#9b9b9b", "L10": "hatch"}


# --------------------------------------------------------------------------
# primitives
# --------------------------------------------------------------------------
@dataclass
class Prim:
    layer: str
    kind: str                    # shape | line | arc | circle | text | dim
    geom: object = None          # shapely geometry (shape) / list of points (line)
    style: dict = field(default_factory=dict)
    group: str | None = None     # element id -> SVG id / DXF block
    meta: dict = field(default_factory=dict)


DIM_FORMAT = {"style": "at"}


def fmt_len(mm: float) -> str:
    """Dimension text. style 'at' (default, Austrian/German plan convention):
    >= 1 m in metres with 2 decimals, below 1 m in centimetres (35 = 0.35 m).
    style 'mm': plain millimetres. style 'm': always metres, 2 decimals."""
    from decimal import ROUND_HALF_UP, Decimal
    if DIM_FORMAT["style"] == "mm":
        return f"{round(mm)}"
    if DIM_FORMAT["style"] == "m":
        return str(Decimal(mm / 1000).quantize(Decimal("0.01"), ROUND_HALF_UP))
    from decimal import ROUND_HALF_UP, Decimal
    mm = round(mm)
    if abs(mm) >= 1000:
        return str(Decimal(mm / 1000).quantize(Decimal("0.01"), ROUND_HALF_UP))
    cm = mm / 10
    return f"{cm:.0f}" if abs(cm - round(cm)) < 1e-6 else f"{cm:.1f}"


def src_is_estimated(*srcs) -> bool:
    return any(s and "E" in str(s) for s in srcs)


# --------------------------------------------------------------------------
# model
# --------------------------------------------------------------------------
class House:
    def __init__(self, path):
        self.path = Path(path)
        self.base = self.path.parent
        self.d = yaml.safe_load(self.path.read_text(encoding="utf-8"))
        DIM_FORMAT["style"] = self.d.get("meta", {}).get("dim_format", "at")
        self.levels = self.d["levels"]
        self.walls = {w["id"]: w for w in self.d["walls"]}
        self.openings = self.d["openings"]
        for o in self.openings:
            if o["wall"] not in self.walls:
                raise ValueError(f"opening {o['id']}: unknown wall {o['wall']}")

    # ---------- basic geometry ----------
    @staticmethod
    def rect_or_poly(e) -> Polygon:
        if "rect" in e:
            x1, y1, x2, y2 = e["rect"]
            return box(min(x1, x2), min(y1, y2), max(x1, x2), max(y1, y2))
        return Polygon(e["poly"])

    def wall_poly(self, w) -> Polygon:
        return self.rect_or_poly(w)

    @staticmethod
    def wall_axis(w) -> str:
        """'x' if the wall runs along x (horizontal in plan) else 'y'."""
        x1, y1, x2, y2 = w["rect"]
        return "x" if abs(x2 - x1) >= abs(y2 - y1) else "y"

    def opening_box(self, o) -> Polygon:
        w = self.walls[o["wall"]]
        x1, y1, x2, y2 = w["rect"]
        h = o["width"] / 2
        if self.wall_axis(w) == "x":
            return box(o["at"] - h, min(y1, y2), o["at"] + h, max(y1, y2))
        return box(min(x1, x2), o["at"] - h, max(x1, x2), o["at"] + h)

    def walls_on(self, level):
        return [w for w in self.d["walls"] if w["level"] == level]

    def openings_on(self, level):
        return [o for o in self.openings if o["level"] == level]

    def wall_cut(self, w) -> Polygon:
        g = self.wall_poly(w)
        for o in self.openings:
            if o["wall"] == w["id"]:
                g = g.difference(self.opening_box(o))
        return g

    def level_walls_union(self, level, cut=False, types=None):
        geoms = []
        for w in self.walls_on(level):
            if types and w["type"] not in types:
                continue
            geoms.append(self.wall_cut(w) if cut else self.wall_poly(w))
        return unary_union(geoms) if geoms else Polygon()

    def north_arrow_at(self):
        at = self.d["meta"].get("north_arrow_at")
        if at:
            return tuple(at)
        allw = unary_union([self.wall_poly(w) for w in self.d["walls"]])
        minx, miny, maxx, maxy = allw.bounds
        return (minx - 1800, maxy - 800)

    def rooms_on(self, level):
        return [r for r in self.d["rooms"] if r["level"] == level]

    def room_poly(self, r) -> Polygon:
        return self.rect_or_poly(r)

    def envelope(self, level) -> Polygon:
        """Filled outer outline of the exterior walls of a level (house only)."""
        ext = [self.wall_poly(w) for w in self.walls_on(level) if w["type"] == "exterior"]
        u = unary_union(ext)
        polys = list(u.geoms) if isinstance(u, MultiPolygon) else [u]
        big = max(polys, key=lambda p: p.area)
        return Polygon(big.exterior)

    def interior_side(self, o):
        """+1/-1: which side of the wall (along its normal) is inside a room."""
        w = self.walls[o["wall"]]
        x1, y1, x2, y2 = w["rect"]
        ax = self.wall_axis(w)
        rooms = [self.room_poly(r) for r in self.rooms_on(o["level"])]
        if ax == "x":
            mid, t = (y1 + y2) / 2, abs(y2 - y1)
            pts = {+1: Point(o["at"], mid + t / 2 + 200), -1: Point(o["at"], mid - t / 2 - 200)}
        else:
            mid, t = (x1 + x2) / 2, abs(x2 - x1)
            pts = {+1: Point(mid + t / 2 + 200, o["at"]), -1: Point(mid - t / 2 - 200, o["at"])}
        for s, p in pts.items():
            if any(r.contains(p) for r in rooms):
                return s
        return +1

    # ---------- stairs ----------
    def stair_treads(self, s):
        """List of tread polygons in walking order (bottom -> top of the
        definition) and the walking-line points."""
        treads, walk = [], []
        self._seg_of = []
        for si, seg in enumerate(s["segments"]):
            n_before = len(treads)
            x1, y1, x2, y2 = seg["box"]
            if seg["type"] == "straight":
                n, d = seg["n"], seg["dir"]
                for i in range(n):
                    if d in ("+x", "-x"):
                        step = (x2 - x1) / n
                        a = x1 + i * step if d == "+x" else x2 - (i + 1) * step
                        treads.append(box(a, y1, a + step, y2))
                    else:
                        step = (y2 - y1) / n
                        a = y1 + i * step if d == "+y" else y2 - (i + 1) * step
                        treads.append(box(x1, a, x2, a + step))
                cx, cy = (x1 + x2) / 2, (y1 + y2) / 2
                line = {"+x": [(x1, cy), (x2, cy)], "-x": [(x2, cy), (x1, cy)],
                        "+y": [(cx, y1), (cx, y2)], "-y": [(cx, y2), (cx, y1)]}[d]
                walk.extend(line)
            elif seg["type"] == "landing":
                treads.append(box(x1, y1, x2, y2))
                walk.extend(tuple(q) for q in seg.get("walk", [((x1 + x2) / 2, (y1 + y2) / 2)]))
            elif seg["type"] == "winder":
                px, py = seg["pivot"]
                bx = box(x1, y1, x2, y2)
                a0, a1, n = seg["a0"], seg["a1"], seg["n"]
                for i in range(n):
                    t0 = a0 + (a1 - a0) * i / n
                    t1 = a0 + (a1 - a0) * (i + 1) / n
                    pts = [(px, py)] + [
                        (px + 20000 * math.cos(math.radians(t0 + (t1 - t0) * k / 16)),
                         py + 20000 * math.sin(math.radians(t0 + (t1 - t0) * k / 16)))
                        for k in range(17)]
                    treads.append(Polygon(pts).intersection(bx))
                r = s["width"] / 2
                walk.extend((px + r * math.cos(math.radians(a0 + (a1 - a0) * k / 12)),
                             py + r * math.sin(math.radians(a0 + (a1 - a0) * k / 12)))
                            for k in range(13))
            self._seg_of += [si] * (len(treads) - n_before)
        # remove consecutive duplicates
        clean = []
        for p in walk:
            if not clean or math.dist(clean[-1], p) > 1:
                clean.append(p)
        return treads, clean

    def stair_info(self, s):
        treads, _ = self.stair_treads(s)
        h = s["rise"] / s["risers"]
        goings = []
        for seg in s["segments"]:
            if seg["type"] == "straight":
                x1, y1, x2, y2 = seg["box"]
                L = abs(x2 - x1) if seg["dir"] in ("+x", "-x") else abs(y2 - y1)
                goings.append(L / seg["n"])
        return {"risers": s["risers"], "riser_h": h, "treads": len(treads),
                "going": min(goings) if goings else None}


# --------------------------------------------------------------------------
# primitive generation per level
# --------------------------------------------------------------------------
def build_level(house: House, level: str) -> list[Prim]:
    H, P = house, []
    lv = H.levels[level]

    # ---- walls (individual cut shapes, fill only) ----
    for w in H.walls_on(level):
        lay = WALL_LAYER[w["type"]]
        P.append(Prim(lay, "shape", H.wall_cut(w),
                      {"fill": WALL_FILL[lay], "stroke": None},
                      group=w["id"], meta={"src": w.get("src"), "note": w.get("note", ""),
                                           "type": w["type"], "role": "wall"}))
    # ---- wall outlines (clean union line work) ----
    for lay, types in (("L01", ("exterior", "garage", "enclosure")),
                       ("L02", ("interior", "knee")),
                       ("L10", ("retaining", "foundation"))):
        u = H.level_walls_union(level, cut=True, types=types)
        if not u.is_empty:
            P.append(Prim(lay, "shape", u, {"fill": None, "stroke": 0.35 if lay != "L01" else 0.5},
                          group=f"{level}-{lay}-outline", meta={"role": "outline"}))

    # ---- openings ----
    for o in H.openings_on(level):
        P.extend(opening_prims(H, o))

    # ---- rooms ----
    for r in H.rooms_on(level):
        poly = H.room_poly(r)
        P.append(Prim("L06", "shape", poly, {"fill": None, "stroke": 0.13, "dash": "room"},
                      group=r["id"], meta={"name": r["name"], "area": poly.area / 1e6,
                                           "area_original": r.get("area_original", r.get("area_1964")), "src": r.get("src"),
                                           "role": "room"}))
        cx, cy = label_point(poly, r.get("label_at"))
        b = poly.bounds
        small = min(b[2] - b[0], b[3] - b[1]) < 1300
        k = 0.62 if small else 1.0
        P.append(Prim("L07", "text", [(cx, cy + 260 * k)], {"text": r["name"], "size": 3.0 * (0.7 if small else 1), "weight": "bold"},
                      group=f"{r['id']}-label"))
        P.append(Prim("L07", "text", [(cx, cy - 60 * k)], {"text": f"{poly.area / 1e6:.2f} m²", "size": 2.5 * (0.72 if small else 1)},
                      group=f"{r['id']}-label"))
        orig = r.get("area_original", r.get("area_1964"))
        olab = H.d["meta"].get("original_area_label", "plan")
        sub = r["id"] + (f" · {olab}: {orig:.2f} m²" if orig else "")
        if r.get("floor_offset"):
            sub += f" · FFB +{r['floor_offset'] / 1000:.2f}"
        P.append(Prim("L07", "text", [(cx, cy - 330 * k)], {"text": sub, "size": 1.7 * (0.8 if small else 1), "color": "#666"},
                      group=f"{r['id']}-label"))

    for a in H.d.get("areas", []):
        if a["level"] != level:
            continue
        poly = H.rect_or_poly(a)
        if a.get("hatch") == "tiles":
            P.extend(tile_hatch(poly, 500, a["id"]))
        P.append(Prim("L10", "shape", poly, {"fill": None, "stroke": 0.18}, group=a["id"]))
        lp = Point(*label_point(poly, a.get("label_at")))
        P.append(Prim("L07", "text", [(lp.x, lp.y + 120)], {"text": a["name"], "size": 2.5 if poly.area > 3e6 else 2.0, "style": "italic"},
                      group=f"{a['id']}-label"))
        if a.get("note"):
            P.append(Prim("L07", "text", [(lp.x, lp.y - 220)], {"text": a["note"], "size": 1.8, "color": "#555"},
                          group=f"{a['id']}-label"))

    # ---- stairs ----
    for s in H.d["stairs"]:
        mode = s.get("draw_on", {}).get(level)
        if mode:
            P.extend(stair_prims(H, s, mode, level))

    # ---- chimneys ----
    for c in H.d["chimneys"]:
        if level in c["levels"]:
            P.extend(chimney_prims(H, c))

    # ---- fixtures ----
    for f in H.d.get("fixtures", []):
        if f["level"] == level:
            P.extend(fixture_prims(H, f))

    # ---- other lines ----
    for ln in H.d.get("lines", []):
        if ln["level"] == level:
            P.append(Prim("L10", "line", [tuple(p) for p in ln["pts"]],
                          {"stroke": 0.18, "dash": "dashed" if ln["style"] == "dashed" else None},
                          group=ln["id"], meta={"note": ln.get("note", "")}))
    ro = (H.d.get("roof_outline") or {}).get(level)
    if ro:
        P.append(Prim("L10", "shape", Polygon(ro["poly"]), {"fill": None, "stroke": 0.25},
                      group=f"{level}-ROOF", meta={"note": "roof outline (eaves)", "src": ro.get("src")}))
    below = lv.get("show_below")
    if below:
        # exterior walls of another level drawn dashed (e.g. knee walls under the cut plane)
        env = H.level_walls_union(below, types=("exterior",))
        P.append(Prim("L10", "shape", env, {"fill": None, "stroke": 0.18, "dash": "dashed"},
                      group=f"{level}-{below}-WALLS-BELOW", meta={"note": f"{below} exterior walls below the cut plane"}))

    # ---- dimensions ----
    for i, dm in enumerate(H.d["dimensions"].get(level, [])):
        P.extend(dim_prims(H, level, dm, i))

    # ---- north arrow (model space, free corner) ----
    if H.d["meta"].get("north_deg_from_plan_up") is not None:
        P.extend(north_arrow(H, H.north_arrow_at()))

    # ---- uncertainty markers ----
    P.extend(uncertainty_markers(H, level))
    return P


def label_point(poly: Polygon, override=None):
    """Explicit position, else the centroid when it sits well inside, else polylabel."""
    if override:
        return override[0], override[1]
    lp = polylabel(poly, tolerance=10)
    c = poly.centroid
    if poly.contains(c) and poly.exterior.distance(c) >= 0.8 * poly.exterior.distance(lp):
        return c.x, c.y
    return lp.x, lp.y


# ---------- openings ----------
def opening_prims(H: House, o) -> list[Prim]:
    w = H.walls[o["wall"]]
    x1, y1, x2, y2 = w["rect"]
    ax = H.wall_axis(w)
    a0, a1 = o["at"] - o["width"] / 2, o["at"] + o["width"] / 2
    if ax == "x":
        f0, f1 = min(y1, y2), max(y1, y2)
        P2 = lambda u, v: (u, v)          # u along wall, v across
    else:
        f0, f1 = min(x1, x2), max(x1, x2)
        P2 = lambda u, v: (v, u)
    t = f1 - f0
    mid = (f0 + f1) / 2
    g, out = o["id"], []
    side_in = H.interior_side(o)
    meta = {"src": o.get("src"), "src_pos": o.get("src_pos"), "kind": o["kind"],
            "width": o["width"], "height": o["height"], "sill": o.get("sill"), "note": o.get("note", "")}
    kind = o["kind"]
    if kind == "window":
        lay = "L04"
        for v in (f0, f1):  # sill / reveal lines along both faces
            out.append(Prim(lay, "line", [P2(a0, v), P2(a1, v)], {"stroke": 0.18}, g, meta))
        for dv in (-25, 25):  # glazing (double line)
            out.append(Prim(lay, "line", [P2(a0, mid + dv), P2(a1, mid + dv)], {"stroke": 0.25}, g, meta))
        for u in (a0, a1):
            out.append(Prim(lay, "line", [P2(u, mid - 25), P2(u, mid + 25)], {"stroke": 0.25}, g, meta))
    elif kind in ("door", "gate"):
        lay = "L03"
        side = +1 if o["swing"]["side"] == "+" else -1
        hinge_u = a0 if o["swing"]["hinge"] == "start" else a1
        free_u = a1 if hinge_u == a0 else a0
        face = f1 if side > 0 else f0
        L = o["width"]
        leaf_end = P2(hinge_u, face + side * L)
        out.append(Prim(lay, "line", [P2(hinge_u, face), leaf_end], {"stroke": 0.35}, g, meta))
        # 90 deg arc from the leaf end to the free jamb, centre at the hinge
        c = P2(hinge_u, face)
        p_free = P2(free_u, face)
        ang_leaf = math.degrees(math.atan2(leaf_end[1] - c[1], leaf_end[0] - c[0]))
        ang_free = math.degrees(math.atan2(p_free[1] - c[1], p_free[0] - c[0]))
        d = (ang_free - ang_leaf + 540) % 360 - 180
        s0, s1 = (ang_leaf, ang_leaf + d) if d > 0 else (ang_leaf + d, ang_leaf)
        out.append(Prim(lay, "arc", None, {"stroke": 0.18, "c": c, "r": L, "a0": s0, "a1": s1}, g, meta))
        for v in (f0, f1):  # threshold lines
            out.append(Prim(lay, "line", [P2(a0, v), P2(a1, v)], {"stroke": 0.13}, g, meta))
    elif kind == "opening":
        lay = "L03"
        for v in (f0, f1):
            out.append(Prim(lay, "line", [P2(a0, v), P2(a1, v)], {"stroke": 0.18, "dash": "dashed"}, g, meta))
    elif kind == "garage_door":
        lay = "L03"
        for v in (f0, f1):
            out.append(Prim(lay, "line", [P2(a0, v), P2(a1, v)], {"stroke": 0.18, "dash": "dashed"}, g, meta))
        out.append(Prim(lay, "line", [P2(a0, mid), P2(a1, mid)], {"stroke": 0.25}, g, meta))
    else:
        raise ValueError(kind)

    # label "width/height" in cm (plan notation) on the interior side
    s_in = H.interior_side(o)
    off = (t / 2 + 330) * s_in
    pos = P2(o["at"], mid + off)
    rot = 0 if ax == "x" else 90
    if o.get("label_at"):
        pos, rot = tuple(o["label_at"][:2]), o["label_at"][2]
    txt = f"{o['width'] // 10}/{o['height'] // 10}"
    if kind == "opening":
        txt = f"{o['width'] // 10}/{o['height'] // 10} o.T."  # ohne Tuer
    out.append(Prim("L07", "text", [pos], {"text": txt, "size": 1.8, "rot": rot}, group=f"{g}-label"))
    return out


# ---------- stairs ----------
def stair_prims(H: House, s, mode: str, level: str) -> list[Prim]:
    treads, walk = H.stair_treads(s)
    seg_of = list(H._seg_of)
    only = (s.get("draw_segments") or {}).get(level)
    if only is not None:   # e.g. DG shows only the main flight
        keep = [i for i, si in enumerate(seg_of) if si in only]
        treads = [treads[i] for i in keep]
        segs = [s["segments"][i] for i in only]
        walk = H.stair_treads({**s, "segments": segs})[1]
    g, out = s["id"], []
    meta = {"src": s.get("src"), "note": s.get("note", ""), "name": s["name"]}
    cut = s.get("cut_after") if mode == "cut" else None
    for i, t in enumerate(treads):
        dashed = (mode == "outline") or (cut is not None and i >= cut)
        out.append(Prim("L05", "shape", t, {"fill": None, "stroke": 0.13 if dashed else 0.25,
                                            "dash": "dashed" if dashed else None}, g, meta))
    if s.get("landing"):
        out.append(Prim("L05", "shape", box(*s["landing"]),
                        {"fill": None, "stroke": 0.13 if mode == "outline" else 0.25,
                         "dash": "dashed" if mode == "outline" else None}, g, meta))
    if mode == "outline":
        return out
    # walking line, oriented in ascent direction
    pts = list(walk)
    if s.get("direction") == "down":
        pts = pts[::-1]
    if cut is not None:
        # walking line only up to the cut
        line = LineString(pts)
        tcut = treads[cut - 1]
        cut_len = line.project(Point(tcut.centroid.x, tcut.centroid.y))
        pts = [line.interpolate(cut_len * k / 20).coords[0] for k in range(21)]
        # break line across the flight
        c = treads[cut].centroid
        b = treads[cut].bounds
        out.append(Prim("L05", "line", [(b[0] - 80, b[1] + 60), (b[2] + 80, b[3] + 60)],
                        {"stroke": 0.25}, g, meta))
    out.append(Prim("L05", "line", pts, {"stroke": 0.18}, g, meta))
    out.append(Prim("L05", "circle", None, {"c": pts[0], "r": 70, "stroke": 0.18}, g, meta))
    # arrow head at the end
    (xa, ya), (xb, yb) = pts[-2], pts[-1]
    ang = math.atan2(yb - ya, xb - xa)
    Lh, Wh = 220, 90
    tip = (xb, yb)
    left = (xb - Lh * math.cos(ang) + Wh * math.sin(ang), yb - Lh * math.sin(ang) - Wh * math.cos(ang))
    right = (xb - Lh * math.cos(ang) - Wh * math.sin(ang), yb - Lh * math.sin(ang) + Wh * math.cos(ang))
    out.append(Prim("L05", "shape", Polygon([tip, left, right]), {"fill": "#000", "stroke": None}, g, meta))
    info = H.stair_info(s)
    label = f"{info['risers']} STG {info['riser_h'] / 10:.1f}/{(info['going'] or 0) / 10:.1f}"
    allb = unary_union(treads).bounds
    horiz = (allb[2] - allb[0]) > (allb[3] - allb[1])
    la = (s.get("label_at") or {}).get(level)
    if la:
        lpos, rot = (la[0], la[1]), la[2]
    elif horiz:
        lpos, rot = ((allb[0] + allb[2]) / 2, allb[1] - 250), 0
    else:
        lpos, rot = (allb[0] - 280, (allb[1] + allb[3]) / 2), 90
    out.append(Prim("L07", "text", [lpos], {"text": label, "size": 1.8, "rot": rot}, group=f"{g}-label"))
    return out


# ---------- chimneys ----------
def chimney_prims(H: House, c) -> list[Prim]:
    x1, y1, x2, y2 = c["rect"]
    g = c["id"]
    meta = {"src": c.get("src"), "flues": c["flues"], "flue_size": c["flue_size"]}
    out = [Prim("L10", "shape", box(x1, y1, x2, y2), {"fill": "#d8d8d8", "stroke": 0.35}, g, meta)]
    fw, fh = c["flue_size"]
    n = c["flues"]
    horiz = (x2 - x1) >= (y2 - y1)
    if horiz:
        gap = ((x2 - x1) - n * fw) / (n + 1)
        for i in range(n):
            fx = x1 + gap + i * (fw + gap)
            fy = (y1 + y2) / 2 - fh / 2
            out.append(Prim("L10", "shape", box(fx, fy, fx + fw, fy + fh), {"fill": "#ffffff", "stroke": 0.25}, g, meta))
    else:
        gap = ((y2 - y1) - n * fw) / (n + 1)
        for i in range(n):
            fy = y1 + gap + i * (fw + gap)
            fx = (x1 + x2) / 2 - fh / 2
            out.append(Prim("L10", "shape", box(fx, fy, fx + fh, fy + fw), {"fill": "#ffffff", "stroke": 0.25}, g, meta))
    return out


# ---------- fixtures ----------
def ellipse(cx, cy, rx, ry):
    return affinity.scale(Point(cx, cy).buffer(1, 32), rx, ry)


def fixture_prims(H: House, f) -> list[Prim]:
    x1, y1, x2, y2 = f["rect"]
    g, out = f["id"], []
    meta = {"src": f.get("src"), "type": f["type"]}
    st = {"fill": None, "stroke": 0.18}
    if f["type"] == "bathtub":
        out.append(Prim("L09", "shape", box(x1, y1, x2, y2), st, g, meta))
        inner = box(x1 + 60, y1 + 60, x2 - 60, y2 - 60).buffer(-120).buffer(120)
        out.append(Prim("L09", "shape", inner, st, g, meta))
        out.append(Prim("L09", "circle", None, {"c": (x2 - 250, (y1 + y2) / 2), "r": 30, "stroke": 0.18}, g, meta))
    elif f["type"] == "basin":
        out.append(Prim("L09", "shape", box(x1, y1, x2, y2), st, g, meta))
        out.append(Prim("L09", "shape", ellipse((x1 + x2) / 2, (y1 + y2) / 2 - 20, (x2 - x1) / 2 - 70, (y2 - y1) / 2 - 80), st, g, meta))
    elif f["type"] == "wc":
        back = f.get("back", "-y")
        if back == "-y":
            out.append(Prim("L09", "shape", box(x1 + 20, y1, x2 - 20, y1 + 180), st, g, meta))
            out.append(Prim("L09", "shape", ellipse((x1 + x2) / 2, y1 + 180 + (y2 - y1 - 180) / 2, (x2 - x1) / 2 - 20, (y2 - y1 - 180) / 2), st, g, meta))
        else:
            out.append(Prim("L09", "shape", box(x1, y1, x2, y2), st, g, meta))
    return out


# ---------- tiles ----------
def tile_hatch(poly: Polygon, step: float, gid: str) -> list[Prim]:
    out = []
    minx, miny, maxx, maxy = poly.bounds
    lines = []
    x = minx + step
    while x < maxx - 1:
        lines.append(LineString([(x, miny), (x, maxy)]))
        x += step
    y = miny + step
    while y < maxy - 1:
        lines.append(LineString([(minx, y), (maxx, y)]))
        y += step
    for ln in lines:
        seg = ln.intersection(poly)
        if seg.is_empty:
            continue
        for s in getattr(seg, "geoms", [seg]):
            out.append(Prim("L10", "line", list(s.coords), {"stroke": 0.1, "color": "#888"}, gid))
    return out


# ---------- dimensions ----------
def dim_ticks(H: House, level, dm):
    if dm["geom"] == "points":
        return sorted(dm["ticks"])
    g = H.level_walls_union(level, cut=(dm["geom"] == "cut"))
    a, b = dm["from"], dm["to"]
    if dm["axis"] == "x":
        probe = LineString([(a, dm["line"]), (b, dm["line"])])
    else:
        probe = LineString([(dm["line"], a), (dm["line"], b)])
    inter = probe.intersection(g)
    ticks = {a, b}
    for part in getattr(inter, "geoms", [inter]):
        if part.is_empty:
            continue
        for x, y in part.coords:
            ticks.add(round(x if dm["axis"] == "x" else y))
    return sorted(t for t in ticks if a <= t <= b)


def dim_prims(H: House, level, dm, idx) -> list[Prim]:
    ticks = dim_ticks(H, level, dm)
    gid = f"{level}-DIM{idx + 1:02d}"
    return [Prim("L08", "dim", None, {"axis": dm["axis"], "ticks": ticks, "place": dm["place"],
                                      "interior": dm.get("interior", False)}, gid)]


# ---------- north arrow ----------
def north_arrow(H: House, at) -> list[Prim]:
    beta = math.radians(H.d["meta"]["north_deg_from_plan_up"])
    ux, uy = math.sin(beta), math.cos(beta)       # north direction in plan coords
    cx, cy = at
    R = 600
    tip = (cx + R * ux, cy + R * uy)
    tail = (cx - R * ux, cy - R * uy)
    px, py = -uy, ux
    l = (tail[0] + 220 * px, tail[1] + 220 * py)
    r = (tail[0] - 220 * px, tail[1] - 220 * py)
    out = [Prim("L10", "circle", None, {"c": (cx, cy), "r": R + 120, "stroke": 0.18}, "NORTH"),
           Prim("L10", "shape", Polygon([tip, l, (cx, cy)]), {"fill": "#000", "stroke": 0.18}, "NORTH"),
           Prim("L10", "shape", Polygon([tip, (cx, cy), r]), {"fill": "#fff", "stroke": 0.18}, "NORTH"),
           Prim("L10", "text", [(cx + (R + 380) * ux, cy + (R + 380) * uy - 100)], {"text": "N", "size": 3.0, "weight": "bold"}, "NORTH")]
    return out


# ---------- uncertainty markers ----------
def uncertainty_markers(H: House, level) -> list[Prim]:
    out = []

    def mark(x, y, gid, why):
        out.append(Prim("L11", "circle", None, {"c": (x, y), "r": 140, "stroke": 0.18, "color": "#d0021b"}, f"{gid}-E",
                        {"why": why}))
        out.append(Prim("L11", "text", [(x, y - 55)], {"text": "E", "size": 1.6, "color": "#d0021b", "weight": "bold"},
                        f"{gid}-E"))

    for o in H.openings_on(level):
        if src_is_estimated(o.get("src_pos"), o.get("src")):
            w = H.walls[o["wall"]]
            x1, y1, x2, y2 = w["rect"]
            s = H.interior_side(o)
            if H.wall_axis(w) == "x":
                y = (y1 + y2) / 2 + s * (abs(y2 - y1) / 2 + 330)
                out_x, out_y = o["at"] + o["width"] / 2 + 90, y + 40
            else:
                x = (x1 + x2) / 2 + s * (abs(x2 - x1) / 2 + 330)
                out_x, out_y = x - 40, o["at"] + o["width"] / 2 + 160
            mark(out_x, out_y, o["id"], "position estimated" if "E" in str(o.get("src_pos")) else "size estimated")
    for s in H.d["stairs"]:
        mode = s.get("draw_on", {}).get(level)
        if mode and mode != "outline" and src_is_estimated(s.get("src")):
            t, _ = H.stair_treads(s)
            b = unary_union(t).bounds
            mark(b[2] + 180, b[3] - 150, s["id"], "stair geometry estimated")
    for c in H.d["chimneys"]:
        if level in c["levels"]:
            x1, y1, x2, y2 = c["rect"]
            mark(x2 + 170, y1 - 120, c["id"], "position estimated")
    for w in H.walls_on(level):
        if str(w.get("src")) in ("E", "D/E") or w.get("mark_estimated"):
            g = H.wall_poly(w)
            mark(g.centroid.x, g.centroid.y, w["id"], "position or extent estimated")
    for r in H.rooms_on(level):
        if src_is_estimated(r.get("src")) and r["id"] in ("D4",):
            pass
    return out


def all_levels(house: House) -> dict[str, list[Prim]]:
    return {lv: build_level(house, lv) for lv in house.levels}


def prims_bounds(prims) -> tuple:
    xs, ys = [], []
    for p in prims:
        if p.kind == "shape" and p.geom is not None and not p.geom.is_empty:
            b = p.geom.bounds
            xs += [b[0], b[2]]; ys += [b[1], b[3]]
        elif p.kind == "line":
            xs += [q[0] for q in p.geom]; ys += [q[1] for q in p.geom]
        elif p.kind in ("arc", "circle"):
            cx, cy = p.style["c"]; r = p.style["r"]
            xs += [cx - r, cx + r]; ys += [cy - r, cy + r]
        elif p.kind == "text":
            xs.append(p.geom[0][0]); ys.append(p.geom[0][1])
        elif p.kind == "dim":
            t = p.style["ticks"]
            if p.style["axis"] == "x":
                xs += [t[0], t[-1]]; ys += [p.style["place"]] * 2
            else:
                ys += [t[0], t[-1]]; xs += [p.style["place"]] * 2
    return min(xs), min(ys), max(xs), max(ys)
