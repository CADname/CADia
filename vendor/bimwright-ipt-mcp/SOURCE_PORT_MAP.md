# bimwright/ipt-mcp -> CADia source/semantic integration map

StandaloneCAD does not bundle Autodesk Inventor or claim Autodesk source/kernel ownership. The public ipt-mcp compatibility surface is treated as an external contract; the CAD implementation is independent OCCT/CadQuery code.

## Surface partition

- pinned default public surface: 58 `inventor_*` tools
- server-local: 3 target/meta + 6 ToolBaker = 9
- CAD-host roundtrip: 49 commands

## CADia execution

`original/public ipt-mcp contract -> StandaloneCAD host -> InventorSemanticAdapter -> CanonicalCadCore -> OCCT/CadQuery`

The semantic adapter owns upstream input restrictions/defaults and high-value response parity. The canonical core owns actual document/history/B-Rep operations. Native `cad_*` extensions enter the same canonical core directly.

## Intentionally independent

- OpenCASCADE replaces Autodesk ShapeManager.
- Standalone history/topology/constraint implementations replace proprietary Inventor internals.
- arbitrary `Inventor.Application` C# is not emulated; standalone optional code mode remains a restricted command DSL.
