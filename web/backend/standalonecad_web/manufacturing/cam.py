from __future__ import annotations

import importlib.util


def opencamlib_capability() -> dict:
    available = importlib.util.find_spec("opencamlib") is not None
    return {
        "available": available,
        "engine": "OpenCAMLib",
        "license": "LGPL-2.1",
        "algorithms": ["drop-cutter", "push-cutter/waterline"],
        "note": "Precision CNC toolpaths require separate validation with approved tooling, stock, WCS, machine kinematics, and post-processors.",
    }
