"""Bill of materials and design checks."""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, field

import pandas as pd

from .model import SHIELD_BACKSHELL, CableDesign, fmt_length, natural_key, parse_shield_term

# (category, default description) in BOM order
CATEGORIES = {
    "connector": "CONNECTOR",
    "contact": "CONTACT",
    "backshell": "BACKSHELL",
    "heatshrink": "BOOT, HEATSHRINK",
    "label": "LABEL, CABLE IDENTIFICATION",
    "cable": "CABLE",
    "wire": "WIRE",
    "shield": "SHIELD, BRAID",
    "shield_term": "SHIELD TERMINATION",
    "splice": "SPLICE",
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
        pins = design.pins_used(c.ref)
        if pins:
            add(design.contact_pn(c.ref), "contact", len(pins), "EA", c.ref)
        add(c.backshell_pn, "backshell", 1, "EA", c.ref)
        add(c.heatshrink_pn, "heatshrink", 1, "EA", c.ref)
        add(c.label_pn, "label", 1, "EA", c.ref)

    cabled = set()
    for g in design.groups:
        if not design.group_members(g.group_id):
            continue
        length = design.group_length(g.group_id)
        if g.cable_pn:
            add(g.cable_pn, "cable", length, design.units, g.group_id)
            cabled.add(g.group_id)
        add(g.shield_pn, "shield", length, design.units, g.group_id)
        for ref in dict.fromkeys(design.group_ends(g.group_id)):
            kind, _ = parse_shield_term(design.shield_term_at(g, ref), ref)
            if kind in (SHIELD_BACKSHELL, "PIN"):
                add(g.shield_term_pn, "shield_term", 1, "EA", f"{g.group_id}@{ref}")
    for w in design.wires:
        if w.group in cabled:
            continue   # conductor of a cable already counted by length
        hint = " ".join(x for x in (f"{w.gauge} AWG" if w.gauge else "", w.color) if x)
        add(w.wire_pn, "wire", design.wire_length(w), design.units, w.wire_id, hint)
    for sp in design.splices:
        if any(sp.ref in (w.from_ref, w.to_ref) for w in design.wires):
            add(sp.splice_pn, "splice", 1, "EA", sp.ref)
    for w in design.wires:
        add(w.label_pn, "wire_label", 2, "EA", w.wire_id)
    for w in design.wires:
        add(w.heatshrink_pn, "wire_heatshrink", 2, "EA", w.wire_id)

    order = list(CATEGORIES)
    items: list[BomItem] = []
    for pn, e in sorted(entries.items(), key=lambda kv: order.index(kv[1]["category"])):
        desc = design.description(pn)
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
    cabled = {g.group_id for g in design.groups if g.cable_pn}
    for w in design.wires:
        for ref, pin in ((w.from_ref, w.from_pin), (w.to_ref, w.to_pin)):
            if design.is_splice(ref):
                continue
            if ref not in refs:
                msgs.append(f"Wire {w.wire_id}: connector {ref} isn't in the connector table.")
            if not pin:
                msgs.append(f"Wire {w.wire_id}: missing a pin number at {ref}.")
        if not w.wire_pn and w.group not in cabled:
            msgs.append(f"Wire {w.wire_id}: no wire part number.")
    pins: dict[tuple[str, str], list[str]] = defaultdict(list)
    for w in design.wires:
        for ref, pin in ((w.from_ref, w.from_pin), (w.to_ref, w.to_pin)):
            if not design.is_splice(ref):
                pins[(ref, pin)].append(w.wire_id)
    for (ref, pin), ids in sorted(pins.items(), key=lambda kv: (natural_key(kv[0][0]), natural_key(kv[0][1]))):
        if pin and len(ids) > 1:
            msgs.append(f"{ref} pin {pin} has {len(ids)} wires ({', '.join(ids)}); confirm the contact accepts a double crimp or add a splice.")
    for sp in design.splices:
        n = sum((w.from_ref == sp.ref) + (w.to_ref == sp.ref) for w in design.wires)
        if n == 0:
            msgs.append(f"Splice {sp.ref}: no wires connect to it.")
        elif n == 1:
            msgs.append(f"Splice {sp.ref}: only one wire connects to it.")
        if not sp.splice_pn and n:
            msgs.append(f"Splice {sp.ref}: no splice part number.")
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
