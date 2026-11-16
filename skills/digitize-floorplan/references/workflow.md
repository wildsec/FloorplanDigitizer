# Digitising workflow – checklist

## Phase 1 – Source analysis
- [ ] List all sources: plans per floor, sections, elevations, site plan, title block, energy certificate or survey.
      Permit sets often hold sections and elevations that are the only source for heights.
- [ ] Per scan: pixel size, approximate px/m (`overall dimension in px / metres`), distortion, missing parts.
- [ ] Rotate scans so that the labels read normally. Keep the untouched originals separately.
- [ ] Orientation: the north arrow may refer to a site plan drawn rotated relative to the floor plans.
      Cross-check it with the elevation titles ("south elevation" shows which facade).
- [ ] Report: quality, completeness, what is dimensioned and what is not, the proposed coordinate system.

## Phase 2 – Questions to ask (only what matters)
- Current state or design state? Known changes since construction (removed walls, new openings, extensions, enclosed porches or terraces).
- Real exterior wall thickness, and whether added insulation sits outside (rooms unchanged) or inside.
- Use of unclear areas (voids, spaces under terraces, crawl spaces).
- Stairs: number of steps, riser height, landings, direction. If unknown: use normal values (riser 170–185 mm) and mark them E.
- Entrance, porches and outside stairs. These are often changed later.
- Which elements to include (garage, outbuildings, site).

## Phase 3 – Measurements
- Transcribe every chain, side by side. Each chain must sum to its overall dimension.
- Resolve unreadable digits with arithmetic only, and document each resolution (e.g. "3.75 → 3.15 because 35+3.15+25+3.87+38 = 8.00").
- Areas: width × depth of the clear dimensions; compare with the area written on the plan.
- Window and door notation such as `90/50` = width/height in cm. Door widths are structural openings.
- Cross-floor alignment: load-bearing walls, chimneys and stairs must line up.

## Phase 4 – Model
- See `model-schema.md`. Rectangles for walls whenever possible. Split L-shaped walls into rectangles.
- Openings sit on a wall and are positioned by `at` (centre along the wall axis).
- Rooms are polygons along the inner faces. `area_original` holds the area written on the plan.
- `dimensions:` chains are computed from the geometry: give the measuring line and the placement.
- `scans:` with ≥ 3 control points (outer corners) per floor enables overlays and Sweet Home 3D backgrounds.

## Phase 5 – Validation
- `floorplan-digitizer check` must be OK.
- Overlays: walls and openings should match the scan within a few cm. Explain every deviation.
- Look at every PNG sheet: overlapping labels, dimension texts, stair labels. Fix them with `label_at`.
- IFC: open it in a viewer (e.g. BlenderBIM or FreeCAD) if available. `ifcopenshell.validate` should report 0 issues.
- Sweet Home 3D: one file per floor; drop catalogue doors and windows on the walls (they cut the walls automatically).

## Phase 6 – Documentation
- `Measurements.md`: chains, derived coordinates and openings, each with its class.
- `Uncertainties.md`: open points (ID, detail, recommendation) and resolved points (date, resolution).
- `Project_Information.md`: building data, conventions, layers, toolchain, change log.

## Privacy
House plans, scans, addresses and owner names are private. Keep them in the user's project
folder and never commit them to a repository (this plugin's repository included).
