from __future__ import annotations

from pathlib import Path
from xml.sax.saxutils import escape, quoteattr
from zipfile import ZIP_DEFLATED, ZipFile

from .mesh_ops import TriangleMesh, triangle_mesh_from_web_mesh

CONTENT_TYPES = """<?xml version=\"1.0\" encoding=\"UTF-8\"?>
<Types xmlns=\"http://schemas.openxmlformats.org/package/2006/content-types\">
  <Default Extension=\"rels\" ContentType=\"application/vnd.openxmlformats-package.relationships+xml\"/>
  <Default Extension=\"model\" ContentType=\"application/vnd.ms-package.3dmanufacturing-3dmodel+xml\"/>
</Types>\n"""

RELS = """<?xml version=\"1.0\" encoding=\"UTF-8\"?>
<Relationships xmlns=\"http://schemas.openxmlformats.org/package/2006/relationships\">
  <Relationship Target=\"/3D/3dmodel.model\" Id=\"rel0\" Type=\"http://schemas.microsoft.com/3dmanufacturing/2013/01/3dmodel\"/>
</Relationships>\n"""


def _model_xml(mesh: TriangleMesh, title: str) -> str:
    verts = "\n".join(f'          <vertex x="{x:.9g}" y="{y:.9g}" z="{z:.9g}"/>' for x, y, z in mesh.vertices)
    tris = "\n".join(f'          <triangle v1="{a}" v2="{b}" v3="{c}"/>' for a, b, c in mesh.triangles)
    safe_title = escape(title or "CADia model")
    title_attr = quoteattr(title or "CADia model")
    return f"""<?xml version=\"1.0\" encoding=\"UTF-8\"?>
<model unit=\"millimeter\" xml:lang=\"en-US\" xmlns=\"http://schemas.microsoft.com/3dmanufacturing/core/2015/02\">
  <metadata name=\"Title\">{safe_title}</metadata>
  <metadata name=\"Application\">CADia</metadata>
  <resources>
    <object id=\"1\" type=\"model\" name={title_attr}>
      <mesh>
        <vertices>
{verts}
        </vertices>
        <triangles>
{tris}
        </triangles>
      </mesh>
    </object>
  </resources>
  <build>
    <item objectid=\"1\"/>
  </build>
</model>\n"""


def write_3mf(web_mesh: dict, path: Path, title: str = "CADia model") -> dict:
    mesh = triangle_mesh_from_web_mesh(web_mesh)
    if not mesh.vertices or not mesh.triangles:
        raise ValueError("No triangle mesh is available for 3MF export.")
    path.parent.mkdir(parents=True, exist_ok=True)
    with ZipFile(path, "w", compression=ZIP_DEFLATED, compresslevel=6) as archive:
        archive.writestr("[Content_Types].xml", CONTENT_TYPES)
        archive.writestr("_rels/.rels", RELS)
        archive.writestr("3D/3dmodel.model", _model_xml(mesh, title))
    return {"path": str(path), "vertices": len(mesh.vertices), "triangles": len(mesh.triangles), "format": "3MF Core"}


def validate_with_lib3mf(path: Path) -> dict:
    """Validate with the official 3MF Consortium library when installed.

    lib3mf is BSD licensed and therefore can be enabled in proprietary builds.
    The package is optional so CADia's base image stays small and deterministic.
    """
    try:
        import lib3mf  # type: ignore
        from lib3mf import get_wrapper  # type: ignore
    except Exception:
        return {"available": False, "validated": False}
    try:
        wrapper = get_wrapper()
        model = wrapper.CreateModel()
        reader = model.QueryReader("3mf")
        reader.ReadFromFile(str(path))
        return {"available": True, "validated": True, "library": getattr(lib3mf, "__version__", "lib3mf")}
    except Exception as exc:
        return {"available": True, "validated": False, "error": str(exc)}
