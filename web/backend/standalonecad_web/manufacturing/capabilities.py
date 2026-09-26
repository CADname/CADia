from __future__ import annotations

import importlib.util

from .cam import opencamlib_capability
from .printers import configured_printers
from .slicer import slicer_capability
from .slant3d import slant3d_capability


def manufacturing_capabilities() -> dict:
    targets = configured_printers()
    return {
        "native": {
            "dfm_mesh_precheck": True,
            "three_mf_core": True,
            "step_ap242": True,
            "kernel": "OCCT/CadQuery",
        },
        "optional": {
            "lib3mf": {
                "available": importlib.util.find_spec("lib3mf") is not None,
                "license": "BSD-2-Clause",
                "role": "official 3MF read/write/validation library",
            },
            "slicer": slicer_capability(),
            "cam": opencamlib_capability(),
        },
        "fulfillment": {
            "slant3d": slant3d_capability(),
        },
        "printers": [
            {
                "name": name,
                "kind": target.kind,
                "configured": True,
                "execution_enabled": target.allow_execution,
            }
            for name, target in targets.items()
        ],
        "safety": (
            "Automatic machine execution is disabled by default. G-code/NC output must be "
            "reviewed and simulated before it is sent to equipment."
        ),
    }
