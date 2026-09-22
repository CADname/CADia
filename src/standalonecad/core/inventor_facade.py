from __future__ import annotations

"""Inventor-style object façade over the CADia modeling backend.

This module provides an object model shaped like Inventor's ComponentDefinition /
Occurrences / ComponentOccurrence / geometry-proxy concepts while delegating real
modeling to the canonical CADia backend.
"""

from dataclasses import dataclass
import base64
import copy
import json
from pathlib import Path
from typing import Any, Iterator

import numpy as np

from .assembly import Occurrence, find_interface, transformed_frame, transform_shape
from .joints import _intent_local_frame, _transform_local_frame


def _encode_key(payload: dict[str, Any]) -> str:
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _decode_key(value: str) -> dict[str, Any]:
    s = str(value)
    s += "=" * ((4 - len(s) % 4) % 4)
    try:
        raw = base64.urlsafe_b64decode(s.encode("ascii"))
        obj = json.loads(raw.decode("utf-8"))
    except Exception as exc:
        raise ValueError("Invalid standalone reference key") from exc
    if not isinstance(obj, dict):
        raise ValueError("Invalid standalone reference key payload")
    return obj


@dataclass(frozen=True)
class GeometryProxy:
    """Assembly-context proxy for a component-local reference.

    ``entity`` is either a named work/origin/iMate reference string or a
    GeometryIntent-like dict already understood by the joint backend.
    """

    occurrence: "ComponentOccurrence"
    entity: str | dict[str, Any]

    @property
    def occurrence_name(self) -> str:
        return self.occurrence.name

    @property
    def native_object(self) -> str | dict[str, Any]:
        return copy.deepcopy(self.entity)

    @property
    def is_named_reference(self) -> bool:
        return isinstance(self.entity, str)

    @property
    def reference_name(self) -> str | None:
        return self.entity if isinstance(self.entity, str) else None

    @property
    def geometry_intent(self) -> dict[str, Any] | None:
        return None if isinstance(self.entity, str) else copy.deepcopy(self.entity)

    def world_frame(self) -> dict[str, Any]:
        occ = self.occurrence._native
        if isinstance(self.entity, str):
            item = find_interface(occ.interfaces, self.entity)
            p, d = transformed_frame(item.get("origin", [0, 0, 0]), item.get("direction"), occ)
            return {
                "origin": [float(x) for x in p],
                "direction": None if d is None else [float(x) for x in d],
                "x_direction": None,
            }
        p, z, x, _ = _intent_local_frame(occ, occ.interfaces, None, self.entity)
        p, z, x = _transform_local_frame(p, z, x, occ)
        return {
            "origin": [float(v) for v in p],
            "direction": None if z is None else [float(v) for v in z],
            "x_direction": None if x is None else [float(v) for v in x],
        }

    def reference_key(self) -> str:
        return self.occurrence.parent.reference_manager.get_reference_key(self)


class ComponentOccurrence:
    """Inventor-like wrapper around one native assembly Occurrence."""

    def __init__(self, parent: "AssemblyComponentDefinition", native: Occurrence, creation_result: dict[str, Any] | None = None):
        self.parent = parent
        self._native = native
        self._creation_result = copy.deepcopy(creation_result) if isinstance(creation_result, dict) else None

    @property
    def name(self) -> str:
        return self._native.name

    @property
    def path(self) -> str:
        return self._native.path

    @property
    def grounded(self) -> bool:
        return bool(self._native.grounded)

    @grounded.setter
    def grounded(self, value: bool) -> None:
        self._native.grounded = bool(value)
        self.parent.document.rebuild_assembly()

    @property
    def suppressed(self) -> bool:
        return bool(self._native.suppressed)

    @suppressed.setter
    def suppressed(self, value: bool) -> None:
        self._native.suppressed = bool(value)
        self.parent.document.rebuild_assembly()

    @property
    def position_mm(self) -> list[float]:
        return list(self._native.position_mm)

    @property
    def rotation_deg_xyz(self) -> list[float]:
        return list(self._native.rotation_deg_xyz)

    @property
    def transformation(self) -> dict[str, list[float]]:
        return {"position_mm": self.position_mm, "rotation_deg_xyz": self.rotation_deg_xyz}

    @property
    def component_type(self) -> str:
        return str(getattr(self._native, "component_type", "part"))

    @property
    def native_object(self) -> Occurrence:
        return self._native

    def set_transform(self, position_mm: list[float] | None = None, rotation_deg_xyz: list[float] | None = None) -> "ComponentOccurrence":
        if position_mm is not None:
            if len(position_mm) != 3:
                raise ValueError("position_mm must contain 3 values")
            self._native.position_mm = [float(x) for x in position_mm]
        if rotation_deg_xyz is not None:
            if len(rotation_deg_xyz) != 3:
                raise ValueError("rotation_deg_xyz must contain 3 values")
            self._native.rotation_deg_xyz = [float(x) for x in rotation_deg_xyz]
        self.parent.document.rebuild_assembly()
        return self

    def create_geometry_proxy(self, geometry: str | dict[str, Any]) -> GeometryProxy:
        return self.parent.reference_manager.create_geometry_proxy(self, geometry)

    def world_shape(self):
        return transform_shape(self._native.shape, self._native.position_mm, self._native.rotation_deg_xyz)

    def reference_key(self) -> str:
        return self.parent.reference_manager.get_reference_key(self)

    def creation_result(self) -> dict[str, Any] | None:
        return copy.deepcopy(self._creation_result)

    def delete(self):
        """Delete this occurrence from the assembly, preserving its source document."""
        return self.parent.occurrences.delete(self.name)

    def replace(self, full_document_name: str, replace_all: bool = False):
        """Inventor ComponentOccurrence.Replace-style component replacement."""
        return self.parent.document.replace_occurrence(self.name, full_document_name, replace_all)

    def suppress(self):
        self.suppressed = True
        return self

    def unsuppress(self):
        self.suppressed = False
        return self


class ComponentOccurrences:
    """Collection wrapper matching Inventor's occurrence-centric access pattern."""

    def __init__(self, parent: "AssemblyComponentDefinition"):
        self.parent = parent

    def __len__(self) -> int:
        return len(self.parent.document.occurrences)

    def __iter__(self) -> Iterator[ComponentOccurrence]:
        for occ in self.parent.document.occurrences.values():
            yield ComponentOccurrence(self.parent, occ)

    def item(self, key: int | str) -> ComponentOccurrence:
        if isinstance(key, int):
            if key < 1:
                raise IndexError("Occurrence index is 1-based")
            try:
                native = list(self.parent.document.occurrences.values())[key - 1]
            except IndexError as exc:
                raise IndexError(f"Occurrence index out of range: {key}") from exc
            return ComponentOccurrence(self.parent, native)
        name = str(key)
        native = self.parent.document.occurrences.get(name)
        if native is None:
            raise KeyError(f"Occurrence not found: {name}")
        return ComponentOccurrence(self.parent, native)

    def __getitem__(self, key: int | str) -> ComponentOccurrence:
        return self.item(key)

    def add(self, full_document_name: str, position: dict[str, Any] | None = None, grounded: bool = False) -> ComponentOccurrence:
        pos = None; rot = None
        if position is not None:
            if not isinstance(position, dict):
                raise ValueError("position must be a dict containing position_mm and/or rotation_deg_xyz")
            pos = position.get("position_mm")
            rot = position.get("rotation_deg_xyz")
        result = self.parent.document.place_occurrence(full_document_name, grounded, pos, rot)
        native = self.parent.document.occurrences[result["occurrence_name"]]
        return ComponentOccurrence(self.parent, native, result)

    def names(self) -> list[str]:
        return list(self.parent.document.occurrences)

    def delete(self, name: str):
        return self.parent.document.delete_occurrence(str(name))


class AssemblyConstraints:
    """Object collection façade delegating to the existing constraint backend."""

    def __init__(self, parent: "AssemblyComponentDefinition"):
        self.parent = parent

    def __iter__(self):
        return iter(self.parent.document.constraints)

    def __len__(self) -> int:
        return len(self.parent.document.constraints)

    def item(self, key: int | str):
        if isinstance(key,int):
            if key < 1:raise IndexError('Constraint index is 1-based')
            try:return self.parent.document.constraints[key-1]
            except IndexError as exc:raise IndexError(f'Constraint index out of range: {key}') from exc
        name=str(key); found=next((x for x in self.parent.document.constraints if x.name==name),None)
        if found is None:raise KeyError(f'Constraint not found: {name}')
        return found

    def delete(self, name: str):
        return self.parent.document.delete_assembly_constraint(str(name))

    @staticmethod
    def _side(value: ComponentOccurrence | GeometryProxy | None, ref: str | None):
        if isinstance(value, GeometryProxy):
            if not value.is_named_reference:
                raise ValueError("Legacy AssemblyConstraint currently requires a named-reference GeometryProxy")
            return value.occurrence_name, value.reference_name
        if isinstance(value, ComponentOccurrence):
            if not ref:
                raise ValueError("A named reference is required with ComponentOccurrence")
            return value.name, ref
        return None, ref

    def add(self, type_: str, a: ComponentOccurrence | GeometryProxy | None, a_ref: str | None,
            b: ComponentOccurrence | GeometryProxy | None, b_ref: str | None,
            offset_mm: float = 0.0, angle_deg: float | None = None, insert_opposed: bool = True):
        a_occ, a_name = self._side(a, a_ref); b_occ, b_name = self._side(b, b_ref)
        if not a_name or not b_name:
            raise ValueError("AssemblyConstraint requires references on both sides")
        return self.parent.document.add_assembly_constraint(
            type_, a_occ, a_name, b_occ, b_name, offset_mm, angle_deg, insert_opposed
        )

    def add_mate(self, a, a_ref, b, b_ref, offset_mm: float = 0.0):
        return self.add("mate", a, a_ref, b, b_ref, offset_mm=offset_mm)

    def add_flush(self, a, a_ref, b, b_ref, offset_mm: float = 0.0):
        return self.add("flush", a, a_ref, b, b_ref, offset_mm=offset_mm)

    def add_insert(self, a, a_ref, b, b_ref, offset_mm: float = 0.0, opposed: bool = True):
        return self.add("insert", a, a_ref, b, b_ref, offset_mm=offset_mm, insert_opposed=opposed)

    def add_angle(self, a, a_ref, b, b_ref, angle_deg: float):
        return self.add("angle", a, a_ref, b, b_ref, angle_deg=angle_deg)


class AssemblyJoints:
    """Inventor-style joint collection over the existing generalized joint backend.

    This is deliberately a façade: all geometry/solver behavior remains in the
    existing CadDocument joint implementation so routing commands through this
    collection cannot create a second source of assembly semantics.
    """

    def __init__(self, parent: "AssemblyComponentDefinition"):
        self.parent = parent

    def __len__(self) -> int:
        return len(self.parent.document.joints)

    def __iter__(self):
        return iter(self.parent.document.joints)

    @staticmethod
    def _side(value: ComponentOccurrence | GeometryProxy | None, ref: str | None, intent: dict[str, Any] | None):
        if isinstance(value, GeometryProxy):
            return value.occurrence_name, value.reference_name, value.geometry_intent
        if isinstance(value, ComponentOccurrence):
            return value.name, ref, copy.deepcopy(intent) if isinstance(intent, dict) else None
        return None, ref, copy.deepcopy(intent) if isinstance(intent, dict) else None

    def add(self, type_: str,
            a: ComponentOccurrence | GeometryProxy | None, a_ref: str | None,
            b: ComponentOccurrence | GeometryProxy | None, b_ref: str | None,
            **kwargs):
        a_occ, ar, ai = self._side(a, a_ref, kwargs.pop("a_intent", None))
        b_occ, br, bi = self._side(b, b_ref, kwargs.pop("b_intent", None))
        linear_position_mm = kwargs.pop("linear_position_mm", None)
        linear_start_mm = kwargs.pop("linear_start_mm", None)
        linear_end_mm = kwargs.pop("linear_end_mm", None)
        angular_position_deg = kwargs.pop("angular_position_deg", None)
        angular_start_deg = kwargs.pop("angular_start_deg", None)
        angular_end_deg = kwargs.pop("angular_end_deg", None)
        name = kwargs.pop("name", None)
        flip_origin_direction = kwargs.pop("flip_origin_direction", False)
        flip_alignment_direction = kwargs.pop("flip_alignment_direction", False)
        if kwargs:
            raise ValueError(f"Unsupported joint option(s): {sorted(kwargs)}")
        return self.parent.document.add_assembly_joint(
            type_, a_occ, ar, b_occ, br,
            linear_position_mm, linear_start_mm, linear_end_mm,
            angular_position_deg, angular_start_deg, angular_end_deg,
            name, ai, bi, flip_origin_direction, flip_alignment_direction,
        )

    def edit(self, name: str, **updates):
        return self.parent.document.edit_assembly_joint(name, **updates)

    def set_limits(self, name: str, linear_start_mm=None, linear_end_mm=None,
                   angular_start_deg=None, angular_end_deg=None):
        return self.parent.document.set_joint_limits(
            name, linear_start_mm, linear_end_mm, angular_start_deg, angular_end_deg
        )

    def drive(self, name: str, linear_position_mm=None, angular_position_deg=None):
        return self.parent.document.drive_joint(name, linear_position_mm, angular_position_deg)

    def list(self):
        return self.parent.document.list_joints()

    def delete(self, name: str):
        return self.parent.document.delete_joint(name)


class ReferenceManager:
    """Small persistent-reference façade inspired by Inventor ReferenceKeyManager.

    Keys are StandaloneCAD keys, not Autodesk byte-for-byte reference keys.  They are
    stable across save/reload as long as the occurrence name and referenced local
    geometry descriptor remain valid.  Actual topology rebinding is delegated to the
    existing persistent-topology/GeometryIntent backend.
    """

    VERSION = 1

    def __init__(self, document):
        self.document = document

    def create_geometry_proxy(self, occurrence: ComponentOccurrence | str, geometry: str | dict[str, Any]) -> GeometryProxy:
        if isinstance(occurrence, str):
            occurrence = self.document.component_definition.occurrences.item(occurrence)
        if not isinstance(occurrence, ComponentOccurrence):
            raise TypeError("occurrence must be ComponentOccurrence or occurrence name")
        if occurrence.parent.document is not self.document:
            raise ValueError("Occurrence belongs to a different document")
        if not isinstance(geometry, (str, dict)):
            raise TypeError("geometry must be a named reference string or GeometryIntent dict")
        proxy = GeometryProxy(occurrence, copy.deepcopy(geometry))
        # Resolve once now so invalid references fail at object creation instead of much later.
        proxy.world_frame()
        return proxy

    def get_reference_key(self, obj: ComponentOccurrence | GeometryProxy) -> str:
        if isinstance(obj, ComponentOccurrence):
            payload = {
                "v": self.VERSION,
                "kind": "occurrence",
                "name": obj.name,
                "path": str(Path(obj.path).expanduser().resolve()),
            }
            return _encode_key(payload)
        if isinstance(obj, GeometryProxy):
            payload = {
                "v": self.VERSION,
                "kind": "geometry_proxy",
                "occurrence": obj.occurrence.name,
                "path": str(Path(obj.occurrence.path).expanduser().resolve()),
                "entity": copy.deepcopy(obj.entity),
            }
            return _encode_key(payload)
        raise TypeError("ReferenceManager supports ComponentOccurrence and GeometryProxy")

    def can_bind_key_to_object(self, key: str) -> bool:
        try:
            self.bind_key_to_object(key)
            return True
        except Exception:
            return False

    def bind_key_to_object(self, key: str) -> ComponentOccurrence | GeometryProxy:
        payload = _decode_key(key)
        if int(payload.get("v", 0)) != self.VERSION:
            raise ValueError("Unsupported standalone reference-key version")
        kind = payload.get("kind")
        comp = self.document.component_definition
        if not isinstance(comp, AssemblyComponentDefinition):
            raise ValueError("Occurrence/proxy reference keys require an assembly document")
        name = str(payload.get("name") if kind == "occurrence" else payload.get("occurrence"))
        occ = comp.occurrences.item(name)
        expected = str(payload.get("path") or "")
        if expected and str(Path(occ.path).expanduser().resolve()) != expected:
            raise ValueError(f"Occurrence path mismatch while binding key: {name}")
        if kind == "occurrence":
            return occ
        if kind == "geometry_proxy":
            return self.create_geometry_proxy(occ, payload.get("entity"))
        raise ValueError(f"Unsupported reference-key kind: {kind}")


class ComponentDefinition:
    def __init__(self, document):
        self.document = document

    @property
    def reference_manager(self) -> ReferenceManager:
        return self.document.reference_manager

    @property
    def type(self) -> str:
        return self.document.doc_type


class PartComponentDefinition(ComponentDefinition):
    @property
    def features(self):
        return self.document.features

    @property
    def sketches(self):
        return self.document.sketches

    @property
    def parameters(self):
        return self.document.parameters

    @property
    def work_planes(self):
        return self.document.work_planes

    @property
    def work_axes(self):
        return self.document.work_axes

    @property
    def work_points(self):
        return getattr(self.document,'work_points',{})

    @property
    def sketches3d(self):
        return getattr(self.document,'sketches3d',{})

    @property
    def surface_bodies(self):
        return [] if self.document.shape is None else [self.document.shape]


class AssemblyComponentDefinition(ComponentDefinition):
    def __init__(self, document):
        super().__init__(document)
        self._occurrences = ComponentOccurrences(self)
        self._constraints = AssemblyConstraints(self)
        self._joints = AssemblyJoints(self)

    @property
    def occurrences(self) -> ComponentOccurrences:
        return self._occurrences

    @property
    def constraints(self) -> AssemblyConstraints:
        return self._constraints

    @property
    def joints(self) -> AssemblyJoints:
        return self._joints

    @property
    def bom(self) -> dict[str, Any]:
        return self.document.assembly_bom()

    def list_interfaces(self, occurrence: str | None = None):
        return self.document.list_interfaces(occurrence)

    def check_interference(self, occurrences=None):
        return self.document.check_interference(occurrences)

    def measure_min_distance(self, a_occurrence: str, a_ref: str | None,
                             b_occurrence: str, b_ref: str | None):
        return self.document.measure_min_distance(a_occurrence, a_ref, b_occurrence, b_ref)

    def get_bom(self, max_rows: int = 500):
        return self.document.assembly_bom(int(max_rows))

    def list_constraints(self):
        return {'constraints': [vars(c) for c in self.document.constraints]}


def component_definition_for(document) -> ComponentDefinition:
    if document.doc_type == "assembly":
        return AssemblyComponentDefinition(document)
    return PartComponentDefinition(document)
