"""Bill of materials and design checks."""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, field

import pandas as pd

from .model import CableDesign, fmt_length, natural_key

# (category, default description) in BOM order
CATEGORIES = {
    "connector": "CONNECTOR",
    "backshell": "BACKSHELL",
    "heatshrink": "BOOT, HEATSHRINK",
    "label": "LABEL, CABLE IDENTIFICATION",
    "wire": "WIRE",
    "wire_label": "MARKER, WIRE IDENTIFICATION",
    "wire_heatshrink": "SLEEVE, HEATSHRINK",
}


@dataclass
class BomItem:
    item: int
    pn: str
    description: str
    category: str
    qty: float | None          # None = as required (AR)
    unit: str                  # "EA" or the length unit
    used_on: list[str] = field(default_factory=list)

    def qty_text(self) -> str:
        if self.qty is None:
            return "AR"
        return fmt_length(self.qty) if self.unit != "EA" else str(int(self.qty))


def build_bom(design: CableDesign) -> list[BomItem]:
    entries: dict[str, dict] = {}

    def add(pn: str, category: str, qty: float | None, unit: str, used_on: str, desc_hint: str = ""):
        pn = pn.strip()
        if not pn:
            return
        e = entries.setdefault(pn, {"category": category, "qty": 0.0, "unit": unit, "used_on": [], "hints": []})
        e["qty"] = None if (e["qty"] is None or qty is None) else e["qty"] + qty
        if used_on and used_on not in e["used_on"]:
            e["used_on"].append(used_on)
        if desc_hint:
            e["hints"].append(desc_hint)

    for c in design.connectors:
        add(c.connector_pn, "connector", 1, "EA", c.ref)
        add(c.backshell_pn, "backshell", 1, "EA", c.ref)
        add(c.heatshrink_pn, "heatshrink", 1, "EA", c.ref)
        add(c.label_pn, "label", 1, "EA", c.ref)
    for w in design.wires:
        hint = " ".join(x for x in (f"{w.gauge} AWG" if w.gauge else "", w.color) if x)
        add(w.wire_pn, "wire", design.wire_length(w), design.units, w.wire_id, hint)
    for w in design.wires:
        add(w.label_pn, "wire_label", 2, "EA", w.wire_id)
    for w in design.wires:
        add(w.heatshrink_pn, "wire_heatshrink", 2, "EA", w.wire_id)

    order = list(CATEGORIES)
    items: list[BomItem] = []
    for pn, e in sorted(entries.items(), key=lambda kv: order.index(kv[1]["category"])):
        desc = design.part_descriptions.get(pn, "").strip()
        if not desc:
            desc = CATEGORIES[e["category"]]
            if e["category"] == "wire":
                gauges = {h.split(" AWG")[0] for h in e["hints"] if "AWG" in h}
                if len(gauges) == 1:
                    desc += f", {gauges.pop()} AWG"
        items.append(BomItem(len(items) + 1, pn, desc, e["category"], e["qty"], e["unit"], e["used_on"]))
    return items


def item_numbers(bom: list[BomItem]) -> dict[str, int]:
    return {b.pn: b.item for b in bom}


def compress_refs(refs: list[str], limit: int = 60) -> str:
    """'W1, W2, W3, W5' -> 'W1-W3, W5' (for wires with a common prefix)."""
    groups: list[list[str]] = []
    for r in sorted(refs, key=natural_key):
        prev = groups[-1][-1] if groups else None
        if prev and _next_in_sequence(prev, r):
            groups[-1].append(r)
        else:
            groups.append([r])
    text = ", ".join(", ".join(g) if len(g) < 3 else f"{g[0]}-{g[-1]}" for g in groups)
    return text if len(text) <= limit else text[: limit - 3].rsplit(",", 1)[0] + ", ..."


def _next_in_sequence(a: str, b: str) -> bool:
    import re

    ma, mb = re.fullmatch(r"(.*?)(\d+)", a), re.fullmatch(r"(.*?)(\d+)", b)
    return bool(ma and mb and ma.group(1) == mb.group(1) and int(mb.group(2)) == int(ma.group(2)) + 1)


def bom_dataframe(bom: list[BomItem], units: str) -> pd.DataFrame:
    return pd.DataFrame(
        [{"Item": b.item, "Qty": b.qty_text(), "Unit": b.unit, "Part Number": b.pn,
          "Description": b.description, "Used On": ", ".join(b.used_on)} for b in bom],
        columns=["Item", "Qty", "Unit", "Part Number", "Description", "Used On"],
    )


def check_design(design: CableDesign) -> list[str]:
    """Return human-readable warnings about gaps or likely mistakes."""
    msgs: list[str] = []
    if not design.wires:
        msgs.append("The wire list is empty.")
    for wid, n in Counter(w.wire_id for w in design.wires).items():
        if n > 1:
            msgs.append(f"Wire ID {wid} is used {n} times.")
    refs = {c.ref for c in design.connectors}
    for w in design.wires:
        for ref in (w.from_ref, w.to_ref):
            if ref not in refs:
                msgs.append(f"Wire {w.wire_id}: connector {ref} isn't in the connector table.")
        if not w.from_pin or not w.to_pin:
            msgs.append(f"Wire {w.wire_id}: missing a pin number.")
        if not w.wire_pn:
            msgs.append(f"Wire {w.wire_id}: no wire part number.")
    pins: dict[tuple[str, str], list[str]] = defaultdict(list)
    for w in design.wires:
        pins[(w.from_ref, w.from_pin)].append(w.wire_id)
        pins[(w.to_ref, w.to_pin)].append(w.wire_id)
    for (ref, pin), ids in sorted(pins.items(), key=lambda kv: (natural_key(kv[0][0]), natural_key(kv[0][1]))):
        if pin and len(ids) > 1:
            msgs.append(f"{ref} pin {pin} has {len(ids)} wires ({', '.join(ids)}); confirm the contact accepts a double crimp or add a splice.")
    used = {r for w in design.wires for r in (w.from_ref, w.to_ref)}
    for c in design.connectors:
        if not c.connector_pn:
            msgs.append(f"{c.ref}: no connector part number.")
        if c.ref not in used:
            msgs.append(f"{c.ref}: no wires connect to this connector.")
    if any(design.wire_length(w) is None for w in design.wires):
        if len(design.connectors) == 2:
            msgs.append("Some wire lengths are unknown: enter the overall cable length, connector leg lengths, or per-wire lengths. Unknown quantities show as AR.")
        else:
            msgs.append("Some wire lengths are unknown: enter each connector's length to the breakout, or per-wire lengths. Unknown quantities show as AR.")
    return msgs
