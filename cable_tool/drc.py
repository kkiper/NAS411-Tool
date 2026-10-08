"""Design rule check (DRC).

Uses part parameters from the parts library to check that:

* each wire's gauge is within its connector contact's range (and splice / shield termination ranges),
* wire insulation OD is within the contact's sealing range,
* the connector has enough contacts for the pins used,
* the wire bundle at each connector fits the backshell clamp, heatshrink boot and ID label,
* wire markers and wire sleeves fit the wire,
* twisted / shielded groups are consistent and their shields are terminated,
* part types match where they are used, and parts missing from the library are listed.

Diameters are inches internally; messages use the design's units.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field

import pandas as pd

from .bom import check_design
from .library import Part, bundle_diameter, circular_mils, parse_awg, typical_od
from .model import SHIELD_BACKSHELL, SHIELD_FLOAT, CableDesign, Wire, WireGroup, fmt_dia, parse_shield_term

ERROR, WARNING, INFO = "ERROR", "WARNING", "INFO"
SEVERITY_ORDER = {ERROR: 0, WARNING: 1, INFO: 2}
DEFAULT_SHIELD_WALL = 0.012     # in, braid over a built-up group when the library gives no Wall
DEFAULT_JACKET_WALL = 0.010     # in, jacket over a cable's conductors when the library gives no OD

# Where a part number is used -> library types that make sense there
EXPECTED_TYPES = {
    "connector": {"connector"},
    "contact": {"contact"},
    "backshell": {"backshell"},
    "boot": {"heatshrink"},
    "label": {"label", "marker", "heatshrink"},
    "wire": {"wire"},
    "cable": {"cable"},
    "wire marker": {"marker", "label", "heatshrink"},
    "wire sleeve": {"heatshrink", "marker"},
    "splice": {"splice"},
    "shield termination": {"shield_term", "splice"},
    "shield": {"shield"},
}


@dataclass
class Finding:
    severity: str
    rule: str
    item: str
    message: str


@dataclass
class Bundle:
    ref: str
    diameter: float | None        # inches
    count: int = 0                # conductors / cables in the bundle
    estimated: int = 0            # ODs estimated from AWG
    unknown: int = 0              # ODs that couldn't be found or estimated


@dataclass
class DrcReport:
    findings: list[Finding] = field(default_factory=list)
    bundles: dict[str, Bundle] = field(default_factory=dict)

    def add(self, severity: str, rule: str, item: str, message: str) -> None:
        self.findings.append(Finding(severity, rule, item, message))

    @property
    def errors(self) -> list[Finding]:
        return [f for f in self.findings if f.severity == ERROR]

    @property
    def warnings(self) -> list[Finding]:
        return [f for f in self.findings if f.severity == WARNING]

    def counts(self) -> dict[str, int]:
        out = {ERROR: 0, WARNING: 0, INFO: 0}
        for f in self.findings:
            out[f.severity] += 1
        return out

    def sorted(self) -> list[Finding]:
        return sorted(self.findings, key=lambda f: SEVERITY_ORDER[f.severity])

    def to_dataframe(self) -> pd.DataFrame:
        return pd.DataFrame([{"Severity": f.severity, "Rule": f.rule, "Item": f.item, "Message": f.message}
                             for f in self.sorted()], columns=["Severity", "Rule", "Item", "Message"])


def _awg(x: float | None) -> str:
    if x is None:
        return "?"
    return f"{int(x)}" if float(x).is_integer() else f"{x:g}"


def _range_text(lo, hi, fmt) -> str:
    if lo is not None and hi is not None:
        return f"{fmt(lo)} to {fmt(hi)}"
    return f"min {fmt(lo)}" if lo is not None else f"max {fmt(hi)}"


class _Checker:
    def __init__(self, design: CableDesign):
        self.d = design
        self.lib = design.library
        self.r = DrcReport()
        self.missing: dict[str, set[str]] = defaultdict(set)     # pn -> uses
        self.no_param: dict[tuple[str, str], set[str]] = defaultdict(set)  # (pn, parameter) -> items

    # helpers -------------------------------------------------------------
    def dia(self, v: float | None) -> str:
        return fmt_dia(v, self.d)

    def part(self, pn: str, use: str, item: str) -> Part | None:
        if not pn:
            return None
        p = self.lib.get(pn)
        if p is None:
            self.missing[pn].add(use)
            return None
        if p.type and p.type not in EXPECTED_TYPES.get(use, {p.type}):
            self.r.add(WARNING, "Part type", item, f"{pn} is a '{p.type}' in the library but is used as a {use}.")
        return p

    def wire_awg(self, w: Wire) -> float | None:
        awg = parse_awg(w.gauge)
        if awg is None:
            p = self.lib.get(w.wire_pn)
            if p and p.awg is not None:
                awg = p.awg
        if awg is None and w.group:
            g = self.d.group(w.group)
            p = self.lib.get(g.cable_pn) if g else None
            if p and p.awg is not None:
                awg = p.awg
        return awg

    def wire_od(self, w: Wire) -> tuple[float | None, bool]:
        """(OD in inches, estimated?)"""
        p = self.lib.get(w.wire_pn)
        if p and p.od:
            return p.od, False
        od = typical_od(self.wire_awg(w))
        return od, od is not None

    def group_od(self, g: WireGroup) -> tuple[float | None, int, int]:
        """(OD, estimated count, unknown count) of a cable or built-up shielded group."""
        members = self.d.group_members(g.group_id)
        if g.cable_pn:
            p = self.lib.get(g.cable_pn)
            if p and p.od:
                return p.od, 0, 0
        ods, est, unk = [], 0, 0
        for w in members:
            od, e = self.wire_od(w)
            if od is None:
                unk += 1
            else:
                ods.append(od)
                est += e
        core = bundle_diameter(ods)
        if core is None:
            return None, est, unk
        if g.cable_pn:
            return core + 2 * DEFAULT_JACKET_WALL, est + 1, unk
        if g.shielded:
            sp = self.lib.get(g.shield_pn)
            wall = sp.wall if sp and sp.wall else DEFAULT_SHIELD_WALL
            return core + 2 * wall, est + (0 if sp and sp.wall else 1), unk
        return core, est, unk

    def fit(self, rule: str, item: str, what: str, pn: str, value: float | None, part: Part | None,
            over_msg: str, under_msg: str, under_severity: str = WARNING) -> None:
        """Check a diameter against a part's Dia Min / Dia Max."""
        if part is None or value is None:
            return
        lo, hi = part.dia_range
        if lo is None and hi is None:
            self.no_param[(pn, "Dia Min/Max")].add(item)
            return
        if hi is not None and value > hi:
            self.r.add(ERROR, rule, item, f"{what} {self.dia(value)} is larger than {pn} max {self.dia(hi)}: {over_msg}")
        elif lo is not None and value < lo:
            self.r.add(under_severity, rule, item, f"{what} {self.dia(value)} is smaller than {pn} min {self.dia(lo)}: {under_msg}")

    # rules -----------------------------------------------------------------
    def completeness(self) -> None:
        for m in check_design(self.d):
            self.r.add(WARNING, "Completeness", "", m)

    def wire_parts(self) -> None:
        for w in self.d.wires:
            p = self.part(w.wire_pn, "wire", w.wire_id)
            given = parse_awg(w.gauge)
            if p and p.awg is not None and given is not None and p.awg != given:
                self.r.add(WARNING, "Wire gauge", w.wire_id,
                           f"Wire list says {_awg(given)} AWG but {w.wire_pn} is {_awg(p.awg)} AWG in the library.")
            od, _ = self.wire_od(w)
            for pn, use, label in ((w.label_pn, "wire marker", "marker"), (w.heatshrink_pn, "wire sleeve", "sleeve")):
                part = self.part(pn, use, w.wire_id)
                self.fit("Wire marker/sleeve fit", w.wire_id, "Wire OD", pn, od, part,
                         f"the {label} won't fit over the wire.", f"the {label} won't shrink down onto the wire.")

    def contacts(self) -> None:
        for c in self.d.connectors:
            conn = self.part(c.connector_pn, "connector", c.ref)
            contact_pn = self.d.contact_pn(c.ref)
            contact = self.part(contact_pn, "contact", c.ref)
            # The contact's range wins; a connector row may carry the range when contacts aren't separate.
            source = contact if contact and (contact.awg_range or any(contact.dia_range)) else conn
            source_pn = contact_pn if source is contact else c.connector_pn
            pins = self.d.pins_used(c.ref)
            if conn and conn.contacts and len(pins) > conn.contacts:
                self.r.add(ERROR, "Contact count", c.ref,
                           f"{len(pins)} pins are used but {c.connector_pn} has {int(conn.contacts)} contacts.")
            if not pins:
                continue
            awg_range = source.awg_range if source else None
            if source and awg_range is None:
                self.no_param[(source_pn, "AWG Min/Max")].add(c.ref)
            for w in self.d.wires:
                for ref, pin in ((w.from_ref, w.from_pin), (w.to_ref, w.to_pin)):
                    if ref != c.ref:
                        continue
                    where = f"{c.ref}-{pin}"
                    awg = self.wire_awg(w)
                    if awg_range and awg is not None and not (awg_range[0] <= awg <= awg_range[1]):
                        self.r.add(ERROR, "Contact wire size", where,
                                   f"Wire {w.wire_id} is {_awg(awg)} AWG; {source_pn} accepts "
                                   f"{_awg(awg_range[0])} to {_awg(awg_range[1])} AWG.")
                    elif awg_range and awg is None:
                        self.r.add(WARNING, "Contact wire size", where, f"Wire {w.wire_id} has no gauge; size not checked.")
                    od, est = self.wire_od(w)
                    if source and any(source.dia_range) and od is not None:
                        lo, hi = source.dia_range
                        if (hi is not None and od > hi) or (lo is not None and od < lo):
                            self.r.add(WARNING, "Contact sealing range", where,
                                       f"Wire {w.wire_id} OD {self.dia(od)}{' (est.)' if est else ''} is outside "
                                       f"{source_pn} sealing range {_range_text(lo, hi, self.dia)}; the grommet may not seal.")
            for g, pin in self.d.shield_pins(c.ref):
                wires_on_pin = [w for w in self.d.wires
                                if (w.from_ref, w.from_pin) == (c.ref, pin) or (w.to_ref, w.to_pin) == (c.ref, pin)]
                others = [w.wire_id for w in wires_on_pin if w.group != g.group_id]
                if others:
                    self.r.add(WARNING, "Shield termination", f"{c.ref}-{pin}",
                               f"Shield of {g.group_id} lands on {c.ref}-{pin}, which also carries {', '.join(others)}.")

    def splices(self) -> None:
        for sp in self.d.splices:
            wires = [w for w in self.d.wires if sp.ref in (w.from_ref, w.to_ref)]
            if not wires:
                continue
            part = self.part(sp.splice_pn, "splice", sp.ref)
            if part is None:
                continue
            rng = part.awg_range
            awgs = [(w, self.wire_awg(w)) for w in wires]
            if rng:
                for w, awg in awgs:
                    if awg is not None and not (rng[0] <= awg <= rng[1]):
                        self.r.add(ERROR, "Splice wire size", sp.ref,
                                   f"Wire {w.wire_id} is {_awg(awg)} AWG; {sp.splice_pn} accepts {_awg(rng[0])} to {_awg(rng[1])} AWG.")
            if part.cma_min is not None or part.cma_max is not None:
                if all(a is not None for _, a in awgs):
                    total = sum(circular_mils(a) for _, a in awgs)
                    if part.cma_max is not None and total > part.cma_max:
                        self.r.add(ERROR, "Splice wire size", sp.ref,
                                   f"Total wire area {total:,.0f} CMA ({len(wires)} wires) exceeds {sp.splice_pn} max {part.cma_max:,.0f} CMA.")
                    elif part.cma_min is not None and total < part.cma_min:
                        self.r.add(ERROR, "Splice wire size", sp.ref,
                                   f"Total wire area {total:,.0f} CMA is below {sp.splice_pn} min {part.cma_min:,.0f} CMA; the crimp won't hold. Add a filler wire or pick a smaller splice.")
            if not rng and part.cma_min is None and part.cma_max is None:
                self.no_param[(sp.splice_pn, "AWG or CMA range")].add(sp.ref)

    def groups(self) -> None:
        for g in self.d.groups:
            members = self.d.group_members(g.group_id)
            if not members:
                self.r.add(WARNING, "Groups", g.group_id, "No wires are assigned to this group.")
                continue
            kind = g.kind.upper()
            expect = 2 if "PAIR" in kind else 3 if "TRIPLE" in kind else 4 if "QUAD" in kind else None
            if expect and len(members) != expect:
                self.r.add(WARNING, "Groups", g.group_id, f"{g.kind.title()} has {len(members)} wires ({', '.join(w.wire_id for w in members)}).")
            ends = {frozenset((w.from_ref, w.to_ref)) for w in members}
            if len(ends) > 1:
                self.r.add(WARNING, "Groups", g.group_id,
                           "Wires in this group don't all run between the same two connectors/splices; the twist or shield can't stay intact.")
            if g.cable_pn:
                cable = self.part(g.cable_pn, "cable", g.group_id)
                if cable and cable.conductors and int(cable.conductors) != len(members):
                    self.r.add(WARNING, "Groups", g.group_id,
                               f"{g.cable_pn} has {int(cable.conductors)} conductors but {len(members)} wires use it.")
                if cable and cable.awg is not None:
                    for w in members:
                        given = parse_awg(w.gauge)
                        if given is not None and given != cable.awg:
                            self.r.add(WARNING, "Wire gauge", w.wire_id,
                                       f"Wire list says {_awg(given)} AWG but cable {g.cable_pn} conductors are {_awg(cable.awg)} AWG.")
            if g.shield_pn:
                self.part(g.shield_pn, "shield", g.group_id)
            if not (g.shielded or (g.cable_pn and "SHIELD" in kind)):
                if g.shield_pn or g.term_from or g.term_to:
                    self.r.add(WARNING, "Shield termination", g.group_id,
                               f"Shield data is given but the group type '{g.kind}' isn't shielded.")
                continue
            term_part = self.part(g.shield_term_pn, "shield termination", g.group_id)
            od, _, _ = self.group_od(g)
            for ref in dict.fromkeys(self.d.group_ends(g.group_id)):
                if self.d.is_splice(ref):
                    continue
                kind_at, pin = parse_shield_term(self.d.shield_term_at(g, ref), ref)
                if not kind_at:
                    self.r.add(WARNING, "Shield termination", f"{g.group_id}@{ref}",
                               f"Shield termination at {ref} isn't specified (BACKSHELL, FLOAT, or a pin).")
                elif kind_at in (SHIELD_BACKSHELL, "PIN") and not g.shield_term_pn:
                    self.r.add(INFO, "Shield termination", f"{g.group_id}@{ref}",
                               f"Shield is terminated at {ref} ({kind_at.lower()}) but no shield termination P/N is given.")
                if kind_at in (SHIELD_BACKSHELL, "PIN") and term_part:
                    self.fit("Shield termination", f"{g.group_id}@{ref}", "Shielded group OD", g.shield_term_pn, od,
                             term_part, "the termination won't fit over the shield.",
                             "the termination is too large to make a good shield connection.")
            kinds = [parse_shield_term(self.d.shield_term_at(g, r), r)[0] for r in dict.fromkeys(self.d.group_ends(g.group_id))]
            if kinds and all(k == SHIELD_FLOAT for k in kinds):
                self.r.add(WARNING, "Shield termination", g.group_id, "Shield floats at both ends, so it gives no shielding.")

    def bundles(self) -> None:
        for c in self.d.connectors:
            members = [w for w in self.d.wires if c.ref in (w.from_ref, w.to_ref)]
            if not members:
                continue
            ods, est, unk, count = [], 0, 0, 0
            seen_groups = set()
            for w in members:
                g = self.d.group(w.group) if w.group else None
                if g and (g.cable_pn or g.shielded):
                    if g.group_id in seen_groups:
                        continue
                    seen_groups.add(g.group_id)
                    od, e, u = self.group_od(g)
                    est += e
                    unk += u
                else:
                    od, e = self.wire_od(w)
                    est += e
                    unk += od is None
                count += 1
                if od is not None:
                    ods.append(od)
            dia = bundle_diameter(ods)
            b = Bundle(c.ref, dia, count, est, unk)
            self.r.bundles[c.ref] = b
            if dia is None:
                self.r.add(WARNING, "Bundle diameter", c.ref, "Bundle diameter can't be calculated: no wire gauges or ODs.")
                continue
            basis = f"{count} wire(s)/cable(s)"
            if est:
                basis += f", {est} OD(s) estimated from AWG"
            if unk:
                basis += f", {unk} unknown (so this is a lower bound)"
            self.r.add(INFO, "Bundle diameter", c.ref, f"Bundle at {c.ref} is {self.dia(dia)} ({basis}).")
            what = f"Bundle at {c.ref}"
            self.fit("Backshell fit", c.ref, what, c.backshell_pn, dia, self.part(c.backshell_pn, "backshell", c.ref),
                     "the cable won't pass through the backshell clamp.",
                     "the clamp won't grip; build up the bundle with tape or sleeving, or use a smaller clamp.")
            self.fit("Boot fit", c.ref, what, c.heatshrink_pn, dia, self.part(c.heatshrink_pn, "boot", c.ref),
                     "the boot won't slide over the cable.", "the boot won't shrink down onto the cable.")
            self.fit("Label fit", c.ref, what, c.label_pn, dia, self.part(c.label_pn, "label", c.ref),
                     "the label sleeve won't fit over the cable.", "the label sleeve won't shrink down onto the cable.")

    def library_coverage(self) -> None:
        if not len(self.lib):
            self.r.add(INFO, "Parts library", "", "No parts library loaded, so size and fit checks were skipped.")
            return
        for pn, uses in sorted(self.missing.items()):
            self.r.add(INFO, "Parts library", pn, f"Not in the parts library (used as {', '.join(sorted(uses))}); its checks were skipped.")
        for (pn, param), items in sorted(self.no_param.items()):
            self.r.add(INFO, "Parts library", pn, f"No {param} in the library; check skipped for {', '.join(sorted(items))}.")

    def run(self) -> DrcReport:
        self.completeness()
        self.wire_parts()
        self.contacts()
        self.splices()
        self.groups()
        self.bundles()
        self.library_coverage()
        return self.r


def run_drc(design: CableDesign) -> DrcReport:
    return _Checker(design).run()


__all__ = ["ERROR", "INFO", "WARNING", "Bundle", "DrcReport", "Finding", "run_drc", "SHIELD_FLOAT"]
