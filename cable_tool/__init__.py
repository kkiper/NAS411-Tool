"""Cable assembly drawing generator: wiring list + part numbers -> drawing package."""

from .bom import BomItem, build_bom, check_design
from .canvas import sheet_to_svg, sheets_to_pdf
from .drawing import DEFAULT_SHEET, SHEET_SIZES, build_drawing
from .drc import DrcReport, Finding, run_drc
from .dxf import sheet_to_dxf
from .library import Part, PartsLibrary, load_library
from .model import CableDesign, ConnectorEnd, Splice, TitleBlock, Wire, WireGroup
from .project import apply_table, load_design, save_design

__all__ = [
    "DEFAULT_SHEET",
    "SHEET_SIZES",
    "BomItem",
    "CableDesign",
    "ConnectorEnd",
    "DrcReport",
    "Finding",
    "Part",
    "PartsLibrary",
    "Splice",
    "WireGroup",
    "apply_table",
    "load_library",
    "run_drc",
    "sheet_to_dxf",
    "TitleBlock",
    "Wire",
    "build_bom",
    "build_drawing",
    "check_design",
    "load_design",
    "save_design",
    "sheet_to_svg",
    "sheets_to_pdf",
]

__version__ = "0.2.0"
