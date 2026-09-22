# CADia Demo Script

A concise run of show for the primary InfinityX demo video. Target: about 3 minutes.

For the second demo focused on topology-aware parametric editing and direct face/edge selection, see [`DEMO_02_TOPOLOGY_EDITING.md`](./DEMO_02_TOPOLOGY_EDITING.md).

## 0:00–0:20 — Problem

Traditional CAD requires users to translate design intent into many commands, constraints and feature operations. CADia lets the user describe the model, select geometry directly, and continue editing the same real CAD state.

## 0:20–0:40 — Product entry

1. Open `https://app.cadia.co.kr`.
2. Click **Launch CADia**.
3. Show the browser CAD workspace, feature tree and Face / Edge / Object selection controls.

## 0:40–1:25 — Create real CAD

Connect a supported AI provider and enter:

```text
Create an 80 x 60 x 8 mm mounting plate with four 6 mm holes positioned 10 mm from each corner.
```

Show the resulting B-Rep model and feature state.

## 1:25–2:00 — Continue editing

Without creating a new project, enter:

```text
Change the plate thickness to 12 mm and the four holes to 8 mm diameter while preserving their offsets.
```

Show that the existing model changes and remains selectable/editable.

## 2:00–2:25 — Direct geometric interaction

Select a face or edge in the browser. Briefly show that CADia can pass the selected CAD entity back into the editing workflow instead of relying on text alone.

## 2:25–2:45 — Engineering handoff

Show STEP AP242 / STL / 3MF export and the 3D-print DFM / PrusaSlicer G-code path.

## 2:45–3:00 — Evidence and close

Show the repository `evidence/` directory with saved examples such as gears, bracket, laptop assembly, clock assembly and tumbler assembly. Close on the core idea: natural language controls an editable B-Rep CAD system, not a disposable mesh generator.
