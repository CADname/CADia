# InfinityX Submission Scope

This repository contains the CADia build submitted for **InfinityX Global Hackathon 2K26**.

## Submission highlights

CADia is an AI-native CAD system for creating, editing, and continuously modifying real B-Rep models through natural language and direct geometric selection. The submitted build brings the CAD engine, browser workspace, AI integration, topology-aware editing, manufacturing/export workflow, and saved modeling evidence together in the deployed CADia experience.

## Web experience

- Browser-based CAD workspace with one-click guest access
- English user interface and user-facing runtime messages
- Isolated project/user state for evaluation
- Live AI connection paths for supported providers

## AI + CAD workflow

- Full-request AI planning against the current CAD state and selection context
- Typed project-scoped CAD tool execution
- Plan/schema validation before CAD execution
- Deterministic CAD geometry execution through OCCT/CadQuery
- Verification, transaction rollback and bounded recovery

## Editable CAD capabilities

- Real B-Rep geometry rather than mesh-only generation
- Parametric feature history and driving parameters for native parametric models
- Direct face/edge/object selection from the browser
- Persistent topology descriptors and topology rebinding after model changes
- History-aware parameter/feature edits with downstream rebuilds when an edit is unambiguous
- Direct B-Rep editing paths when a history-based edit is not appropriate
- Continuous follow-up modification of the same model
- Assemblies, constraints, joints and standard-component generators
- 118 project-scoped CAD operations exposed through the AI integration layer

## Downstream handoff

- STEP AP242 export
- STL and 3MF export
- 3D-print DFM checks
- PrusaSlicer-based slicing and G-code generation

## Evaluation material

- Live product: `https://app.cadia.co.kr`
- Demo Video 1: `https://youtu.be/L2ocXoW0v_4`
- Demo Video 2 — topology-aware parametric editing: `https://youtu.be/bsyfQU5MiZ4`
- Judge path: `docs/JUDGE_GUIDE.md`
- Architecture: `docs/ARCHITECTURE.md`
- Demo 2 topology-aware editing workflow: `docs/DEMO_02_TOPOLOGY_EDITING.md`
- Saved modeling evidence: `evidence/`
- Devpost copy: `DEVPOST_SUBMISSION.md`
