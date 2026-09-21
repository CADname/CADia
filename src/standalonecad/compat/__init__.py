"""Compatibility adapters that preserve upstream CAD semantics without Autodesk binaries."""
from .inventor_semantics import InventorSemanticAdapter, UPSTREAM_HOST_COMMANDS

__all__ = ["InventorSemanticAdapter", "UPSTREAM_HOST_COMMANDS"]
