"""
export_svg.py - writes one A3 sheet (SVG, mm) per level from hdt primitives.
Layers become Inkscape layers; every element is an addressable <g id=...>.
PDF and PNG are rendered from these SVGs (cairosvg).
"""
from __future__ import annotations

import datetime as dt
import html
import math
from collections import defaultdict

from shapely.geometry import MultiPolygon, Polygon

from .model import DIM_FORMAT, LAYERS, SCALE, House, Prim, fmt_len, prims_bounds

PAPER_W, PAPER_H = 420.0, 297.0          # A3 landscape
MARGIN = 10.0
TB_W = 100.0                              # title block width
FONT = "Liberation Sans, Arial, Helvetica, sans-serif"
DASH = {"dashed": "2.0,1.2", "room": None}


class Sheet:
    def __init__(self, origin):
        self.ox, self.oy = origin            # paper position of model (0,0)

    def p(self, x, y):
        return self.ox + x / SCALE, self.oy - y / SCALE

    def L(self, mm):
        return mm / SCALE


def common_origin(levels_prims: dict) -> tuple:
    b = [prims_bounds(p) for p in levels_prims.values()]
    minx = min(q[0] for q in b); miny = min(q[1] for q in b)
    maxx = max(q[2] for q in b); maxy = max(q[3] for q in b)
    area_w = PAPER_W - 2 * MARGIN - TB_W - 5
    area_h = PAPER_H - 2 * MARGIN
    w, h = (maxx - minx) / SCALE, (maxy - miny) / SCALE
    if w > area_w or h > area_h:
        raise ValueError(f"plan {w:.0f}x{h:.0f} mm does not fit {area_w:.0f}x{area_h:.0f} mm at 1:{SCALE}")
    ox = MARGIN + (area_w - w) / 2 - minx / SCALE
    oy = MARGIN + (area_h - h) / 2 + maxy / SCALE
    return ox, oy


def _f(v):
    return f"{v:.3f}".rstrip("0").rstrip(".")


def path_from_geom(S: Sheet, g) -> str:
    polys = list(g.geoms) if isinstance(g, MultiPolygon) else [g]
    d = []
    for poly in polys:
        if poly.is_empty:
            continue
        for ring in [poly.exterior, *poly.interiors]:
            pts = [S.p(x, y) for x, y in ring.coords]
            d.append("M" + " L".join(f"{_f(a)},{_f(b)}" for a, b in pts) + " Z")
    return " ".join(d)


def stroke_attr(st, default_color="#000"):
    if st.get("stroke") is None:
        return 'stroke="none"'
    s = f'stroke="{st.get("color", default_color)}" stroke-width="{st["stroke"]}" stroke-linejoin="round" stroke-linecap="round"'
    if st.get("dash") and DASH.get(st["dash"]):
        s += f' stroke-dasharray="{DASH[st["dash"]]}"'
    return s


def text_el(x, y, txt, size, anchor="middle", rot=0, weight="normal", color="#000", style="normal", extra=""):
    tr = f' transform="rotate({_f(-rot)} {_f(x)} {_f(y)})"' if rot else ""
    return (f'<text x="{_f(x)}" y="{_f(y)}" font-family="{FONT}" font-size="{size}" font-weight="{weight}" '
            f'font-style="{style}" fill="{color}" text-anchor="{anchor}"{tr}{extra}>{html.escape(txt)}</text>')


def render_prim(S: Sheet, p: Prim) -> str:
    st = p.style
    if p.kind == "shape":
        fill = st.get("fill")
        if fill == "hatch":
            fill = "url(#hatch45)"
        return (f'<path d="{path_from_geom(S, p.geom)}" fill="{fill or "none"}" fill-rule="evenodd" '
                f'{stroke_attr(st)}/>')
    if p.kind == "line":
        pts = " ".join(f"{_f(a)},{_f(b)}" for a, b in (S.p(x, y) for x, y in p.geom))
        return f'<polyline points="{pts}" fill="none" {stroke_attr(st)}/>'
    if p.kind == "circle":
        cx, cy = S.p(*st["c"])
        return f'<circle cx="{_f(cx)}" cy="{_f(cy)}" r="{_f(S.L(st["r"]))}" fill="none" {stroke_attr(st)}/>'
    if p.kind == "arc":
        cx, cy = st["c"]; r = st["r"]
        a0, a1 = math.radians(st["a0"]), math.radians(st["a1"])
        x0, y0 = S.p(cx + r * math.cos(a0), cy + r * math.sin(a0))
        x1, y1 = S.p(cx + r * math.cos(a1), cy + r * math.sin(a1))
        large = 1 if (st["a1"] - st["a0"]) % 360 > 180 else 0
        R = S.L(r)
        # model ccw == paper cw (y flipped) -> sweep flag 0
        return (f'<path d="M{_f(x0)},{_f(y0)} A{_f(R)},{_f(R)} 0 {large} 0 {_f(x1)},{_f(y1)}" '
                f'fill="none" {stroke_attr(st)}/>')
    if p.kind == "text":
        x, y = S.p(*p.geom[0])
        size = st.get("size", 2.5)
        return text_el(x, y + size * 0.35 if not st.get("rot") else y, st["text"], size,
                       st.get("anchor", "middle"), st.get("rot", 0), st.get("weight", "normal"),
                       st.get("color", "#000"), st.get("style", "normal"),
                       ) if not st.get("rot") else text_el(x + size * 0.35, y, st["text"], size,
                                                           st.get("anchor", "middle"), st.get("rot", 0),
                                                           st.get("weight", "normal"), st.get("color", "#000"))
    if p.kind == "dim":
        return render_dim(S, p)
    raise ValueError(p.kind)


def render_dim(S: Sheet, p: Prim) -> str:
    st = p.style
    t, place, ax = st["ticks"], st["place"], st["axis"]
    out = []
    size = 1.8
    tick = 1.0   # half-length of the oblique tick, paper mm
    ext = 1.4
    if ax == "x":
        a, b = S.p(t[0], place), S.p(t[-1], place)
        out.append(f'<line x1="{_f(a[0] - 1.5)}" y1="{_f(a[1])}" x2="{_f(b[0] + 1.5)}" y2="{_f(b[1])}" stroke="#000" stroke-width="0.18"/>')
        for v in t:
            x, y = S.p(v, place)
            if not st["interior"]:
                out.append(f'<line x1="{_f(x)}" y1="{_f(y - ext)}" x2="{_f(x)}" y2="{_f(y + ext)}" stroke="#000" stroke-width="0.18"/>')
            out.append(f'<line x1="{_f(x - tick)}" y1="{_f(y + tick)}" x2="{_f(x + tick)}" y2="{_f(y - tick)}" stroke="#000" stroke-width="0.35"/>')
        lift = 0
        for i in range(len(t) - 1):
            seg = t[i + 1] - t[i]
            txt = fmt_len(seg)
            x0, y0 = S.p((t[i] + t[i + 1]) / 2, place)
            room = S.L(seg)
            need = len(txt) * size * 0.55
            dy = -0.8
            if need > room - 0.6:
                lift = 0 if lift else 1
                dy = -0.8 - lift * (size + 0.3)
            else:
                lift = 0
            out.append(text_el(x0, y0 + dy, txt, size))
    else:
        a, b = S.p(place, t[0]), S.p(place, t[-1])
        out.append(f'<line x1="{_f(a[0])}" y1="{_f(a[1] + 1.5)}" x2="{_f(b[0])}" y2="{_f(b[1] - 1.5)}" stroke="#000" stroke-width="0.18"/>')
        for v in t:
            x, y = S.p(place, v)
            if not st["interior"]:
                out.append(f'<line x1="{_f(x - ext)}" y1="{_f(y)}" x2="{_f(x + ext)}" y2="{_f(y)}" stroke="#000" stroke-width="0.18"/>')
            out.append(f'<line x1="{_f(x - tick)}" y1="{_f(y + tick)}" x2="{_f(x + tick)}" y2="{_f(y - tick)}" stroke="#000" stroke-width="0.35"/>')
        lift = 0
        for i in range(len(t) - 1):
            seg = t[i + 1] - t[i]
            txt = fmt_len(seg)
            x0, y0 = S.p(place, (t[i] + t[i + 1]) / 2)
            room = S.L(seg)
            need = len(txt) * size * 0.55
            dx = -0.8
            if need > room - 0.6:
                lift = 0 if lift else 1
                dx = -0.8 - lift * (size + 0.3)
            else:
                lift = 0
            out.append(text_el(x0 + dx, y0, txt, size, rot=90))
    return "\n".join(out)


def title_block(H: House, level: str, sheet_no: int, n_sheets: int, status: str) -> str:
    lv = H.levels[level]
    x0 = PAPER_W - MARGIN - TB_W
    y0, y1 = MARGIN, PAPER_H - MARGIN
    o = []
    o.append(f'<rect x="{x0}" y="{y0}" width="{TB_W}" height="{y1 - y0}" fill="none" stroke="#000" stroke-width="0.5"/>')
    tx = x0 + 5
    y = y0 + 12
    o.append(text_el(tx, y, H.d["meta"]["project"].upper(), 5.0, "start", weight="bold")); y += 6.5
    o.append(text_el(tx, y, H.d["meta"]["title"], 3.2, "start")); y += 5
    o.append(text_el(tx, y, H.d["meta"]["variant"], 2.6, "start", color="#444")); y += 7
    o.append(f'<line x1="{x0}" y1="{y}" x2="{x0 + TB_W}" y2="{y}" stroke="#000" stroke-width="0.25"/>'); y += 11
    o.append(text_el(tx, y, f"PLAN {lv['plan_no']}", 4.0, "start", weight="bold")); y += 8
    o.append(text_el(tx, y, lv["name"].upper(), 6.0, "start", weight="bold")); y += 6
    o.append(text_el(tx, y, lv["name_en"], 3.2, "start", color="#444")); y += 7
    fl = lv["elevation"]
    o.append(text_el(tx, y, f"FFB / FFL {'±' if fl == 0 else ''}{fl / 1000:+.2f} m" if fl else "FFB / FFL ±0.00 m", 3.0, "start")); y += 5
    o.append(text_el(tx, y, f"lichte Raumhöhe / clear height {lv['clear_height'] / 1000:.2f} m", 2.6, "start")); y += 9
    o.append(f'<line x1="{x0}" y1="{y}" x2="{x0 + TB_W}" y2="{y}" stroke="#000" stroke-width="0.25"/>'); y += 9
    o.append(text_el(tx, y, "MASSSTAB / SCALE  1:100   (A3)", 3.2, "start", weight="bold")); y += 6
    # scale bar 0..5 m
    sb_y = y
    for i in range(5):
        fill = "#000" if i % 2 == 0 else "#fff"
        o.append(f'<rect x="{_f(tx + i * 10)}" y="{_f(sb_y)}" width="10" height="2" fill="{fill}" stroke="#000" stroke-width="0.2"/>')
    for i in range(6):
        o.append(text_el(tx + i * 10, sb_y + 5.5, f"{i}", 2.2))
    o.append(text_el(tx + 55, sb_y + 5.5, "m", 2.2, "start")); y += 13
    o.append(text_el(tx, y, {"at": "Maße: ≥1 m in m, <1 m in cm (35 = 0.35 m)", "mm": "Maße / dimensions in mm",
                             "m": "Maße / dimensions in m"}[DIM_FORMAT["style"]], 2.2, "start")); y += 4
    o.append(text_el(tx, y, "Öffnungen / openings: Breite/Höhe in cm", 2.2, "start")); y += 4
    o.append(text_el(tx, y, "STG = Steigungen  Höhe/Auftritt in cm", 2.2, "start")); y += 8
    o.append(f'<line x1="{x0}" y1="{y}" x2="{x0 + TB_W}" y2="{y}" stroke="#000" stroke-width="0.25"/>'); y += 7
    o.append(text_el(tx, y, "LEGENDE / LEGEND", 2.8, "start", weight="bold")); y += 5
    leg = [("#3c3c3c", "Außenwand / exterior wall"), ("#9b9b9b", "Innenwand / interior wall"),
           ("url(#hatch45)", "Stütz-/Fundamentmauer / retaining")]
    for fill, txt in leg:
        o.append(f'<rect x="{_f(tx)}" y="{_f(y - 2.6)}" width="8" height="3" fill="{fill}" stroke="#000" stroke-width="0.2"/>')
        o.append(text_el(tx + 11, y, txt, 2.3, "start")); y += 4.8
    o.append(f'<circle cx="{_f(tx + 4)}" cy="{_f(y - 1)}" r="1.4" fill="none" stroke="#d0021b" stroke-width="0.18"/>')
    o.append(text_el(tx + 4, y - 0.4, "E", 1.6, color="#d0021b", weight="bold"))
    o.append(text_el(tx + 11, y, "geschätzt / estimated (not dimensioned on the plan)", 2.3, "start")); y += 4.8
    o.append(f'<line x1="{_f(tx)}" y1="{_f(y - 1)}" x2="{_f(tx + 8)}" y2="{_f(y - 1)}" stroke="#000" stroke-width="0.18" stroke-dasharray="2,1.2"/>')
    o.append(text_el(tx + 11, y, "darüber / unterhalb / above or below", 2.3, "start")); y += 9
    o.append(f'<line x1="{x0}" y1="{y}" x2="{x0 + TB_W}" y2="{y}" stroke="#000" stroke-width="0.25"/>'); y += 7
    o.append(text_el(tx, y, "Grundlage / Source:", 2.4, "start", weight="bold")); y += 4.5
    for line in H.d["meta"].get("source_lines", []):
        o.append(text_el(tx, y, line, 2.3, "start")); y += 4
    y = y1 - 22
    o.append(f'<line x1="{x0}" y1="{y}" x2="{x0 + TB_W}" y2="{y}" stroke="#000" stroke-width="0.25"/>')
    o.append(text_el(tx, y + 6, f"Stand / Date: {dt.date.today().isoformat()}   Modell v{H.d['meta']['version']}", 2.4, "start"))
    o.append(text_el(tx, y + 11, f"Blatt / Sheet {sheet_no} / {n_sheets}", 2.4, "start"))
    if status:
        o.append(text_el(tx, y + 18, status, 3.4, "start", weight="bold", color="#d0021b"))
    return "\n".join(o)


def write_svg(H: House, level: str, prims, origin, path, sheet_no=1, n_sheets=3, status=""):
    S = Sheet(origin)
    by_layer = defaultdict(lambda: defaultdict(list))
    order = []
    for p in prims:
        by_layer[p.layer][p.group or "misc"].append(p)
    out = [f'<?xml version="1.0" encoding="UTF-8"?>',
           f'<svg xmlns="http://www.w3.org/2000/svg" xmlns:inkscape="http://www.inkscape.org/namespaces/inkscape" '
           f'width="{PAPER_W}mm" height="{PAPER_H}mm" viewBox="0 0 {PAPER_W} {PAPER_H}">',
           f'<title>{html.escape(H.d["meta"]["project"])} - {H.levels[level]["name"]}</title>',
           '<defs><pattern id="hatch45" patternUnits="userSpaceOnUse" width="1.2" height="1.2" patternTransform="rotate(45)">'
           '<rect width="1.2" height="1.2" fill="#f2f2f2"/><line x1="0" y1="0" x2="0" y2="1.2" stroke="#555" stroke-width="0.15"/></pattern></defs>',
           f'<rect x="0" y="0" width="{PAPER_W}" height="{PAPER_H}" fill="#fff"/>',
           f'<rect x="{MARGIN / 2}" y="{MARGIN / 2}" width="{PAPER_W - MARGIN}" height="{PAPER_H - MARGIN}" fill="none" stroke="#000" stroke-width="0.35"/>']
    # draw order: rooms & other first, walls, openings, stairs, labels, dims, markers
    draw_order = ["L06", "L10", "L09", "L05", "L02", "L01", "L04", "L03", "L07", "L08", "L11"]
    for lay in draw_order:
        if lay not in by_layer:
            continue
        name = LAYERS[lay][0]
        out.append(f'<g id="{name}" inkscape:groupmode="layer" inkscape:label="{name}">')
        # walls: fills first, then outlines
        groups = by_layer[lay]
        keys = sorted(groups, key=lambda k: ("outline" in k, k))
        for gid in keys:
            ps = groups[gid]
            meta = ps[0].meta or {}
            attrs = f' id="{html.escape(level + "_" + gid if not gid.startswith(level) else gid)}"'
            if meta.get("src"):
                attrs += f' data-source="{html.escape(str(meta["src"]))}"'
            if meta.get("src_pos"):
                attrs += f' data-source-position="{html.escape(str(meta["src_pos"]))}"'
            if meta.get("note"):
                attrs += f' data-note="{html.escape(str(meta["note"]))}"'
            out.append(f"<g{attrs}>")
            out.extend(render_prim(S, p) for p in ps)
            out.append("</g>")
        out.append("</g>")
    out.append('<g id="TITLE_BLOCK" inkscape:groupmode="layer" inkscape:label="TITLE_BLOCK">')
    out.append(title_block(H, level, sheet_no, n_sheets, status))
    out.append("</g>")
    out.append("</svg>")
    path.write_text("\n".join(out), encoding="utf-8")
