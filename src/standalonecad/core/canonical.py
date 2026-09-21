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
    """Thin, explicit boundary around the existing hardened CadEngine implementation.

    v9.3 already had the mature OCCT feature/history implementation.  v10 does not
    fork or duplicate it; it makes it the single canonical backend and moves upstream
    Inventor semantics into an adapter in front of this boundary.
    """

    def __init__(self, engine):
        self.engine = engine

    def dispatch(self, op: CanonicalOperation, *, read_only: bool = False):
        return self.engine._execute_impl(op.name, dict(op.params), read_only=read_only)
