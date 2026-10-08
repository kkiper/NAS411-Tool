"""Cable assembly drawing generator: wiring list + part numbers -> drawing package."""

from .bom import BomItem, build_bom, check_design
from .canvas import sheet_to_svg, sheets_to_pdf
from .drawing import DEFAULT_SHEET, SHEET_SIZES, build_drawing
from .model import CableDesign, ConnectorEnd, TitleBlock, Wire
from .project import load_design, save_design

__all__ = [
    "DEFAULT_SHEET",
    "SHEET_SIZES",
    "BomItem",
    "CableDesign",
    "ConnectorEnd",
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

__version__ = "0.1.0"
