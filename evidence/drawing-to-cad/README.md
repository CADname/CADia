# CADia — Drawing-to-CAD Evidence

This directory contains selected examples from CADia's experimental engineering-drawing-to-CAD workflow. Each case pairs a source engineering drawing with the reconstructed CAD result.

**Try it:** Upload an engineering drawing at https://cadia.co.kr.

## Goal

CAD and mechanical-engineering workflows can be difficult to enter because they traditionally require specialized knowledge, CAD command experience, and desktop software. CADia's long-term goal is to lower that barrier so people without a mechanical-engineering background can approach 3D modeling and CAD more easily.

The broader direction is to make engineering modeling accessible from a browser: users should be able to start from natural language or a drawing, obtain an editable CAD model, and continue inspecting and revising it from anywhere without being tied to one workstation. Drawing-to-CAD is one part of that goal.

## Current status

Drawing-to-CAD is currently experimental. Clear, well-defined engineering drawings produce the most reliable results.

In a small internal evaluation using selected engineering drawings, approximately **50% of the tested cases produced end-to-end reconstructions that were considered sufficiently faithful to the source drawing**.

This is an internal development measurement, not a standardized benchmark. Performance varies substantially with drawing complexity and clarity.

More difficult or ambiguous drawings can still result in:

- missing or incomplete features
- incorrect dimensions or proportions
- incomplete internal geometry
- disconnected intermediate solids
- incorrect interpretation of ambiguous views, sections, or hidden geometry

## Examples

### 01 — Pulley

| Input drawing | CADia result |
| --- | --- |
| <img src="./01-pulley/input-drawing.jpg" width="180" alt="Pulley input drawing"> | <img src="./01-pulley/result.png" width="180" alt="Pulley CADia result"> |

### 02 — Piston

| Input drawing | CADia result |
| --- | --- |
| <img src="./02-piston/input-drawing.jpg" width="180" alt="Piston input drawing"> | <img src="./02-piston/result.png" width="180" alt="Piston CADia result"> |

### 03 — Flange / hub

| Input drawing | CADia result |
| --- | --- |
| <img src="./03-flange-hub/input-drawing.jpg" width="180" alt="Flange hub input drawing"> | <img src="./03-flange-hub/result.png" width="180" alt="Flange hub CADia result"> |

### 04 — Spur gear

| Input drawing | CADia result |
| --- | --- |
| <img src="./04-spur-gear/input-drawing.jpg" width="180" alt="Spur gear input drawing"> | <img src="./04-spur-gear/result.png" width="180" alt="Spur gear CADia result"> |
