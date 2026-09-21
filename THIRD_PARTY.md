# Third-party components

## bimwright/ipt-mcp

- Project: `bimwright/ipt-mcp`
- Compatibility target in this package: public v0.1.0 contract/runtime checks
- License: Apache License 2.0
- The upstream project is an MCP gateway that calls the Autodesk Inventor API; it does not redistribute Inventor binaries or the Inventor SDK.
- CADia keeps the 58-tool MCP-facing compatibility surface as a reference contract and runs its own OCCT backend.
- License copy: `vendor/bimwright-ipt-mcp/LICENSE`
- Integration map: `vendor/bimwright-ipt-mcp/SOURCE_PORT_MAP.md`

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
- Integration: optional runtime dependency only; this release does not vendor or modify its source tree. CADia calls it through `core/mechanical_accelerator.py` for selected advanced gear families. The existing native spur-gear implementation remains the default stable spur path.
