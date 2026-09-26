from __future__ import annotations

import base64
import hashlib
import logging
import re
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..auth import CurrentUser
from ..cad_runtime import _safe_name
from ..database import get_db
from ..manufacturing import analyze_print_dfm, manufacturing_capabilities, write_3mf
from ..manufacturing.printers import PrinterError, configured_printers, upload_gcode
from ..manufacturing.slicer import SlicerError, configured_build_volume_mm, slice_stl
from ..manufacturing.step_export import write_step_ap242
from ..manufacturing.slant3d import Slant3DConfig, Slant3DError, call_slant_tool, list_materials
from .cad import runtime_for


router = APIRouter(prefix="/projects/{project_id}/manufacturing", tags=["manufacturing"])
logger = logging.getLogger(__name__)


class SliceRequest(BaseModel):
    overrides: dict[str, str] = Field(default_factory=dict)


class SliceUploadRequest(BaseModel):
    overrides: dict[str, str] = Field(default_factory=dict)
    start: bool = False


class SlantQuoteRequest(BaseModel):
    filament_id: str | None = Field(default=None, max_length=200)
    material: str | None = Field(default=None, max_length=80)
    color: str | None = Field(default=None, max_length=80)


class SlantQuantityRequest(BaseModel):
    quote_id: str = Field(min_length=1, max_length=200)
    quantity: int = Field(ge=1, le=100000)


class SlantAddress(BaseModel):
    # Keep request parsing permissive enough to return human-readable validation
    # errors from the route instead of FastAPI's nested 422 objects.
    name: str = Field(default="", max_length=160)
    email: str = Field(default="", max_length=320)
    line1: str = Field(default="", max_length=200)
    line2: str | None = Field(default=None, max_length=200)
    city: str = Field(default="", max_length=120)
    state: str | None = Field(default=None, max_length=120)
    postal_code: str = Field(default="", max_length=40)
    country: str = Field(default="", max_length=2)


class SlantShippingRequest(BaseModel):
    quote_id: str = Field(min_length=1, max_length=200)
    address: SlantAddress


class SlantCheckoutRequest(BaseModel):
    quote_id: str = Field(min_length=1, max_length=200)
    shipping_service: str = Field(min_length=1, max_length=200)
    email: str | None = Field(default=None, max_length=320)


def _clean_slant_address(address: SlantAddress) -> dict[str, str | None]:
    values: dict[str, str | None] = {
        "name": address.name.strip(),
        "email": address.email.strip(),
        "line1": address.line1.strip(),
        "line2": (address.line2 or "").strip() or None,
        "city": address.city.strip(),
        "state": (address.state or "").strip() or None,
        "postal_code": address.postal_code.strip(),
        "country": address.country.strip().upper(),
    }
    required = {
        "name": "recipient name",
        "email": "email address",
        "line1": "street address",
        "city": "city",
        "postal_code": "postal code",
        "country": "country",
    }
    missing = [label for key, label in required.items() if not values[key]]
    if missing:
        raise HTTPException(status_code=400, detail="Enter " + ", ".join(missing) + ".")
    email = str(values["email"])
    if not re.match(r"^[^\s@]+@[^\s@]+\.[^\s@]+$", email):
        raise HTTPException(status_code=400, detail="Enter a valid email address.")
    country = str(values["country"])
    if not re.match(r"^[A-Z]{2}$", country):
        raise HTTPException(status_code=400, detail="Country must be a 2-letter ISO code.")
    state = str(values["state"] or "")
    if len(state) < 2:
        raise HTTPException(status_code=400, detail="Enter a state, province, or region (at least 2 characters).")
    postal_code = str(values["postal_code"] or "")
    if len(postal_code) < 3:
        raise HTTPException(status_code=400, detail="Enter a valid postal code (at least 3 characters).")
    return values


def _provider_error(exc: Slant3DError, action: str) -> HTTPException:
    # Keep provider protocol/schema diagnostics in server logs, never in the end-user UI.
    logger.warning("Slant 3D %s failed: %s", action, exc)
    messages = {
        "materials": "Could not load the available production materials. Please try again.",
        "quote": "The production quote could not be calculated. Check the model and material selection, then try again.",
        "quantity": "The quantity price could not be updated. Please try again.",
        "shipping": "Shipping could not be calculated for this address. Check the state/region and postal code, then try again.",
        "checkout": "The checkout link could not be prepared. Please try again.",
    }
    return HTTPException(status_code=502, detail=messages.get(action, "The production provider could not complete this request. Please try again."))


def _export_dir(runtime) -> Path:
    path = runtime.root / "exports"
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    return path


def _active_title(runtime) -> str:
    with runtime.engine.lock:
        doc = runtime.engine.doc
        return _safe_name(doc.title if doc is not None else "model", "model")


def _effective_build_volume(
    build_x: float | None = None,
    build_y: float | None = None,
    build_z: float | None = None,
) -> tuple[float, float, float]:
    # Keep DFM and slicing on the same source of truth. Explicit query values
    # remain available for ad-hoc checks, but omitted values come from the
    # configured slicer profile instead of the old 256 mm hard-coded default.
    configured = configured_build_volume_mm() or (256.0, 256.0, 256.0)
    return (
        float(build_x) if build_x is not None else configured[0],
        float(build_y) if build_y is not None else configured[1],
        float(build_z) if build_z is not None else configured[2],
    )


def _preflight_build_volume(runtime) -> None:
    volume = _effective_build_volume()
    report = analyze_print_dfm(runtime.mesh(include_edges=False), build_volume_mm=volume)
    check = next((row for row in report.get("checks", []) if row.get("id") == "build_volume"), None)
    if check and check.get("status") == "fail":
        raise HTTPException(status_code=422, detail=check.get("message") or "Model does not fit the configured build volume.")


@router.get("/capabilities")
def capabilities(project_id: str, user: CurrentUser, db: Session = Depends(get_db)):
    runtime_for(project_id, user, db)
    return manufacturing_capabilities()


@router.get("/dfm")
def dfm_precheck(
    project_id: str,
    user: CurrentUser,
    db: Session = Depends(get_db),
    build_x: float | None = Query(None, gt=0, le=5000),
    build_y: float | None = Query(None, gt=0, le=5000),
    build_z: float | None = Query(None, gt=0, le=5000),
    overhang_angle_deg: float = Query(45.0, ge=0, le=89),
):
    runtime = runtime_for(project_id, user, db)
    try:
        mesh = runtime.mesh(include_edges=False)
        return analyze_print_dfm(mesh, build_volume_mm=_effective_build_volume(build_x, build_y, build_z), overhang_angle_deg=overhang_angle_deg)
    except Exception as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/export/3mf")
def export_3mf(project_id: str, user: CurrentUser, db: Session = Depends(get_db)):
    runtime = runtime_for(project_id, user, db)
    try:
        title = _active_title(runtime)
        path = _export_dir(runtime) / f"{title}-{uuid.uuid4().hex[:8]}.3mf"
        result = write_3mf(runtime.mesh(include_edges=False), path, title=title)
        # Optional official lib3mf validation is deliberately not a hard dependency.
        from ..manufacturing.three_mf import validate_with_lib3mf
        result["validation"] = validate_with_lib3mf(path)
    except Exception as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return FileResponse(path, media_type="model/3mf", filename=path.name, headers={"X-CADia-3MF": "core"})


@router.get("/export/step-ap242")
def export_step_ap242(project_id: str, user: CurrentUser, db: Session = Depends(get_db)):
    runtime = runtime_for(project_id, user, db)
    try:
        with runtime.operation_lock, runtime.engine.lock:
            doc = runtime.engine.doc
            if doc is None or doc.shape is None:
                raise ValueError("No active B-Rep part is available for export.")
            title = _safe_name(doc.title, "model")
            path = _export_dir(runtime) / f"{title}-{uuid.uuid4().hex[:8]}-AP242.step"
            write_step_ap242(doc.shape, path)
    except Exception as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return FileResponse(path, media_type="model/step", filename=path.name, headers={"X-CADia-STEP-Schema": "AP242"})


@router.post("/slice")
def slice_model(project_id: str, payload: SliceRequest, user: CurrentUser, db: Session = Depends(get_db)):
    runtime = runtime_for(project_id, user, db)
    try:
        _preflight_build_volume(runtime)
        stl = runtime.export("stl")
        gcode = _export_dir(runtime) / f"{stl.stem}-{uuid.uuid4().hex[:8]}.gcode"
        slice_stl(stl, gcode, overrides=payload.overrides)
    except SlicerError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return FileResponse(gcode, media_type="text/x.gcode", filename=gcode.name)


@router.post("/slice-upload/{target_name}")
def slice_and_upload(
    project_id: str,
    target_name: str,
    payload: SliceUploadRequest,
    user: CurrentUser,
    db: Session = Depends(get_db),
):
    runtime = runtime_for(project_id, user, db)
    targets = configured_printers()
    target = targets.get(target_name)
    if target is None:
        raise HTTPException(status_code=404, detail="Configured printer target was not found.")
    try:
        _preflight_build_volume(runtime)
        stl = runtime.export("stl")
        gcode = _export_dir(runtime) / f"{stl.stem}-{uuid.uuid4().hex[:8]}.gcode"
        slicing = slice_stl(stl, gcode, overrides=payload.overrides)
        delivery = upload_gcode(target, gcode, start_print=payload.start)
        return {"slicing": slicing, "delivery": delivery, "file": gcode.name}
    except (SlicerError, PrinterError) as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

@router.get("/fulfillment/slant3d/materials")
def slant3d_materials(project_id: str, user: CurrentUser, db: Session = Depends(get_db)):
    runtime_for(project_id, user, db)
    try:
        return {"provider": "Slant 3D", "materials": list_materials()}
    except Slant3DError as exc:
        raise _provider_error(exc, "materials") from exc


@router.post("/fulfillment/slant3d/quote")
def slant3d_quote(
    project_id: str,
    payload: SlantQuoteRequest,
    user: CurrentUser,
    db: Session = Depends(get_db),
):
    runtime = runtime_for(project_id, user, db)
    cfg = Slant3DConfig.from_env()
    try:
        mesh = runtime.mesh(include_edges=False)
        dfm = analyze_print_dfm(mesh, build_volume_mm=(220.0, 220.0, 220.0))
        if not dfm.get("ok"):
            failures = [row.get("message", "DFM precheck failed") for row in dfm.get("checks", []) if row.get("status") == "fail"]
            raise HTTPException(status_code=422, detail="Slant 3D preflight failed: " + "; ".join(failures))
        stl = runtime.export("stl")
        raw = stl.read_bytes()
        if len(raw) > cfg.max_stl_bytes:
            raise HTTPException(status_code=413, detail=f"STL is too large for live quote upload ({len(raw)} bytes; limit {cfg.max_stl_bytes}).")
        encoded = base64.b64encode(raw).decode("ascii")
        quote = call_slant_tool(
            "quote_upload_part",
            {
                "stl_base64": encoded,
                "filename": stl.name,
                "filament_id": payload.filament_id,
                "material": payload.material,
                "color": payload.color,
            },
        )
        return {
            "provider": "Slant 3D",
            "demo_mode": cfg.demo_mode,
            "model": {
                "filename": stl.name,
                "bytes": len(raw),
                "sha256": hashlib.sha256(raw).hexdigest(),
                "size_mm": (dfm.get("metrics") or {}).get("size_mm"),
            },
            "dfm": dfm,
            "quote": quote,
        }
    except HTTPException:
        raise
    except Slant3DError as exc:
        raise _provider_error(exc, "quote") from exc
    except Exception as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/fulfillment/slant3d/quantity")
def slant3d_quantity(
    project_id: str,
    payload: SlantQuantityRequest,
    user: CurrentUser,
    db: Session = Depends(get_db),
):
    runtime_for(project_id, user, db)
    try:
        result = call_slant_tool("quote_set_quantity", {"quote_id": payload.quote_id, "quantity": payload.quantity})
        return {"provider": "Slant 3D", "quantity": payload.quantity, "result": result}
    except Slant3DError as exc:
        raise _provider_error(exc, "quantity") from exc


@router.post("/fulfillment/slant3d/shipping")
def slant3d_shipping(
    project_id: str,
    payload: SlantShippingRequest,
    user: CurrentUser,
    db: Session = Depends(get_db),
):
    runtime_for(project_id, user, db)
    values = {"quote_id": payload.quote_id, **_clean_slant_address(payload.address)}
    try:
        result = call_slant_tool("quote_shipping_options", values)
        return {"provider": "Slant 3D", "result": result}
    except Slant3DError as exc:
        raise _provider_error(exc, "shipping") from exc


@router.post("/fulfillment/slant3d/checkout")
def slant3d_checkout(
    project_id: str,
    payload: SlantCheckoutRequest,
    user: CurrentUser,
    db: Session = Depends(get_db),
):
    runtime_for(project_id, user, db)
    cfg = Slant3DConfig.from_env()
    if cfg.demo_mode:
        raise HTTPException(status_code=403, detail="Checkout is disabled in the CADia hackathon demo. No order or payment was created.")
    try:
        result = call_slant_tool(
            "quote_checkout",
            {
                "quote_id": payload.quote_id,
                "shipping_service": payload.shipping_service,
                "email": payload.email,
            },
        )
        return {"provider": "Slant 3D", "result": result}
    except Slant3DError as exc:
        raise _provider_error(exc, "checkout") from exc
