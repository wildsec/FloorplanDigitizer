"""
export_dxf.py - one DXF (R2013, millimetres) per level from hdt primitives.

* Layers LAYER_01..LAYER_11 with colour, line weight and line type.
* Every wall is its own SOLID HATCH (or ANSI31 for retaining walls) carrying
  XDATA (id, source class, note); clean wall outlines are LWPOLYLINEs.
* Every door / window / opening is a named BLOCK with ATTRIBs
  (ID, SIZE, SILL, SOURCE) inserted at its centre - move / edit as one object.
* Dimensions are real DIMENSION entities (architectural ticks, Austrian text).
* All levels share the same origin, so the files overlay as XREFs.
"""
from __future__ import annotations

import math
from collections import defaultdict

import ezdxf
from ezdxf import units
from ezdxf.enums import TextEntityAlignment
from shapely.geometry import MultiPolygon

from .model import LAYERS, House, Prim, fmt_len

APPID = "FLOORPLAN_DIGITIZER"
TXT_SCALE = 100  # paper mm -> model mm at 1:100


def _rings(g):
    polys = list(g.geoms) if isinstance(g, MultiPolygon) else [g]
    for p in polys:
        if p.is_empty:
            continue
        yield p, [list(p.exterior.coords)[:-1]] + [list(r.coords)[:-1] for r in p.interiors]


def setup(doc):
    doc.units = units.MM
    doc.header["$INSUNITS"] = 4
    doc.header["$MEASUREMENT"] = 1
    doc.header["$LTSCALE"] = 50
    if "DASHED" not in doc.linetypes:
        doc.linetypes.add("DASHED", pattern=[12.0, 8.0, -4.0], description="Dashed __ __ __")
    for key, (name, color, lw) in LAYERS.items():
        lay = doc.layers.add(name, color=color)
        lay.dxf.lineweight = lw
    if "DEFPOINTS" not in doc.layers:
        doc.layers.add("DEFPOINTS")
    if APPID not in doc.appids:
        doc.appids.add(APPID)
    if "ARCH" not in doc.styles:
        doc.styles.add("ARCH", font="LiberationSans-Regular.ttf")
    ds = doc.dimstyles.add("ARCH100")
    ds.dxf.dimtsz = 120          # oblique tick size (architectural)
    ds.dxf.dimtxt = 180          # 1.8 mm at 1:100
    ds.dxf.dimexe = 140
    ds.dxf.dimexo = 0
    ds.dxf.dimgap = 60
    ds.dxf.dimtad = 1            # text above the line
    ds.dxf.dimdec = 2
    ds.dxf.dimtxsty = "ARCH"
    ds.dxf.dimclrd = 1
    ds.dxf.dimclre = 1
    ds.dxf.dimtih = 0
    ds.dxf.dimtoh = 0


def layer_name(key):
    return LAYERS[key][0]


def attribs(p: Prim):
    m = p.meta or {}
    out = [(1000, p.group or "")]
    for k in ("src", "src_pos", "note", "type", "kind"):
        if m.get(k):
            out.append((1000, f"{k}={m[k]}"))
    return out


def add_shape(msp, p: Prim, lay):
    fill = p.style.get("fill")
    for poly, rings in _rings(p.geom):
        color = {"#3c3c3c": 250, "#9b9b9b": 253, "#d8d8d8": 254, "#000": 7, "hatch": 8}.get(fill)
        if color is not None:          # white fills (#fff) are omitted in CAD
            h = msp.add_hatch(color=color, dxfattribs={"layer": lay})
            if fill == "hatch":
                h.set_pattern_fill("ANSI31", scale=20, color=8)
            for i, ring in enumerate(rings):
                h.paths.add_polyline_path(ring, is_closed=True,
                                          flags=ezdxf.const.BOUNDARY_PATH_EXTERNAL if i == 0 else 0)
            if p.group and p.meta.get("role") == "wall":
                h.set_xdata(APPID, attribs(p))
        if p.style.get("stroke"):
            for ring in rings:
                e = msp.add_lwpolyline(ring, close=True, dxfattribs={"layer": lay})
                if p.style.get("dash") == "dashed":
                    e.dxf.linetype = "DASHED"
                if p.meta.get("role") == "room":
                    e.set_xdata(APPID, attribs(p) + [(1000, f"name={p.meta['name']}"),
                                                     (1040, round(p.meta["area"], 3))])


def add_prim(target, p: Prim, lay, dx=0.0, dy=0.0):
    st = p.style
    tr = lambda q: (q[0] - dx, q[1] - dy)
    if p.kind == "shape":
        from shapely import affinity
        q = Prim(p.layer, p.kind, affinity.translate(p.geom, -dx, -dy), p.style, p.group, p.meta)
        add_shape(target, q, lay)
    elif p.kind == "line":
        e = target.add_lwpolyline([tr(q) for q in p.geom], dxfattribs={"layer": lay})
        if st.get("dash") == "dashed":
            e.dxf.linetype = "DASHED"
        if st.get("color"):
            e.dxf.color = 8
    elif p.kind == "circle":
        e = target.add_circle(tr(st["c"]), st["r"], dxfattribs={"layer": lay})
        if st.get("color"):
            e.dxf.color = 1
    elif p.kind == "arc":
        target.add_arc(tr(st["c"]), st["r"], st["a0"], st["a1"], dxfattribs={"layer": lay})
    elif p.kind == "text":
        h = st.get("size", 2.5) * TXT_SCALE * 0.72
        t = target.add_text(st["text"], height=h, rotation=st.get("rot", 0),
                            dxfattribs={"layer": lay, "style": "ARCH"})
        t.set_placement(tr(p.geom[0]), align=TextEntityAlignment.MIDDLE_CENTER)
        if st.get("color") and st["color"] != "#000":
            t.dxf.color = 1 if st["color"] == "#d0021b" else 8


def add_dim(msp, p: Prim):
    st = p.style
    t, place, ax = st["ticks"], st["place"], st["axis"]
    for a, b in zip(t[:-1], t[1:]):
        if ax == "x":
            p1, p2, base, ang = (a, place), (b, place), ((a + b) / 2, place), 0
        else:
            p1, p2, base, ang = (place, a), (place, b), (place, (a + b) / 2), 90
        dim = msp.add_linear_dim(base=base, p1=p1, p2=p2, angle=ang, dimstyle="ARCH100",
                                 text=fmt_len(b - a), dxfattribs={"layer": layer_name("L08")})
        dim.render()


def write_dxf(H: House, level: str, prims: list[Prim], path):
    doc = ezdxf.new("R2013", setup=True)
    setup(doc)
    msp = doc.modelspace()

    # group opening primitives into blocks
    opening_ids = {o["id"]: o for o in H.openings_on(level)}
    blocks = defaultdict(list)
    for p in prims:
        if p.group in opening_ids and p.layer in ("L03", "L04"):
            blocks[p.group].append(p)

    for p in prims:
        lay = layer_name(p.layer)
        if p.group in blocks and p.layer in ("L03", "L04"):
            continue
        if p.kind == "dim":
            add_dim(msp, p)
        else:
            add_prim(msp, p, lay)

    for oid, ps in blocks.items():
        o = opening_ids[oid]
        w = H.walls[o["wall"]]
        x1, y1, x2, y2 = w["rect"]
        if H.wall_axis(w) == "x":
            cx, cy = o["at"], (y1 + y2) / 2
        else:
            cx, cy = (x1 + x2) / 2, o["at"]
        bname = f"{level}_{oid}_{o['kind'].upper()}"
        blk = doc.blocks.new(name=bname, base_point=(0, 0))
        for p in ps:
            add_prim(blk, p, layer_name(p.layer), cx, cy)
        tags = [("ID", oid), ("SIZE", f"{o['width'] // 10}/{o['height'] // 10}"),
                ("SILL", str(o.get("sill", ""))), ("SOURCE", f"size {o.get('src')} / pos {o.get('src_pos')}")]
        for i, (tag, _) in enumerate(tags):
            blk.add_attdef(tag, (0, -60 * (i + 1)), dxfattribs={"height": 40, "layer": "DEFPOINTS", "flags": 1})
        ins = msp.add_blockref(bname, (cx, cy), dxfattribs={"layer": layer_name("L04" if o["kind"] == "window" else "L03")})
        ins.add_auto_attribs(dict(tags))
        ins.set_xdata(APPID, [(1000, oid), (1000, f"src={o.get('src')}"), (1000, f"src_pos={o.get('src_pos')}"),
                              (1000, f"note={o.get('note', '')}")])

    from .model import prims_bounds
    b = prims_bounds(prims)
    doc.header["$EXTMIN"] = (b[0], b[1], 0)
    doc.header["$EXTMAX"] = (b[2], b[3], 0)
    doc.saveas(path)
    auditor = doc.audit()
    if auditor.has_errors:
        raise RuntimeError(f"DXF audit errors in {path}: {len(auditor.errors)}")
