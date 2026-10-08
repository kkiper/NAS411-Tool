"""Read wiring lists / connector tables (CSV or Excel) and save/load whole projects.

Column headers are matched loosely (case, spaces and punctuation are ignored), and title rows
above the header are skipped, so most existing wiring lists load without editing.
"""

from __future__ import annotations

import io
import math
import re
from pathlib import Path

import pandas as pd

from .library import LIBRARY_COLUMNS, PartsLibrary, library_from_dataframe
from .model import CableDesign, ConnectorEnd, Splice, Wire, WireGroup, natural_key

# field -> accepted header spellings (normalised: lowercase alphanumerics only)
WIRE_COLUMNS: dict[str, list[str]] = {
    "wire_id": ["wireid", "wire", "wireno", "wirenumber", "wirenum", "id", "circuit", "ckt", "circuitid", "wiretag"],
    "from_ref": ["from", "fromconn", "fromconnector", "fromref", "fromrefdes", "fromdesignator", "source", "conna", "end1", "fromplug"],
    "from_pin": ["frompin", "fromcontact", "frompos", "fromcavity", "pina", "end1pin", "sourcepin"],
    "to_ref": ["to", "toconn", "toconnector", "toref", "torefdes", "todesignator", "destination", "dest", "connb", "end2", "toplug"],
    "to_pin": ["topin", "tocontact", "topos", "tocavity", "pinb", "end2pin", "destpin", "destinationpin"],
    "signal": ["signal", "signalname", "function", "net", "netname", "description", "name"],
    "gauge": ["gauge", "awg", "wiregauge", "wiresize", "size", "ga"],
    "color": ["color", "colour", "wirecolor", "wirecolour", "insulationcolor"],
    "wire_pn": ["wirepn", "wirepartnumber", "wirepartno", "wiretype", "pn", "partnumber", "partno", "cablepn", "conductorpn"],
    "length": ["length", "wirelength", "cutlength", "len"],
    "label_pn": ["labelpn", "wirelabelpn", "markerpn", "wiremarkerpn", "wiremarker", "label", "marker", "wirelabel"],
    "heatshrink_pn": ["heatshrinkpn", "wireheatshrinkpn", "shrinkpn", "hspn", "sleevepn", "sleeve", "heatshrink"],
    "notes": ["notes", "note", "remarks", "comment", "comments"],
    "group": ["group", "groupid", "pair", "twistedpair", "twistgroup", "shieldgroup", "cable", "cableid", "bundle", "twist"],
}

CONNECTOR_COLUMNS: dict[str, list[str]] = {
    "ref": ["ref", "refdes", "reference", "designator", "connector", "conn", "plug", "end"],
    "description": ["description", "desc", "destination", "goesto", "mateswith", "function", "name"],
    "connector_pn": ["connectorpn", "connpn", "connectorpartnumber", "connectorpartno", "partnumber", "pn", "partno"],
    "backshell_pn": ["backshellpn", "backshell", "backshellpartnumber", "adapterpn", "strainreliefpn"],
    "heatshrink_pn": ["heatshrinkpn", "heatshrink", "bootpn", "boot", "shrinkpn", "shrinkboot", "transitionpn"],
    "label_pn": ["labelpn", "label", "markerpn", "marker", "labelpartnumber", "idlabelpn"],
    "label_text": ["labeltext", "legend", "labellegend", "marking", "labelmarking", "text"],
    "length": ["length", "leglength", "breakoutlength", "lengthtobreakout", "len"],
    "contact_pn": ["contactpn", "contact", "contactpartnumber", "contacts"],
}

GROUP_COLUMNS: dict[str, list[str]] = {
    "group_id": ["group", "groupid", "id", "pair", "cable", "cableid", "name"],
    "kind": ["type", "kind", "grouptype", "construction"],
    "cable_pn": ["cablepn", "cablepartnumber", "cabletype", "pn", "partnumber"],
    "shield_pn": ["shieldpn", "braidpn", "shield", "braid", "overbraidpn"],
    "shield_term_pn": ["shieldtermpn", "shieldterminationpn", "termpn", "soldersleevepn", "shieldtermpartnumber"],
    "term_from": ["shieldtermfrom", "termfrom", "shieldfrom", "fromshield", "shieldend1", "end1shield", "fromtermination"],
    "term_to": ["shieldtermto", "termto", "shieldto", "toshield", "shieldend2", "end2shield", "totermination"],
    "notes": ["notes", "note", "remarks", "twistrate", "laylength"],
}

SPLICE_COLUMNS: dict[str, list[str]] = {
    "ref": ["ref", "splice", "spliceid", "refdes", "id", "designator"],
    "splice_pn": ["splicepn", "pn", "partnumber", "splicepartnumber", "part"],
    "near": ["near", "leg", "onleg", "nearconnector", "from", "measuredfrom", "connector"],
    "distance": ["distance", "location", "distancefromconnector", "dist", "position", "offset"],
    "notes": ["notes", "note", "remarks"],
}

PART_COLUMNS: dict[str, list[str]] = {
    "pn": ["pn", "partnumber", "partno", "part"],
    "description": ["description", "desc", "name", "nomenclature"],
}

WIRE_HEADERS = {
    "wire_id": "Wire ID", "from_ref": "From", "from_pin": "From Pin", "to_ref": "To", "to_pin": "To Pin",
    "signal": "Signal", "gauge": "Gauge", "color": "Color", "wire_pn": "Wire P/N", "length": "Length",
    "label_pn": "Wire Label P/N", "heatshrink_pn": "Wire Heatshrink P/N", "group": "Group", "notes": "Notes",
}
CONNECTOR_HEADERS = {
    "ref": "Ref", "description": "Description", "connector_pn": "Connector P/N", "contact_pn": "Contact P/N",
    "backshell_pn": "Backshell P/N",
    "heatshrink_pn": "Heatshrink P/N", "label_pn": "Label P/N", "label_text": "Label Text", "length": "Length",
}
GROUP_HEADERS = {
    "group_id": "Group", "kind": "Type", "cable_pn": "Cable P/N", "shield_pn": "Shield P/N",
    "shield_term_pn": "Shield Term P/N", "term_from": "Shield Term From", "term_to": "Shield Term To", "notes": "Notes",
}
SPLICE_HEADERS = {"ref": "Ref", "splice_pn": "Splice P/N", "near": "Near", "distance": "Distance", "notes": "Notes"}
TITLE_FIELDS = {
    "title": "Title", "drawing_number": "Drawing Number", "revision": "Revision", "company": "Company",
    "drawn_by": "Drawn By", "checked_by": "Checked By", "date": "Date", "scale": "Scale",
}

# The standardised headers above are accepted too, so saved projects reload exactly.
for _cols, _headers in ((WIRE_COLUMNS, WIRE_HEADERS), (CONNECTOR_COLUMNS, CONNECTOR_HEADERS),
                        (GROUP_COLUMNS, GROUP_HEADERS), (SPLICE_COLUMNS, SPLICE_HEADERS)):
    for _f, _h in _headers.items():
        _n = re.sub(r"[^a-z0-9]", "", _h.lower())
        if _n not in _cols[_f]:
            _cols[_f].insert(0, _n)


def _norm(header) -> str:
    return re.sub(r"[^a-z0-9]", "", str(header).lower())


def _cell(value) -> str:
    if value is None or (isinstance(value, float) and math.isnan(value)) or value is pd.NA or value is pd.NaT:
        return ""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value).strip()


def _number(value) -> float | None:
    text = _cell(value)
    if not text:
        return None
    m = re.search(r"-?\d+(?:\.\d+)?", text.replace(",", ""))
    return float(m.group()) if m else None


def map_columns(headers, spec: dict[str, list[str]]) -> dict[str, str]:
    """Map model fields to the matching header in ``headers`` (first, exact-alias match wins)."""
    normed = {h: _norm(h) for h in headers}
    mapping: dict[str, str] = {}
    used: set = set()
    # Pass by alias priority so that e.g. "Wire P/N" wins wire_pn before "Wire" claims wire_id.
    for field, aliases in sorted(spec.items(), key=lambda kv: 0 if kv[0].endswith("_pn") else 1):
        for alias in aliases:
            hit = next((h for h, n in normed.items() if n == alias and h not in used), None)
            if hit is not None:
                mapping[field] = hit
                used.add(hit)
                break
    return mapping


def _find_header_row(raw: pd.DataFrame, spec: dict[str, list[str]], required: set[str]) -> int | None:
    best, best_score = None, 0
    for i in range(min(len(raw), 25)):
        mapping = map_columns([_cell(v) for v in raw.iloc[i].tolist()], spec)
        score = len(mapping) + (10 if required <= mapping.keys() else 0)
        if score > best_score:
            best, best_score = i, score
    return best if best_score >= 2 else None


def _with_header(raw: pd.DataFrame, spec, required) -> pd.DataFrame:
    """Turn a header-less sheet into a DataFrame using the detected header row."""
    row = _find_header_row(raw, spec, required)
    if row is None:
        return pd.DataFrame()
    headers = [_cell(v) or f"Column {j + 1}" for j, v in enumerate(raw.iloc[row].tolist())]
    df = raw.iloc[row + 1:].copy()
    df.columns = headers
    return df.dropna(how="all").reset_index(drop=True)


def _split_ref_pin(value: str) -> tuple[str, str]:
    """'P1-3', 'P1:3', 'P1.3' or 'P1 pin 3' -> ('P1', '3')."""
    m = re.match(r"^\s*(.+?)\s*(?:[:.\-]|\s+pin\s*|\s+)\s*([A-Za-z]?\d+[A-Za-z]?|[A-Za-z])\s*$", value, re.I)
    return (m.group(1), m.group(2)) if m else (value.strip(), "")


def wires_from_dataframe(df: pd.DataFrame) -> tuple[list[Wire], list[str]]:
    """Build wires from a table with any recognised headers. Returns (wires, warnings)."""
    warnings: list[str] = []
    mapping = map_columns(list(df.columns), WIRE_COLUMNS)
    if "from_ref" not in mapping or "to_ref" not in mapping:
        return [], ["Wiring list needs 'From' and 'To' columns (connector, or connector-pin such as P1-3)."]
    wires: list[Wire] = []
    for i, row in enumerate(df.to_dict("records"), start=1):
        get = lambda f: _cell(row.get(mapping[f])) if f in mapping else ""  # noqa: E731
        from_ref, from_pin = get("from_ref"), get("from_pin")
        to_ref, to_pin = get("to_ref"), get("to_pin")
        if not any([from_ref, to_ref, get("wire_id")]):
            continue
        if from_ref and not from_pin:
            from_ref, from_pin = _split_ref_pin(from_ref)
        if to_ref and not to_pin:
            to_ref, to_pin = _split_ref_pin(to_ref)
        if not from_ref or not to_ref:
            warnings.append(f"Row {i}: skipped, missing From or To connector.")
            continue
        wires.append(Wire(
            wire_id=get("wire_id") or f"W{len(wires) + 1}",
            from_ref=from_ref, from_pin=from_pin, to_ref=to_ref, to_pin=to_pin,
            signal=get("signal"), gauge=get("gauge"), color=get("color"), wire_pn=get("wire_pn"),
            length=_number(row.get(mapping["length"])) if "length" in mapping else None,
            label_pn=get("label_pn"), heatshrink_pn=get("heatshrink_pn"), notes=get("notes"), group=get("group"),
        ))
    return wires, warnings


def connectors_from_dataframe(df: pd.DataFrame) -> list[ConnectorEnd]:
    mapping = map_columns(list(df.columns), CONNECTOR_COLUMNS)
    if "ref" not in mapping:
        return []
    out: list[ConnectorEnd] = []
    for row in df.to_dict("records"):
        get = lambda f: _cell(row.get(mapping[f])) if f in mapping else ""  # noqa: E731
        if not get("ref"):
            continue
        out.append(ConnectorEnd(
            ref=get("ref"), description=get("description"), connector_pn=get("connector_pn"),
            backshell_pn=get("backshell_pn"), heatshrink_pn=get("heatshrink_pn"), label_pn=get("label_pn"),
            label_text=get("label_text"), contact_pn=get("contact_pn"),
            length=_number(row.get(mapping["length"])) if "length" in mapping else None,
        ))
    return out


def groups_from_dataframe(df: pd.DataFrame) -> list[WireGroup]:
    mapping = map_columns(list(df.columns), GROUP_COLUMNS)
    if "group_id" not in mapping:
        return []
    out = []
    for row in df.to_dict("records"):
        get = lambda f: _cell(row.get(mapping[f])) if f in mapping else ""  # noqa: E731
        if get("group_id"):
            out.append(WireGroup(group_id=get("group_id"), kind=(get("kind") or "TWISTED PAIR").upper(),
                                 cable_pn=get("cable_pn"), shield_pn=get("shield_pn"),
                                 shield_term_pn=get("shield_term_pn"), term_from=get("term_from"),
                                 term_to=get("term_to"), notes=get("notes")))
    return out


def splices_from_dataframe(df: pd.DataFrame) -> list[Splice]:
    mapping = map_columns(list(df.columns), SPLICE_COLUMNS)
    if "ref" not in mapping:
        return []
    out = []
    for row in df.to_dict("records"):
        get = lambda f: _cell(row.get(mapping[f])) if f in mapping else ""  # noqa: E731
        if get("ref"):
            out.append(Splice(ref=get("ref"), splice_pn=get("splice_pn"), near=get("near"),
                              distance=_number(row.get(mapping["distance"])) if "distance" in mapping else None,
                              notes=get("notes")))
    return out


def parts_from_dataframe(df: pd.DataFrame) -> dict[str, str]:
    mapping = map_columns(list(df.columns), PART_COLUMNS)
    if "pn" not in mapping or "description" not in mapping:
        return {}
    return {
        _cell(r[mapping["pn"]]): _cell(r[mapping["description"]])
        for r in df.to_dict("records") if _cell(r[mapping["pn"]]) and _cell(r[mapping["description"]])
    }


def _read_raw_sheets(name: str, data: bytes) -> dict[str, pd.DataFrame]:
    suffix = Path(name).suffix.lower()
    if suffix in (".xlsx", ".xlsm", ".xls"):
        return pd.read_excel(io.BytesIO(data), sheet_name=None, header=None, dtype=object)
    if suffix in (".csv", ".txt", ".tsv"):
        text = data.decode("utf-8-sig", errors="replace")
        sep = "\t" if suffix == ".tsv" or text.count("\t") > text.count(",") else ","
        return {Path(name).stem: pd.read_csv(io.StringIO(text), header=None, dtype=object, sep=sep,
                                             skip_blank_lines=True, keep_default_na=False, na_values=[""])}
    raise ValueError(f"Unsupported file type {suffix!r}; use .xlsx, .xls, .csv or .tsv")


def _sheet_kind(name: str) -> str | None:
    n = name.lower()
    if "generated" in n or n.startswith("bom") or "drc" in n:
        return "ignore"
    if "title" in n or n in ("info", "drawing", "project"):
        return "title"
    if "note" in n:
        return "notes"
    if "splice" in n:
        return "splices"
    if "group" in n or "shield" in n or "twist" in n:
        return "groups"
    if "conn" in n or n.endswith("ends"):
        return "connectors"
    if "part" in n or "catalog" in n or "library" in n:
        return "parts"
    if "wire" in n or "wiring" in n or "list" in n or "pin" in n:
        return "wires"
    return None


def load_design(name: str, data: bytes, design: CableDesign | None = None) -> tuple[CableDesign, list[str]]:
    """Load a wiring list (and, for workbooks, optional Connectors / Parts / Title / Notes sheets)."""
    design = design or CableDesign()
    warnings: list[str] = []
    sheets = _read_raw_sheets(name, data)
    kinds = {sheet: _sheet_kind(sheet) for sheet in sheets}
    wire_sheets = [s for s, k in kinds.items() if k == "wires"] or [s for s, k in kinds.items() if k is None][:1]

    for sheet in wire_sheets:
        df = _with_header(sheets[sheet], WIRE_COLUMNS, {"from_ref", "to_ref"})
        wires, w = wires_from_dataframe(df)
        design.wires.extend(wires)
        warnings.extend(f"{sheet}: {m}" for m in w)
    for sheet, kind in kinds.items():
        raw = sheets[sheet]
        if kind == "connectors":
            add_connectors(design, connectors_from_dataframe(_with_header(raw, CONNECTOR_COLUMNS, {"ref"})))
        elif kind == "parts":
            lib = library_from_dataframe(_with_header(raw, LIBRARY_COLUMNS, {"pn"}), source=f"{name}:{sheet}")
            design.library = design.library.merged(lib)
        elif kind == "groups":
            add_groups(design, groups_from_dataframe(_with_header(raw, GROUP_COLUMNS, {"group_id"})))
        elif kind == "splices":
            add_splices(design, splices_from_dataframe(_with_header(raw, SPLICE_COLUMNS, {"ref"})))
        elif kind == "title":
            _apply_title_sheet(design, raw)
        elif kind == "notes":
            notes = [_cell(v) for v in raw.iloc[:, 0].tolist() if _cell(v) and _norm(v) != "notes"]
            if notes:
                design.notes = notes
    if not design.wires:
        warnings.append("No wires were found. Check that the file has From / To columns.")
    sync_connectors(design)
    return design, warnings


def _apply_title_sheet(design: CableDesign, raw: pd.DataFrame) -> None:
    lookup = {_norm(v): k for k, v in TITLE_FIELDS.items()}
    lookup.update({"dwgno": "drawing_number", "drawingno": "drawing_number", "partnumber": "drawing_number", "rev": "revision"})
    for row in raw.itertuples(index=False):
        if len(row) < 2:
            continue
        key, value = _norm(row[0]), _cell(row[1])
        if key in lookup:
            setattr(design.title_block, lookup[key], value)
        elif key == "units" and value:
            design.units = value.upper()
        elif key == "tolerance" and value:
            design.tolerance = value
        elif key == "overalllength":
            design.overall_length = _number(value)


def add_connectors(design: CableDesign, connectors: list[ConnectorEnd]) -> None:
    """Add/replace connector ends by ref designator."""
    for c in connectors:
        existing = design.connector(c.ref)
        if existing:
            design.connectors[design.connectors.index(existing)] = c
        else:
            design.connectors.append(c)


def add_groups(design: CableDesign, groups: list[WireGroup]) -> None:
    for g in groups:
        existing = design.group(g.group_id)
        if existing:
            design.groups[design.groups.index(existing)] = g
        else:
            design.groups.append(g)


def add_splices(design: CableDesign, splices: list[Splice]) -> None:
    for sp in splices:
        existing = design.splice(sp.ref)
        if existing:
            design.splices[design.splices.index(existing)] = sp
        else:
            design.splices.append(sp)


def sync_connectors(design: CableDesign) -> list[str]:
    """Give every connector, splice and group named in the wire list an entry. Returns refs added."""
    added = []
    for w in design.wires:
        for ref in (w.from_ref, w.to_ref):
            if not ref or design.connector(ref) or design.splice(ref):
                continue
            if design.is_splice(ref):
                design.splices.append(Splice(ref=ref))
            else:
                design.connectors.append(ConnectorEnd(ref=ref))
            added.append(ref)
        if w.group and design.group(w.group) is None:
            design.groups.append(WireGroup(group_id=w.group))
            added.append(w.group)
    return added


def connectors_used(design: CableDesign) -> list[str]:
    refs = []
    for w in design.wires:
        for ref in (w.from_ref, w.to_ref):
            if ref not in refs:
                refs.append(ref)
    return sorted(refs, key=natural_key)


# ---------------------------------------------------------------------------
# DataFrame round-trip (used by the web app editors) and project save
# ---------------------------------------------------------------------------
def wires_to_dataframe(wires: list[Wire]) -> pd.DataFrame:
    return pd.DataFrame([{h: getattr(w, f) for f, h in WIRE_HEADERS.items()} for w in wires],
                        columns=list(WIRE_HEADERS.values()))


def connectors_to_dataframe(connectors: list[ConnectorEnd]) -> pd.DataFrame:
    return pd.DataFrame([{h: getattr(c, f) for f, h in CONNECTOR_HEADERS.items()} for c in connectors],
                        columns=list(CONNECTOR_HEADERS.values()))


def groups_to_dataframe(groups: list[WireGroup]) -> pd.DataFrame:
    return pd.DataFrame([{h: getattr(g, f) for f, h in GROUP_HEADERS.items()} for g in groups],
                        columns=list(GROUP_HEADERS.values()))


def splices_to_dataframe(splices: list[Splice]) -> pd.DataFrame:
    return pd.DataFrame([{h: getattr(sp, f) for f, h in SPLICE_HEADERS.items()} for sp in splices],
                        columns=list(SPLICE_HEADERS.values()))


def library_with_descriptions(design: CableDesign) -> PartsLibrary:
    """The design's library plus any stand-alone part descriptions, as one library."""
    from .library import Part

    lib = PartsLibrary(dict(design.library.parts), source=design.library.source)
    for pn, desc in design.part_descriptions.items():
        if pn in lib.parts:
            if desc:
                lib.parts[pn].description = desc
        else:
            lib.add(Part(pn=pn, description=desc))
    return lib


def save_design(design: CableDesign) -> bytes:
    """Save the whole project as a workbook that :func:`load_design` reads back."""
    from .bom import bom_dataframe, build_bom

    title = pd.DataFrame(
        [(label, getattr(design.title_block, f)) for f, label in TITLE_FIELDS.items()]
        + [("Units", design.units), ("Tolerance", design.tolerance), ("Overall Length", design.overall_length)],
        columns=["Field", "Value"],
    )
    parts = library_with_descriptions(design).to_dataframe()
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
        wires_to_dataframe(design.wires).to_excel(writer, sheet_name="Wire List", index=False)
        connectors_to_dataframe(design.connectors).to_excel(writer, sheet_name="Connectors", index=False)
        groups_to_dataframe(design.groups).to_excel(writer, sheet_name="Groups", index=False)
        splices_to_dataframe(design.splices).to_excel(writer, sheet_name="Splices", index=False)
        parts.to_excel(writer, sheet_name="Parts Library", index=False)
        title.to_excel(writer, sheet_name="Title Block", index=False)
        pd.DataFrame({"Notes": design.notes}).to_excel(writer, sheet_name="Notes", index=False)
        bom_dataframe(build_bom(design), design.units).to_excel(writer, sheet_name="BOM (generated)", index=False)
        for ws in writer.book.worksheets:
            ws.freeze_panes = "A2"
            for col in ws.columns:
                width = max((len(str(c.value)) for c in col if c.value is not None), default=8)
                ws.column_dimensions[col[0].column_letter].width = min(max(width + 2, 10), 70)
    return buf.getvalue()


def read_table(name: str, data: bytes, kind: str) -> pd.DataFrame:
    """Read the first sheet of a file as a table of ``kind`` (connectors, groups, splices, parts)."""
    spec, required = {
        "connectors": (CONNECTOR_COLUMNS, {"ref"}),
        "groups": (GROUP_COLUMNS, {"group_id"}),
        "splices": (SPLICE_COLUMNS, {"ref"}),
        "parts": (LIBRARY_COLUMNS, {"pn"}),
    }[kind]
    sheets = _read_raw_sheets(name, data)
    preferred = [s for s in sheets if _sheet_kind(s) == kind] or list(sheets)
    return _with_header(sheets[preferred[0]], spec, required)


def apply_table(design: CableDesign, kind: str, name: str, data: bytes) -> None:
    """Merge a stand-alone connectors / groups / splices / parts-library file into ``design``."""
    df = read_table(name, data, kind)
    if kind == "connectors":
        add_connectors(design, connectors_from_dataframe(df))
    elif kind == "groups":
        add_groups(design, groups_from_dataframe(df))
    elif kind == "splices":
        add_splices(design, splices_from_dataframe(df))
    elif kind == "parts":
        design.library = design.library.merged(library_from_dataframe(df, source=name))
    sync_connectors(design)
