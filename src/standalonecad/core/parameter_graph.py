from __future__ import annotations

"""Conservative parameter dependency analysis.

This module never changes CAD geometry.  It is used only after the established full
rebuild path has already failed, to determine whether a safe suffix rebuild can reuse
an unaffected prefix from the pre-change feature cache.
"""

import re
from typing import Any

_IDENT = re.compile(r"\b[A-Za-z_][A-Za-z0-9_]*\b")


def names_in_value(value: Any, known: set[str]) -> set[str]:
    out: set[str] = set()
    if isinstance(value, str):
        out.update(x for x in _IDENT.findall(value) if x in known)
    elif isinstance(value, dict):
        for v in value.values():
            out.update(names_in_value(v, known))
    elif isinstance(value, (list, tuple)):
        for v in value:
            out.update(names_in_value(v, known))
    return out


def parameter_dependencies(parameters: dict) -> dict[str, set[str]]:
    known=set(parameters)
    graph={name:set() for name in known}
    for name,p in parameters.items():
        graph[name]=names_in_value(getattr(p,'expression',''),known)-{name}
    return graph


def dependent_closure(parameters: dict, changed: str) -> set[str]:
    """Return changed parameter plus every parameter that transitively depends on it."""
    graph=parameter_dependencies(parameters)
    affected={changed}
    grew=True
    while grew:
        grew=False
        for name,deps in graph.items():
            if name not in affected and deps & affected:
                affected.add(name); grew=True
    return affected


def _sketch_refs(doc, sketch_name: str, known: set[str]) -> set[str]:
    sm=doc.sketches.get(sketch_name)
    if sm is None:return set()
    out=set()
    for ent in sm.entities:out.update(names_in_value(ent.data,known))
    for c in sm.constraints:out.update(names_in_value(c,known))
    for dim in sm.dimensions:out.update(names_in_value(dim,known))
    return out


def feature_parameter_refs(doc, feature) -> set[str]:
    known=set(doc.parameters); refs=names_in_value(feature.params,known)
    p=feature.params
    for key in ('sketch_name','profile_sketch_name'):
        if isinstance(p.get(key),str):refs.update(_sketch_refs(doc,p[key],known))
    for name in p.get('sketch_names') or []:
        if isinstance(name,str):refs.update(_sketch_refs(doc,name,known))
    return refs


def earliest_affected_feature(doc, changed: str) -> int | None:
    """Find the earliest feature that can depend on ``changed``.

    ``None`` means no recorded feature refers to the changed parameter (directly or
    through another user parameter).  Returning 0 is conservative and disables prefix
    reuse.
    """
    if changed not in doc.parameters:return 0
    affected=dependent_closure(doc.parameters,changed)
    for i,f in enumerate(doc.features):
        if feature_parameter_refs(doc,f) & affected:return i
    return None
