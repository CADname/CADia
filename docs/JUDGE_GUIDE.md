# Judge Guide

CADia is submitted to **InfinityX Global Hackathon 2K26**. This guide is the shortest path to evaluating the working product and the supporting repository evidence.

## Demo videos

- **Demo Video 1 — creation, assembly, and manufacturing handoff:** https://youtu.be/L2ocXoW0v_4  
  Walkthrough: [`DEMO_01_CREATION_ASSEMBLY_MANUFACTURING.md`](./DEMO_01_CREATION_ASSEMBLY_MANUFACTURING.md)
- **Demo Video 2 — topology-aware parametric editing:** https://youtu.be/bsyfQU5MiZ4  
  Walkthrough: [`DEMO_02_TOPOLOGY_EDITING.md`](./DEMO_02_TOPOLOGY_EDITING.md)

Demo Video 1 shows the broader browser-based CAD workflow: creating a bevel gear, adding a matching second gear as a separate part, assembling the gears at a 90-degree shaft angle, adding 10 mm through bores while preserving the existing gear geometry and assembly, and then creating a product-style computer mouse model.

Demo Video 2 focuses on continued editing of the same B-Rep model. A mechanical mounting bracket is built incrementally and then modified through direct face and edge selection.

The second demo shows a center-hole diameter change from Ø40 mm to Ø50 mm, vertical-plate thickness modification from 8 mm to 12 mm, a selected-edge 5 mm fillet, a selected-edge 3 mm chamfer, B-Rep regeneration, and verification.

## 1. Open the live product

1. Open https://app.cadia.co.kr.
2. Click **Launch CADia**.
3. The guest CAD workspace opens without CADia registration.
4. Inspect the feature tree, Face / Edge / Object selection modes, and export controls.

## 2. Run a representative CAD workflow

If you want to test live AI modeling, click **Connect AI** and connect a supported provider. Then create a model:

```text
Create an 80 x 60 x 8 mm mounting plate with four 6 mm holes positioned 10 mm from each corner.
```

Continue on the same model:

```text
Change the plate thickness to 12 mm and the four holes to 8 mm diameter while preserving their offsets.
```

Inspect that the second request edits the existing CAD state rather than starting a new one. Faces and edges remain selectable, and the model can be exported through STEP.

## 3. Inspect saved modeling evidence

Open [`../evidence/README.md`](../evidence/README.md). It indexes 20 saved modeling examples with the exact prompt and available preview / STEP AP242 / STEP / STL / 3MF / 3D-print G-code artifacts.

Suggested examples:

- `01-spur-gear` — parametric mechanical component
- `03-spur-gear-assembly` — multi-part gear assembly
- `10-u-shaped-bracket` — compound bracket geometry
- `23-laptop-assembly` — larger assembly
- `24-clock-assembly` — multi-component clock with hands and numerals
- `25-tumbler-assembly` — product-style assembly

## 4. Technical path

CADia separates language interpretation from geometry execution:

```text
Natural-language request + CAD state + direct selection
  -> AI planning / structured tool selection
  -> schema-validated CAD operation
  -> OCCT / CadQuery B-Rep kernel
  -> verification / rollback / recovery
  -> updated CAD state / topology / exports
```

For the component-level view, see [`ARCHITECTURE.md`](./ARCHITECTURE.md).

## 5. Where the InfinityX criteria are visible

- **Innovation & Creativity:** natural-language creation plus continued editing of real B-Rep CAD rather than one-shot mesh generation
- **Technical Implementation:** typed CAD operations, persistent topology, feature/history-aware edits, direct B-Rep editing, verification/recovery, assemblies and manufacturing export
- **Real-World Impact:** lower-friction CAD creation and modification for engineering workflows
- **User Experience & Design:** browser CAD workspace, direct geometric selection, guest access and follow-up editing
- **Scalability & Feasibility:** provider-independent CAD core, project-scoped execution and containerized deployment
- **Presentation & Demo:** live deployment, saved modeling evidence and demo-video workflow

## Access note

No separate CADia judge account is required for the guest workspace. Live AI generation uses the connected AI provider's authentication and usage limits.
