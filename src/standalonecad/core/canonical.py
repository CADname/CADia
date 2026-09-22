from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class CanonicalOperation:
    """One normalized StandaloneCAD operation.

    External contracts (the upstream ipt-mcp wire protocol, the native cad_* tools,
    and future supported Inventor-modeling adapters) all terminate here.  Keeping one
    execution core prevents two compatibility paths from silently producing different
    geometry for the same operation.
    """

    name: str
    params: dict[str, Any]
    source: str = "native"


class CanonicalCadCore:
    """Single canonical execution boundary for the CADia CadEngine.

    Modeling adapters normalize requests before dispatch so every supported tool
    surface reaches the same OCCT/CadQuery feature and history implementation.
    """

    def __init__(self, engine):
        self.engine = engine

    def dispatch(self, op: CanonicalOperation, *, read_only: bool = False):
        return self.engine._execute_impl(op.name, dict(op.params), read_only=read_only)
