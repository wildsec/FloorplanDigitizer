---
name: dxf-to-sweethome3d
description: Convert a 2D DXF floor plan (from any CAD program, e.g. CADCraft, LibreCAD, QCAD, AutoCAD) into a Sweet Home 3D .sh3d file with real walls, curved walls, joined corners, rooms with names and an optional background image of the drawing. Use when someone wants to open or furnish a DXF floor plan in Sweet Home 3D, or says "convert my plan to Sweet Home 3D".
---

# DXF → Sweet Home 3D

The converter is `dxf2sh3d` from this plugin (the plugin root is `<skill dir>/../..`).
Install it once into a venv in the user's project folder (never system-wide):

```bash
python3 -m venv .venv && .venv/bin/pip install "<plugin root>[background]"
```

## Steps

1. **Analyse first and show the result. Don't convert yet.**
   ```bash
   .venv/bin/dxf2sh3d plan.dxf --analyse
   ```
   It reports the units ($INSUNITS), the layers with entity counts, the bounding box, the wall
   layers it detected, and whether walls are drawn as **(a) single centre lines** or
   **(b) double parallel lines**, with the most common wall thicknesses.
   Ask the user to confirm the wall layer(s), the units and the mode when anything looks unusual:
   unitless drawings, no layer named like WALL/WAND, or a low pairing percentage.
2. **Convert:**
   ```bash
   .venv/bin/dxf2sh3d plan.dxf -o plan.sh3d --rooms [--background] \
       [--wall-layer WALLS] [--units mm|cm|m] [--mode auto|centerline|double] \
       [--thickness 12] [--height 250] [--snap 1] [--close-gap 300]
   ```
   The summary lists the walls (total length, curved, connected), the rooms with names, the
   unpaired face length and any skipped entity types. Report it.
3. **Verify:**
   - The file is a ZIP with `Home.xml`. It validates against `SweetHome3D.dtd`
     (`floorplan_digitizer.sh3d.validate_xml`).
   - If Java and Sweet Home 3D are installed, load the file headless with Sweet Home 3D's own
     reader:
     `java -cp /usr/share/java/sweethome3d/SweetHome3D.jar <plugin root>/scripts/Sh3dCheck.java plan.sh3d`
   - To open the result: `sweethome3d plan.sh3d` (or File › Open).

## How the drawing should be prepared

- Put walls on a dedicated layer (`WALLS`/`WÄNDE`/`MAUER` are detected automatically; or pass `--wall-layer`).
- Either single centre lines (thickness from `--thickness`) or both faces of every wall. Don't mix the two on one layer.
- Leave openings as real gaps. Rooms are still detected, because gaps up to `--close-gap` cm are bridged for room detection only.
- Room names: one text per room inside it. The largest text with letters and no digits wins.
- Set the drawing units ($INSUNITS) or pass `--units`.

## Known limitations

- Doors and windows are not created as furniture yet. Drop them from the Sweet Home 3D catalogue onto the walls.
- Splines, ellipses and circles on wall layers are skipped (and reported).
- Rooms whose door fills a whole wall segment can't be closed and are not detected.
