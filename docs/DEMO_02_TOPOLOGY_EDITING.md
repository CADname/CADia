# Demo 02 — Topology-Aware Parametric Editing

**Video:** https://youtu.be/bsyfQU5MiZ4

This second CADia demo focuses on continuous editing of the same real B-Rep CAD model through direct geometric selection.

The workflow first builds a mechanical mounting bracket incrementally, then demonstrates CADia's editing workflow using selected B-Rep faces and edges.

## What the demo shows

- Incremental construction of an L-shaped motor mounting bracket
- Persistent CAD project state across multiple natural-language instructions
- Addition of a central Ø40 mm through-hole
- Addition of two reinforcing gussets
- Direct selection of the cylindrical B-Rep face of the center hole
- Hole-diameter modification from Ø40 mm to Ø50 mm
- Direct face selection for vertical-plate thickness modification from 8 mm to 12 mm
- Direct edge selection for a 5 mm fillet
- Direct edge selection for a 3 mm chamfer
- B-Rep regeneration and verification

## Editing workflow

CADia combines the current project state, feature history, B-Rep topology, geometric selection context, structured CAD operations, and verification.

For an unambiguous history-aware edit, selected B-Rep geometry can be resolved back to the relevant feature or driving parameter. CADia can update that source and rebuild the dependent geometry.

For local edge operations such as fillet and chamfer, the selected B-Rep edges become explicit geometric inputs to the new CAD operation.

The selected geometry therefore provides precise CAD context without requiring the user to describe the entire model again.

## Prompt sequence used in the demo

### 1. Create the base bracket

```text
Create a single-solid L-shaped motor mounting bracket with a 100 × 70 × 8 mm horizontal base plate and a 70 × 60 × 8 mm vertical plate. Position the vertical plate perpendicular to the base at 90 degrees and join the two plates as one continuous solid body. Add four Ø8 mm through mounting holes to the horizontal base, arranged symmetrically in a rectangular pattern.
```

### 2. Add the center hole

```text
Add a Ø40 mm through hole at the center of the vertical plate. The hole axis must be perpendicular to the vertical plate and the hole must pass completely through the plate. Preserve the existing bracket and mounting holes.
```

### 3. Add reinforcing gussets

```text
Add two identical triangular reinforcing gussets directly inside the 90-degree corner where the vertical plate meets the top surface of the base. Each gusset must have a 25 mm horizontal leg along the base, a 25 mm vertical leg along the vertical plate, and a thickness of 6 mm. Position the two gussets symmetrically near the left and right sides of the bracket. Each gusset must physically contact both the base and the vertical plate and be Boolean-unioned with the existing bracket as one continuous solid body.
```

### 4. Modify the selected center hole

Select the cylindrical face of the center hole in the viewport, then run:

```text
Change the diameter of the hole associated with the selected cylindrical face from 40 mm to 50 mm. Modify the existing hole feature and preserve all other geometry.
```

### 5. Modify the selected vertical plate

Select the relevant face of the vertical plate, then run:

```text
Change the thickness of the vertical plate associated with the selected face from 8 mm to 12 mm. Preserve the base plate, holes, gussets, and all other existing features.
```

### 6. Apply a selected-edge fillet

Select the target edges in the viewport, then run:

```text
Apply a 5 mm radius fillet only to the selected edges. Preserve all other geometry and dimensions of the existing bracket.
```

### 7. Apply a selected-edge chamfer

Select the target edges in the viewport, then run:

```text
Apply a 3 mm chamfer only to the selected edges. Preserve all other geometry and dimensions of the existing bracket.
```

## Evaluation focus

Demo Video 1 shows CADia's broader creation, assembly, modification, and manufacturing-oriented workflow.

Demo Video 2 focuses specifically on direct geometric selection and continued editing of the same evolving B-Rep model.
