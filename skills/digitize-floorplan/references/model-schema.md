# Building model schema (YAML)

Units: **millimetres**. Plan coordinates: x to the right, y upwards (CAD convention). All floors
share one origin. `src` = source class (`O`, `D`, `E`, `M`, or combined such as `O/E`, meaning
size O and position E). A full working example is `examples/demo-house/model.yaml`.

```yaml
meta:
  project: "My House"                 # title block, IFC project, Sweet Home 3D name
  title: "Digitised floor plans"
  variant: "current state"
  file_stem: "House"                  # name of the IFC (and of the combined .sh3d)
  north_deg_from_plan_up: 30          # optional north arrow (clockwise from plan up)
  north_arrow_at: [-1500, 9000]       # optional position (mm)
  dim_format: at                      # at = >=1 m in m, <1 m in cm | m | mm
  original_area_label: "plan"         # label for area_original in the room labels
  source_lines: ["Permit plan", "Owner information"]

levels:                               # key = short level id
  EG: {file: Ground_Floor, name: "Erdgeschoss", name_en: "Ground Floor", plan_no: "01",
       elevation: 0, clear_height: 2600, slab_above: 300, floor_thickness: 200,
       knee_height: 1000,             # optional (attic)
       show_below: KG}                # optional: draw the exterior walls of level KG dashed

walls:                                # rect [x1,y1,x2,y2] or poly [[x,y],...]
  - {id: EG-W1, level: EG, type: exterior, rect: [0, 0, 9000, 300], src: O, note: "..."}
  # type: exterior | interior | knee | garage | enclosure | retaining | foundation
  # optional: height, z (relative to the level), mark_estimated: true

openings:                             # on a wall; at = centre along the wall axis
  - {id: W1, level: EG, wall: EG-W1, kind: window, at: 2700, width: 1800, height: 1400, sill: 800, src: O, src_pos: O}
  - {id: D1, level: EG, wall: EG-W1, kind: door, at: 6700, width: 1000, height: 2100,
     swing: {side: "+", hinge: start}, src: O, src_pos: E}
  # kind: window | door | opening (no door) | garage_door | gate
  # swing.side: + / - = which side of the wall the leaf opens to (normal direction)
  # swing.hinge: start (smaller coordinate) | end
  # optional: label_at [x, y, rotation]

rooms:                                # rect or poly along the inner faces
  - {id: R1, level: EG, name: "Living", rect: [300, 300, 5400, 6700], area_original: 32.64, src: O}
  # optional: label_at [x, y], floor_offset (raised floor, mm), height

areas:                                # labelled open areas (terraces, ...)
  - {id: A1, level: EG, name: "Terrace", poly: [...], hatch: tiles, note: "covered"}

stairs:
  - id: S1
    name: "Stair"
    from: EG
    to: OG
    rise: 2900                        # floor to floor
    risers: 16
    width: 1000
    direction: up                     # 'down' for outside stairs leaving a terrace
    draw_on: {EG: cut, OG: full}      # cut = lower part solid, rest dashed; outline = dashed only
    cut_after: 6
    draw_segments: {OG: [1]}          # optional: draw only these segments on a level
    label_at: {EG: [x, y, rot]}
    segments:                         # in walking order, bottom -> top
      - {type: straight, box: [x1, y1, x2, y2], dir: "+y", n: 15}
      - {type: landing,  box: [x1, y1, x2, y2], walk: [[x, y]]}
      - {type: winder,   box: [...], pivot: [x, y], a0: -90, a1: 0, n: 3}

chimneys:
  - {id: C1, levels: [EG, OG], rect: [...], flues: 2, flue_size: [140, 205], top: 7000, src: O/E}

fixtures:                             # plumbing layer
  - {id: F1, level: OG, type: bathtub, rect: [...], back: "-y"}   # bathtub | basin | wc

lines:                                # extra lines on layer 10
  - {id: L1, level: EG, style: dashed, pts: [[x, y], [x, y]], note: "beam above"}

roof_outline:
  OG: {poly: [[x, y], ...], src: O/E}

slabs:                                # IFC slabs; top relative to the level, thickness downwards
  - {id: SLAB-OG, level: OG, envelope: EG, top: 0, thickness: 300, holes: [S1]}
  - {id: SLAB-TERRACE, level: EG, poly: [...], thickness: 200}

dimensions:                           # chains, ticks computed from the geometry
  EG:
    - {axis: x, geom: cut,    line: 150,  from: 0, to: 9000, place: -1200}   # opening edges
    - {axis: x, geom: walls,  line: 1000, from: 0, to: 9000, place: -1800}   # wall faces
    - {axis: x, geom: points, ticks: [0, 9000], place: -2400}                # overall
    # interior: true for a chain drawn inside the building

scans:                                # optional: overlays + Sweet Home 3D backgrounds
  EG:
    image: ../Originals/ground_floor.png      # relative to the model file
    rotate: 90                                # optional, clockwise degrees
    control_points: [[0, 0, 138.5, 834.5], [12, 0, 560.8, 834.5], [12, 10, 560.8, 485.4]]
    # [x_m, y_m, pixel_x, pixel_y] - model metres to image pixels
```

## CAD layers (fixed)

`LAYER_01_EXTERIOR_WALLS` … `LAYER_10_OTHER_ELEMENTS` (walls, doors, windows, stairs, rooms,
labels, dimensions, plumbing, other), plus `LAYER_11_UNCERTAIN` for the markers of estimated elements.
