# Demo 2 — Topology-Aware Parametric Editing

**Video:** https://youtu.be/bsyfQU5MiZ4

Demo Video 2 shows CADia continuously editing the same real B-Rep motor-mounting bracket through natural-language instructions and direct geometric selection.

The demo is intentionally organized as consecutive requests on one evolving model. It starts with bracket creation, adds new geometry while preserving the existing design, then uses selected B-Rep faces and edges to modify the original feature history and apply local edge operations.

## Prompt sequence used in Demo Video 2

### 1. Create the L-shaped motor mounting bracket

```text
Create a single-solid L-shaped motor mounting bracket with a 100 × 70 × 8 mm horizontal base plate and a 70 × 60 × 8 mm vertical plate. Position the vertical plate perpendicular to the base at 90 degrees and join the two plates as one continuous solid body. Add four Ø8 mm through mounting holes to the horizontal base, arranged symmetrically in a rectangular pattern.
```

This step demonstrates natural-language construction of a single-solid mechanical B-Rep part with explicit dimensions and four mounting holes.

### 2. Add the center hole while preserving the existing bracket

```text
Add a Ø40 mm through hole at the center of the vertical plate. The hole axis must be perpendicular to the vertical plate and the hole must pass completely through the plate. Preserve the existing bracket and mounting holes.
```

This step demonstrates continued modeling from the existing CAD state rather than regenerating the part from scratch.

### 3. Add two reinforcing gussets

```text
Add two identical triangular reinforcing gussets directly inside the 90-degree corner where the vertical plate meets the top surface of the base. Each gusset must have a 25 mm horizontal leg along the base, a 25 mm vertical leg along the vertical plate, and a thickness of 6 mm. Position the two gussets symmetrically near the left and right sides of the bracket. Each gusset must physically contact both the base and the vertical plate and be Boolean-unioned with the existing bracket as one continuous solid body.
```

This step demonstrates addition of new geometry to the same model while preserving the bracket and previously created holes.

### 4. Modify the existing center-hole feature from a selected cylindrical face

Select the cylindrical face of the center hole in the viewport, then run:

```text
Change the diameter of the hole associated with the selected cylindrical face from 40 mm to 50 mm. Modify the existing hole feature and preserve all other geometry.
```

This step demonstrates topology-aware, history-aware editing: the selected cylindrical B-Rep face provides the geometric target for modifying the existing hole rather than creating a disconnected replacement.

### 5. Modify the vertical-plate thickness from a selected face

Select the relevant face of the vertical plate in the viewport, then run:

```text
Change the thickness of the vertical plate associated with the selected face from 8 mm to 12 mm. Preserve the base plate, holes, gussets, and all other existing features.
```

This step demonstrates selected-face parameter editing and regeneration while preserving downstream geometry.

### 6. Apply a fillet only to selected edges

Select the target edges in the viewport, then run:

```text
Apply a 5 mm radius fillet only to the selected edges. Preserve all other geometry and dimensions of the existing bracket.
```

This step demonstrates direct edge-targeted B-Rep modification using explicit viewport selection.

### 7. Apply a chamfer only to selected edges

Select the target edges in the viewport, then run:

```text
Apply a 3 mm chamfer only to the selected edges.
```

This step demonstrates another local edge operation on the same evolving CAD model without replacing the rest of the design.

## What to look for

- Incremental construction of one L-shaped motor mounting bracket.
- Persistent CAD project state across multiple natural-language instructions.
- Preservation of previously created geometry during follow-up operations.
- A central through-hole changing from Ø40 mm to Ø50 mm by editing the existing feature.
- Vertical-plate thickness changing from 8 mm to 12 mm through selected-face editing.
- Two Boolean-unioned reinforcing gussets remaining part of the same solid.
- Direct B-Rep face selection for feature/parameter modification.
- Direct edge selection for 5 mm fillet and 3 mm chamfer operations.
- B-Rep regeneration and verification on the same evolving model.

## Editing workflow

CADia combines the current project state, feature history, B-Rep topology, geometric selection context, structured CAD operations, and verification.

For an unambiguous history-aware edit, selected B-Rep geometry can be resolved back to the relevant feature or driving parameter. CADia can update that source and rebuild dependent geometry.

For local edge operations such as fillet and chamfer, the selected B-Rep edges become explicit geometric inputs to the CAD operation.

The selected geometry therefore provides precise CAD context without requiring the user to describe the entire model again.

## Relationship to Demo Video 1

Demo Video 1 focuses on CAD creation, separate-part assembly, follow-up modification, and broader manufacturing-oriented workflow.

Demo Video 2 focuses specifically on continuous topology-aware editing of one evolving B-Rep model through selected faces and edges.
