"""DXF -> Sweet Home 3D converter tests with synthetic DXF files."""
from __future__ import annotations

import math
import zipfile

import ezdxf
import pytest

from floorplan_digitizer import dxf2sh3d as D
from floorplan_digitizer.sh3d import read_home_xml, validate_xml


# --------------------------------------------------------------------------
# synthetic drawings
# --------------------------------------------------------------------------
def new_doc(insunits=5):
    doc = ezdxf.new("R2013")
    doc.header["$INSUNITS"] = insunits
    doc.layers.add("WALLS")
    doc.layers.add("TEXT")
    return doc


def rect(msp, x0, y0, x1, y1, layer="WALLS"):
    msp.add_lwpolyline([(x0, y0), (x1, y0), (x1, y1), (x0, y1)], close=True, dxfattribs={"layer": layer})


def rect_centerline(scale=1.0, insunits=5):
    doc = new_doc(insunits)
    rect(doc.modelspace(), 0, 0, 250 * scale, 200 * scale)
    return doc


def rect_double(scale=1.0, insunits=5, t=12.0):
    doc = new_doc(insunits)
    m = doc.modelspace()
    rect(m, 0, 0, 250 * scale, 200 * scale)
    rect(m, t * scale, t * scale, (250 - t) * scale, (200 - t) * scale)
    m.add_text("Bath", height=10 * scale, dxfattribs={"layer": "TEXT"}).set_placement((125 * scale, 100 * scale))
    return doc


def l_shape_double(t=20.0):
    doc = new_doc()
    m = doc.modelspace()
    outer = [(0, 0), (600, 0), (600, 300), (300, 300), (300, 500), (0, 500)]
    inner = [(t, t), (600 - t, t), (600 - t, 300 - t), (300 - t, 300 - t), (300 - t, 500 - t), (t, 500 - t)]
    m.add_lwpolyline(outer, close=True, dxfattribs={"layer": "WALLS"})
    m.add_lwpolyline(inner, close=True, dxfattribs={"layer": "WALLS"})
    return doc


def save(doc, tmp_path, name):
    p = tmp_path / name
    doc.saveas(p)
    return p


def convert_file(path, tmp_path, *args):
    out = tmp_path / (path.stem + ".sh3d")
    assert D.main([str(path), "-o", str(out), *args]) == 0
    return out


def axis_lengths(res):
    return sorted(round(math.dist(w.a, w.b), 1) for w in res.walls)


# --------------------------------------------------------------------------
# analysis
# --------------------------------------------------------------------------
def test_analysis_detects_drawing_style():
    a = D.analyse(rect_centerline())
    assert a["style"] == "centerline"
    assert a["wall_layers"] == ["WALLS"]
    assert a["units"].startswith("cm")
    b = D.analyse(rect_double())
    assert b["style"] == "double"
    assert b["typical_thickness_cm"][0] == 12
    assert b["layers"]["WALLS"]["LWPOLYLINE"] == 2


# --------------------------------------------------------------------------
# geometry
# --------------------------------------------------------------------------
def test_rectangle_centerline():
    res = D.convert(rect_centerline(), mode="centerline", thickness=12, rooms=True)
    assert len(res.walls) == 4
    assert axis_lengths(res) == [200.0, 200.0, 250.0, 250.0]
    assert all(w.t == 12 for w in res.walls)
    assert all(w.at_start and w.at_end for w in res.walls)
    assert len(res.rooms) == 1
    from shapely.geometry import Polygon
    assert Polygon(res.rooms[0][0]).area == pytest.approx(238 * 188, rel=1e-3)


def test_rectangle_double():
    res = D.convert(rect_double(), rooms=True)
    assert len(res.walls) == 4
    assert axis_lengths(res) == [188.0, 188.0, 238.0, 238.0]   # axes 6 cm inside the outer face
    assert all(w.t == pytest.approx(12) for w in res.walls)
    assert all(w.at_start and w.at_end for w in res.walls)
    pts, name = res.rooms[0]
    from shapely.geometry import Polygon
    assert Polygon(pts).area == pytest.approx(226 * 176, rel=1e-3)
    assert name == "Bath"


def test_l_shaped_room_double():
    res = D.convert(l_shape_double(t=20), rooms=True)
    assert len(res.walls) == 6
    assert all(w.t == pytest.approx(20) for w in res.walls)
    assert all(w.at_start and w.at_end for w in res.walls)
    from shapely.geometry import Polygon
    assert Polygon(res.rooms[0][0]).area == pytest.approx(560 * 260 + 260 * 200, rel=1e-3)


def test_arc_wall_centerline():
    doc = new_doc()
    doc.modelspace().add_arc((0, 0), 300, 0, 90, dxfattribs={"layer": "WALLS"})
    res = D.convert(doc, mode="centerline")
    (w,) = res.walls
    assert w.arc == pytest.approx(math.pi / 2)
    assert w.a == pytest.approx([300, 0]) and w.b == pytest.approx([0, 300], abs=1e-9)


def test_arc_wall_double():
    doc = new_doc()
    m = doc.modelspace()
    m.add_arc((0, 0), 290, 0, 90, dxfattribs={"layer": "WALLS"})
    m.add_arc((0, 0), 310, 0, 90, dxfattribs={"layer": "WALLS"})
    res = D.convert(doc, mode="double")
    (w,) = res.walls
    assert w.t == pytest.approx(20)
    assert math.dist(w.a, (0, 0)) == pytest.approx(300)
    assert w.arc == pytest.approx(math.pi / 2)


def test_units_mm_equals_cm():
    cm = D.convert(rect_double())
    mm = D.convert(rect_double(scale=10, insunits=4))
    assert axis_lengths(mm) == axis_lengths(cm)
    assert sorted(w.t for w in mm.walls) == pytest.approx(sorted(w.t for w in cm.walls))


def test_units_override_for_unitless_drawing():
    doc = rect_double(scale=10, insunits=0)          # unitless, values in mm
    res = D.convert(doc, units="mm")
    assert axis_lengths(res) == [188.0, 188.0, 238.0, 238.0]


def test_door_gap_keeps_gap_and_still_finds_room():
    doc = new_doc()
    m = doc.modelspace()
    t = 12
    # outer and inner faces with a 90 cm door in the bottom wall (x 80..170)
    m.add_lwpolyline([(80, 0), (0, 0), (0, 200), (250, 200), (250, 0), (170, 0)], dxfattribs={"layer": "WALLS"})
    m.add_lwpolyline([(80, t), (t, t), (t, 200 - t), (250 - t, 200 - t), (250 - t, t), (170, t)],
                     dxfattribs={"layer": "WALLS"})
    m.add_line((80, 0), (80, t), dxfattribs={"layer": "WALLS"})      # jambs
    m.add_line((170, 0), (170, t), dxfattribs={"layer": "WALLS"})
    res = D.convert(doc, rooms=True)
    assert len(res.walls) == 5      # left, top, right + the bottom wall in two pieces
    xs = sorted(round(x) for w in res.walls for x in (w.a[0], w.b[0]) if abs(w.a[1] - 6) < 1 and abs(w.b[1] - 6) < 1)
    assert 80 in xs and 170 in xs   # the door gap is kept
    assert len(res.rooms) == 1


def test_annotations_are_ignored():
    doc = rect_double()
    m = doc.modelspace()
    m.add_text("ignore me", dxfattribs={"layer": "WALLS"})
    m.add_linear_dim(base=(0, -30), p1=(0, 0), p2=(250, 0), dxfattribs={"layer": "WALLS"}).render()
    h = m.add_hatch(dxfattribs={"layer": "WALLS"})
    h.paths.add_polyline_path([(0, 0), (12, 0), (12, 12)], is_closed=True)
    res = D.convert(doc)
    assert len(res.walls) == 4


# --------------------------------------------------------------------------
# files: Home.xml against the DTD, background, Sweet Home 3D's own reader
# --------------------------------------------------------------------------
@pytest.mark.parametrize("maker,args", [
    (rect_centerline, ["--mode", "centerline"]),
    (rect_double, ["--rooms"]),
    (l_shape_double, ["--rooms"]),
])
def test_output_is_valid_against_dtd(tmp_path, dtd_path, maker, args):
    out = convert_file(save(maker(), tmp_path, "plan.dxf"), tmp_path, *args)
    with zipfile.ZipFile(out) as z:
        assert z.namelist()[0] == "Home.xml"
        assert "ContentDigests" not in z.namelist()
    assert validate_xml(read_home_xml(out), dtd_path) == []


def test_background_image_is_embedded(tmp_path, dtd_path):
    pytest.importorskip("matplotlib")
    out = convert_file(save(rect_double(), tmp_path, "bg.dxf"), tmp_path, "--background")
    with zipfile.ZipFile(out) as z:
        assert "0" in z.namelist() and z.read("0")[:8] == b"\x89PNG\r\n\x1a\n"
    assert validate_xml(read_home_xml(out), dtd_path) == []


def test_sweet_home_3d_reads_walls_and_rooms(tmp_path, sh3d_oracle):
    out = convert_file(save(rect_double(), tmp_path, "oracle.dxf"), tmp_path, "--rooms")
    r = sh3d_oracle(out)
    assert r["counts"][:2] == (4, 1)
    assert all(w["at_start"] and w["at_end"] for w in r["walls"])
    assert all(w["t"] == pytest.approx(12) for w in r["walls"])
    assert r["rooms"][0][0] == "Bath"
    assert r["rooms"][0][2] == pytest.approx(226 * 176, rel=1e-3)
    # the y axis is flipped: CAD y=+6 becomes Sweet Home 3D y=-6
    assert any(w["ys"] == pytest.approx(-6) for w in r["walls"])


def test_sweet_home_3d_arc_direction(tmp_path, sh3d_oracle):
    doc = new_doc()
    doc.modelspace().add_arc((0, 0), 300, 0, 90, dxfattribs={"layer": "WALLS"})
    out = convert_file(save(doc, tmp_path, "arc.dxf"), tmp_path, "--mode", "centerline", "--thickness", "20")
    (w,) = sh3d_oracle(out)["walls"]
    assert w["length"] == pytest.approx(math.pi / 2 * 300, rel=2e-3)
    # the arc bulges away from the centre (0,0): its middle is about r from the origin
    mx, my = w["mid"]
    assert math.hypot(mx, my) == pytest.approx(300, abs=15)
    assert my < 0          # CAD quadrant +y is Sweet Home 3D -y


def test_sweet_home_3d_reads_background(tmp_path, sh3d_oracle):
    pytest.importorskip("matplotlib")
    out = convert_file(save(rect_double(), tmp_path, "bg2.dxf"), tmp_path, "--background")
    scale, x_origin, y_origin, w_px, h_px = sh3d_oracle(out)["bg"]
    assert scale * w_px == pytest.approx(250, rel=0.01)       # image spans the 250 cm drawing
    assert x_origin == pytest.approx(0, abs=1) and y_origin == pytest.approx(200, abs=1)
