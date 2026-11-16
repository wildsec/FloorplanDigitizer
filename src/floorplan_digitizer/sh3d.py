"""
sh3d.py - minimal writer for Sweet Home 3D files (.sh3d).

A .sh3d file is a ZIP archive. Since Sweet Home 3D 5.3 it may contain a
`Home.xml` entry following https://www.sweethome3d.com/SweetHome3D.dtd, which
Sweet Home 3D reads when it opens the file. A ZIP with only `Home.xml` (plus
referenced content entries such as a background image) is enough; no
`ContentDigests` entry is written on purpose.

Conventions: Sweet Home 3D works in centimetres and its plan Y axis points
down. This module takes plan coordinates in centimetres with Y pointing UP
(CAD convention) and flips them once, in `_y()`.

Element order inside <home> follows the DTD:
  property*, furnitureVisibleProperty*, environment?, backgroundImage?, print?,
  compass?, (camera|observerCamera)*, level*, (furniture)*, wall*, room*,
  polyline*, dimensionLine*, label*
"""
from __future__ import annotations

import io
import math
import zipfile
from dataclasses import dataclass, field
from xml.sax.saxutils import quoteattr

SH3D_VERSION = "7200"


def _f(v: float) -> str:
    s = f"{float(v):.3f}".rstrip("0").rstrip(".")
    return "0" if s in ("-0", "") else s


@dataclass
class Wall:
    id: str
    xs: float
    ys: float
    xe: float
    ye: float
    thickness: float
    height: float | None = None
    level: str | None = None
    arc_extent: float | None = None        # radians, CAD sense (counter-clockwise, y up)
    at_start: str | None = None
    at_end: str | None = None


@dataclass
class Room:
    id: str
    points: list
    name: str | None = None
    level: str | None = None
    area_visible: bool = True


@dataclass
class Level:
    id: str
    name: str
    elevation: float
    floor_thickness: float
    height: float
    elevation_index: int = -1


@dataclass
class Background:
    entry: str               # ZIP entry name of the image
    data: bytes
    scale_distance: float    # cm between the two scale points
    x0: float                # scale points in image pixels
    y0: float
    x1: float
    y1: float
    x_origin: float          # cm: image top-left is drawn at (-x_origin, -y_origin) in SH3D plan coords
    y_origin: float
    level: str | None = None


class Home:
    def __init__(self, name: str = "", wall_height: float = 250.0):
        self.name = name
        self.wall_height = wall_height
        self.walls: list[Wall] = []
        self.rooms: list[Room] = []
        self.levels: list[Level] = []
        self.backgrounds: list[Background] = []
        self._n = 0

    # ---------- building ----------
    def _id(self, prefix):
        self._n += 1
        return f"{prefix}{self._n}"

    def add_level(self, name, elevation, floor_thickness, height, id=None) -> str:
        lid = id or self._id("level")
        self.levels.append(Level(lid, name, elevation, floor_thickness, height, len(self.levels)))
        return lid

    def add_wall(self, xs, ys, xe, ye, thickness, height=None, level=None, arc_extent=None) -> Wall:
        w = Wall(self._id("wall"), xs, ys, xe, ye, thickness, height, level, arc_extent)
        self.walls.append(w)
        return w

    @staticmethod
    def connect(a: Wall, a_end: str, b: Wall, b_end: str):
        """Join wall a (its 'start' or 'end') with wall b (its 'start' or 'end')."""
        setattr(a, "at_start" if a_end == "start" else "at_end", b.id)
        setattr(b, "at_start" if b_end == "start" else "at_end", a.id)

    def add_room(self, points, name=None, level=None, area_visible=True) -> Room:
        r = Room(self._id("room"), list(points), name, level, area_visible)
        self.rooms.append(r)
        return r

    def set_background(self, png: bytes, scale_distance, x0, y0, x1, y1, x_origin, y_origin, level=None):
        self.backgrounds.append(Background(str(len(self.backgrounds)), png, scale_distance,
                                           x0, y0, x1, y1, x_origin, y_origin, level))

    # ---------- serialisation ----------
    @staticmethod
    def _y(y):
        return -y

    def _bg_xml(self, b: Background) -> str:
        return (f'<backgroundImage image={quoteattr(b.entry)} scaleDistance="{_f(b.scale_distance)}" '
                f'scaleDistanceXStart="{_f(b.x0)}" scaleDistanceYStart="{_f(b.y0)}" '
                f'scaleDistanceXEnd="{_f(b.x1)}" scaleDistanceYEnd="{_f(b.y1)}" '
                f'xOrigin="{_f(b.x_origin)}" yOrigin="{_f(b.y_origin)}"/>')

    def to_xml(self) -> bytes:
        o = ['<?xml version="1.0" encoding="UTF-8"?>',
             f'<home version="{SH3D_VERSION}" name={quoteattr(self.name)} wallHeight="{_f(self.wall_height)}">']
        for b in self.backgrounds:
            if b.level is None:
                o.append(self._bg_xml(b))
        for lv in self.levels:
            bgs = [b for b in self.backgrounds if b.level == lv.id]
            attrs = (f'id={quoteattr(lv.id)} name={quoteattr(lv.name)} elevation="{_f(lv.elevation)}" '
                     f'floorThickness="{_f(lv.floor_thickness)}" height="{_f(lv.height)}" '
                     f'elevationIndex="{lv.elevation_index}"')
            if bgs:
                o.append(f"<level {attrs}>{self._bg_xml(bgs[0])}</level>")
            else:
                o.append(f"<level {attrs}/>")
        for w in self.walls:
            a = [f'id={quoteattr(w.id)}']
            if w.level:
                a.append(f'level={quoteattr(w.level)}')
            if w.at_start:
                a.append(f'wallAtStart={quoteattr(w.at_start)}')
            if w.at_end:
                a.append(f'wallAtEnd={quoteattr(w.at_end)}')
            a += [f'xStart="{_f(w.xs)}"', f'yStart="{_f(self._y(w.ys))}"',
                  f'xEnd="{_f(w.xe)}"', f'yEnd="{_f(self._y(w.ye))}"']
            if w.height is not None:
                a.append(f'height="{_f(w.height)}"')
            a.append(f'thickness="{_f(w.thickness)}"')
            if w.arc_extent:
                # y is flipped, so the sense of rotation flips as well
                a.append(f'arcExtent="{_f(-w.arc_extent)}"')
            o.append(f"<wall {' '.join(a)}/>")
        for r in self.rooms:
            a = [f'id={quoteattr(r.id)}']
            if r.level:
                a.append(f'level={quoteattr(r.level)}')
            if r.name:
                a.append(f'name={quoteattr(r.name)}')
            a += [f'areaVisible="{str(r.area_visible).lower()}"', 'floorVisible="true"', 'ceilingVisible="true"']
            pts = "".join(f'<point x="{_f(x)}" y="{_f(self._y(y))}"/>' for x, y in r.points)
            o.append(f"<room {' '.join(a)}>{pts}</room>")
        o.append("</home>")
        return "\n".join(o).encode("utf-8")

    def save(self, path):
        with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
            z.writestr("Home.xml", self.to_xml())
            for b in self.backgrounds:
                z.writestr(b.entry, b.data)


def validate_xml(xml: bytes, dtd_path) -> list[str]:
    """Validate Home.xml against SweetHome3D.dtd with lxml. Returns error strings."""
    from lxml import etree
    text = open(dtd_path, "rb").read()
    # The official DTD (2024) contains three typos that strict parsers reject:
    # "renderer CADATA", an attribute without a type ("name #REQUIRED") and a
    # stray ';' after a default value ('endMarkSize CDATA "10";').
    # Patch them in memory; the file on disk stays untouched.
    import re
    text = text.replace(b"CADATA", b"CDATA")
    text = re.sub(rb"(\n\s+[A-Za-z]+)\s+(#REQUIRED|#IMPLIED)", rb"\1 CDATA \2", text)
    text = re.sub(rb'("[^"\n]*");(\s*\n)', rb"\1\2", text)
    dtd = etree.DTD(io.BytesIO(text))
    root = etree.fromstring(xml)
    ok = dtd.validate(root)
    # DTD_CONTENT_NOT_DETERMINIST is a remark about the DTD itself
    # ("texture?, texture?"), not about the document.
    errs = [e for e in dtd.error_log.filter_from_errors() if e.type_name != "DTD_CONTENT_NOT_DETERMINIST"]
    return [str(e) for e in errs]


def read_home_xml(path) -> bytes:
    with zipfile.ZipFile(path) as z:
        return z.read("Home.xml")


def polygon_area(points) -> float:
    a = 0.0
    for (x1, y1), (x2, y2) in zip(points, points[1:] + points[:1]):
        a += x1 * y2 - x2 * y1
    return abs(a) / 2


def arc_from_three(p0, pm, p1):
    """Signed sweep (radians, CCW positive, y up) of the arc p0 -> pm -> p1."""
    (x0, y0), (xm, ym), (x1, y1) = p0, pm, p1
    d = 2 * (x0 * (ym - y1) + xm * (y1 - y0) + x1 * (y0 - ym))
    ux = ((x0**2 + y0**2) * (ym - y1) + (xm**2 + ym**2) * (y1 - y0) + (x1**2 + y1**2) * (y0 - ym)) / d
    uy = ((x0**2 + y0**2) * (x1 - xm) + (xm**2 + ym**2) * (x0 - x1) + (x1**2 + y1**2) * (xm - x0)) / d
    a0 = math.atan2(y0 - uy, x0 - ux)
    am = math.atan2(ym - uy, xm - ux)
    a1 = math.atan2(y1 - uy, x1 - ux)
    ccw = (a1 - a0) % (2 * math.pi)
    return ccw if (am - a0) % (2 * math.pi) < ccw else ccw - 2 * math.pi
