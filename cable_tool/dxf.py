"""DXF output (AutoCAD R12 ASCII), one file per sheet.

R12 is the most widely readable DXF flavour: AutoCAD, SolidWorks, Inventor, Creo, CATIA, NX,
DraftSight, LibreCAD, QCAD and Fusion all open it. Drawings are written at true sheet size in
inches, on layers named after the drawing areas (BORDER, TITLE_BLOCK, ASSEMBLY, WIRING, TABLES,
NOTES). Cable bodies drawn as thick strokes in the PDF become two outline polylines.
"""

from __future__ import annotations

import math

from .canvas import Circle, Group, Line, Poly, Rect, Sheet, Text

PT = 1 / 72.0   # points -> inches

LAYERS = {   # name -> ACI colour
    "0": 7, "BORDER": 7, "TITLE_BLOCK": 7, "ASSEMBLY": 7, "CABLE": 7, "DIMENSIONS": 3, "BALLOONS": 5,
    "WIRING": 7, "SHIELDS": 4, "TABLES": 7, "NOTES": 7, "TEXT": 7,
}


def _clean_text(s: str) -> str:
    s = s.replace("±", "%%p").replace("Ø", "%%c").replace("°", "%%d").replace("…", "...")
    return s.encode("ascii", "replace").decode("ascii")


def _offset_polyline(pts: list[tuple[float, float]], dist: float) -> list[tuple[float, float]]:
    """Offset an open polyline by ``dist`` (left side positive) with mitred corners."""
    pts = [p for i, p in enumerate(pts) if i == 0 or p != pts[i - 1]]
    if len(pts) < 2:
        return pts

    def normal(a, b):
        dx, dy = b[0] - a[0], b[1] - a[1]
        n = math.hypot(dx, dy) or 1
        return (-dy / n, dx / n)

    out = []
    for i, p in enumerate(pts):
        if i == 0:
            nx, ny = normal(pts[0], pts[1])
        elif i == len(pts) - 1:
            nx, ny = normal(pts[-2], pts[-1])
        else:
            n1, n2 = normal(pts[i - 1], p), normal(p, pts[i + 1])
            mx, my = n1[0] + n2[0], n1[1] + n2[1]
            m = math.hypot(mx, my)
            if m < 1e-6:
                nx, ny = n1
            else:
                mx, my = mx / m, my / m
                cos = mx * n1[0] + my * n1[1]
                k = 1 / max(cos, 0.25)
                nx, ny = mx * k, my * k
        out.append((p[0] + nx * dist, p[1] + ny * dist))
    return out


class _Writer:
    def __init__(self, page_h: float):
        self.page_h = page_h
        self.out: list[str] = []
        self.layers: dict[str, int] = dict(LAYERS)

    def g(self, code: int, value) -> None:
        if isinstance(value, float):
            value = f"{value:.4f}"
        self.out.append(f"{code}\n{value}")

    def pt(self, x: float, y: float, base: int = 10) -> None:
        self.g(base, x * PT)
        self.g(base + 10, (self.page_h - y) * PT)
        self.g(base + 20, 0.0)

    def common(self, kind: str, layer: str, dashed: bool = False) -> None:
        self.layers.setdefault(layer, 7)
        self.g(0, kind)
        self.g(8, layer)
        if dashed:
            self.g(6, "DASHED")

    def line(self, layer, x1, y1, x2, y2, dashed=False):
        self.common("LINE", layer, dashed)
        self.pt(x1, y1, 10)
        self.pt(x2, y2, 11)

    def polyline(self, layer, pts, closed=False, dashed=False):
        if len(pts) < 2:
            return
        self.common("POLYLINE", layer, dashed)
        self.g(66, 1)
        self.g(10, 0.0)
        self.g(20, 0.0)
        self.g(30, 0.0)
        self.g(70, 1 if closed else 0)
        for x, y in pts:
            self.g(0, "VERTEX")
            self.g(8, layer)
            self.pt(x, y)
        self.g(0, "SEQEND")
        self.g(8, layer)

    def solid(self, layer, pts):
        p = list(pts) + [pts[-1]] * (4 - len(pts))
        self.common("SOLID", layer)
        # SOLID vertex order is 1-2-4-3
        for code, (x, y) in zip((10, 11, 13, 12), p):
            self.pt(x, y, code)

    def circle(self, layer, cx, cy, r):
        self.common("CIRCLE", layer)
        self.pt(cx, cy)
        self.g(40, r * PT)

    def text(self, layer, x, y, s, size, anchor):
        s = _clean_text(s)
        if not s.strip():
            return
        self.common("TEXT", layer)
        self.pt(x, y)
        self.g(40, size * 0.72 * PT)    # cap height
        self.g(1, s)
        h = {"start": 0, "middle": 1, "end": 2}[anchor]
        if h:
            self.g(72, h)
            self.pt(x, y, 11)


def _walk(w: _Writer, group: Group, dx: float, dy: float, s: float, layer: str) -> None:
    layer = group.layer or layer
    X = lambda x: dx + x * s  # noqa: E731
    Y = lambda y: dy + y * s  # noqa: E731
    for it in group.items:
        if isinstance(it, Group):
            _walk(w, it, X(it.dx), Y(it.dy), s * it.scale, layer)
        elif isinstance(it, Line):
            if it.color.lower() not in ("#ffffff", "white"):
                w.line(layer, X(it.x1), Y(it.y1), X(it.x2), Y(it.y2), dashed=bool(it.dash))
        elif isinstance(it, Rect):
            if it.stroke:
                x0, y0, x1, y1 = X(it.x), Y(it.y), X(it.x + it.w), Y(it.y + it.h)
                w.polyline(layer, [(x0, y0), (x1, y0), (x1, y1), (x0, y1)], closed=True)
        elif isinstance(it, Circle):
            if it.stroke or (it.fill and it.fill.lower() == "#000000"):
                w.circle(layer, X(it.cx), Y(it.cy), it.r * s)
        elif isinstance(it, Poly):
            pts = [(X(x), Y(y)) for x, y in it.points]
            if it.stroke and it.stroke.lower() == "#ffffff":
                continue                      # white overlay strokes are a raster trick only
            if it.width * s >= 4 and not it.closed:
                half = it.width * s / 2       # thick stroke (cable): draw its two edges
                w.polyline("CABLE", _offset_polyline(pts, half))
                w.polyline("CABLE", _offset_polyline(pts, -half))
            elif it.closed and it.fill and it.fill.lower() == "#000000" and len(pts) <= 4:
                w.solid(layer, pts)           # arrowheads
            elif it.stroke:
                w.polyline(layer, pts, closed=it.closed, dashed=bool(it.dash))
        elif isinstance(it, Text):
            w.text(layer, X(it.x), Y(it.y), it.s, it.size * s, it.anchor)


def sheet_to_dxf(sheet: Sheet) -> str:
    w = _Writer(sheet.height)
    body = w.out
    _walk(w, sheet.root, 0.0, 0.0, 1.0, "0")
    entities = body[:]
    w.out = []
    # Header
    w.g(0, "SECTION"); w.g(2, "HEADER")  # noqa: E702
    w.g(9, "$ACADVER"); w.g(1, "AC1009")  # noqa: E702
    w.g(9, "$INSUNITS"); w.g(70, 1)  # noqa: E702
    w.g(9, "$EXTMIN"); w.g(10, 0.0); w.g(20, 0.0); w.g(30, 0.0)  # noqa: E702
    w.g(9, "$EXTMAX"); w.g(10, sheet.width * PT); w.g(20, sheet.height * PT); w.g(30, 0.0)  # noqa: E702
    w.g(9, "$LIMMIN"); w.g(10, 0.0); w.g(20, 0.0)  # noqa: E702
    w.g(9, "$LIMMAX"); w.g(10, sheet.width * PT); w.g(20, sheet.height * PT)  # noqa: E702
    w.g(0, "ENDSEC")
    # Tables: line types and layers
    w.g(0, "SECTION"); w.g(2, "TABLES")  # noqa: E702
    w.g(0, "TABLE"); w.g(2, "LTYPE"); w.g(70, 2)  # noqa: E702
    w.g(0, "LTYPE"); w.g(2, "CONTINUOUS"); w.g(70, 0); w.g(3, "Solid line"); w.g(72, 65); w.g(73, 0); w.g(40, 0.0)  # noqa: E702
    w.g(0, "LTYPE"); w.g(2, "DASHED"); w.g(70, 0); w.g(3, "__ __ __"); w.g(72, 65); w.g(73, 2); w.g(40, 0.15)  # noqa: E702
    w.g(49, 0.1); w.g(49, -0.05)  # noqa: E702
    w.g(0, "ENDTAB")
    w.g(0, "TABLE"); w.g(2, "LAYER"); w.g(70, len(w.layers))  # noqa: E702
    for name, color in w.layers.items():
        w.g(0, "LAYER"); w.g(2, name); w.g(70, 0); w.g(62, color); w.g(6, "CONTINUOUS")  # noqa: E702
    w.g(0, "ENDTAB")
    w.g(0, "ENDSEC")
    w.g(0, "SECTION"); w.g(2, "ENTITIES")  # noqa: E702
    w.out.extend(entities)
    w.g(0, "ENDSEC")
    w.g(0, "EOF")
    return "\n".join(w.out) + "\n"
