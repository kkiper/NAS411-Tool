"""Lay out the cable drawing.

Sheet 1   Assembly view (connector ends, backshells, heatshrink boots, labels, lengths, balloons),
          bill of materials, notes.
Sheet 2   Wiring diagram (pin-out of every connector, wires drawn point to point).
Sheet 3+  Wire list and label schedule (continued onto more sheets as needed).
Every sheet has a zoned border and a title block.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from .bom import BomItem, build_bom, compress_refs, item_numbers
from .canvas import Group, Sheet, fit_text, text_width
from .model import SHIELD_BACKSHELL, CableDesign, ConnectorEnd, Wire, fmt_dia, fmt_length, natural_key, parse_shield_term

IN = 72.0
MM = 72.0 / 25.4

SHEET_SIZES: dict[str, tuple[str, float, float]] = {
    "ANSI B (17 x 11 in)": ("B", 17 * IN, 11 * IN),
    "ANSI C (22 x 17 in)": ("C", 22 * IN, 17 * IN),
    "ANSI D (34 x 22 in)": ("D", 34 * IN, 22 * IN),
    "ISO A3 (420 x 297 mm)": ("A3", 420 * MM, 297 * MM),
    "ISO A2 (594 x 420 mm)": ("A2", 594 * MM, 420 * MM),
    "ISO A1 (841 x 594 mm)": ("A1", 841 * MM, 594 * MM),
}
DEFAULT_SHEET = "ANSI B (17 x 11 in)"

MARGIN, ZONE_BAND, PAD = 27.0, 18.0, 12.0
TB_W, TB_H = 432.0, 124.0
SHADE = "#d9d9d9"
HEADER_FILL = "#eeeeee"


@dataclass
class Frame:
    x0: float
    y0: float
    x1: float
    y1: float

    @property
    def tb_top(self) -> float:
        return self.y1 - TB_H

    @property
    def tb_left(self) -> float:
        return self.x1 - TB_W


# ---------------------------------------------------------------------------
# Border and title block
# ---------------------------------------------------------------------------
def draw_frame(g: Group, w: float, h: float) -> Frame:
    g.rect(MARGIN, MARGIN, w - 2 * MARGIN, h - 2 * MARGIN, width=1.5)
    f = Frame(MARGIN + ZONE_BAND, MARGIN + ZONE_BAND, w - MARGIN - ZONE_BAND, h - MARGIN - ZONE_BAND)
    g.rect(f.x0, f.y0, f.x1 - f.x0, f.y1 - f.y0, width=1.0)
    ncols = max(2, round(w / (4.25 * IN)))
    nrows = max(2, round(h / (5.5 * IN)))
    cw, rh = (f.x1 - f.x0) / ncols, (f.y1 - f.y0) / nrows
    for i in range(ncols):  # zone numbers increase right to left
        cx = f.x0 + (i + 0.5) * cw
        label = str(ncols - i)
        g.text(cx, MARGIN + ZONE_BAND / 2 + 3, label, size=8, anchor="middle")
        g.text(cx, h - MARGIN - ZONE_BAND / 2 + 3, label, size=8, anchor="middle")
        if i:
            x = f.x0 + i * cw
            g.line(x, MARGIN, x, f.y0, width=0.5)
            g.line(x, f.y1, x, h - MARGIN, width=0.5)
    for j in range(nrows):  # zone letters increase bottom to top
        cy = f.y0 + (j + 0.5) * rh
        label = "ABCDEFGHJK"[nrows - 1 - j]
        g.text(MARGIN + ZONE_BAND / 2, cy + 3, label, size=8, anchor="middle")
        g.text(w - MARGIN - ZONE_BAND / 2, cy + 3, label, size=8, anchor="middle")
        if j:
            y = f.y0 + j * rh
            g.line(MARGIN, y, f.x0, y, width=0.5)
            g.line(f.x1, y, w - MARGIN, y, width=0.5)
    return f


def draw_title_block(g: Group, f: Frame, design: CableDesign, size_letter: str, sheet_no: int, total: int) -> None:
    tb = design.title_block
    x, y = f.tb_left, f.tb_top
    g.rect(x, y, TB_W, TB_H, width=1.25, fill="#ffffff")

    def cell(cx, cy, cw, ch, caption, value, size=9, bold=False, center=False):
        g.rect(cx, cy, cw, ch, width=0.75)
        g.text(cx + 3, cy + 7, caption, size=5.5)
        value = fit_text(value, size, cw - 8, bold)
        if center:
            g.text(cx + cw / 2, cy + ch - 7, value, size=size, bold=bold, anchor="middle")
        else:
            g.text(cx + 5, cy + ch - 6, value, size=size, bold=bold)

    cell(x, y, TB_W, 22, "COMPANY", tb.company, size=10, bold=True, center=True)
    cell(x, y + 22, TB_W, 34, "TITLE", tb.title, size=12, bold=True, center=True)
    r = y + 56
    cell(x, r, 60, 22, "SIZE", size_letter, bold=True, center=True)
    cell(x + 60, r, 300, 22, "DWG NO.", tb.drawing_number, size=10, bold=True)
    cell(x + 360, r, 72, 22, "REV", tb.revision, size=10, bold=True, center=True)
    r += 22
    cell(x, r, 144, 22, "DRAWN", tb.drawn_by)
    cell(x + 144, r, 144, 22, "CHECKED", tb.checked_by)
    cell(x + 288, r, 144, 22, "DATE", tb.date)
    r += 22
    cell(x, r, 144, 24, "SCALE", tb.scale or "NONE")
    cell(x + 144, r, 144, 24, "UNITS", design.units)
    cell(x + 288, r, 144, 24, "SHEET", f"{sheet_no} OF {total}", bold=True)


# ---------------------------------------------------------------------------
# Tables
# ---------------------------------------------------------------------------
ROW_H, HEADER_H, TITLE_H = 12.0, 14.0, 16.0


def table_widths(headers: list[str], rows: list[list[str]], size: float = 7, caps: list[float] | None = None) -> list[float]:
    widths = []
    for j, h in enumerate(headers):
        w = max([text_width(h, size, True)] + [text_width(r[j], size) for r in rows]) + 8
        if caps and caps[j]:
            w = min(w, caps[j])
        widths.append(max(w, 20))
    return widths


def draw_table(g: Group, x: float, y: float, headers: list[str], rows: list[list[str]], widths: list[float],
               title: str = "", size: float = 7, center_cols: frozenset[int] = frozenset()) -> float:
    """Draw a ruled table with its top-left at (x, y). Returns the height used."""
    top = y
    if title:
        g.text(x, y + 11, title, size=9, bold=True)
        y += TITLE_H
    total_w = sum(widths)
    g.rect(x, y, total_w, HEADER_H, fill=HEADER_FILL, width=0.75)
    cx = x
    for j, (h, w) in enumerate(zip(headers, widths)):
        g.text(cx + w / 2, y + HEADER_H - 4, fit_text(h, size, w - 4, True), size=size, bold=True, anchor="middle")
        cx += w
    y += HEADER_H
    for row in rows:
        cx = x
        for j, (val, w) in enumerate(zip(row, widths)):
            val = fit_text(val, size, w - 6)
            if j in center_cols:
                g.text(cx + w / 2, y + ROW_H - 3.5, val, size=size, anchor="middle")
            else:
                g.text(cx + 3, y + ROW_H - 3.5, val, size=size)
            cx += w
        g.line(x, y + ROW_H, x + total_w, y + ROW_H, width=0.4)
        y += ROW_H
    g.rect(x, top + (TITLE_H if title else 0), total_w, y - top - (TITLE_H if title else 0), width=0.75)
    cx = x
    for w in widths[:-1]:
        cx += w
        g.line(cx, top + (TITLE_H if title else 0), cx, y, width=0.4)
    return y - top


# ---------------------------------------------------------------------------
# Connector side assignment (shared by the assembly view and wiring diagram)
# ---------------------------------------------------------------------------
def assign_sides(design: CableDesign) -> tuple[list[ConnectorEnd], list[ConnectorEnd]]:
    """Split connectors into a left and right column so most wires cross the cable."""
    conns = list(design.connectors)
    if not conns:
        return [], []
    left, right = [conns[0]], []
    for c in conns[1:]:
        to_left = to_right = 0
        for w in design.wires:
            ends = {w.from_ref, w.to_ref}
            if c.ref in ends:
                other = (ends - {c.ref}) or {c.ref}
                to_left += sum(1 for o in left if o.ref in other)
                to_right += sum(1 for o in right if o.ref in other)
        if to_left > to_right or (to_left == to_right and len(right) <= len(left)):
            right.append(c)
        else:
            left.append(c)
    return left, right


# ---------------------------------------------------------------------------
# Assembly view
# ---------------------------------------------------------------------------
BALLOON_R = 9.0
BALLOON_OFF = 62.0
DIM_OFF = 72.0
LEG_SPACING = 215.0
CABLE_W = 14.0


def balloon(g: Group, x: float, y: float, item: int | str, anchor: tuple[float, float] | None = None) -> None:
    g.circle(x, y, BALLOON_R, fill="#ffffff", width=0.9)
    g.text(x, y + 3, str(item), size=8, anchor="middle", bold=True)
    if anchor:
        ax, ay = anchor
        d = math.hypot(ax - x, ay - y) or 1
        g.line(x + (ax - x) / d * BALLOON_R, y + (ay - y) / d * BALLOON_R, ax, ay, width=0.6)
        g.circle(ax, ay, 1.4, fill="#000000", width=0.3)


def end_length(c: ConnectorEnd) -> float:
    a = 104.0 if c.backshell_pn else 56.0
    if c.heatshrink_pn:
        a = a - (22 if c.backshell_pn else 6) + 60
    if c.label_pn:
        a += 26 + 58
    return a


def draw_connector_end(g: Group, x_face: float, y: float, d: int, c: ConnectorEnd, items: dict[str, int],
                       balloon_side: int) -> None:
    """Draw a connector end with its mating face at x_face; the cable leaves in direction d (+1 right, -1 left)."""
    f = lambda a: x_face + d * a  # noqa: E731
    span = lambda a0, a1: (min(f(a0), f(a1)), abs(a1 - a0))  # noqa: E731
    anchors: list[tuple[str, float, float]] = []  # (pn, x, half-height)

    # Coupling ring with knurl lines, then connector body
    x, w = span(0, 18)
    g.rect(x, y - 31, w, 62, fill="#ffffff", width=1.0)
    for a in (4.5, 9, 13.5):
        g.line(f(a), y - 31, f(a), y + 31, width=0.35)
    x, w = span(18, 56)
    g.rect(x, y - 26, w, 52, fill="#ffffff", width=1.0)
    g.text(f(37), y + 4, fit_text(c.ref, 11, 34, True), size=11, bold=True, anchor="middle")
    anchors.append((c.connector_pn, f(37), 26))
    a = 56.0

    if c.backshell_pn:
        g.poly([(f(56), y - 21), (f(104), y - 10), (f(104), y + 10), (f(56), y + 21)], closed=True, fill="#ffffff", width=1.0)
        g.line(f(64), y - 19.3, f(64), y + 19.3, width=0.5)
        anchors.append((c.backshell_pn, f(70), 18))
        a = 104.0
    if c.heatshrink_pn:
        start = a - (22 if c.backshell_pn else 6)
        x, w = span(start, start + 60)
        g.rect(x, y - 13, w, 26, fill=SHADE, width=0.9)
        anchors.append((c.heatshrink_pn, f(start + 36), 13))
        a = start + 60
    if c.label_pn:
        start = a + 26
        x, w = span(start, start + 58)
        g.rect(x, y - 10, w, 20, fill="#ffffff", width=0.9)
        g.text(f(start + 29), y + 2.5, fit_text(c.legend, 7, 54, True), size=7, bold=True, anchor="middle")
        anchors.append((c.label_pn, f(start + 29), 10))

    s = balloon_side
    for pn, ax, half in anchors:
        if pn in items:
            balloon(g, ax, y + s * BALLOON_OFF, items[pn], (ax, y + s * half))
    # Description outboard of the mating face (the ref designator is on the connector body)
    if c.description:
        g.text(f(-8), y + 3, c.description.upper(), size=8, anchor="end" if d == 1 else "start")


def break_symbol(g: Group, x: float, y: float) -> None:
    g.rect(x - 4, y - CABLE_W / 2 - 2, 8, CABLE_W + 4, fill="#ffffff", stroke=None)
    for dx in (-4, 4):
        g.line(x + dx - 3, y + CABLE_W / 2 + 3, x + dx + 3, y - CABLE_W / 2 - 3, width=0.8)


def dimension(g: Group, xa: float, xb: float, y: float, ext_a: float, ext_b: float, label: str) -> None:
    """Horizontal dimension at height y with extension lines starting at ext_a / ext_b."""
    s = 1 if y > ext_a else -1
    g.line(xa, ext_a, xa, y + s * 6, width=0.5)
    g.line(xb, ext_b, xb, y + s * 6, width=0.5)
    lo, hi = min(xa, xb), max(xa, xb)
    g.line(lo, y, hi, y, width=0.6)
    g.arrow(lo, y, hi, y)
    g.arrow(hi, y, lo, y)
    tw = text_width(label, 9, True)
    g.rect((lo + hi) / 2 - tw / 2 - 3, y - 7, tw + 6, 12, fill="#ffffff", stroke=None)
    g.text((lo + hi) / 2, y + 3.5, label, size=9, bold=True, anchor="middle")


def assembly_view(design: CableDesign, items: dict[str, int]) -> Group:
    g = Group(layer="ASSEMBLY")
    left, right = assign_sides(design)
    if not left:
        g.text(0, 0, "NO CONNECTORS DEFINED", size=12, bold=True)
        return g
    located = {sp.ref for sp in design.splices if sp.near and sp.distance is not None and design.connector(sp.near)}
    used_splices = [sp for sp in design.splices if any(sp.ref in (w.from_ref, w.to_ref) for w in design.wires)]
    bundle_pns = [p for w in design.wires for p in (w.wire_pn, w.label_pn, w.heatshrink_pn)]
    bundle_pns += [p for gr in design.groups if design.group_members(gr.group_id)
                   for p in (gr.cable_pn, gr.shield_pn, gr.shield_term_pn)]
    bundle_pns += [sp.splice_pn for sp in used_splices if sp.ref not in located]
    wire_items = sorted({items[p] for p in bundle_pns if p in items})
    straight = len(left) == 1 and len(right) == 1
    units = design.units

    cable_min = max(170.0, 80 + min(len(wire_items), 4) * 2 * BALLOON_R + 70)
    max_dy = max(LEG_SPACING * (max(len(left), len(right)) - 1) / 2, 0)
    bend = 0.0 if straight else 40 + 0.45 * max_dy
    left_end = max(end_length(c) for c in left)
    right_end = max((end_length(c) for c in right), default=0)
    bx, by = left_end + cable_min + bend, 0.0
    right_face = bx + bend + cable_min + right_end

    legs = []  # (connector, face_x, y, d)
    for side, face, d in ((left, 0.0, 1), (right, right_face, -1)):
        for i, c in enumerate(side):
            legs.append((c, face, by + (i - (len(side) - 1) / 2) * LEG_SPACING, d))

    # Cable: outline first (black), then the inside (white) so legs merge cleanly at the breakout
    paths = []
    for c, face, y, d in legs:
        start = face + d * (end_length(c) - (58 if c.label_pn else 0) - 4)
        paths.append([(start, y), (bx - d * bend, y), (bx, by)])
    if not right:
        paths.append([(bx, by), (bx + 30, by)])
    for p in paths:
        g.poly(p, width=CABLE_W, round_joins=True)
    for p in paths:
        g.poly(p, width=CABLE_W - 2.5, stroke="#ffffff", round_joins=True)
    if right and not straight:
        g.circle(bx, by, 10, fill=SHADE, width=1.0)
        g.text(bx + 14, by + 3, "BREAKOUT", size=7, bold=True)
    if not right:
        for k in (-6, -2, 2, 6):
            g.line(bx + 37, by + k * 0.8, bx + 52, by + k * 1.6, width=0.6)
        g.text(bx + 58, by + 3, "WIRE ENDS PER WIRE LIST", size=7, bold=True)

    # Breaks on each leg (not to scale), connector ends, balloons, dimensions
    for idx, (c, face, y, d) in enumerate(legs):
        run_start = face + d * end_length(c)
        run_end = bx - d * bend
        if straight:
            dim_side = 1
        else:
            dim_side = -1 if y < by - 1 else 1
        bal_side = -dim_side
        mid = (run_start + run_end) / 2
        break_symbol(g, mid + d * 25, y)
        draw_connector_end(g, face, y, d, c, items, bal_side)
        if idx == 0 and wire_items:
            # Wire, marker and sleeve items as one balloon cluster with a shared leader
            per_row = 4
            x0 = run_start + d * 50
            y0 = y + bal_side * BALLOON_OFF * 0.75
            balloon(g, x0, y0, wire_items[0], (x0 - d * 12, y + bal_side * CABLE_W / 2))
            for k, item in enumerate(wire_items[1:], start=1):
                balloon(g, x0 + d * (k % per_row) * 2 * BALLOON_R, y0 + bal_side * (k // per_row) * 2 * BALLOON_R, item)

    # Splices with a location: marker on the leg, balloon, and location callout
    leg_of = {c.ref: (face, y, d, c) for c, face, y, d in legs}
    for sp in used_splices:
        if sp.ref not in located or sp.near not in leg_of:
            continue
        face, y, d, c = leg_of[sp.near]
        run_start, run_end = face + d * end_length(c), bx - d * bend
        leg_len = c.length if c.length else design.end_to_end_length()
        frac = min(max((sp.distance / leg_len) if leg_len else 0.5, 0.2), 0.8)
        if straight and leg_len and design.end_to_end_length():
            frac = min(max(sp.distance / design.end_to_end_length() * 2, 0.2), 0.8)
        x = run_start + (run_end - run_start) * frac
        g.rect(x - 8, y - CABLE_W / 2 - 3, 16, CABLE_W + 6, fill=SHADE, width=0.8)
        side = 1 if (straight or y >= by - 1) else -1
        by_ = y + side * 36
        if sp.splice_pn in items:
            balloon(g, x, by_, items[sp.splice_pn], (x, y + side * (CABLE_W / 2 + 3)))
        g.text(x + 13, by_ + 3, f"{sp.ref} @ {fmt_length(sp.distance)} {units} FROM {sp.near}", size=7, bold=True)

    if straight:
        (_, fa, ya, _), (_, fb, yb, _) = legs
        total = design.end_to_end_length()
        label = f"{fmt_length(total)} {units}" if total is not None else "LENGTH TBD"
        dimension(g, fa, fb, ya + DIM_OFF, ya + 34, yb + 34, label)
    else:
        for c, face, y, d in legs:
            s = -1 if y < by - 1 else 1
            label = f"{fmt_length(c.length)} {units}" if c.length is not None else "TBD"
            dimension(g, face, bx, y + s * DIM_OFF, y + s * 34, by + s * 12, label)
    return g


# ---------------------------------------------------------------------------
# Wiring diagram
# ---------------------------------------------------------------------------
def _gauge_text(g: str) -> str:
    return f"{g} AWG" if g and g.replace(".", "").isdigit() else g


def wire_tag(w: Wire) -> str:
    return "  ".join(x for x in (w.wire_id, _gauge_text(w.gauge), w.color.upper()) if x)


@dataclass
class _Row:
    pin: str
    signal: str
    wire: Wire | None = None
    end_no: int = 0
    shield_of: str = ""        # group id when this row is a shield drain to a pin


def _ground_symbol(g: Group, x: float, y: float, d: int) -> None:
    """Chassis ground drawn sideways (lead along x) so it fits between pin rows."""
    for k, h in enumerate((9.0, 6.0, 3.0)):
        xx = x + d * k * 2.2
        g.line(xx, y - h / 2, xx, y + h / 2, width=0.7)


def wiring_diagram(design: CableDesign) -> Group:
    g = Group(layer="WIRING")
    shields = Group(layer="SHIELDS")
    left, right = assign_sides(design)
    side_of = {c.ref: 1 for c in left} | {c.ref: -1 for c in right}
    has_groups = any(design.group_members(gr.group_id) for gr in design.groups)

    # Rows per connector: wire ends plus shield drains that land on otherwise unused pins
    rows: dict[str, list[_Row]] = {c.ref: [] for c in design.connectors}
    for w in design.wires:
        for end_no, (ref, pin) in enumerate(((w.from_ref, w.from_pin), (w.to_ref, w.to_pin))):
            if ref in rows:
                rows[ref].append(_Row(pin, w.signal, w, end_no))
    for c in design.connectors:
        for gr, pin in design.shield_pins(c.ref):
            if not any(r.pin == pin for r in rows[c.ref]):
                rows[c.ref].append(_Row(pin, f"{gr.group_id} SHIELD", None, 0, gr.group_id))
    for lst in rows.values():
        lst.sort(key=lambda r: (natural_key(r.pin), natural_key(r.wire.wire_id if r.wire else "")))

    size, row_h = 7.0, 13.0
    tag_end = max([60.0] + [text_width(wire_tag(w), 6.5) + 10 for w in design.wires])
    stub = max(90.0, tag_end + (40 if has_groups else 8))
    oval_off = tag_end + 12

    def table_dims(c: ConnectorEnd):
        rs = rows.get(c.ref, [])
        pin_w = max([30.0] + [text_width(r.pin, size) + 12 for r in rs])
        sig_w = min(170.0, max([80.0] + [text_width(r.signal, size) + 12 for r in rs]))
        head_h = 18 + 10 * sum(1 for x in (c.connector_pn, c.description) if x)
        return pin_w, sig_w, head_h, max(len(rs), 1)

    tw_left = max([sum(table_dims(c)[:2]) for c in left] + [0])
    same = {d: [w for w in design.wires if side_of.get(w.from_ref) == d and side_of.get(w.to_ref) == d] for d in (1, -1)}
    used_splices = [sp for sp in design.splices if any(sp.ref in (w.from_ref, w.to_ref) for w in design.wires)]
    center = max(180 + 6 * (len(same[1]) + len(same[-1])), 240 if used_splices else 0)
    x_left_anchor = tw_left + stub
    x_right_table = tw_left + 2 * stub + center
    x_right_anchor = x_right_table - stub

    anchor: dict[tuple[int, int], tuple[float, float]] = {}       # (id(wire), end) -> stub tip
    row_y: dict[tuple[str, str], float] = {}                        # (ref, pin) -> row centre y
    edges: dict[str, tuple[float, int]] = {}                        # ref -> (table edge x, direction)
    for side, d in ((left, 1), (right, -1)):
        y = 0.0
        for c in side:
            pin_w, sig_w, head_h, nrows = table_dims(c)
            tw = pin_w + sig_w
            x = tw_left - tw if d == 1 else x_right_table
            g.rect(x, y, tw, head_h, fill=HEADER_FILL, width=1.0)
            g.text(x + tw / 2, y + 13, c.ref, size=10, bold=True, anchor="middle")
            ty = y + 13
            for line in (c.connector_pn, c.description.upper()):
                if line:
                    ty += 10
                    g.text(x + tw / 2, ty, fit_text(line, 7, tw - 6), size=7, anchor="middle")
            y += head_h
            g.rect(x, y, tw, 12, fill="#ffffff", width=0.75)
            pin_x, sig_x = (x, x + pin_w) if d == 1 else (x + sig_w, x)
            g.text(pin_x + pin_w / 2, y + 9, "PIN", size=6.5, bold=True, anchor="middle")
            g.text(sig_x + sig_w / 2, y + 9, "SIGNAL", size=6.5, bold=True, anchor="middle")
            y += 12
            rs = rows.get(c.ref, [])
            g.rect(x, y, tw, nrows * row_h, width=0.75)
            div = x + (pin_w if d == 1 else sig_w)
            g.line(div, y - 12, div, y + nrows * row_h, width=0.5)
            if not rs:
                g.text(x + tw / 2, y + 9.5, "NO WIRES", size=size, anchor="middle")
            edge = x + tw if d == 1 else x
            edges[c.ref] = (edge, d)
            for r, row in enumerate(rs):
                ry = y + r * row_h
                if r:
                    g.line(x, ry, x + tw, ry, width=0.35)
                g.text(pin_x + pin_w / 2, ry + 9.5, row.pin, size=size, bold=True, anchor="middle")
                g.text(sig_x + 4, ry + 9.5, fit_text(row.signal, size, sig_w - 8), size=size)
                cy = ry + row_h / 2
                row_y.setdefault((c.ref, row.pin), cy)
                g.circle(edge, cy, 1.3, fill="#000000", width=0.3)
                if row.wire is None:     # shield drain row: dashed stub to the shield lead
                    shields.line(edge, cy, edge + d * (oval_off + 14), cy, width=0.75, dash=(3, 2))
                    shields.text(edge + d * 5, cy - 2.5, f"{row.shield_of} SHLD", size=6.5,
                                 anchor="start" if d == 1 else "end")
                    continue
                tip = edge + d * stub
                g.line(edge, cy, tip, cy, width=0.75)
                g.text(edge + d * 5, cy - 2.5, wire_tag(row.wire), size=6.5, anchor="start" if d == 1 else "end")
                anchor[(id(row.wire), row.end_no)] = (tip, cy)
            y += nrows * row_h + 28

    # Splice nodes in the centre column, near the wires they join
    node: dict[str, tuple[float, float]] = {}
    sx = (x_left_anchor + x_right_anchor) / 2
    wanted = []
    for sp in used_splices:
        ys = [anchor[(id(w), 1 - e)][1] for w in design.wires for e, ref in enumerate((w.from_ref, w.to_ref))
              if ref == sp.ref and (id(w), 1 - e) in anchor]
        wanted.append((sum(ys) / len(ys) if ys else 0.0, sp))
    last = -1e9
    for want, sp in sorted(wanted, key=lambda t: t[0]):
        yy = max(want, last + 30)
        node[sp.ref] = (sx, yy)
        last = yy

    lane = {1: 0, -1: 0}
    for w in design.wires:
        ends = []
        for e, ref in enumerate((w.from_ref, w.to_ref)):
            if ref in node:
                ends.append(node[ref])
            elif (id(w), e) in anchor:
                ends.append(anchor[(id(w), e)])
        if len(ends) != 2:
            continue
        a, b = ends
        sa, sb = side_of.get(w.from_ref), side_of.get(w.to_ref)
        if sa is not None and sa == sb:
            lx = (x_left_anchor + 12 + 6 * lane[1]) if sa == 1 else (x_right_anchor - 12 - 6 * lane[-1])
            lane[sa] += 1
            g.poly([a, (lx, a[1]), (lx, b[1]), b], width=0.75)
        else:
            g.line(a[0], a[1], b[0], b[1], width=0.75)

    for sp in used_splices:
        x, y = node[sp.ref]
        g.circle(x, y, 3.5, fill="#000000", width=0.5)
        g.text(x, y - 7, sp.ref, size=7.5, bold=True, anchor="middle")
        if sp.splice_pn:
            g.text(x, y + 13, sp.splice_pn, size=6, anchor="middle")

    # Twists, shields, jackets and shield terminations at each connector
    for gr in design.groups:
        members = design.group_members(gr.group_id)
        if not members:
            continue
        shielded = gr.shielded or "SHIELD" in gr.kind.upper()
        for ref, (edge, d) in edges.items():
            ys = sorted(row_y[(ref, p)] for w in members for r, p in ((w.from_ref, w.from_pin), (w.to_ref, w.to_pin))
                        if r == ref and (ref, p) in row_y)
            if not ys:
                continue
            ox = edge + d * oval_off
            top, bot = ys[0], ys[-1]
            cy, ry = (top + bot) / 2, (bot - top) / 2 + 5.5
            if gr.twisted and len(ys) >= 2:
                for y1, y2 in zip(ys, ys[1:]):
                    shields.line(ox - 4, y1, ox + 4, y2, width=0.6)
                    shields.line(ox - 4, y2, ox + 4, y1, width=0.6)
            if shielded or gr.jacketed:
                shields.ellipse(ox, cy, 7, ry, width=0.7, dash=(2.5, 1.5) if shielded else None)
            shields.text(ox + d * 9, top - 2.5, gr.group_id, size=5.5, bold=True, anchor="start" if d == 1 else "end")
            if not shielded:
                continue
            kind, pin = parse_shield_term(design.shield_term_at(gr, ref), ref)
            lead_x = ox + d * 7
            if kind == SHIELD_BACKSHELL:
                shields.line(lead_x, cy, lead_x + d * 7, cy, width=0.6, dash=(2, 1.5))
                _ground_symbol(shields, lead_x + d * 7, cy, d)
            elif kind == "PIN" and (ref, pin) in row_y:
                py = row_y[(ref, pin)]
                jx = lead_x + d * 7
                shields.poly([(lead_x, cy), (jx, cy), (jx, py)], width=0.6, dash=(2, 1.5))
                shields.circle(jx, py, 1.4, fill="#000000", width=0.3)
            elif kind == "":
                shields.text(lead_x + d * 3, cy + 2.5, "?", size=7, bold=True, anchor="start" if d == 1 else "end")
    g.add(shields)

    # Legend for the symbols used
    if has_groups:
        x0, y0 = 0.0, g.bounds()[3] + 18
        lg = Group(layer="WIRING")
        lg.text(0, 0, "LEGEND:", size=7, bold=True)
        lg.line(46, -6, 54, 0, width=0.6)
        lg.line(46, 0, 54, -6, width=0.6)
        lg.text(58, 0, "TWISTED", size=6.5)
        lg.ellipse(110, -3, 5, 6, width=0.7, dash=(2.5, 1.5))
        lg.text(119, 0, "SHIELD", size=6.5)
        lg.ellipse(160, -3, 5, 6, width=0.7)
        lg.text(169, 0, "JACKETED CABLE", size=6.5)
        _ground_symbol(lg, 236, -3, 1)
        lg.text(246, 0, "SHIELD TO BACKSHELL", size=6.5)
        lg.text(330, 0, "? = SHIELD TERMINATION NOT SPECIFIED", size=6.5)
        lg.dx, lg.dy = x0, y0
        g.add(lg)
    return g


# ---------------------------------------------------------------------------
# Notes
# ---------------------------------------------------------------------------
def wrap(text: str, size: float, width: float) -> list[str]:
    lines, cur = [], ""
    for word in text.split():
        trial = f"{cur} {word}".strip()
        if text_width(trial, size) <= width or not cur:
            cur = trial
        else:
            lines.append(cur)
            cur = word
    if cur:
        lines.append(cur)
    return lines or [""]


def auto_notes(design: CableDesign, bom: list[BomItem], table_sheets: tuple[int, int], bundles: dict | None = None) -> list[str]:
    def items(cat: str) -> str:
        nums = [str(b.item) for b in bom if b.category == cat]
        return ("ITEM " if len(nums) == 1 else "ITEMS ") + ", ".join(nums) if nums else ""

    first, last = table_sheets
    sheets = f"SHEET {first}" if first == last else f"SHEETS {first} THRU {last}"
    tables = ["WIRE LIST"]
    active_groups = [gr for gr in design.groups if design.group_members(gr.group_id)]
    used_splices = [sp for sp in design.splices if any(sp.ref in (w.from_ref, w.to_ref) for w in design.wires)]
    if active_groups:
        tables.append("WIRE GROUPS")
    if used_splices:
        tables.append("SPLICES")
    tables.append("LABEL SCHEDULE")
    notes = [f"WIRING DIAGRAM ON SHEET 2. {', '.join(tables[:-1])} AND {tables[-1]} ON {sheets}."]
    if any(gr.twisted and not gr.cable_pn for gr in active_groups):
        notes.append("TWIST THE WIRES OF EACH TWISTED GROUP TOGETHER OVER THEIR FULL LENGTH (SEE WIRE GROUPS TABLE).")
    if items("shield"):
        notes.append(f"INSTALL OVERALL SHIELDS ({items('shield')}) OVER SHIELDED GROUPS PER WIRE GROUPS TABLE.")
    if any(gr.shielded for gr in active_groups):
        term = f" USING {items('shield_term')}" if items("shield_term") else ""
        notes.append(f"TERMINATE SHIELDS PER WIRE GROUPS TABLE{term}. INSULATE FLOATING SHIELD ENDS.")
    if items("splice"):
        notes.append(f"INSTALL SPLICES ({items('splice')}) PER SPLICE TABLE.")
    if bundles:
        parts = [f"{ref} {fmt_dia(b.diameter, design)}" for ref, b in bundles.items() if b.diameter]
        if parts:
            notes.append("CALCULATED BUNDLE DIAMETER AT CONNECTORS (REF): " + ", ".join(parts) + ".")
    if items("heatshrink"):
        notes.append(f"RECOVER HEATSHRINK BOOTS ({items('heatshrink')}) OVER BACKSHELL CABLE CLAMPS.")
    if items("label"):
        notes.append(f"INSTALL CABLE IDENTIFICATION LABELS ({items('label')}) ADJACENT TO EACH CONNECTOR, "
                     "MARKED PER LABEL SCHEDULE.")
    if items("wire_label"):
        notes.append(f"INSTALL WIRE MARKERS ({items('wire_label')}) AT EACH END OF EACH WIRE, MARKED WITH WIRE ID.")
    if items("wire_heatshrink"):
        notes.append(f"INSTALL HEATSHRINK SLEEVES ({items('wire_heatshrink')}) AT EACH END OF EACH WIRE.")
    return notes


def notes_block(notes: list[str], width: float) -> Group:
    g = Group(layer="NOTES")
    g.text(0, 10, "NOTES:", size=9, bold=True)
    y = 26.0
    for i, note in enumerate(notes, start=1):
        g.text(0, y, f"{i}.", size=8)
        for line in wrap(note, 8, width - 18):
            g.text(18, y, line, size=8)
            y += 11
        y += 2
    return g


# ---------------------------------------------------------------------------
# Wire list / label schedule rows
# ---------------------------------------------------------------------------
def wire_list_rows(design: CableDesign) -> tuple[list[str], list[list[str]]]:
    headers = ["WIRE", "FROM", "PIN", "TO", "PIN", "SIGNAL", "AWG", "COLOR", "WIRE P/N", "GROUP",
               f"LENGTH ({design.units})", "NOTES"]
    rows = []
    for w in design.wires:
        length = design.wire_length(w)
        gr = design.group(w.group) if w.group else None
        wire_pn = w.wire_pn or (f"({gr.cable_pn})" if gr and gr.cable_pn else "")
        rows.append([w.wire_id, w.from_ref, w.from_pin, w.to_ref, w.to_pin, w.signal, w.gauge, w.color.upper(),
                     wire_pn, w.group, fmt_length(length) if length is not None else "AR", w.notes])
    return headers, rows


def _term_text(design: CableDesign, gr, ref: str) -> str:
    if not ref:
        return ""
    if design.is_splice(ref):
        return f"{ref}: (SPLICE)"
    kind, pin = parse_shield_term(design.shield_term_at(gr, ref), ref)
    if not gr.shielded:
        return ""
    if kind == "PIN":
        return f"{ref}: PIN {pin}"
    return f"{ref}: {kind or 'TBD'}"


def group_rows(design: CableDesign, items: dict[str, int]) -> tuple[list[str], list[list[str]]]:
    headers = ["GROUP", "TYPE", "WIRES", "CABLE P/N", "SHIELD P/N", "SHIELD TERM", "SHIELD (FROM END)",
               "SHIELD (TO END)", "NOTES"]
    rows = []
    for gr in design.groups:
        members = design.group_members(gr.group_id)
        if not members:
            continue
        a, b = design.group_ends(gr.group_id)
        term = gr.shield_term_pn + (f" (ITEM {items[gr.shield_term_pn]})" if gr.shield_term_pn in items else "")
        rows.append([gr.group_id, gr.kind.upper(), compress_refs([w.wire_id for w in members], 40), gr.cable_pn,
                     gr.shield_pn, term, _term_text(design, gr, a), _term_text(design, gr, b) if b != a else "",
                     gr.notes])
    return headers, rows


def splice_rows(design: CableDesign, items: dict[str, int]) -> tuple[list[str], list[list[str]]]:
    headers = ["SPLICE", "ITEM", "SPLICE P/N", "LOCATION", "WIRES", "NOTES"]
    rows = []
    for sp in design.splices:
        wires = [w.wire_id for w in design.wires if sp.ref in (w.from_ref, w.to_ref)]
        if not wires:
            continue
        loc = f"{fmt_length(sp.distance)} {design.units} FROM {sp.near} FACE" if sp.near and sp.distance is not None else "AR"
        rows.append([sp.ref, str(items.get(sp.splice_pn, "")), sp.splice_pn, loc, ", ".join(wires), sp.notes])
    return headers, rows


def _end_text(ref: str, pin: str) -> str:
    return f"{ref}-{pin}" if pin else ref


def label_schedule_rows(design: CableDesign, items: dict[str, int]) -> tuple[list[str], list[list[str]]]:
    headers = ["ITEM", "LABEL P/N", "LEGEND", "LOCATION", "QTY"]
    rows = []
    for c in design.connectors:
        if c.label_pn:
            rows.append([str(items.get(c.label_pn, "")), c.label_pn, c.legend, f"CABLE, ADJACENT TO {c.ref}", "1"])
    for w in design.wires:
        if w.label_pn:
            rows.append([str(items.get(w.label_pn, "")), w.label_pn, w.wire_id,
                         f"WIRE {w.wire_id}, AT {_end_text(w.from_ref, w.from_pin)} AND {_end_text(w.to_ref, w.to_pin)}", "2"])
    return headers, rows


# ---------------------------------------------------------------------------
# Sheet assembly
# ---------------------------------------------------------------------------
def _new_sheet(w: float, h: float, name: str) -> tuple[Sheet, Frame]:
    sheet = Sheet(w, h, name=name)
    border = Group(layer="BORDER")
    frame = draw_frame(border, w, h)
    sheet.root.add(border)
    return sheet, frame


def _heading(g: Group, f: Frame, text: str) -> float:
    g.text(f.x0 + PAD, f.y0 + PAD + 12, text, size=12, bold=True)
    return f.y0 + PAD + 24


def build_drawing(design: CableDesign, sheet_size: str = DEFAULT_SHEET) -> tuple[list[Sheet], list[str]]:
    """Return the drawing sheets and any layout warnings."""
    letter, W, H = SHEET_SIZES[sheet_size]
    bom = build_bom(design)
    items = item_numbers(bom)
    warnings: list[str] = []

    # Sheets 3+: tables (laid out first so sheet 1's notes can reference the sheet numbers)
    table_sheets: list[tuple[Sheet, Frame]] = []
    sheet, f = _new_sheet(W, H, "Wire list")
    y = _heading(sheet.root, f, "WIRE LIST AND LABEL SCHEDULE")
    table_sheets.append((sheet, f))
    region_w = f.x1 - f.x0 - 2 * PAD
    col_top, col_x, col_right = y, 0.0, 0.0     # tables flow down, then into the next column, then onto a new sheet
    for title, (headers, rows), caps, centered in (
        ("WIRE LIST", wire_list_rows(design), [60, 60, 40, 60, 40, 170, 40, 70, 150, 50, 70, 200], frozenset({2, 4, 6, 10})),
        ("WIRE GROUPS AND SHIELDS", group_rows(design, items), [50, 150, 120, 150, 130, 170, 110, 110, 200], frozenset()),
        ("SPLICES", splice_rows(design, items), [50, 36, 150, 200, 200, 200], frozenset({1})),
        ("LABEL SCHEDULE", label_schedule_rows(design, items), [36, 150, 120, 260, 32], frozenset({0, 4})),
    ):
        if not rows:
            continue
        widths = table_widths(headers, rows, caps=caps)
        s = min(1.0, region_w / sum(widths))
        remaining, first = rows, True
        while remaining:
            bottom = f.tb_top - PAD
            capacity = int(((bottom - y) / s - TITLE_H - HEADER_H) // ROW_H)
            if capacity < 1:
                if col_right and col_right + 24 + sum(widths) * s <= region_w:
                    col_x, y = col_right + 24, col_top
                else:
                    sheet, f = _new_sheet(W, H, "Wire list (cont.)")
                    y = col_top = _heading(sheet.root, f, "WIRE LIST AND LABEL SCHEDULE (CONTINUED)")
                    col_x = col_right = 0.0
                    table_sheets.append((sheet, f))
                continue
            chunk, remaining = remaining[:capacity], remaining[capacity:]
            col_right = max(col_right, col_x + sum(widths) * s)
            tg = Group(dx=f.x0 + PAD + col_x, dy=y, scale=s, layer="TABLES")
            used = draw_table(tg, 0, 0, headers, chunk, widths, title=title if first else f"{title} (CONTINUED)",
                              center_cols=centered)
            sheet.root.add(tg)
            y += used * s + 20
            first = False
    n_table = len(table_sheets)
    total = 2 + n_table

    # Sheet 1: assembly, BOM, notes
    s1, f1 = _new_sheet(W, H, "Assembly")
    bom_headers = ["ITEM", "QTY", "UNIT", "PART NUMBER", "DESCRIPTION", "USED ON"]
    bom_rows = [[str(b.item), b.qty_text(), b.unit, b.pn, b.description, compress_refs(b.used_on, 40)] for b in bom]
    bom_widths = table_widths(bom_headers, bom_rows or [[""] * 6], caps=[0, 0, 0, 160, 190, 140])
    bom_g = Group(layer="TABLES")
    draw_table(bom_g, 0, 0, bom_headers, bom_rows or [["", "", "", "NO PARTS ENTERED", "", ""]], bom_widths,
               title="BILL OF MATERIALS", center_cols=frozenset({0, 1, 2}))
    inner_w = f1.x1 - f1.x0
    col_w = max(TB_W, min(sum(bom_widths) + PAD, 0.45 * inner_w))
    bom_box = (f1.x1 - col_w, f1.y0 + PAD, col_w - PAD, f1.tb_top - PAD - (f1.y0 + PAD))
    s1.root.add(bom_g.fit_into(*bom_box, align="right", valign="bottom"))
    if bom_g.scale < 0.6:
        warnings.append("The bill of materials was shrunk to fit sheet 1; consider a larger sheet size.")

    from .drc import run_drc

    notes = design.rendered_notes() + auto_notes(design, bom, (3, total), run_drc(design).bundles)
    notes_w = (f1.x1 - col_w) - f1.x0 - 2 * PAD
    ng = notes_block(notes, notes_w)
    nb = ng.bounds()
    notes_h = nb[3] - nb[1]
    ng.dx, ng.dy = f1.x0 + PAD, f1.y1 - PAD - notes_h
    s1.root.add(ng)

    top = _heading(s1.root, f1, "ASSEMBLY VIEW (NOT TO SCALE)")
    av = assembly_view(design, items)
    # Use whichever is roomier: beside the BOM column, or the full width above the BOM
    bom_top = bom_g.dy + bom_g.bounds()[1] * bom_g.scale
    beside = (f1.x0 + PAD, top + 10, notes_w, (f1.y1 - PAD - notes_h - 20) - (top + 10))
    above = (f1.x0 + PAD, top + 10, f1.x1 - f1.x0 - 2 * PAD, min(bom_top - 20, f1.y1 - PAD - notes_h - 20) - (top + 10))
    bx0, by0, bx1, by1 = av.bounds()
    fit = lambda box: min(box[2] / max(bx1 - bx0, 1), box[3] / max(by1 - by0, 1))  # noqa: E731
    s1.root.add(av.fit_into(*max(beside, above, key=fit), max_scale=1.3))
    if av.scale < 0.45:
        warnings.append("The assembly view was shrunk a lot to fit sheet 1; consider a larger sheet size.")

    # Sheet 2: wiring diagram
    s2, f2 = _new_sheet(W, H, "Wiring diagram")
    top = _heading(s2.root, f2, "WIRING DIAGRAM")
    wd = wiring_diagram(design)
    s2.root.add(wd.fit_into(f2.x0 + PAD, top + 6, f2.x1 - f2.x0 - 2 * PAD, f2.tb_top - PAD - (top + 6),
                            max_scale=1.5, valign="top"))
    if wd.scale < 0.45:
        warnings.append(f"The wiring diagram was scaled to {wd.scale:.0%} to fit; consider a larger sheet size.")

    sheets = [(s1, f1), (s2, f2), *table_sheets]
    for n, (sheet, frame) in enumerate(sheets, start=1):
        tb = Group(layer="TITLE_BLOCK")
        draw_title_block(tb, frame, design, letter, n, total)
        sheet.root.add(tb)
    return [s for s, _ in sheets], warnings
