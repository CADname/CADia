# Third-party components

## CadQuery / OCP / Open CASCADE

Used for B-Rep modeling, booleans, sweeps/lofts, tessellation and geometry export. Their respective upstream licenses apply.

## VTK

Used for interactive 3D visualization, face/edge picking and camera controls. Its upstream license applies.

## Python

CPython may be installed automatically on first run when no suitable 64-bit Python 3.11/3.12 runtime is present.

## Optional Design Accelerator backend: cq_gears

- Project: `meadiode/cq_gears`
- Upstream: https://github.com/meadiode/cq_gears
- License: Apache License 2.0
- Pinned revision used by `requirements_accelerator.txt`: `e73874cf17a25447a99b1e7c22a4d5af38560e9c`
- Integration: optional runtime dependency only; this release does not vendor or modify its source tree. CADia calls it through `core/mechanical_accelerator.py` for selected advanced gear families. CADia's native spur-gear implementation is the default stable spur path.
