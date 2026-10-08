"""Data model for a cable assembly: connector ends, wires, title block, notes."""

from __future__ import annotations

import re
from dataclasses import dataclass, field


@dataclass
class ConnectorEnd:
    """One end of the cable: a connector plus the hardware that goes with it."""

    ref: str                      # reference designator, e.g. P1, J2
    connector_pn: str = ""
    backshell_pn: str = ""
    heatshrink_pn: str = ""       # boot / transition shrink over the backshell
    label_pn: str = ""            # identification label/marker sleeve on the cable near this end
    label_text: str = ""          # legend printed on the label; defaults to the ref designator
    length: float | None = None   # distance from the connector face to the breakout (or the far end)
    description: str = ""         # e.g. "TO FLIGHT COMPUTER"

    @property
    def legend(self) -> str:
        return self.label_text or self.ref


@dataclass
class Wire:
    wire_id: str
    from_ref: str
    from_pin: str
    to_ref: str
    to_pin: str
    signal: str = ""
    gauge: str = ""
    color: str = ""
    wire_pn: str = ""
    length: float | None = None
    label_pn: str = ""            # wire marker, one at each end
    heatshrink_pn: str = ""       # e.g. ID / insulation sleeve, one at each end
    notes: str = ""


@dataclass
class TitleBlock:
    title: str = "CABLE ASSEMBLY"
    drawing_number: str = ""
    revision: str = "-"
    company: str = ""
    drawn_by: str = ""
    checked_by: str = ""
    date: str = ""
    scale: str = "NONE"


UNIT_NAMES = {"IN": "INCHES", "MM": "MILLIMETERS", "CM": "CENTIMETERS", "FT": "FEET", "M": "METERS"}

DEFAULT_NOTES = [
    "INTERPRET DRAWING PER ASME Y14.100.",
    "DIMENSIONS ARE IN {UNITS_NAME}. LENGTH TOLERANCE ±{TOL} {UNITS}.",
    "WIRE PER WIRE LIST. TERMINATE CONTACTS PER CONNECTOR MANUFACTURER'S INSTRUCTIONS.",
    "100% CONTINUITY AND ISOLATION TEST PER WIRE LIST.",
]


@dataclass
class CableDesign:
    title_block: TitleBlock = field(default_factory=TitleBlock)
    connectors: list[ConnectorEnd] = field(default_factory=list)
    wires: list[Wire] = field(default_factory=list)
    notes: list[str] = field(default_factory=lambda: list(DEFAULT_NOTES))
    part_descriptions: dict[str, str] = field(default_factory=dict)
    units: str = "IN"
    tolerance: str = "0.5"
    overall_length: float | None = None   # used for two-connector cables when ends have no lengths

    def connector(self, ref: str) -> ConnectorEnd | None:
        for c in self.connectors:
            if c.ref == ref:
                return c
        return None

    def leg_length(self, ref: str) -> float | None:
        c = self.connector(ref)
        return c.length if c else None

    def end_to_end_length(self) -> float | None:
        """Overall length of a two-connector cable."""
        if len(self.connectors) != 2:
            return None
        if self.overall_length:
            return self.overall_length
        a, b = (c.length for c in self.connectors)
        return a + b if a is not None and b is not None else None

    def wire_length(self, wire: Wire) -> float | None:
        """Wire cut length: explicit value, otherwise the cable path between its two connectors."""
        if wire.length is not None:
            return wire.length
        if wire.from_ref == wire.to_ref:
            return None
        if len(self.connectors) == 2 and {wire.from_ref, wire.to_ref} == {c.ref for c in self.connectors}:
            return self.end_to_end_length()
        a, b = self.leg_length(wire.from_ref), self.leg_length(wire.to_ref)
        return a + b if a is not None and b is not None else None

    def rendered_notes(self) -> list[str]:
        name = UNIT_NAMES.get(self.units.upper(), self.units)
        return [n.replace("{UNITS_NAME}", name).replace("{UNITS}", self.units).replace("{TOL}", self.tolerance)
                for n in self.notes if n.strip()]


def natural_key(s: str):
    """Sort key so that pin 2 comes before pin 10 and A before B."""
    return [(0, int(t), "") if t.isdigit() else (1, 0, t.upper()) for t in re.findall(r"\d+|\D+", str(s))]


def fmt_length(value: float | None) -> str:
    if value is None:
        return ""
    return f"{value:.2f}".rstrip("0").rstrip(".") if value != int(value) else f"{int(value)}"
