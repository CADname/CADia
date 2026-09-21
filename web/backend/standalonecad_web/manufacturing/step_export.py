from __future__ import annotations

from pathlib import Path

from OCP.Interface import Interface_Static
from OCP.STEPControl import STEPControl_AsIs, STEPControl_Controller, STEPControl_Writer


def write_step_ap242(shape, path: Path) -> dict:
    if shape is None:
        raise ValueError("No shape is available for STEP AP242 export.")
    STEPControl_Controller.Init_s()
    if not Interface_Static.SetIVal_s("write.step.schema", 5):
        raise RuntimeError("OCCT rejected the AP242 STEP schema setting.")
    try:
        writer = STEPControl_Writer()
        transfer_status = writer.Transfer(shape.wrapped, STEPControl_AsIs)
        if "RetDone" not in str(transfer_status):
            raise RuntimeError(f"STEP AP242 transfer failed: {transfer_status}")
        path.parent.mkdir(parents=True, exist_ok=True)
        write_status = writer.Write(str(path))
        if "RetDone" not in str(write_status) or not path.exists() or path.stat().st_size <= 0:
            raise RuntimeError(f"STEP AP242 file write failed: {write_status}")
        return {"path": str(path), "schema": "AP242", "bytes": path.stat().st_size}
    finally:
        # Avoid leaking OCCT's process-global writer setting to legacy AP214 paths.
        Interface_Static.SetIVal_s("write.step.schema", 4)
