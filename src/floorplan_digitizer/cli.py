"""
cli.py - command line front end.

  floorplan-digitizer build   MODEL.yaml --out DIR [--draft] [--formats svg png pdf dxf ifc sh3d] [--dpi 300]
  floorplan-digitizer overlay MODEL.yaml --out DIR
  floorplan-digitizer check   MODEL.yaml            # load + geometry sanity checks
  dxf2sh3d INPUT.dxf -o OUT.sh3d [...]              # separate command, see dxf2sh3d.py
"""
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

from .model import House, all_levels

ALL = ["svg", "png", "pdf", "dxf", "ifc", "sh3d"]


def file_name(H, lv):
    return H.levels[lv].get("file", lv)


def build(model, out, draft=False, formats=None, dpi=300, quiet=False, sh3d_combined=False):
    from .export_svg import common_origin, write_svg
    formats = formats or ALL
    H = House(model)
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    prims = all_levels(H)
    origin = common_origin(prims)
    status = "ENTWURF / DRAFT – zur Prüfung" if draft else ""
    sfx = "_DRAFT" if draft else ""
    written = []

    def log(p):
        written.append(Path(p))
        if not quiet:
            print("wrote", p)

    pdf_pages = []
    n = len(H.levels)
    for i, lv in enumerate(H.levels, start=1):
        name = file_name(H, lv)
        if {"svg", "png", "pdf"} & set(formats):
            svg = out / f"{name}.svg"
            write_svg(H, lv, prims[lv], origin, svg, i, n, status)
            if "svg" in formats:
                log(svg)
            if "png" in formats:
                import cairosvg
                png = out / f"{name}.png"
                cairosvg.svg2png(url=str(svg), write_to=str(png), dpi=dpi, background_color="white")
                log(png)
            if "pdf" in formats:
                import cairosvg
                page = out / f".{name}.page.pdf"
                cairosvg.svg2pdf(url=str(svg), write_to=str(page))
                pdf_pages.append(page)
            if "svg" not in formats:
                svg.unlink()
        if "dxf" in formats:
            from .export_dxf import write_dxf
            p = out / f"{name}.dxf"
            write_dxf(H, lv, prims[lv], p)
            log(p)
    if pdf_pages:
        target = out / f"Complete_Floor_Plans{sfx}.pdf"
        if len(pdf_pages) == 1:
            shutil.move(pdf_pages[0], target)
        elif shutil.which("pdfunite"):
            subprocess.run(["pdfunite", *map(str, pdf_pages), str(target)], check=True)
            for p in pdf_pages:
                p.unlink()
        else:
            from pypdf import PdfWriter  # optional fallback
            wr = PdfWriter()
            for p in pdf_pages:
                wr.append(str(p))
            wr.write(str(target))
            for p in pdf_pages:
                p.unlink()
        log(target)
    base = H.d["meta"].get("file_stem", "House")
    if "ifc" in formats:
        from .export_ifc import write_ifc
        p = out / f"{base}{sfx}.ifc"
        write_ifc(H, p)
        log(p)
    if "sh3d" in formats:
        from .export_sh3d import write_sh3d
        for p in write_sh3d(H, out, backgrounds=True, combined=sh3d_combined, suffix=sfx):
            log(p)
    return written


def check(model):
    H = House(model)
    problems = []
    ids = [w["id"] for w in H.d["walls"]] + [o["id"] for o in H.openings] + [r["id"] for r in H.d["rooms"]]
    dup = {i for i in ids if ids.count(i) > 1}
    if dup:
        problems.append(f"duplicate ids: {sorted(dup)}")
    for o in H.openings:
        w = H.walls[o["wall"]]
        if not H.wall_poly(w).buffer(1).contains(H.opening_box(o)):
            problems.append(f"opening {o['id']} sticks out of wall {w['id']}")
    for r in H.d["rooms"]:
        rp = H.room_poly(r)
        if not rp.is_valid:
            problems.append(f"room {r['id']} polygon invalid")
        for w in H.walls_on(r["level"]):
            ov = rp.intersection(H.wall_poly(w)).area
            if ov > 1e4 and not rp.buffer(1).contains(H.wall_poly(w)):   # crosses the room edge
                problems.append(f"room {r['id']} overlaps wall {w['id']} by {ov / 1e6:.3f} m²")
    for p in problems:
        print("PROBLEM:", p)
    print(f"{len(H.d['walls'])} walls, {len(H.openings)} openings, {len(H.d['rooms'])} rooms on "
          f"{len(H.levels)} levels - {'OK' if not problems else f'{len(problems)} problem(s)'}")
    return 0 if not problems else 1


def main(argv=None):
    ap = argparse.ArgumentParser(prog="floorplan-digitizer")
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build", help="generate outputs from a model")
    b.add_argument("model")
    b.add_argument("--out", required=True)
    b.add_argument("--draft", action="store_true", help="mark sheets and file names as DRAFT")
    b.add_argument("--formats", nargs="+", choices=ALL, default=ALL)
    b.add_argument("--dpi", type=int, default=300)
    b.add_argument("--sh3d-combined", action="store_true",
                   help="one .sh3d with all floors as levels (default: one .sh3d per floor)")
    o = sub.add_parser("overlay", help="draw the model over the original scans")
    o.add_argument("model")
    o.add_argument("--out", required=True)
    c = sub.add_parser("check", help="sanity-check a model")
    c.add_argument("model")
    a = ap.parse_args(argv)
    if a.cmd == "build":
        build(a.model, a.out, a.draft, a.formats, a.dpi, sh3d_combined=a.sh3d_combined)
        return 0
    if a.cmd == "overlay":
        from .overlay import write_overlays
        H = House(a.model)
        for r in write_overlays(H, a.out):
            print(f"{r['level']}: {r['px_per_m']:.1f} px/m, residuals "
                  + ", ".join(f"{v:.0f} cm" for v in r["residuals_cm"]) + f" -> {r['file']}")
        return 0
    return check(a.model)


if __name__ == "__main__":
    sys.exit(main())
