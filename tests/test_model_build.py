"""Building model -> all outputs, using the invented demo house."""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import ezdxf
import pytest
from lxml import etree

from floorplan_digitizer import dxf2sh3d as D
from floorplan_digitizer.cli import build, check
from floorplan_digitizer.model import House, fmt_len
from floorplan_digitizer.sh3d import read_home_xml, validate_xml

DEMO = Path(__file__).resolve().parent.parent / "examples" / "demo-house" / "model.yaml"


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    out = tmp_path_factory.mktemp("demo")
    files = build(DEMO, out, dpi=60, quiet=True)
    return out, {f.name for f in files}


def test_model_check_passes():
    assert check(DEMO) == 0


def test_all_formats_written(built):
    out, names = built
    for n in ["Ground_Floor.svg", "Upper_Floor.svg", "Ground_Floor.png", "Ground_Floor.dxf", "Upper_Floor.dxf",
              "Complete_Floor_Plans.pdf", "DemoHouse.ifc", "Ground_Floor.sh3d", "Upper_Floor.sh3d"]:
        assert n in names, n
        assert (out / n).stat().st_size > 300, n


def test_svg_has_cad_layers_and_element_ids(built):
    out, _ = built
    root = etree.parse(str(out / "Ground_Floor.svg")).getroot()
    labels = {g.get("{http://www.inkscape.org/namespaces/inkscape}label") for g in root.iter("{*}g")}
    assert {"LAYER_01_EXTERIOR_WALLS", "LAYER_03_DOORS", "LAYER_04_WINDOWS", "LAYER_08_DIMENSIONS"} <= labels
    ids = {g.get("id") for g in root.iter("{*}g")}
    assert {"EG-W1", "EG_D1", "EG_R1"} <= ids


def test_dxf_layers_blocks_dimensions(built):
    out, _ = built
    doc = ezdxf.readfile(out / "Ground_Floor.dxf")
    assert not doc.audit().has_errors
    assert doc.units == ezdxf.units.MM
    assert "LAYER_02_INTERIOR_WALLS" in doc.layers
    inserts = [e for e in doc.modelspace() if e.dxftype() == "INSERT"]
    assert {i.get_attrib_text("ID") for i in inserts} == {"D1", "D2", "D3", "W1", "W2", "W3"}
    assert sum(1 for e in doc.modelspace() if e.dxftype() == "DIMENSION") > 5


def test_pdf_has_one_page_per_level(built):
    out, _ = built
    data = (out / "Complete_Floor_Plans.pdf").read_bytes()
    assert data.startswith(b"%PDF")
    if shutil.which("pdfinfo"):
        info = subprocess.run(["pdfinfo", str(out / "Complete_Floor_Plans.pdf")], capture_output=True, text=True).stdout
        import re
        assert re.search(r"^Pages:\s+2$", info, re.M)


def test_ifc_is_valid(built):
    ifcopenshell = pytest.importorskip("ifcopenshell")
    import ifcopenshell.validate
    f = ifcopenshell.open(str(built[0] / "DemoHouse.ifc"))
    log = ifcopenshell.validate.json_logger()
    ifcopenshell.validate.validate(f, log)
    assert log.statements == []
    assert len(f.by_type("IfcWall")) == 12
    assert len(f.by_type("IfcBuildingStorey")) == 2
    assert len(f.by_type("IfcDoor")) == 5 and len(f.by_type("IfcWindow")) == 5
    est = [p for p in f.by_type("IfcPropertySingleValue") if p.Name == "SourceClass" and p.NominalValue
           and "E" in p.NominalValue.wrappedValue]
    assert est, "estimated elements must stay marked in the IFC"


def test_native_sh3d_valid(built, dtd_path):
    for n in ("Ground_Floor.sh3d", "Upper_Floor.sh3d"):
        assert validate_xml(read_home_xml(built[0] / n), dtd_path) == []


def test_native_sh3d_one_file_per_floor(built, sh3d_oracle):
    r = sh3d_oracle(built[0] / "Ground_Floor.sh3d")
    assert r["counts"] == (6, 3, 0)            # walls, rooms, no levels
    living = [a for n, _, a in r["rooms"] if n == "Living"]
    assert living == [pytest.approx(510 * 640, rel=1e-4)]
    assert all(w["t"] in (pytest.approx(30), pytest.approx(12)) for w in r["walls"])
    assert sh3d_oracle(built[0] / "Upper_Floor.sh3d")["counts"] == (6, 3, 0)


def test_native_sh3d_doors_and_windows(built, sh3d_oracle):
    import re
    dw = {re.search(r"\b([A-Z]+\d+)\b", d["name"]).group(1): d
          for d in sh3d_oracle(built[0] / "Ground_Floor.sh3d")["dw"]}
    assert set(dw) == {"D1", "D2", "D3", "W1", "W2", "W3"}
    assert all(d["model"] for d in dw.values()), "embedded 3D models must load"
    d1, w1 = dw["D1"], dw["W1"]
    assert (d1["x"], d1["width"], d1["height"]) == pytest.approx((670, 100, 210))
    assert d1["depth"] >= 30 - 1e-6           # at least as deep as the wall, so it is cut through
    assert d1["sashes"] == 1 and w1["sashes"] in (0, 2)   # 2 with the installed catalogue double window
    assert (w1["elevation"], w1["width"]) == pytest.approx((80, 180))


def test_sweet_home_3d_plan_render(built, tmp_path):
    import os
    import subprocess
    jar = os.environ.get("SH3D_JAR", "/usr/share/java/sweethome3d/SweetHome3D.jar")
    if not (Path(jar).exists() and shutil.which("java")):
        pytest.skip("java / SweetHome3D.jar not available")
    png = tmp_path / "plan.png"
    script = Path(__file__).resolve().parent.parent / "scripts" / "Sh3dRender.java"
    r = subprocess.run(["java", "-cp", jar, str(script), str(built[0] / "Ground_Floor.sh3d"), str(png)],
                       capture_output=True, text=True, timeout=180)
    assert r.returncode == 0, r.stderr[-1500:]
    assert png.stat().st_size > 5000


def test_native_sh3d_combined_levels(tmp_path, sh3d_oracle):
    from floorplan_digitizer.export_sh3d import write_sh3d
    (p,) = write_sh3d(House(DEMO), tmp_path, combined=True)
    r = sh3d_oracle(p)
    assert r["counts"] == (12, 6, 2)
    assert [lv[0] for lv in r["levels"]] == ["EG Erdgeschoss", "OG Obergeschoss"]
    assert r["levels"][1][1] == pytest.approx(r["levels"][0][1] + r["levels"][0][3] + r["levels"][1][2])


def test_generated_dxf_converts_back_to_the_same_rooms(built):
    """Round trip: model -> DXF -> dxf2sh3d finds the model's rooms with the same areas."""
    doc = ezdxf.readfile(built[0] / "Ground_Floor.dxf")
    res = D.convert(doc, rooms=True)
    H = House(DEMO)
    from shapely.geometry import Polygon
    found = {n: Polygon(p).area * 100 for p, n in res.rooms}          # cm2 -> mm2
    for r in H.rooms_on("EG"):
        assert r["name"] in found, r["name"]
        assert found[r["name"]] == pytest.approx(H.room_poly(r).area, rel=2e-3)


def test_dimension_text_styles():
    from floorplan_digitizer import model
    model.DIM_FORMAT["style"] = "at"
    assert (fmt_len(350), fmt_len(1250), fmt_len(70), fmt_len(225)) == ("35", "1.25", "7", "22.5")
    model.DIM_FORMAT["style"] = "m"
    assert fmt_len(350) == "0.35"
    model.DIM_FORMAT["style"] = "mm"
    assert fmt_len(350) == "350"
