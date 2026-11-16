"""
dxf2sh3d.py - convert a 2D DXF floorplan into a Sweet Home 3D (.sh3d) file
with real walls (and optionally rooms and a background image).

Usage:
  dxf2sh3d input.dxf --analyse                 # step 1: report only
  dxf2sh3d input.dxf -o out.sh3d [options]

Wall drawing styles:
  centerline  every line / arc on a wall layer is a wall axis (thickness from --thickness)
  double      walls are drawn as two parallel faces; faces are paired, the centre line
              becomes the wall axis and the face distance its thickness
  auto        (default) decided by the analysis

Coordinates: everything is converted to centimetres; the Y flip for Sweet Home 3D
is done in sh3d.py. Dimensions, texts and hatches are ignored (texts can name rooms).
The code is structured so that doors/windows (block INSERTs or wall gaps) can be
mapped to catalogue models later (see `Converter.openings`, not implemented yet).
"""
from __future__ import annotations

import argparse
import io
import math
import re
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass, field

import ezdxf
from ezdxf import bbox as ezbbox

from .sh3d import Home

INSUNITS_TO_CM = {1: 2.54, 2: 30.48, 4: 0.1, 5: 1.0, 6: 100.0, 14: 10.0}
INSUNITS_NAME = {0: "unitless", 1: "inch", 2: "foot", 4: "mm", 5: "cm", 6: "m", 14: "dm"}
UNIT_ARG = {"mm": 0.1, "cm": 1.0, "m": 100.0, "in": 2.54, "ft": 30.48}
WALL_LAYER_RE = re.compile(r"wall|wand|w(ae|ä)nde|mauer|muur|\bmur|tabique|parede", re.I)
LINEAR = ("LINE", "LWPOLYLINE", "POLYLINE", "ARC")


# --------------------------------------------------------------------------
# geometry primitives (cm, y up)
# --------------------------------------------------------------------------
@dataclass
class Seg:
    a: tuple
    b: tuple
    layer: str = ""

    @property
    def length(self):
        return math.dist(self.a, self.b)

    @property
    def u(self):
        L = self.length or 1.0
        return ((self.b[0] - self.a[0]) / L, (self.b[1] - self.a[1]) / L)


@dataclass
class Arc:
    c: tuple
    r: float
    a0: float        # radians, CCW
    a1: float        # radians, CCW (a1 > a0, unwrapped)
    layer: str = ""

    def pt(self, a):
        return (self.c[0] + self.r * math.cos(a), self.c[1] + self.r * math.sin(a))


@dataclass
class WallDef:
    a: list
    b: list
    t: float
    arc: float | None = None     # sweep in radians, CCW positive (CAD sense)
    src: str = ""
    at_start: object = None
    at_end: object = None


@dataclass
class Result:
    walls: list = field(default_factory=list)
    rooms: list = field(default_factory=list)
    skipped: Counter = field(default_factory=Counter)
    unpaired_length: float = 0.0
    notes: list = field(default_factory=list)


# --------------------------------------------------------------------------
# reading
# --------------------------------------------------------------------------
def unit_factor(doc, override: str | None):
    if override:
        return UNIT_ARG[override], f"{override} (--units)"
    code = doc.header.get("$INSUNITS", 0)
    if code in INSUNITS_TO_CM:
        return INSUNITS_TO_CM[code], f"{INSUNITS_NAME[code]} ($INSUNITS={code})"
    # unitless: guess from the drawing size
    try:
        ext = ezbbox.extents(doc.modelspace(), fast=True)
        size = max(ext.size.x, ext.size.y)
    except Exception:
        size = 0
    guess = "mm" if size > 3000 else ("cm" if size > 300 else "m")
    return UNIT_ARG[guess], f"unitless ($INSUNITS={code}); guessed {guess} from drawing size {size:.0f} – override with --units"


def iter_entities(msp, depth=0):
    """Model-space entities with INSERTs exploded (entities on layer '0' inside a
    block inherit the layer of the INSERT)."""
    for e in msp:
        if e.dxftype() == "INSERT" and depth < 6:
            try:
                for v in e.virtual_entities():
                    if v.dxf.layer == "0":
                        v.dxf.layer = e.dxf.layer
                    if v.dxftype() == "INSERT":
                        yield from iter_entities([v], depth + 1)
                    else:
                        yield v, e
            except Exception:
                yield e, None
        else:
            yield e, None


def detect_wall_layers(doc, given):
    layers = [l.dxf.name for l in doc.layers]
    if given:
        missing = [g for g in given if g not in layers]
        if missing:
            raise SystemExit(f"wall layer(s) not in the drawing: {', '.join(missing)}")
        return list(given), "given"
    auto = [l for l in layers if WALL_LAYER_RE.search(l)]
    if auto:
        return auto, "auto-detected by name"
    return None, "none found - using all layers"


def collect(doc, f, wall_layers):
    segs, arcs, skipped = [], [], Counter()
    for e, parent in iter_entities(doc.modelspace()):
        t = e.dxftype()
        lay = e.dxf.layer
        if wall_layers is not None and lay not in wall_layers:
            continue
        if t == "LINE":
            a, b = e.dxf.start, e.dxf.end
            segs.append(Seg((a.x * f, a.y * f), (b.x * f, b.y * f), lay))
        elif t in ("LWPOLYLINE", "POLYLINE"):
            for v in e.virtual_entities():
                if v.dxftype() == "LINE":
                    a, b = v.dxf.start, v.dxf.end
                    if math.dist((a.x, a.y), (b.x, b.y)) > 1e-9:
                        segs.append(Seg((a.x * f, a.y * f), (b.x * f, b.y * f), lay))
                elif v.dxftype() == "ARC":
                    arcs.append(arc_of(v, f, lay))
        elif t == "ARC":
            arcs.append(arc_of(e, f, lay))
        elif t in ("DIMENSION", "TEXT", "MTEXT", "HATCH", "ATTRIB", "ATTDEF", "POINT", "SOLID", "LEADER", "MULTILEADER"):
            continue
        else:
            skipped[t] += 1
    return segs, arcs, skipped


def arc_of(e, f, lay):
    a0 = math.radians(e.dxf.start_angle)
    a1 = math.radians(e.dxf.end_angle)
    while a1 <= a0:
        a1 += 2 * math.pi
    c = e.dxf.center
    return Arc((c.x * f, c.y * f), e.dxf.radius * f, a0, a1, lay)


def texts(doc, f):
    out = []
    for e, _ in iter_entities(doc.modelspace()):
        if e.dxftype() == "TEXT":
            p = e.dxf.insert
            if e.dxf.get("halign", 0) or e.dxf.get("valign", 0):
                p = e.dxf.get("align_point", p)
            out.append(((p.x * f, p.y * f), e.dxf.text, e.dxf.height * f))
        elif e.dxftype() == "MTEXT":
            p = e.dxf.insert
            out.append(((p.x * f, p.y * f), e.plain_text(), e.dxf.char_height * f))
    return out


# --------------------------------------------------------------------------
# analysis (step 1)
# --------------------------------------------------------------------------
def analyse(doc, units=None, wall_layers=None, tmin=4.0, tmax=60.0) -> dict:
    f, unit_note = unit_factor(doc, units)
    per_layer = defaultdict(Counter)
    for e in doc.modelspace():
        per_layer[e.dxf.layer][e.dxftype()] += 1
    try:
        ext = ezbbox.extents(doc.modelspace(), fast=True)
        bb = (ext.extmin.x * f, ext.extmin.y * f, ext.extmax.x * f, ext.extmax.y * f)
    except Exception:
        bb = None
    wl, how = detect_wall_layers(doc, wall_layers)
    segs, arcs, _ = collect(doc, f, wl)
    paired = pair_fraction(segs, tmin, tmax)
    style = "double" if paired >= 0.5 else "centerline"
    dists = face_distances(segs, tmin, tmax)
    return {"units": unit_note, "factor_to_cm": f, "layers": {k: dict(v) for k, v in sorted(per_layer.items())},
            "bbox_cm": bb, "wall_layers": wl, "wall_layer_detection": how,
            "wall_segments": len(segs), "wall_arcs": len(arcs),
            "paired_fraction": paired, "style": style, "typical_thickness_cm": dists}


def face_distances(segs, tmin, tmax):
    c = Counter()
    for i, j, d, *_ in candidate_pairs(segs, tmin, tmax):
        c[round(d)] += 1
    return [k for k, _ in c.most_common(5)]


def pair_fraction(segs, tmin, tmax):
    if not segs:
        return 0.0
    tot = sum(s.length for s in segs)
    paired = set()
    for i, j, *_ in candidate_pairs(segs, tmin, tmax):
        paired.update((i, j))
    return sum(segs[i].length for i in paired) / tot if tot else 0.0


def format_analysis(a: dict, path: str) -> str:
    o = [f"DXF analysis: {path}", f"  units      : {a['units']}  (x{a['factor_to_cm']} -> cm)"]
    if a["bbox_cm"]:
        x0, y0, x1, y1 = a["bbox_cm"]
        o.append(f"  bounding box: {x1 - x0:.1f} x {y1 - y0:.1f} cm   ({x0:.1f}, {y0:.1f}) .. ({x1:.1f}, {y1:.1f})")
    o.append("  layers (entity counts):")
    for lay, cnt in a["layers"].items():
        o.append(f"    {lay:<32} " + ", ".join(f"{k} {v}" for k, v in sorted(cnt.items())))
    o.append(f"  wall layers: {a['wall_layers'] or 'ALL'}  ({a['wall_layer_detection']})")
    o.append(f"  wall geometry: {a['wall_segments']} straight segments, {a['wall_arcs']} arcs")
    o.append(f"  parallel-face pairing: {a['paired_fraction'] * 100:.0f}% of the wall line length has a parallel partner")
    o.append(f"  -> walls look like: {'(b) DOUBLE parallel lines (inner + outer face)' if a['style'] == 'double' else '(a) single CENTRE lines'}")
    if a["style"] == "double" and a["typical_thickness_cm"]:
        o.append(f"     most common face distances (= wall thicknesses): {', '.join(f'{t} cm' for t in a['typical_thickness_cm'])}")
    return "\n".join(o)


# --------------------------------------------------------------------------
# double-line pairing
# --------------------------------------------------------------------------
def candidate_pairs(segs, tmin, tmax, min_overlap=8.0, max_angle_deg=2.0):
    s_ang = math.sin(math.radians(max_angle_deg))
    out = []
    n = len(segs)
    for i in range(n):
        si = segs[i]
        if si.length < 1:
            continue
        ui = si.u
        for j in range(i + 1, n):
            sj = segs[j]
            if sj.length < 1:
                continue
            uj = sj.u
            if abs(ui[0] * uj[1] - ui[1] * uj[0]) > s_ang:
                continue
            # perpendicular distance of sj's midpoint to line i
            mx, my = (sj.a[0] + sj.b[0]) / 2, (sj.a[1] + sj.b[1]) / 2
            d = abs((mx - si.a[0]) * -ui[1] + (my - si.a[1]) * ui[0])
            if not (tmin <= d <= tmax):
                continue
            ta = (sj.a[0] - si.a[0]) * ui[0] + (sj.a[1] - si.a[1]) * ui[1]
            tb = (sj.b[0] - si.a[0]) * ui[0] + (sj.b[1] - si.a[1]) * ui[1]
            lo, hi = max(0.0, min(ta, tb)), min(si.length, max(ta, tb))
            if hi - lo >= min_overlap:
                out.append((i, j, d, lo, hi))
    return out


def _subtract(interval, covered):
    lo, hi = interval
    parts = [(lo, hi)]
    for c0, c1 in covered:
        nxt = []
        for a, b in parts:
            if c1 <= a or c0 >= b:
                nxt.append((a, b))
                continue
            if c0 > a:
                nxt.append((a, c0))
            if c1 < b:
                nxt.append((c1, b))
        parts = nxt
    return parts


def pair_double(segs, tmin, tmax, min_len=8.0, snap=1.0):
    cands = candidate_pairs(segs, tmin, tmax)
    # thicknesses that occur a lot (weighted by face overlap) are trusted first;
    # this keeps short piers between two openings from pairing their jambs
    weight = Counter()
    for _, _, d, lo, hi in cands:
        weight[round(d)] += hi - lo
    top = max(weight.values()) if weight else 0
    common = {t for t, w in weight.items() if w >= 0.1 * top}
    cands.sort(key=lambda c: (0 if round(c[2]) in common else 1, c[2]))
    cov = defaultdict(list)          # segment index -> covered intervals (own parameter)
    walls = []

    def is_cap(sg):
        """sg closes the end of an accepted wall (perpendicular, as long as the wall is thick)."""
        mx, my = (sg.a[0] + sg.b[0]) / 2, (sg.a[1] + sg.b[1]) / 2
        for w in walls:
            if abs(sg.length - w.t) > 2 * snap + 1:
                continue
            uw = _dir(w)
            us = sg.u
            if abs(uw[0] * us[0] + uw[1] * us[1]) > 0.1:      # not perpendicular
                continue
            if min(math.dist((mx, my), w.a), math.dist((mx, my), w.b)) <= snap + 1:
                return True
        return False

    for i, j, d, lo, hi in cands:
        si, sj = segs[i], segs[j]
        if is_cap(si) or is_cap(sj):
            continue
        ui = si.u
        # j's covered intervals mapped into i's parameter
        def to_i(t):
            p = (sj.a[0] + sj.u[0] * t, sj.a[1] + sj.u[1] * t)
            return (p[0] - si.a[0]) * ui[0] + (p[1] - si.a[1]) * ui[1]
        cov_j_in_i = [tuple(sorted((to_i(a), to_i(b)))) for a, b in cov[j]]
        for a, b in _subtract((lo, hi), cov[i] + cov_j_in_i):
            if b - a < min_len:
                continue
            pa = (si.a[0] + ui[0] * a, si.a[1] + ui[1] * a)
            pb = (si.a[0] + ui[0] * b, si.a[1] + ui[1] * b)
            # offset to the middle between the faces (towards j)
            mx, my = (sj.a[0] + sj.b[0]) / 2, (sj.a[1] + sj.b[1]) / 2
            side = 1 if ((mx - si.a[0]) * -ui[1] + (my - si.a[1]) * ui[0]) > 0 else -1
            nx, ny = -ui[1] * side * d / 2, ui[0] * side * d / 2
            walls.append(WallDef([pa[0] + nx, pa[1] + ny], [pb[0] + nx, pb[1] + ny], d, src="pair"))
            cov[i].append((a, b))
            # coverage on j in its own parameter
            def to_j(p):
                return (p[0] - sj.a[0]) * sj.u[0] + (p[1] - sj.a[1]) * sj.u[1]
            cov[j].append(tuple(sorted((to_j(pa), to_j(pb)))))
    # unpaired face length: uncovered pieces longer than the largest wall
    # thickness (shorter rests are the corner stubs of the outer faces)
    unpaired = 0.0
    for k, s in enumerate(segs):
        for a, b in _subtract((0.0, s.length), sorted(cov[k])):
            if b - a > tmax:
                unpaired += b - a
    return walls, unpaired


def pair_arcs(arcs, tmin, tmax, tol=1.0):
    walls, used = [], set()
    cands = []
    for i in range(len(arcs)):
        for j in range(i + 1, len(arcs)):
            A, B = arcs[i], arcs[j]
            if math.dist(A.c, B.c) > tol:
                continue
            d = abs(A.r - B.r)
            if not (tmin <= d <= tmax):
                continue
            lo, hi = max(A.a0, B.a0), min(A.a1, B.a1)
            if hi - lo <= 1e-3:   # try the wrapped interval
                lo, hi = max(A.a0, B.a0 + 2 * math.pi), min(A.a1, B.a1 + 2 * math.pi)
            if hi - lo > 1e-3:
                cands.append((d, i, j, lo, hi))
    for d, i, j, lo, hi in sorted(cands):
        if i in used or j in used:
            continue
        A, B = arcs[i], arcs[j]
        r = (A.r + B.r) / 2
        c = A.c
        p0 = (c[0] + r * math.cos(lo), c[1] + r * math.sin(lo))
        p1 = (c[0] + r * math.cos(hi), c[1] + r * math.sin(hi))
        walls.append(WallDef(list(p0), list(p1), d, arc=hi - lo, src="arcpair"))
        used.update((i, j))
    return walls, [a for k, a in enumerate(arcs) if k not in used]


# --------------------------------------------------------------------------
# joining
# --------------------------------------------------------------------------
def _intersect(p, u, q, v):
    den = u[0] * v[1] - u[1] * v[0]
    if abs(den) < 1e-9:
        return None
    t = ((q[0] - p[0]) * v[1] - (q[1] - p[1]) * v[0]) / den
    return (p[0] + u[0] * t, p[1] + u[1] * t)


def _dir(w):
    L = math.dist(w.a, w.b) or 1.0
    return ((w.b[0] - w.a[0]) / L, (w.b[1] - w.a[1]) / L)


def join_walls(walls, snap):
    """Corner joins (extend both walls to the axis intersection and connect them),
    T-joins (extend into the other wall's axis) and snapping of close ends."""
    straight = [w for w in walls if not w.arc]
    ends = [(w, k) for w in walls for k in ("a", "b")]

    def end_pt(w, k):
        return getattr(w, k)

    # 1) corner joins between two wall ends
    for x in range(len(ends)):
        w1, k1 = ends[x]
        for y in range(x + 1, len(ends)):
            w2, k2 = ends[y]
            if w1 is w2:
                continue
            p1, p2 = end_pt(w1, k1), end_pt(w2, k2)
            reach = max(w1.t, w2.t) + snap
            if math.dist(p1, p2) > reach * 1.5:
                continue
            if getattr(w1, "at_start" if k1 == "a" else "at_end") or getattr(w2, "at_start" if k2 == "a" else "at_end"):
                continue
            if w1.arc or w2.arc:
                if math.dist(p1, p2) <= snap:
                    m = [(p1[0] + p2[0]) / 2, (p1[1] + p2[1]) / 2]
                    setattr(w1, k1, m); setattr(w2, k2, list(m))
                    _link(w1, k1, w2, k2)
                continue
            X = _intersect(p1, _dir(w1), p2, _dir(w2))
            if X is None:
                if math.dist(p1, p2) <= snap:     # collinear and touching
                    m = [(p1[0] + p2[0]) / 2, (p1[1] + p2[1]) / 2]
                    setattr(w1, k1, m); setattr(w2, k2, list(m))
                    _link(w1, k1, w2, k2)
                continue
            if math.dist(X, p1) <= reach and math.dist(X, p2) <= reach \
                    and _extends(w1, k1, X, snap) and _extends(w2, k2, X, snap):
                setattr(w1, k1, list(X)); setattr(w2, k2, list(X))
                _link(w1, k1, w2, k2)
    # 2) T-joins: a free end close to another wall's axis
    for w1, k1 in ends:
        if w1.arc or getattr(w1, "at_start" if k1 == "a" else "at_end"):
            continue
        p = end_pt(w1, k1)
        u1 = _dir(w1)
        for w2 in straight:
            if w2 is w1:
                continue
            u2 = _dir(w2)
            if abs(u1[0] * u2[1] - u1[1] * u2[0]) < 0.2:      # nearly parallel
                continue
            L = math.dist(w2.a, w2.b)
            t = (p[0] - w2.a[0]) * u2[0] + (p[1] - w2.a[1]) * u2[1]
            if not (-snap <= t <= L + snap):
                continue
            foot = (w2.a[0] + u2[0] * t, w2.a[1] + u2[1] * t)
            dist = math.dist(p, foot)
            if dist <= w2.t / 2 + w1.t / 2 + snap and dist > 0.01:
                X = _intersect(p, u1, w2.a, u2)
                if X and math.dist(X, p) <= w2.t / 2 + w1.t / 2 + snap:
                    setattr(w1, k1, list(X))
                break
    # 3) merge collinear chains with equal thickness (fewer, cleaner walls)
    return merge_collinear(walls, snap)


def _extends(w, k, X, snap):
    """True if moving end k of wall w to X lengthens the wall (or keeps it)."""
    p = getattr(w, k)
    other = w.b if k == "a" else w.a
    L = math.dist(p, other) or 1.0
    out = ((p[0] - other[0]) / L, (p[1] - other[1]) / L)
    return (X[0] - p[0]) * out[0] + (X[1] - p[1]) * out[1] >= -snap


def _link(w1, k1, w2, k2):
    setattr(w1, "at_start" if k1 == "a" else "at_end", (w2, k2))
    setattr(w2, "at_start" if k2 == "a" else "at_end", (w1, k1))


def merge_collinear(walls, snap):
    changed = True
    while changed:
        changed = False
        for w1 in walls:
            if w1.arc:
                continue
            for k1 in ("a", "b"):
                link = getattr(w1, "at_start" if k1 == "a" else "at_end")
                if not link:
                    continue
                w2, k2 = link
                if w2.arc or abs(w1.t - w2.t) > 0.5:
                    continue
                u1, u2 = _dir(w1), _dir(w2)
                if abs(u1[0] * u2[1] - u1[1] * u2[0]) > 1e-3:
                    continue
                # w1 absorbs w2: replace w1's end k1 by w2's far end
                far = "b" if k2 == "a" else "a"
                setattr(w1, k1, getattr(w2, far))
                far_link = getattr(w2, "at_start" if far == "a" else "at_end")
                setattr(w1, "at_start" if k1 == "a" else "at_end", far_link)
                if far_link:
                    w3, k3 = far_link
                    setattr(w3, "at_start" if k3 == "a" else "at_end", (w1, k1))
                walls.remove(w2)
                changed = True
                break
            if changed:
                break
    return walls


def centerline_walls(segs, arcs, thickness, snap):
    walls = [WallDef(list(s.a), list(s.b), thickness, src="line") for s in segs if s.length >= 1]
    walls += [WallDef(list(A.pt(A.a0)), list(A.pt(A.a1)), thickness, arc=A.a1 - A.a0, src="arc") for A in arcs]
    # snap ends into clusters
    pts = [(w, k) for w in walls for k in ("a", "b")]
    parent = list(range(len(pts)))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i
    for i in range(len(pts)):
        for j in range(i + 1, len(pts)):
            if math.dist(getattr(*pts[i]), getattr(*pts[j])) <= snap:
                parent[find(i)] = find(j)
    clusters = defaultdict(list)
    for i in range(len(pts)):
        clusters[find(i)].append(pts[i])
    for members in clusters.values():
        xs = [getattr(w, k)[0] for w, k in members]
        ys = [getattr(w, k)[1] for w, k in members]
        m = [sum(xs) / len(xs), sum(ys) / len(ys)]
        for w, k in members:
            setattr(w, k, list(m))
        if len(members) == 2 and members[0][0] is not members[1][0]:
            _link(members[0][0], members[0][1], members[1][0], members[1][1])
    return merge_collinear(walls, snap)


# --------------------------------------------------------------------------
# rooms
# --------------------------------------------------------------------------
def detect_rooms(walls, close_gap=300.0, min_area_cm2=5000.0, label_texts=(), side_gap=130.0):
    """Closed regions bounded by walls. Openings (doors, windows) are gaps between
    two collinear wall ends; such gaps up to `close_gap` cm are bridged for the
    detection only. Room names come from the largest text inside the room
    (texts with digits, e.g. areas or sizes, are ignored)."""
    from shapely.geometry import LineString, Point, Polygon
    from shapely.ops import unary_union
    shapes = []
    for w in walls:
        pts = arc_points(w, 24) if w.arc else [tuple(w.a), tuple(w.b)]
        shapes.append(LineString(pts).buffer(w.t / 2, cap_style=2, join_style=2))
    # openings: from every FREE straight wall end, look along the wall axis for
    # the next wall and bridge the gap (detection only). A gap that continues the
    # wall's own line (door/window in a wall) is bridged up to close_gap; a gap
    # ending at a perpendicular wall (door next to a corner) up to side_gap.
    from shapely.strtree import STRtree

    def ends():
        for k, w in enumerate(walls):
            if not w.arc:
                u = _dir(w)
                yield k, w, w.b, (u[0], u[1])
                yield k, w, w.a, (-u[0], -u[1])

    def cast(pass_no, obstacles, bridges):
        tree = STRtree(obstacles)
        for k, w, p, d in ends():
            probe = LineString([p, (p[0] + d[0] * 2.0, p[1] + d[1] * 2.0)])
            touching = [i for i in tree.query(Point(p).buffer(1.0)) if i != k
                        and obstacles[i].distance(Point(p)) < 1.0]
            # closed off when a wall it touches continues in the ray direction
            if any(probe.intersection(obstacles[i]).length > 0.5 for i in touching):
                continue
            ray = LineString([p, (p[0] + d[0] * close_gap, p[1] + d[1] * close_gap)])
            hits = []
            for idx in tree.query(ray):
                if idx == k or idx in touching:
                    continue
                hit = ray.intersection(obstacles[idx])
                if not hit.is_empty:
                    hits.append((Point(p).distance(hit), idx))
            if not hits:
                continue
            hits.sort()
            first = hits[0][0]
            if first <= 0.5:
                continue
            target = None
            if pass_no == 1:
                # collinear partner (door/window in a wall line), even if a
                # perpendicular wall end sits just in front of it
                for dist, idx in hits:
                    if idx >= len(walls) or walls[idx].arc or dist > first + tmax_wall:
                        continue
                    w2 = walls[idx]
                    u2 = _dir(w2)
                    off = abs((w2.a[0] - p[0]) * -d[1] + (w2.a[1] - p[1]) * d[0])
                    if abs(d[0] * u2[1] - d[1] * u2[0]) < 1e-3 and off <= max(w.t, w2.t) / 2:
                        target = dist
                        break
            elif first <= side_gap:
                target = first       # door next to a corner / perpendicular wall
            if target:
                q = (p[0] + d[0] * target, p[1] + d[1] * target)
                bridges.append(LineString([p, q]).buffer(w.t / 2, cap_style=2))

    tmax_wall = max((w.t for w in walls), default=0)
    b1 = []
    cast(1, list(shapes), b1)
    b2 = []
    cast(2, list(shapes) + b1, b2)
    shapes = shapes + b1 + b2
    if not shapes:
        return []
    u = unary_union(shapes).buffer(0.5, join_style=2).buffer(-0.5, join_style=2)
    rooms = []
    for poly in getattr(u, "geoms", [u]):
        for ring in poly.interiors:
            r = Polygon(ring).simplify(0.5)
            if r.area < min_area_cm2:
                continue
            name, best = None, -1
            for (x, y), txt, h in label_texts:
                if len(re.findall(r"[A-Za-zÄÖÜäöüß]", txt)) >= 2 and not re.search(r"\d", txt) and r.contains(Point(x, y)):
                    if h > best:
                        name, best = txt.strip(), h
            rooms.append((list(r.exterior.coords)[:-1], name))
    return rooms


def arc_points(w, n):
    from .sh3d import arc_from_three  # noqa: F401  (kept for symmetry)
    (x0, y0), (x1, y1) = w.a, w.b
    chord = math.dist(w.a, w.b)
    r = chord / (2 * math.sin(abs(w.arc) / 2))
    mx, my = (x0 + x1) / 2, (y0 + y1) / 2
    h = math.sqrt(max(r * r - (chord / 2) ** 2, 0))
    ux, uy = (x1 - x0) / chord, (y1 - y0) / chord
    s = 1 if w.arc > 0 else -1
    big = abs(w.arc) > math.pi
    cx, cy = mx - s * uy * h * (-1 if big else 1), my + s * ux * h * (-1 if big else 1)
    a0 = math.atan2(y0 - cy, x0 - cx)
    return [(cx + r * math.cos(a0 + w.arc * k / n), cy + r * math.sin(a0 + w.arc * k / n)) for k in range(n + 1)]


# --------------------------------------------------------------------------
# background image
# --------------------------------------------------------------------------
def render_background(doc, f, px_per_cm=2.0):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from ezdxf.addons.drawing import Frontend, RenderContext
    from ezdxf.addons.drawing.config import BackgroundPolicy, ColorPolicy, Configuration
    from ezdxf.addons.drawing.matplotlib import MatplotlibBackend
    ext = ezbbox.extents(doc.modelspace(), fast=True)
    x0, y0, x1, y1 = ext.extmin.x, ext.extmin.y, ext.extmax.x, ext.extmax.y
    w_cm, h_cm = (x1 - x0) * f, (y1 - y0) * f
    W, Hh = max(1, int(w_cm * px_per_cm)), max(1, int(h_cm * px_per_cm))
    dpi = 100
    fig = plt.figure(figsize=(W / dpi, Hh / dpi), dpi=dpi)
    ax = fig.add_axes([0, 0, 1, 1])
    cfg = Configuration(background_policy=BackgroundPolicy.WHITE, color_policy=ColorPolicy.BLACK)
    Frontend(RenderContext(doc), MatplotlibBackend(ax), config=cfg).draw_layout(doc.modelspace(), finalize=False)
    ax.set_xlim(x0, x1); ax.set_ylim(y0, y1); ax.set_axis_off(); ax.set_aspect("auto")
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=dpi, facecolor="white")
    plt.close(fig)
    # image pixel (0, Hh) .. (W, Hh) spans w_cm; top-left = (x0, y1) in CAD cm
    return buf.getvalue(), W, Hh, w_cm, (x0 * f, y1 * f)


# --------------------------------------------------------------------------
# conversion
# --------------------------------------------------------------------------
def convert(doc, *, mode="auto", wall_layers=None, units=None, thickness=12.0, height=250.0,
            snap=1.0, tmin=4.0, tmax=60.0, rooms=False, close_gap=300.0) -> Result:
    f, _ = unit_factor(doc, units)
    wl, _ = detect_wall_layers(doc, wall_layers)
    segs, arcs, skipped = collect(doc, f, wl)
    res = Result(skipped=skipped)
    if mode == "auto":
        mode = "double" if pair_fraction(segs, tmin, tmax) >= 0.5 else "centerline"
        res.notes.append(f"mode auto -> {mode}")
    if mode == "double":
        walls, res.unpaired_length = pair_double(segs, tmin, tmax, snap=snap)
        aw, single_arcs = pair_arcs(arcs, tmin, tmax)
        walls += aw
        if single_arcs:
            res.notes.append(f"{len(single_arcs)} arc(s) without a concentric partner skipped")
        walls = join_walls(walls, snap)
    else:
        walls = centerline_walls(segs, arcs, thickness, snap)
    res.walls = [w for w in walls if math.dist(w.a, w.b) >= 1.0]
    if rooms:
        res.rooms = detect_rooms(res.walls, close_gap, label_texts=texts(doc, f))
    return res


def build_home(res: Result, name="", height=250.0) -> Home:
    h = Home(name=name, wall_height=height)
    obj = {}
    for w in res.walls:
        obj[id(w)] = h.add_wall(w.a[0], w.a[1], w.b[0], w.b[1], round(w.t, 2), height=height, arc_extent=w.arc)
    for w in res.walls:
        sw = obj[id(w)]
        for attr, link in (("at_start", w.at_start), ("at_end", w.at_end)):
            if link and id(link[0]) in obj:
                setattr(sw, attr, obj[id(link[0])].id)
    for pts, nm in res.rooms:
        h.add_room(pts, name=nm)
    return h


def main(argv=None):
    ap = argparse.ArgumentParser(prog="dxf2sh3d", description="Convert a DXF floorplan to a Sweet Home 3D .sh3d file.")
    ap.add_argument("input")
    ap.add_argument("-o", "--output")
    ap.add_argument("--analyse", "--analyze", action="store_true", help="only print the DXF analysis")
    ap.add_argument("--wall-layer", action="append", help="layer with walls (repeatable)")
    ap.add_argument("--units", choices=list(UNIT_ARG), help="drawing units (overrides $INSUNITS)")
    ap.add_argument("--mode", choices=["auto", "centerline", "double"], default="auto")
    ap.add_argument("--thickness", type=float, default=12.0, help="wall thickness in cm (centerline mode)")
    ap.add_argument("--height", type=float, default=250.0, help="wall height in cm")
    ap.add_argument("--snap", type=float, default=1.0, help="end-point snap tolerance in cm")
    ap.add_argument("--min-thickness", type=float, default=4.0, help="double mode: smallest face distance in cm")
    ap.add_argument("--max-thickness", type=float, default=60.0, help="double mode: largest face distance in cm")
    ap.add_argument("--rooms", action="store_true", help="detect closed rooms and create floors")
    ap.add_argument("--close-gap", type=float, default=300.0, help="rooms: bridge openings (doors, windows) up to this width in cm")
    ap.add_argument("--background", action="store_true", help="embed a rendering of the DXF as background image")
    ap.add_argument("--name", default=None, help="home name (default: file name)")
    a = ap.parse_args(argv)

    doc = ezdxf.readfile(a.input)
    info = analyse(doc, a.units, a.wall_layer, a.min_thickness, a.max_thickness)
    print(format_analysis(info, a.input))
    if a.analyse:
        return 0
    if not a.output:
        ap.error("-o/--output is required unless --analyse is given")
    res = convert(doc, mode=a.mode, wall_layers=a.wall_layer, units=a.units, thickness=a.thickness,
                  height=a.height, snap=a.snap, tmin=a.min_thickness, tmax=a.max_thickness,
                  rooms=a.rooms, close_gap=a.close_gap)
    import os
    home = build_home(res, a.name or os.path.splitext(os.path.basename(a.input))[0], a.height)
    if a.background:
        png, W, Hh, w_cm, (left, top) = render_background(doc, info["factor_to_cm"])
        # SH3D draws the image top-left at (-xOrigin, -yOrigin) in its y-down plan
        home.set_background(png, w_cm, 0, Hh, W, Hh, x_origin=-left, y_origin=top)
    home.save(a.output)
    total = sum(math.dist(w.a, w.b) if not w.arc else abs(w.arc) * math.dist(w.a, w.b) / (2 * math.sin(abs(w.arc) / 2))
                for w in res.walls)
    print(f"\nwritten {a.output}")
    print(f"  walls : {len(res.walls)} (total axis length {total / 100:.2f} m, "
          f"{sum(1 for w in res.walls if w.arc)} curved, "
          f"{sum(1 for w in home.walls if w.at_start or w.at_end)} connected)")
    if res.rooms:
        print(f"  rooms : {len(res.rooms)} ({', '.join(n or '?' for _, n in res.rooms)})")
    if res.unpaired_length:
        print(f"  unpaired face length (not converted): {res.unpaired_length / 100:.2f} m")
    if res.skipped:
        print("  skipped entity types: " + ", ".join(f"{k} {v}" for k, v in res.skipped.items()))
    for n in res.notes:
        print("  note: " + n)
    return 0


if __name__ == "__main__":
    sys.exit(main())
