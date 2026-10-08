"""Parts library: part numbers with the parameters the design rule check needs.

One row per part. Only ``P/N`` is required; every other column is optional and only the
parameters relevant to a part's type are used. Dimensions are inches unless the column header
says mm (e.g. ``OD (mm)``).

=================  ====================================================================
Type               Parameters used
=================  ====================================================================
wire               AWG, OD (insulation), Color
cable              OD (jacket), Conductors, AWG  (jacketed / shielded multi-conductor cable)
connector          Contact P/N (default contact), Contacts (cavity count),
                   AWG Min/Max and Dia Min/Max (wire sealing range) if contacts aren't separate
contact            AWG Min/Max (accepted wire), Dia Min/Max (insulation OD sealing range)
backshell          Dia Min/Max = cable clamp range
heatshrink         Dia Min = fully recovered ID, Dia Max = expanded (as supplied) ID
label / marker     same as heatshrink (heatshrink marker sleeves); blank = flat label, not checked
splice             AWG Min/Max (each wire), CMA Min/Max (total circular mil area of wires)
shield_term        like splice, plus Dia Min/Max over the shielded cable / group
shield             Wall (braid or overall-shield thickness added to a built-up group)
=================  ====================================================================
"""

from __future__ import annotations

import io
import math
import re
from dataclasses import dataclass, field, fields
from pathlib import Path

import pandas as pd

PART_TYPES = ["wire", "cable", "connector", "contact", "backshell", "heatshrink", "label", "marker",
              "splice", "shield_term", "shield", "other"]

TYPE_ALIASES = {
    "wire": "wire", "hookupwire": "wire", "conductor": "wire",
    "cable": "cable", "multiconductor": "cable", "jacketedcable": "cable", "shieldedcable": "cable", "twistedpair": "cable",
    "connector": "connector", "conn": "connector", "plug": "connector", "receptacle": "connector",
    "contact": "contact", "pin": "contact", "socket": "contact", "terminal": "contact",
    "backshell": "backshell", "strainrelief": "backshell", "adapter": "backshell",
    "heatshrink": "heatshrink", "boot": "heatshrink", "shrinkboot": "heatshrink", "sleeve": "heatshrink",
    "heatshrinksleeve": "heatshrink", "tubing": "heatshrink",
    "label": "label", "idlabel": "label", "cablelabel": "label",
    "marker": "marker", "wiremarker": "marker", "markersleeve": "marker",
    "splice": "splice", "crimpsplice": "splice", "soldersleeve": "splice", "buttsplice": "splice",
    "shieldterm": "shield_term", "shieldtermination": "shield_term", "shieldterminator": "shield_term",
    "groundlug": "shield_term", "shieldsplice": "shield_term", "band": "shield_term",
    "shield": "shield", "braid": "shield", "overbraid": "shield", "shielding": "shield",
}

# field -> accepted header spellings (normalised)
LIBRARY_COLUMNS: dict[str, list[str]] = {
    "pn": ["pn", "partnumber", "partno", "part"],
    "type": ["type", "parttype", "category", "class", "kind"],
    "description": ["description", "desc", "nomenclature", "name"],
    "awg": ["awg", "gauge", "wiregauge"],
    "od": ["od", "insulationod", "outsidediameter", "diameter", "jacketod", "nominalod"],
    "awg_min": ["awgmin", "minawg", "wireawgmin", "awgfrom", "accepts awg min"],
    "awg_max": ["awgmax", "maxawg", "wireawgmax", "awgto"],
    "awg_range": ["awgrange", "wirerange", "wiresizerange", "acceptsawg", "acceptedawg"],
    "dia_min": ["diamin", "mindia", "mindiameter", "recoveredid", "clampmin", "sealingmin", "minod", "diametermin"],
    "dia_max": ["diamax", "maxdia", "maxdiameter", "expandedid", "suppliedid", "clampmax", "sealingmax", "maxod", "diametermax"],
    "cma_min": ["cmamin", "mincma", "mincircularmils"],
    "cma_max": ["cmamax", "maxcma", "maxcircularmils"],
    "contact_pn": ["contactpn", "contact", "defaultcontact", "contactpartnumber"],
    "contacts": ["contacts", "contactcount", "cavities", "pincount", "positions", "ways"],
    "conductors": ["conductors", "conductorcount", "cores"],
    "wall": ["wall", "wallthickness", "thickness", "shieldwall"],
    "color": ["color", "colour"],
    "notes": ["notes", "note", "remarks", "source"],
}
NUMERIC = {"od", "dia_min", "dia_max", "wall"}            # lengths (inches)
COUNTS = {"awg", "awg_min", "awg_max", "cma_min", "cma_max", "contacts", "conductors"}

LIBRARY_HEADERS = {
    "pn": "P/N", "type": "Type", "description": "Description", "awg": "AWG", "od": "OD",
    "awg_min": "AWG Min", "awg_max": "AWG Max", "dia_min": "Dia Min", "dia_max": "Dia Max",
    "cma_min": "CMA Min", "cma_max": "CMA Max", "contact_pn": "Contact P/N", "contacts": "Contacts",
    "conductors": "Conductors", "wall": "Wall", "color": "Color", "notes": "Notes",
}


def _norm(s) -> str:
    return re.sub(r"[^a-z0-9]", "", str(s).lower())


for _f, _h in LIBRARY_HEADERS.items():
    if _norm(_h) not in LIBRARY_COLUMNS[_f]:
        LIBRARY_COLUMNS[_f].insert(0, _norm(_h))
LIBRARY_COLUMNS = {k: [_norm(a) for a in v] for k, v in LIBRARY_COLUMNS.items()}


@dataclass
class Part:
    pn: str
    type: str = ""
    description: str = ""
    awg: float | None = None
    od: float | None = None           # inches
    awg_min: float | None = None      # numerically smallest gauge accepted (largest wire)
    awg_max: float | None = None      # numerically largest gauge accepted (smallest wire)
    dia_min: float | None = None      # inches
    dia_max: float | None = None
    cma_min: float | None = None
    cma_max: float | None = None
    contact_pn: str = ""
    contacts: float | None = None
    conductors: float | None = None
    wall: float | None = None
    color: str = ""
    notes: str = ""

    @property
    def awg_range(self) -> tuple[float, float] | None:
        if self.awg_min is None and self.awg_max is None:
            return None
        lo = self.awg_min if self.awg_min is not None else self.awg_max
        hi = self.awg_max if self.awg_max is not None else self.awg_min
        return (min(lo, hi), max(lo, hi))

    @property
    def dia_range(self) -> tuple[float | None, float | None]:
        return (self.dia_min, self.dia_max)


@dataclass
class PartsLibrary:
    parts: dict[str, Part] = field(default_factory=dict)
    source: str = ""

    def get(self, pn: str) -> Part | None:
        if not pn:
            return None
        return self.parts.get(pn.strip()) or self.parts.get(pn.strip().upper())

    def __contains__(self, pn) -> bool:
        return self.get(pn) is not None

    def __len__(self) -> int:
        return len(self.parts)

    def add(self, part: Part) -> None:
        self.parts[part.pn] = part

    def merged(self, other: "PartsLibrary") -> "PartsLibrary":
        """A new library: this one with ``other``'s parts added (``other`` wins on duplicates)."""
        out = PartsLibrary(dict(self.parts), source=" + ".join(s for s in (self.source, other.source) if s))
        out.parts.update(other.parts)
        return out

    def description(self, pn: str) -> str:
        p = self.get(pn)
        return p.description if p else ""

    def to_dataframe(self) -> pd.DataFrame:
        rows = []
        for p in self.parts.values():
            rows.append({h: _fmt(getattr(p, f)) for f, h in LIBRARY_HEADERS.items()})
        return pd.DataFrame(rows, columns=list(LIBRARY_HEADERS.values()))


def _fmt(v):
    if v is None:
        return None
    if isinstance(v, float) and v.is_integer():
        return int(v)
    return v


def normalize_type(value: str) -> str:
    n = _norm(value)
    if not n:
        return ""
    if n in TYPE_ALIASES:
        return TYPE_ALIASES[n]
    for alias, t in TYPE_ALIASES.items():
        if alias in n:
            return t
    return "other"


def _cell(v) -> str:
    if v is None or v is pd.NA or (isinstance(v, float) and math.isnan(v)):
        return ""
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    return str(v).strip()


def _num(v) -> float | None:
    m = re.search(r"-?\d+(?:\.\d+)?", _cell(v).replace(",", ""))
    return float(m.group()) if m else None


def _parse_range(text: str) -> tuple[float | None, float | None]:
    nums = [float(x) for x in re.findall(r"\d+(?:\.\d+)?", text)]
    if not nums:
        return None, None
    return min(nums), max(nums)


def map_library_columns(headers) -> dict[str, str]:
    """field -> header. Headers that say mm are converted to inches when read."""
    mapping: dict[str, str] = {}
    used = set()
    for field_name, aliases in LIBRARY_COLUMNS.items():
        for alias in aliases:
            for h in headers:
                n = _norm(re.sub(r"\((?:in|inch|inches|mm)\)|\b(?:in|mm)\b$", "", str(h), flags=re.I))
                if h not in used and n == alias:
                    mapping[field_name] = h
                    used.add(h)
                    break
            if field_name in mapping:
                break
    return mapping


def library_from_dataframe(df: pd.DataFrame, source: str = "") -> PartsLibrary:
    mapping = map_library_columns(list(df.columns))
    lib = PartsLibrary(source=source)
    if "pn" not in mapping:
        return lib
    for row in df.to_dict("records"):
        pn = _cell(row.get(mapping["pn"]))
        if not pn:
            continue
        p = Part(pn=pn)
        for f in fields(Part):
            if f.name == "pn" or f.name not in mapping:
                continue
            header = mapping[f.name]
            raw = row.get(header)
            if f.name in NUMERIC:
                val = _num(raw)
                if val is not None and re.search(r"\(mm\)|\bmm\b", str(header), re.I):
                    val = val / 25.4
                setattr(p, f.name, val)
            elif f.name in COUNTS:
                setattr(p, f.name, _num(raw))
            elif f.name == "type":
                p.type = normalize_type(_cell(raw))
            else:
                setattr(p, f.name, _cell(raw))
        if "awg_range" in mapping and p.awg_min is None and p.awg_max is None:
            p.awg_min, p.awg_max = _parse_range(_cell(row.get(mapping["awg_range"])))
        if p.awg_min is not None and p.awg_max is not None and p.awg_min > p.awg_max:
            p.awg_min, p.awg_max = p.awg_max, p.awg_min
        if p.dia_min is not None and p.dia_max is not None and p.dia_min > p.dia_max:
            p.dia_min, p.dia_max = p.dia_max, p.dia_min
        lib.add(p)
    return lib


def load_library(name: str, data: bytes) -> PartsLibrary:
    """Read a library from .csv/.xlsx. Title rows above the header are skipped."""
    suffix = Path(name).suffix.lower()
    if suffix in (".xlsx", ".xlsm", ".xls"):
        sheets = pd.read_excel(io.BytesIO(data), sheet_name=None, header=None, dtype=object)
        preferred = [s for s in sheets if re.search(r"lib|part|catalog", s, re.I)] or list(sheets)
        raw = sheets[preferred[0]]
    else:
        text = data.decode("utf-8-sig", errors="replace")
        sep = "\t" if text.count("\t") > text.count(",") else ","
        raw = pd.read_csv(io.StringIO(text), header=None, dtype=object, sep=sep, keep_default_na=False, na_values=[""])
    best, best_n = 0, 0
    for i in range(min(len(raw), 20)):
        n = len(map_library_columns([_cell(v) for v in raw.iloc[i].tolist()]))
        if n > best_n:
            best, best_n = i, n
    headers = [_cell(v) or f"Column {j + 1}" for j, v in enumerate(raw.iloc[best].tolist())]
    df = raw.iloc[best + 1:].copy()
    df.columns = headers
    return library_from_dataframe(df.dropna(how="all"), source=name)


# ---------------------------------------------------------------------------
# Wire size helpers
# ---------------------------------------------------------------------------
# Typical insulation OD (in) for thin-wall aerospace wire (M22759/16-style), used when the library has no OD.
TYPICAL_OD = {30: 0.032, 28: 0.036, 26: 0.040, 24: 0.046, 22: 0.052, 20: 0.062, 18: 0.072, 16: 0.084,
              14: 0.102, 12: 0.125, 10: 0.155, 8: 0.210, 6: 0.265, 4: 0.325, 2: 0.395}


def parse_awg(text) -> float | None:
    s = _cell(text).upper().replace("AWG", "").strip()
    if not s:
        return None
    m = re.match(r"^(\d+)\s*/\s*0$", s)
    if m:
        return float(1 - int(m.group(1)))   # 1/0 -> 0, 2/0 -> -1, 4/0 -> -3
    m = re.match(r"^\d+(?:\.\d+)?", s)
    return float(m.group()) if m else None


def circular_mils(awg: float) -> float:
    d_mils = 5 * 92 ** ((36 - awg) / 39)
    return d_mils * d_mils


def typical_od(awg: float | None) -> float | None:
    if awg is None:
        return None
    key = min(TYPICAL_OD, key=lambda k: abs(k - awg))
    return TYPICAL_OD[key] if abs(key - awg) <= 1 else None


PACKING_FACTOR = 1.2   # bundle OD ≈ 1.2 × sqrt(Σ d²): a common harness-design rule of thumb


def bundle_diameter(diameters: list[float]) -> float | None:
    ds = [d for d in diameters if d]
    if not ds:
        return None
    if len(ds) == 1:
        return ds[0]
    return PACKING_FACTOR * math.sqrt(sum(d * d for d in ds))
