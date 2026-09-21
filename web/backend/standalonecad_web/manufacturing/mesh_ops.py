from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from math import sqrt
from typing import Iterable


@dataclass(frozen=True)
class TriangleMesh:
    vertices: list[tuple[float, float, float]]
    triangles: list[tuple[int, int, int]]


def triangle_mesh_from_web_mesh(mesh: dict, precision: int = 7) -> TriangleMesh:
    """Merge CADia's per-face tessellation into one indexed triangle mesh.

    Vertices are deduplicated by rounded coordinates so manifold-edge tests can
    work across B-Rep face boundaries. This is a manufacturing-analysis mesh,
    not a replacement for the exact OCCT B-Rep.
    """
    vertices: list[tuple[float, float, float]] = []
    triangles: list[tuple[int, int, int]] = []
    index_by_key: dict[tuple[float, float, float], int] = {}

    for face in mesh.get("faces") or []:
        pos = face.get("positions") or []
        idx = face.get("indices") or []
        local_to_global: dict[int, int] = {}
        for local in set(int(i) for i in idx):
            base = local * 3
            if base + 2 >= len(pos):
                continue
            xyz = (float(pos[base]), float(pos[base + 1]), float(pos[base + 2]))
            key = tuple(round(v, precision) for v in xyz)
            global_index = index_by_key.get(key)
            if global_index is None:
                global_index = len(vertices)
                index_by_key[key] = global_index
                vertices.append(xyz)
            local_to_global[local] = global_index
        for offset in range(0, len(idx) - 2, 3):
            a, b, c = int(idx[offset]), int(idx[offset + 1]), int(idx[offset + 2])
            if a in local_to_global and b in local_to_global and c in local_to_global:
                ga, gb, gc = local_to_global[a], local_to_global[b], local_to_global[c]
                if ga != gb and gb != gc and ga != gc:
                    triangles.append((ga, gb, gc))
    return TriangleMesh(vertices=vertices, triangles=triangles)


def triangle_normal(vertices: list[tuple[float, float, float]], tri: tuple[int, int, int]) -> tuple[float, float, float]:
    a, b, c = (vertices[i] for i in tri)
    ux, uy, uz = b[0] - a[0], b[1] - a[1], b[2] - a[2]
    vx, vy, vz = c[0] - a[0], c[1] - a[1], c[2] - a[2]
    nx, ny, nz = uy * vz - uz * vy, uz * vx - ux * vz, ux * vy - uy * vx
    length = sqrt(nx * nx + ny * ny + nz * nz)
    if length <= 1e-15:
        return (0.0, 0.0, 0.0)
    return (nx / length, ny / length, nz / length)


def triangle_area(vertices: list[tuple[float, float, float]], tri: tuple[int, int, int]) -> float:
    a, b, c = (vertices[i] for i in tri)
    ux, uy, uz = b[0] - a[0], b[1] - a[1], b[2] - a[2]
    vx, vy, vz = c[0] - a[0], c[1] - a[1], c[2] - a[2]
    nx, ny, nz = uy * vz - uz * vy, uz * vx - ux * vz, ux * vy - uy * vx
    return 0.5 * sqrt(nx * nx + ny * ny + nz * nz)


def edge_use_counts(triangles: Iterable[tuple[int, int, int]]) -> Counter[tuple[int, int]]:
    edges: Counter[tuple[int, int]] = Counter()
    for a, b, c in triangles:
        for x, y in ((a, b), (b, c), (c, a)):
            edges[(x, y) if x < y else (y, x)] += 1
    return edges
