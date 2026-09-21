"""Manufacturing integrations for CADia.

The default runtime only uses permissive/LGPL CAD components already present in
CADia. Copyleft slicers are treated as external, opt-in executables and are not
bundled into the application image.
"""

from .capabilities import manufacturing_capabilities
from .dfm import analyze_print_dfm
from .three_mf import write_3mf

__all__ = ["manufacturing_capabilities", "analyze_print_dfm", "write_3mf"]
