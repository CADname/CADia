# Demo 1 — Creation, Assembly, and Manufacturing Handoff

Demo Video 1 shows CADia's broader browser-based CAD workflow: creating a mechanical part, adding a second part, assembling the parts at a defined relationship, applying follow-up modifications while preserving the existing model, and then creating a separate product-style model in the same CAD workspace.

The demo is intentionally organized as consecutive natural-language requests so judges can see that CADia is not only producing one isolated shape. It can continue from existing CAD state, add parts, modify previously created geometry, and support broader CAD creation workflows in the browser.

## Prompt sequence used in Demo Video 1

### 1. Create the first bevel gear

```text
Create a straight bevel gear with module 2, 20 teeth, a pressure angle of 20 degrees, and a face width of 15 mm.
```

This step demonstrates natural-language creation of a mechanical B-Rep component with explicit gear parameters.

### 2. Add and assemble the matching gear

```text
Create a second matching straight bevel gear as a separate part and assemble it with the existing gear at a 90-degree shaft angle so that the two gears mesh correctly.
```

This step demonstrates continuation from the existing model state, creation of a separate matching part, and assembly-oriented placement at a 90-degree shaft relationship.

### 3. Modify both gears while preserving the assembly

```text
Add a 10 mm through bore at the center of each bevel gear while keeping the existing gear geometry and assembly unchanged.
```

This step demonstrates follow-up modification of existing CAD geometry. The requested bore operation is applied to both bevel gears while the previously created gear geometry and assembly relationship remain the active design context.

### 4. Create a product-style model

```text
Model a computer mouse.
```

This step demonstrates broader natural-language CAD creation beyond standard mechanical components, showing that the same workspace can handle product-style modeling requests as well as gear-oriented workflows.

## What to look for

- Browser-based CAD workflow without requiring a local desktop CAD installation.
- Natural-language creation of a parameterized mechanical part.
- Continued modeling from existing CAD state instead of one-shot regeneration.
- Separate part creation and assembly-oriented placement.
- Follow-up modification while preserving existing geometry and assembly context.
- Broader product-style modeling in the same CADia workspace.
- Downstream handoff through CAD/manufacturing export workflows shown in the broader project materials.

## Relationship to Demo Video 2

Demo Video 1 focuses on the broader creation, assembly, modification, and manufacturing-oriented workflow.

Demo Video 2 focuses more narrowly on CADia's topology-aware editing workflow: direct B-Rep face and edge selection, history-aware parameter modification, selected-edge fillet and chamfer operations, regeneration, and verification on the same evolving CAD model.
