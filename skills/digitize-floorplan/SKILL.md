---
name: digitize-floorplan
description: Digitise old, scanned, photographed or hand-drawn floor plans of a building into one precise, editable building model (YAML, millimetres, every value marked original/derived/estimated) and generate professional outputs from it - SVG and PDF plan sheets, PNG previews, CAD (DXF with layers, blocks and real dimensions), BIM (IFC4) and Sweet Home 3D files - plus overlay checks against the scans. Use when someone wants to turn old house/apartment plans into digital 2D/3D data, a CAD drawing, a BIM model, a digital twin, or a basis for renovation or interior planning.
---

# Digitise a floor plan

You reconstruct a building from its old drawings. Accuracy and honesty beat speed:
**never invent a measurement**. Every number in the model carries a source class:

| Class | Meaning |
|---|---|
| `O` original | read from a dimension or label on the drawing |
| `D` derived | calculated from O values (chain sums, area checks, cross-floor alignment, owner statements) |
| `E` estimated | scaled from the image or assumed. Marked with a red "E" on the sheets and kept in the IFC/DXF data. |
| `M` measured | measured on site by the owner; overrides everything else |

## Setup (once per project)

The plugin root is two directories above this skill's base directory (`<skill dir>/../..`).
Create a virtual environment **in the user's project folder** (never install system-wide):

```bash
python3 -m venv .venv
.venv/bin/pip install "<plugin root>[bim,background]"      # adds the floorplan-digitizer and dxf2sh3d commands
```

Keep the user's plans, scans, models and outputs in the user's own project folder:
**never copy them into this plugin's directory or any repository**.

## Workflow (do not skip phases; the user reviews between them)

Read `references/workflow.md` for the full checklist. In short:

1. **Source analysis.** Look at every scan, zoomed in. Rotate scans to reading orientation.
   Note the resolution (px per metre), the scale, the dimension chains, labels, the north arrow,
   sections and elevations (the only source for heights). Report the quality and wait.
2. **Questions.** Ask targeted questions:
   - Is this the current state, or were walls, openings or extensions changed?
   - What are the real wall thicknesses? Plans often show the masonry without insulation.
   - What are unclear areas used for, how are the stairs built, which way does the building face?
3. **Measurements.** Transcribe every dimension chain. A chain must add up to its overall
   dimension; use that to resolve unreadable digits (handwritten 1/7, 3/5). Check every room
   area written on the plan against width × depth. Record everything with its class.
4. **Model.** Write `model.yaml` (schema: `references/model-schema.md`; example:
   `<plugin root>/examples/demo-house/model.yaml`). Choose one origin and keep it on every floor.
5. **Build and check.**
   ```bash
   .venv/bin/floorplan-digitizer check   model.yaml
   .venv/bin/floorplan-digitizer build   model.yaml --out Review --draft
   .venv/bin/floorplan-digitizer overlay model.yaml --out Review     # needs `scans:` in the model
   ```
   Look at the PNGs and overlays yourself, and fix label clutter and collisions (`label_at`).
   Then show the user the sheets and overlays.
6. **Corrections loop.** Apply the owner's corrections in the model, rebuild, and show the result again.
7. **Final export:** run `build` without `--draft` into the final folders. Then write the
   documentation: measurements, uncertainties (open questions plus resolved items with dates),
   and project information.

## Outputs of `build`

`<Level>.svg / .png / .dxf / .sh3d` per floor, `Complete_Floor_Plans.pdf` (A3, 1:100, title
block, scale bar, north arrow, legend) and `<file_stem>.ifc`. Use `--formats` to limit them and
`--sh3d-combined` for one Sweet Home 3D file with all floors as levels.

## Rules that matter

- Written dimensions win over scaled ones. When they disagree, keep the written value and record the deviation.
- Don't guess silently. Every assumption becomes an `E` value plus an entry in the uncertainties list.
- Wall thickness changes: ask on which side the extra thickness goes (insulation outside, so the
  rooms keep their size, or inside). Model it explicitly.
- Prefer a few precise questions over long lists. Do give a recommendation for each open point.
- Show intermediate results (rendered PNGs, overlays) before the final export.
