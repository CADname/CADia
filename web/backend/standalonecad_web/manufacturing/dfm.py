from __future__ import annotations

from math import cos, radians

from .mesh_ops import edge_use_counts, triangle_area, triangle_mesh_from_web_mesh, triangle_normal


def analyze_print_dfm(
    web_mesh: dict,
    *,
    build_volume_mm: tuple[float, float, float] = (256.0, 256.0, 256.0),
    overhang_angle_deg: float = 45.0,
) -> dict:
    """Fast, deterministic mesh pre-check for additive manufacturing.

    This intentionally does not claim slicer equivalence: wall thickness,
    extrusion feasibility, supports and process-time estimation belong to the
    configured slicer/profile and are reported as limitations.
    """
    tri_mesh = triangle_mesh_from_web_mesh(web_mesh)
    bounds = web_mesh.get("bounds")
    if not bounds or not tri_mesh.triangles:
        return {
            "ok": False,
            "scope": "mesh_precheck",
            "checks": [{"id": "mesh", "status": "fail", "message": "No 3D mesh is available for inspection."}],
            "issues": [{"severity": "error", "code": "NO_MESH", "message": "No 3D mesh is available for inspection."}],
            "metrics": {},
            "limitations": ["Final printability must be verified with the selected slicer and printer profile."],
        }

    mins, maxs = bounds["min"], bounds["max"]
    size = [max(0.0, float(maxs[i]) - float(mins[i])) for i in range(3)]
    bed = [float(v) for v in build_volume_mm]
    edge_counts = edge_use_counts(tri_mesh.triangles)
    boundary_edges = sum(1 for count in edge_counts.values() if count == 1)
    nonmanifold_edges = sum(1 for count in edge_counts.values() if count > 2)

    checks: list[dict] = []
    issues: list[dict] = []

    if boundary_edges:
        message = f"{boundary_edges} open boundary edges were detected."
        checks.append({"id": "watertight", "status": "fail", "message": message, "value": boundary_edges})
        issues.append({"severity": "error", "code": "OPEN_MESH", "message": message})
    else:
        checks.append({"id": "watertight", "status": "pass", "message": "No open boundaries were detected; the mesh appears watertight."})

    if nonmanifold_edges:
        message = f"{nonmanifold_edges} non-manifold edges were detected."
        checks.append({"id": "manifold", "status": "fail", "message": message, "value": nonmanifold_edges})
        issues.append({"severity": "error", "code": "NON_MANIFOLD", "message": message})
    else:
        checks.append({"id": "manifold", "status": "pass", "message": "No non-manifold edges were detected."})

    exceeds = any(size[i] > bed[i] + 1e-6 for i in range(3))
    if exceeds:
        message = f"Model size {size[0]:.1f}×{size[1]:.1f}×{size[2]:.1f} mm exceeds the build volume {bed[0]:.1f}×{bed[1]:.1f}×{bed[2]:.1f} mm."
        checks.append({"id": "build_volume", "status": "fail", "message": message})
        issues.append({"severity": "error", "code": "BUILD_VOLUME", "message": message})
    else:
        checks.append({"id": "build_volume", "status": "pass", "message": f"The model fits within the {bed[0]:.0f}×{bed[1]:.0f}×{bed[2]:.0f} mm build volume."})

    # Build direction is +Z. A sufficiently downward-facing triangle is only a
    # support-risk candidate; the actual support decision belongs to a slicer.
    threshold = cos(radians(90.0 - max(0.0, min(89.0, overhang_angle_deg))))
    overhang_area = 0.0
    total_area = 0.0
    for tri in tri_mesh.triangles:
        area = triangle_area(tri_mesh.vertices, tri)
        total_area += area
        if triangle_normal(tri_mesh.vertices, tri)[2] < -threshold:
            overhang_area += area
    ratio = (overhang_area / total_area) if total_area > 1e-12 else 0.0
    if ratio > 0.02:
        message = f"Downward-facing overhang candidates account for about {ratio * 100:.1f}% of the triangle area. Confirm support requirements in the slicer."
        checks.append({"id": "overhang", "status": "warning", "message": message, "value": ratio})
        issues.append({"severity": "warning", "code": "OVERHANG_RISK", "message": message})
    else:
        checks.append({"id": "overhang", "status": "pass", "message": "No significant downward-facing overhang area was detected."})

    min_extent = min(size)
    if min_extent < 0.8:
        message = f"The smallest overall model extent is {min_extent:.2f} mm. Compare it with nozzle and process limits."
        checks.append({"id": "global_extent", "status": "warning", "message": message, "value": min_extent})
        issues.append({"severity": "warning", "code": "THIN_GLOBAL_EXTENT", "message": message})
    else:
        checks.append({"id": "global_extent", "status": "pass", "message": f"The smallest overall model extent is {min_extent:.2f} mm."})

    return {
        "ok": not any(row["status"] == "fail" for row in checks),
        "scope": "mesh_precheck",
        "checks": checks,
        "issues": issues,
        "metrics": {
            "size_mm": {"x": size[0], "y": size[1], "z": size[2]},
            "build_volume_mm": {"x": bed[0], "y": bed[1], "z": bed[2]},
            "vertices": len(tri_mesh.vertices),
            "triangles": len(tri_mesh.triangles),
            "boundary_edges": boundary_edges,
            "nonmanifold_edges": nonmanifold_edges,
            "overhang_candidate_area_mm2": overhang_area,
            "overhang_candidate_ratio": ratio,
        },
        "limitations": [
            "Local wall thickness and minimum feature size are not yet evaluated with B-Rep thickness analysis.",
            "Supports, extrusion feasibility, print time, and material usage must be confirmed by the actual slicer and selected profile.",
        ],
    }
