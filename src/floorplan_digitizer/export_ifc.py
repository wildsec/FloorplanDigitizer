"""
export_ifc.py - IFC4 (BIM) model from house.yaml via ifcopenshell.

Structure: IfcProject > IfcSite > IfcBuilding > IfcBuildingStorey (KG/EG/DG)
Elements : IfcWall (+ IfcOpeningElement voids), IfcDoor, IfcWindow, IfcSpace,
           IfcSlab, IfcStair, IfcChimney, IfcSanitaryTerminal.
Each element has property set "Pset_FPD_Source" with SourceClass (O/D/E/M),
SourcePosition, Note and ModelId, so estimated parts stay identifiable.
Not modelled: roofs (describe them in the model notes; planned).
"""
from __future__ import annotations

import numpy as np
import ifcopenshell
import ifcopenshell.api.aggregate
import ifcopenshell.api.context
import ifcopenshell.api.feature
import ifcopenshell.api.geometry
import ifcopenshell.api.project
import ifcopenshell.api.pset
import ifcopenshell.api.root
import ifcopenshell.api.spatial
import ifcopenshell.api.unit
from shapely.geometry import MultiPolygon, Polygon, box
from shapely.ops import unary_union

from .model import House

API = ifcopenshell.api

def wall_extent(H: House, w):
    """(z_rel, height) of a wall: explicit 'z'/'height' in the model win;
    otherwise exterior-type walls run floor to floor (clear height + slab
    above), knee walls use the level's knee_height, the rest the clear height."""
    lv = H.levels[w["level"]]
    if "height" in w:
        return w.get("z", 0), w["height"]
    if w["type"] in ("exterior", "retaining", "foundation"):
        return w.get("z", 0), lv["clear_height"] + lv.get("slab_above", 0)
    if w["type"] == "knee":
        return w.get("z", 0), lv.get("knee_height", 1000)
    return w.get("z", 0), lv["clear_height"]


class Writer:
    def __init__(self, H: House):
        self.H = H
        f = self.f = API.project.create_file(version="IFC4")
        self.project = API.root.create_entity(f, ifc_class="IfcProject", name=H.d["meta"]["project"])
        API.unit.assign_unit(f, length={"is_metric": True, "raw": "MILLIMETERS"})
        model = API.context.add_context(f, context_type="Model")
        self.body = API.context.add_context(f, context_type="Model", context_identifier="Body",
                                            target_view="MODEL_VIEW", parent=model)
        self.site = API.root.create_entity(f, ifc_class="IfcSite", name=H.d["meta"].get("site_name", "Site"))
        self.building = API.root.create_entity(f, ifc_class="IfcBuilding", name=H.d["meta"]["project"])
        API.aggregate.assign_object(f, products=[self.site], relating_object=self.project)
        API.aggregate.assign_object(f, products=[self.building], relating_object=self.site)
        self.storeys = {}
        for key, lv in H.levels.items():
            st = API.root.create_entity(f, ifc_class="IfcBuildingStorey", name=f"{key} {lv['name']}")
            st.Elevation = float(lv["elevation"])
            self.place(st, 0, 0, lv["elevation"])
            API.aggregate.assign_object(f, products=[st], relating_object=self.building)
            self.storeys[key] = st

    # ---------- helpers ----------
    def place(self, product, x, y, z):
        m = np.eye(4)
        m[:3, 3] = (x, y, z)
        API.geometry.edit_object_placement(self.f, product=product, matrix=m, is_si=False)

    def solid(self, poly: Polygon, z0, h):
        f = self.f
        items = []
        polys = list(poly.geoms) if isinstance(poly, MultiPolygon) else [poly]
        for p in polys:
            if p.is_empty or p.area < 1:
                continue
            outer = f.createIfcPolyline([f.createIfcCartesianPoint((float(x), float(y)))
                                         for x, y in p.exterior.coords])
            if p.interiors:
                inner = [f.createIfcPolyline([f.createIfcCartesianPoint((float(x), float(y))) for x, y in r.coords])
                         for r in p.interiors]
                prof = f.createIfcArbitraryProfileDefWithVoids("AREA", None, outer, inner)
            else:
                prof = f.createIfcArbitraryClosedProfileDef("AREA", None, outer)
            pos = f.createIfcAxis2Placement3D(f.createIfcCartesianPoint((0.0, 0.0, float(z0))), None, None)
            items.append(f.createIfcExtrudedAreaSolid(prof, pos, f.createIfcDirection((0.0, 0.0, 1.0)), float(h)))
        return items

    def element(self, cls, name, level, items, src=None, src_pos=None, note="", mid="", predef=None, z=None):
        f = self.f
        el = API.root.create_entity(f, ifc_class=cls, name=name, predefined_type=predef)
        if cls == "IfcSpace":
            API.aggregate.assign_object(f, products=[el], relating_object=self.storeys[level])
        elif cls != "IfcOpeningElement":
            API.spatial.assign_container(f, products=[el], relating_structure=self.storeys[level])
        self.place(el, 0, 0, self.H.levels[level]["elevation"] if z is None else z)
        if items:
            rep = f.createIfcShapeRepresentation(self.body, "Body", "SweptSolid", items)
            API.geometry.assign_representation(f, product=el, representation=rep)
        ps = API.pset.add_pset(f, product=el, name="Pset_FPD_Source")
        API.pset.edit_pset(f, pset=ps, properties={"ModelId": mid or name, "SourceClass": str(src or ""),
                                                   "SourcePosition": str(src_pos or ""), "Note": note or ""})
        return el

    # ---------- building elements ----------
    def walls(self):
        H, self.wall_el = self.H, {}
        for w in H.d["walls"]:
            z0, h = wall_extent(H, w)
            el = self.element("IfcWall", w["id"], w["level"], self.solid(H.wall_poly(w), z0, h),
                              w.get("src"), None, w.get("note", ""), w["id"])
            ps = API.pset.add_pset(self.f, product=el, name="Pset_WallCommon")
            API.pset.edit_pset(self.f, pset=ps, properties={
                "IsExternal": w["type"] in ("exterior", "garage", "enclosure", "retaining", "foundation"),
                "LoadBearing": w["type"] in ("exterior", "retaining", "foundation") or "25" in str(w.get("note", ""))})
            self.wall_el[w["id"]] = el

    def openings(self):
        H, f = self.H, self.f
        for o in H.openings:
            w = H.walls[o["wall"]]
            bx = H.opening_box(o)
            sill = o.get("sill", 0) if o["kind"] == "window" else 0
            # void: slightly thicker than the wall
            grow = bx.buffer(30, join_style=2) if True else bx
            void = self.element("IfcOpeningElement", f"{o['id']}-void", o["level"], self.solid(grow, sill, o["height"]),
                                o.get("src"), o.get("src_pos"), "", f"{o['id']}-void", predef="OPENING")
            API.feature.add_feature(f, feature=void, element=self.wall_el[w["id"]])
            if o["kind"] == "opening":
                continue
            # thin panel in the middle of the wall
            x1, y1, x2, y2 = bx.bounds
            if H.wall_axis(w) == "x":
                m = (y1 + y2) / 2
                panel = box(x1, m - 30, x2, m + 30)
            else:
                m = (x1 + x2) / 2
                panel = box(m - 30, y1, m + 30, y2)
            cls = "IfcWindow" if o["kind"] == "window" else "IfcDoor"
            el = self.element(cls, o["id"], o["level"], self.solid(panel, sill, o["height"]), o.get("src"),
                              o.get("src_pos"), o.get("note", ""), o["id"],
                              predef=("GATE" if o["kind"] in ("gate", "garage_door") else "DOOR") if cls == "IfcDoor" else "WINDOW")
            el.OverallWidth = float(o["width"])
            el.OverallHeight = float(o["height"])
            API.feature.add_filling(f, opening=void, element=el)

    def spaces(self):
        H = self.H
        for r in H.d["rooms"]:
            lv = H.levels[r["level"]]
            h = r.get("height", lv["clear_height"])
            fo = r.get("floor_offset", 0)
            if fo:   # raised floor (platform) inside the room
                self.element("IfcSlab", f"{r['name']} Podest", r["level"], self.solid(H.room_poly(r), 0, fo), "E",
                             None, "raised floor", f"{r['id']}-PODEST", predef="FLOOR")
            el = self.element("IfcSpace", r["name"], r["level"], self.solid(H.room_poly(r), fo, h - fo), r.get("src"),
                              None, r.get("note", ""), r["id"], predef="INTERNAL")
            el.LongName = f"{r['id']} {r['name']}"
            q = API.pset.add_qto(self.f, product=el, name="Qto_SpaceBaseQuantities")
            API.pset.edit_qto(self.f, qto=q, properties={"NetFloorArea": round(H.room_poly(r).area / 1e6, 3)})

    def slabs(self):
        """Slabs listed in the model: {id, name, level, rect|poly, top (rel. to
        the level, default 0), thickness, holes: [stair ids]}."""
        H = self.H
        stairs = {s["id"]: s for s in H.d.get("stairs", [])}
        for sl in H.d.get("slabs", []):
            g = H.envelope(sl["envelope"]) if sl.get("envelope") else H.rect_or_poly(sl)
            for sid in sl.get("holes", []):
                g = g.difference(unary_union(H.stair_treads(stairs[sid])[0]).envelope)
            t = sl["thickness"]
            top = sl.get("top", 0)
            self.element("IfcSlab", sl.get("name", sl["id"]), sl["level"], self.solid(g, top - t, t),
                         sl.get("src"), None, sl.get("note", ""), sl["id"], predef=sl.get("type", "FLOOR"))

    def stairs(self):
        H = self.H
        for s in H.d.get("stairs", []):
            treads, _ = H.stair_treads(s)
            h = s["rise"] / s["risers"]
            items = []
            down = s.get("direction") == "down"
            for i, t in enumerate(treads):
                if down:   # tread 0 is next to the upper (terrace / floor) level
                    top = -(i + 1) * h
                else:
                    top = (i + 1) * h
                items += self.solid(t, top - h, h)
            el = self.element("IfcStair", s["name"], s["from"], items, s.get("src"), None, s.get("note", ""),
                              s["id"], predef="NOTDEFINED")
            ps = API.pset.add_pset(self.f, product=el, name="Pset_StairCommon")
            API.pset.edit_pset(self.f, pset=ps, properties={"NumberOfRiser": s["risers"],
                                                            "NumberOfTreads": len(treads),
                                                            "RiserHeight": round(h, 1)})

    def chimneys(self):
        H = self.H
        for c in H.d.get("chimneys", []):
            base = c["levels"][0]
            top_abs = c.get("top", max(H.levels[l]["elevation"] for l in c["levels"]) + 3500)
            h = top_abs - H.levels[base]["elevation"]
            self.element("IfcChimney", c["id"], base, self.solid(H.rect_or_poly(c), 0, h), c.get("src"), None,
                         c.get("note", f"{c.get('flues', '')} flues; top {top_abs / 1000:+.2f}"), c["id"])

    def fixtures(self):
        dims = {"bathtub": (0, 550), "basin": (750, 150), "wc": (0, 400)}
        for fx in self.H.d.get("fixtures", []):
            z0, h = dims[fx["type"]]
            pred = {"bathtub": "BATH", "basin": "WASHHANDBASIN", "wc": "TOILETPAN"}[fx["type"]]
            self.element("IfcSanitaryTerminal", fx["id"], fx["level"], self.solid(box(*fx["rect"]), z0, h),
                         fx.get("src"), None, fx["type"], fx["id"], predef=pred)


def write_ifc(H: House, path):
    w = Writer(H)
    w.walls()
    w.openings()
    w.spaces()
    w.slabs()
    w.stairs()
    w.chimneys()
    w.fixtures()
    w.f.write(str(path))
