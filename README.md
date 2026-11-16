# FloorplanDigitizer

Turn old, scanned or hand-drawn floor plans into a **precise, editable digital building model**, and
generate everything else from that one model:

| Output | What you get |
|---|---|
| **SVG** | one A3 sheet per floor at 1:100; Inkscape layers = CAD layers; every wall, opening, room and stair is an addressable group with its source class |
| **PDF** | all floors, print-ready: title block, scale bar, north arrow, legend |
| **PNG** | high-resolution previews |
| **DXF** | per floor, millimetres, 11 layers, one block per door/window (with attributes), real DIMENSION entities, wall hatches with metadata; all floors share one origin (usable as XREFs) |
| **IFC4 (BIM)** | storeys, walls with openings, doors, windows, spaces with areas, slabs, stairs, chimneys, sanitary fixtures; every element keeps its source class |
| **Sweet Home 3D** | one `.sh3d` per floor (or one file with levels): exact walls, named rooms, the original scan as background |
| **Overlays** | the model drawn over the original scans, to check the reconstruction |

It also ships **`dxf2sh3d`**, a converter from any DXF floor plan to Sweet Home 3D, with real walls,
curved walls, joined corners, rooms with names and an optional background image.

Everything comes as a **Claude Code plugin**: Claude guides you through the digitising workflow
(source analysis → questions → measurements → model → review → export) and never invents a measurement.
Every value is marked **O** (original, read from the plan), **D** (derived), **E** (estimated) or
**M** (measured on site), and estimates stay visible in every output.

## Install

### As a Claude Code plugin

```text
/plugin marketplace add wildsec/FloorplanDigitizer
/plugin install floorplan-digitizer@floorplan-digitizer
```

Then just ask, e.g. *"Digitise the floor plans in ./scans"* or *"Convert plan.dxf to Sweet Home 3D"*.
The skills are `digitize-floorplan` and `dxf-to-sweethome3d`.

### As a Python tool

```bash
python3 -m venv .venv
.venv/bin/pip install "floorplan-digitizer[bim,background] @ git+https://github.com/wildsec/FloorplanDigitizer"
```

Requirements: Python ≥ 3.10. Optional: `pdfunite` (poppler) for merging PDF pages, Java + Sweet Home 3D
for the headless file check.

## Usage

```bash
floorplan-digitizer check   model.yaml                       # sanity checks
floorplan-digitizer build   model.yaml --out Review --draft  # all formats, marked DRAFT
floorplan-digitizer overlay model.yaml --out Review          # model over the scans
floorplan-digitizer build   model.yaml --out Final --formats dxf ifc sh3d [--sh3d-combined]

dxf2sh3d plan.dxf --analyse                                  # what is in the DXF, how are walls drawn?
dxf2sh3d plan.dxf -o plan.sh3d --rooms --background
```

Try it with the invented example: `floorplan-digitizer build examples/demo-house/model.yaml --out /tmp/demo`.
The model format is documented in
[`skills/digitize-floorplan/references/model-schema.md`](skills/digitize-floorplan/references/model-schema.md).

## dxf2sh3d – preparing your drawing

- Walls on their own layer (`WALLS`, `WÄNDE`, `MAUER` … are detected; otherwise pass `--wall-layer`).
- Draw either **centre lines** (`--mode centerline`, thickness from `--thickness`) or **both faces** of every
  wall (`--mode double`, thickness = face distance). `--mode auto` (default) decides from the analysis.
- Leave door and window openings as gaps. For room detection (`--rooms`), collinear gaps up to
  `--close-gap` cm (default 300) and gaps next to a corner up to 130 cm are bridged.
- One text per room (letters, no digits) names the room.
- Set `$INSUNITS` in the DXF, or pass `--units mm|cm|m`.

**Known limitations:**
- Doors and windows are not created as Sweet Home 3D furniture yet. Drop them from the catalogue
  onto the walls, and Sweet Home 3D cuts the walls itself.
- Splines, ellipses and circles on wall layers are skipped (and reported).
- No roofs.

## How it is verified

`pytest` builds synthetic DXFs (rectangle in centre-line and double-line mode, an L-shaped room, arc walls,
mm vs cm, a door gap) and the demo house, then checks:
- the geometry;
- every `Home.xml` against the official `SweetHome3D.dtd`, which is downloaded at test time and not vendored, since it's GPL;
- IFC schema validity;
- DXF audits;
- and, when Java and Sweet Home 3D are installed, loads every `.sh3d` with **Sweet Home 3D's own reader**
  (`scripts/Sh3dCheck.java`) to check walls, joins, room areas, arc direction, levels and backgrounds.

The official DTD has three typos that strict parsers reject. The validator fixes them in memory
(see `sh3d.validate_xml`).

## Privacy

This repository contains **no real floor plans** and must never contain any. Keep your scans, models and
outputs in your own project folder. A pre-commit hook (`scripts/install-git-hooks.sh`) and the CI job
refuse plan, scan, CAD, BIM, PDF and image files, and any YAML model outside `examples/`.

## License

MIT, see [LICENSE](LICENSE). Uses ezdxf, shapely, ifcopenshell, cairosvg, lxml, Pillow and NumPy.
Sweet Home 3D is © Space Mushrooms (GPL) and is not included.
