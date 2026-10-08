"""Backend-neutral vector drawing, rendered to SVG or PDF.

Coordinates are points (1/72 in) with the origin at the top-left and y increasing downward.
"""

from __future__ import annotations

import io
from dataclasses import dataclass, field
from xml.sax.saxutils import escape

from reportlab.pdfbase.pdfmetrics import stringWidth

FONT = "Helvetica"
FONT_BOLD = "Helvetica-Bold"


def text_width(s: str, size: float, bold: bool = False) -> float:
    return stringWidth(str(s), FONT_BOLD if bold else FONT, size)


def fit_text(s: str, size: float, max_width: float, bold: bool = False) -> str:
    s = str(s)
    if text_width(s, size, bold) <= max_width:
        return s
    while s and text_width(s + "…", size, bold) > max_width:
        s = s[:-1]
    return s + "…"


@dataclass
class Line:
    x1: float
    y1: float
    x2: float
    y2: float
    width: float = 0.75
    dash: tuple[float, ...] | None = None
    color: str = "#000000"


@dataclass
class Poly:
    points: list[tuple[float, float]]
    closed: bool = False
    width: float = 0.75
    fill: str | None = None
    stroke: str | None = "#000000"
    round_joins: bool = False
    dash: tuple[float, ...] | None = None


@dataclass
class Rect:
    x: float
    y: float
    w: float
    h: float
    width: float = 0.75
    fill: str | None = None
    stroke: str | None = "#000000"


@dataclass
class Circle:
    cx: float
    cy: float
    r: float
    width: float = 0.75
    fill: str | None = None
    stroke: str | None = "#000000"


@dataclass
class Text:
    x: float
    y: float          # baseline
    s: str
    size: float = 8
    anchor: str = "start"   # start | middle | end
    bold: bool = False
    color: str = "#000000"


@dataclass
class Group:
    items: list = field(default_factory=list)
    dx: float = 0.0
    dy: float = 0.0
    scale: float = 1.0
    layer: str = ""           # CAD layer name for DXF output (inherited by nested groups)

    # Drawing helpers --------------------------------------------------------
    def add(self, item):
        self.items.append(item)
        return item

    def line(self, x1, y1, x2, y2, **kw):
        return self.add(Line(x1, y1, x2, y2, **kw))

    def poly(self, points, **kw):
        return self.add(Poly(list(points), **kw))

    def rect(self, x, y, w, h, **kw):
        return self.add(Rect(x, y, w, h, **kw))

    def circle(self, cx, cy, r, **kw):
        return self.add(Circle(cx, cy, r, **kw))

    def text(self, x, y, s, **kw):
        return self.add(Text(x, y, str(s), **kw))

    def ellipse(self, cx, cy, rx, ry, n=40, **kw):
        import math

        pts = [(cx + rx * math.cos(2 * math.pi * i / n), cy + ry * math.sin(2 * math.pi * i / n)) for i in range(n)]
        return self.add(Poly(pts, closed=True, **kw))

    def arrow(self, x, y, toward_x, toward_y, size=6.0):
        """Filled arrowhead with its tip at (x, y), pointing away from (toward_x, toward_y)."""
        import math

        ang = math.atan2(y - toward_y, x - toward_x)
        a1, a2 = ang + math.radians(160), ang - math.radians(160)
        self.poly([(x, y), (x + size * math.cos(a1), y + size * math.sin(a1)),
                   (x + size * math.cos(a2), y + size * math.sin(a2))], closed=True, fill="#000000", width=0.5)

    def bounds(self) -> tuple[float, float, float, float]:
        """(x0, y0, x1, y1) of the contents in this group's own coordinates."""
        xs: list[float] = []
        ys: list[float] = []
        for it in self.items:
            if isinstance(it, Line):
                xs += [it.x1, it.x2]
                ys += [it.y1, it.y2]
            elif isinstance(it, Poly):
                xs += [p[0] for p in it.points]
                ys += [p[1] for p in it.points]
            elif isinstance(it, Rect):
                xs += [it.x, it.x + it.w]
                ys += [it.y, it.y + it.h]
            elif isinstance(it, Circle):
                xs += [it.cx - it.r, it.cx + it.r]
                ys += [it.cy - it.r, it.cy + it.r]
            elif isinstance(it, Text):
                w = text_width(it.s, it.size, it.bold)
                x0 = {"start": it.x, "middle": it.x - w / 2, "end": it.x - w}[it.anchor]
                xs += [x0, x0 + w]
                ys += [it.y - it.size * 0.8, it.y + it.size * 0.25]
            elif isinstance(it, Group):
                bx0, by0, bx1, by1 = it.bounds()
                xs += [it.dx + bx0 * it.scale, it.dx + bx1 * it.scale]
                ys += [it.dy + by0 * it.scale, it.dy + by1 * it.scale]
        if not xs:
            return (0.0, 0.0, 0.0, 0.0)
        return (min(xs), min(ys), max(xs), max(ys))

    def fit_into(self, x: float, y: float, w: float, h: float, max_scale: float = 1.0,
                 align: str = "center", valign: str = "middle") -> "Group":
        """Wrap this group so its contents fit (scaled down if needed) into the given box."""
        bx0, by0, bx1, by1 = self.bounds()
        bw, bh = max(bx1 - bx0, 1e-6), max(by1 - by0, 1e-6)
        s = min(max_scale, w / bw, h / bh)
        ox = {"left": x, "center": x + (w - bw * s) / 2, "right": x + w - bw * s}[align]
        oy = {"top": y, "middle": y + (h - bh * s) / 2, "bottom": y + h - bh * s}[valign]
        self.scale, self.dx, self.dy = s, ox - bx0 * s, oy - by0 * s
        return self


@dataclass
class Sheet:
    width: float
    height: float
    root: Group = field(default_factory=Group)
    name: str = ""


# ---------------------------------------------------------------------------
# SVG
# ---------------------------------------------------------------------------
def _num(v: float) -> str:
    return f"{v:.2f}".rstrip("0").rstrip(".")


def _svg_items(group: Group, out: list[str]) -> None:
    for it in group.items:
        if isinstance(it, Line):
            dash = f' stroke-dasharray="{",".join(_num(d) for d in it.dash)}"' if it.dash else ""
            out.append(f'<line x1="{_num(it.x1)}" y1="{_num(it.y1)}" x2="{_num(it.x2)}" y2="{_num(it.y2)}" '
                       f'stroke="{it.color}" stroke-width="{_num(it.width)}"{dash}/>')
        elif isinstance(it, Poly):
            tag = "polygon" if it.closed else "polyline"
            pts = " ".join(f"{_num(x)},{_num(y)}" for x, y in it.points)
            join = ' stroke-linejoin="round" stroke-linecap="round"' if it.round_joins else ""
            if it.dash:
                join += f' stroke-dasharray="{",".join(_num(d) for d in it.dash)}"'
            out.append(f'<{tag} points="{pts}" fill="{it.fill or "none"}" stroke="{it.stroke or "none"}" '
                       f'stroke-width="{_num(it.width)}"{join}/>')
        elif isinstance(it, Rect):
            out.append(f'<rect x="{_num(it.x)}" y="{_num(it.y)}" width="{_num(it.w)}" height="{_num(it.h)}" '
                       f'fill="{it.fill or "none"}" stroke="{it.stroke or "none"}" stroke-width="{_num(it.width)}"/>')
        elif isinstance(it, Circle):
            out.append(f'<circle cx="{_num(it.cx)}" cy="{_num(it.cy)}" r="{_num(it.r)}" '
                       f'fill="{it.fill or "none"}" stroke="{it.stroke or "none"}" stroke-width="{_num(it.width)}"/>')
        elif isinstance(it, Text):
            weight = ' font-weight="bold"' if it.bold else ""
            out.append(f'<text x="{_num(it.x)}" y="{_num(it.y)}" font-size="{_num(it.size)}" '
                       f'text-anchor="{it.anchor}" fill="{it.color}"{weight}>{escape(it.s)}</text>')
        elif isinstance(it, Group):
            out.append(f'<g transform="translate({_num(it.dx)} {_num(it.dy)}) scale({it.scale:.5f})">')
            _svg_items(it, out)
            out.append("</g>")


def sheet_to_svg(sheet: Sheet) -> str:
    out = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{_num(sheet.width / 72)}in" height="{_num(sheet.height / 72)}in" '
        f'viewBox="0 0 {_num(sheet.width)} {_num(sheet.height)}" font-family="Helvetica, Arial, sans-serif">',
        f'<rect x="0" y="0" width="{_num(sheet.width)}" height="{_num(sheet.height)}" fill="#ffffff"/>',
    ]
    _svg_items(sheet.root, out)
    out.append("</svg>")
    return "\n".join(out)


# ---------------------------------------------------------------------------
# PDF
# ---------------------------------------------------------------------------
def _hex(c: str):
    from reportlab.lib.colors import HexColor

    return HexColor(c)


def _pdf_items(c, group: Group, dx: float, dy: float, s: float, page_h: float) -> None:
    X = lambda x: dx + x * s  # noqa: E731
    Y = lambda y: page_h - (dy + y * s)  # noqa: E731
    for it in group.items:
        if isinstance(it, Line):
            c.setStrokeColor(_hex(it.color))
            c.setLineWidth(it.width * s)
            c.setDash([d * s for d in it.dash] if it.dash else [])
            c.line(X(it.x1), Y(it.y1), X(it.x2), Y(it.y2))
            c.setDash([])
        elif isinstance(it, (Poly, Rect, Circle)):
            c.setLineWidth(it.width * s)
            if it.fill:
                c.setFillColor(_hex(it.fill))
            if it.stroke:
                c.setStrokeColor(_hex(it.stroke))
            fill, stroke = int(bool(it.fill)), int(bool(it.stroke))
            if isinstance(it, Rect):
                c.rect(X(it.x), Y(it.y + it.h), it.w * s, it.h * s, fill=fill, stroke=stroke)
            elif isinstance(it, Circle):
                c.circle(X(it.cx), Y(it.cy), it.r * s, fill=fill, stroke=stroke)
            else:
                c.setLineJoin(1 if it.round_joins else 0)
                c.setLineCap(1 if it.round_joins else 0)
                c.setDash([d * s for d in it.dash] if it.dash else [])
                p = c.beginPath()
                p.moveTo(X(it.points[0][0]), Y(it.points[0][1]))
                for x, y in it.points[1:]:
                    p.lineTo(X(x), Y(y))
                if it.closed:
                    p.close()
                c.drawPath(p, fill=fill, stroke=stroke)
                c.setDash([])
                c.setLineJoin(0)
                c.setLineCap(0)
        elif isinstance(it, Text):
            c.setFillColor(_hex(it.color))
            c.setFont(FONT_BOLD if it.bold else FONT, it.size * s)
            draw = {"start": c.drawString, "middle": c.drawCentredString, "end": c.drawRightString}[it.anchor]
            draw(X(it.x), Y(it.y), it.s)
        elif isinstance(it, Group):
            _pdf_items(c, it, X(it.dx), dy + it.dy * s, s * it.scale, page_h)


def sheets_to_pdf(sheets: list[Sheet], title: str = "") -> bytes:
    from reportlab.pdfgen import canvas

    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=(sheets[0].width, sheets[0].height) if sheets else (792, 612))
    if title:
        c.setTitle(title)
    for sheet in sheets:
        c.setPageSize((sheet.width, sheet.height))
        _pdf_items(c, sheet.root, 0.0, 0.0, 1.0, sheet.height)
        c.showPage()
    c.save()
    return buf.getvalue()
