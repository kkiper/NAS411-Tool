"""Reference list handling: the NAS 411-1 Hazardous Materials Target List (HMTL).

The official NAS 411-1 list is published by the Aerospace Industries Association
(AIA) and is not redistributed with this tool. Users import their own copy
(Excel, CSV, or PDF). A built-in *starter* list of commonly targeted aerospace
hazardous materials is included so the tool works out of the box, but its
entries are NOT the official NAS 411-1 content and carry no NAS 411-1 category.
"""

from __future__ import annotations

import io
import re
from dataclasses import dataclass, field
from importlib import resources
from pathlib import Path

import pandas as pd

from .cas import CAS_PATTERN, split_cas_field

STARTER_SOURCE = "Starter list (not official NAS 411-1)"
UNCATEGORIZED = "Verify in NAS 411-1"


@dataclass
class Substance:
    name: str
    cas: list[str] = field(default_factory=list)
    category: str = ""
    synonyms: list[str] = field(default_factory=list)
    basis: str = ""
    notes: str = ""
    source: str = ""

    @property
    def display_category(self) -> str:
        return self.category or UNCATEGORIZED

    def search_terms(self) -> list[str]:
        """Name, name variants, and synonyms used for text matching."""
        terms: list[str] = []
        for t in [self.name, *name_variants(self.name), *self.synonyms]:
            t = t.strip()
            if t and t.lower() not in {x.lower() for x in terms}:
                terms.append(t)
        return terms


@dataclass
class ReferenceList:
    substances: list[Substance]
    source: str

    def __len__(self) -> int:
        return len(self.substances)

    def find_by_cas(self, cas: str) -> list[Substance]:
        return [s for s in self.substances if cas in s.cas]

    def find_by_name(self, name: str) -> list[Substance]:
        key = name.strip().lower()
        return [s for s in self.substances if key in {t.lower() for t in s.search_terms()}]

    def to_dataframe(self) -> pd.DataFrame:
        return pd.DataFrame(
            [
                {
                    "Substance": s.name,
                    "CAS": "; ".join(s.cas),
                    "Category": s.display_category,
                    "Synonyms": "; ".join(s.synonyms),
                    "Regulatory Basis": s.basis,
                    "Notes": s.notes,
                    "Source": s.source,
                }
                for s in self.substances
            ]
        )


# ---------------------------------------------------------------------------
# Name handling
# ---------------------------------------------------------------------------

_COMPOUND_SUFFIXES = [
    # "Cadmium and cadmium compounds", "Lead and its compounds"
    re.compile(
        r"\s*,?\s+(?:and|&)\s+(?:its\s+|their\s+)?(?:[\w-]+\s+)?(?:inorganic\s+|organic\s+)?"
        r"(?:compounds?|salts?)\s*$",
        re.IGNORECASE,
    ),
    # "Lead compounds", "Hexavalent chromium compounds"
    re.compile(r"\s+(?:inorganic\s+|organic\s+)?(?:compounds?|salts?)\s*$", re.IGNORECASE),
]


def name_variants(name: str) -> list[str]:
    """Generate simpler search variants from an HMTL-style substance name.

    "Cadmium and cadmium compounds" -> ["Cadmium"]
    "Perfluorooctanoic acid (PFOA)" -> ["Perfluorooctanoic acid", "PFOA"]
    """
    variants: list[str] = []
    oxidation_state = re.compile(r"\s*\((?:[IVX]+|[ivx]+|\d\+?|\+\d)\)")

    def drop_paren(m: re.Match) -> str:
        # Oxidation states like "(VI)" change the meaning, so keep them in the name.
        return m.group(0) if oxidation_state.fullmatch(m.group(0)) else ""

    base = re.sub(r"\s*\([^()]*\)", drop_paren, name).strip()
    if base and base != name:
        variants.append(base)
    for m in re.finditer(r"\s*\(([^()]+)\)", name):
        p = m.group(1).strip()
        if oxidation_state.fullmatch(m.group(0)):
            continue
        # Keep parentheticals that look like abbreviations or alternate names.
        if len(p) >= 2 and not re.search(r"\b(?:e\.g|i\.e|see|incl|including|all)\b", p, re.I):
            variants.append(p)
    for pattern in _COMPOUND_SUFFIXES:
        stripped = pattern.sub("", base).strip(" ,;")
        if stripped.lower() != base.lower():
            if len(stripped) >= 4:
                variants.append(stripped)
            break
    for v in list(variants):
        tight = oxidation_state.sub(lambda m: "(" + m.group(0).strip(" ()") + ")", v)
        if tight != v:
            variants.append(tight)
    return variants


def _split_list(value) -> list[str]:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return []
    parts = re.split(r"[;\n|]", str(value))
    return [p.strip() for p in parts if p.strip() and p.strip().lower() not in {"nan", "none", "-"}]


def _clean(value) -> str:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return ""
    s = str(value).strip()
    return "" if s.lower() in {"nan", "none"} else s


# ---------------------------------------------------------------------------
# Column detection for imported lists
# ---------------------------------------------------------------------------

COLUMN_KEYWORDS: dict[str, list[str]] = {
    "cas": ["cas"],
    "name": ["substance", "chemical name", "hazardous material", "material name", "chemical", "name", "material"],
    "category": ["category", "status", "classification", "designation", "list", "level", "type"],
    "synonyms": ["synonym", "alias", "other name", "also known", "alternate", "common name", "trade name"],
    "basis": ["regulat", "basis", "driver", "rationale", "reason", "authority", "source"],
    "notes": ["note", "comment", "remark", "use", "application", "description"],
}


def guess_columns(columns: list[str]) -> dict[str, str | None]:
    """Guess which column holds each field, based on header keywords."""
    lowered = {c: str(c).strip().lower() for c in columns}
    mapping: dict[str, str | None] = {}
    used: set[str] = set()
    for fld in ["cas", "synonyms", "name", "category", "basis", "notes"]:
        chosen = None
        for kw in COLUMN_KEYWORDS[fld]:
            for col, low in lowered.items():
                if col in used:
                    continue
                if fld == "name" and "cas" in low:
                    continue
                if kw in low:
                    chosen = col
                    break
            if chosen:
                break
        mapping[fld] = chosen
        if chosen:
            used.add(chosen)
    return mapping


def _locate_header(raw: pd.DataFrame) -> pd.DataFrame:
    """Find the header row in a sheet that may have title rows above the table."""
    for i in range(min(len(raw), 30)):
        row = [str(v).strip().lower() for v in raw.iloc[i].tolist()]
        if any("cas" in v for v in row) and sum(1 for v in row if v and v != "nan") >= 2:
            header = [str(v).strip() if str(v) != "nan" else f"Column {j + 1}" for j, v in enumerate(raw.iloc[i])]
            body = raw.iloc[i + 1 :].copy()
            body.columns = _dedupe(header)
            return body.dropna(how="all").reset_index(drop=True)
    header = [f"Column {j + 1}" for j in range(raw.shape[1])]
    raw = raw.copy()
    raw.columns = header
    return raw.dropna(how="all").reset_index(drop=True)


def _dedupe(names: list[str]) -> list[str]:
    seen: dict[str, int] = {}
    out = []
    for n in names:
        if n in seen:
            seen[n] += 1
            out.append(f"{n} ({seen[n]})")
        else:
            seen[n] = 0
            out.append(n)
    return out


def read_reference_table(filename: str, data: bytes) -> pd.DataFrame:
    """Read an imported reference list (CSV, Excel, or PDF) into a DataFrame."""
    ext = Path(filename).suffix.lower()
    if ext in {".csv", ".txt"}:
        text = _decode(data)
        raw = pd.read_csv(io.StringIO(text), header=None, dtype=str, keep_default_na=False, na_values=[""])
        return _locate_header(raw)
    if ext in {".xlsx", ".xlsm", ".xls"}:
        sheets = pd.read_excel(io.BytesIO(data), sheet_name=None, header=None, dtype=str)
        frames = []
        for sheet_name, raw in sheets.items():
            if raw.empty:
                continue
            df = _locate_header(raw)
            if any("cas" in str(c).lower() for c in df.columns):
                df.insert(0, "Sheet", sheet_name)
                frames.append(df)
        if not frames:
            first = next(iter(sheets.values()))
            return _locate_header(first)
        return pd.concat(frames, ignore_index=True)
    if ext == ".pdf":
        return _read_reference_pdf(data)
    raise ValueError(f"Unsupported reference file type: {ext}")


def _decode(data: bytes) -> str:
    for enc in ("utf-8-sig", "cp1252", "latin-1"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def _read_reference_pdf(data: bytes) -> pd.DataFrame:
    """Parse a substance table out of a PDF copy of the list.

    Tries real table extraction first; falls back to one-row-per-CAS-number
    line parsing. Parsed PDFs should always be reviewed by the user.
    """
    import pdfplumber

    header: list[str] | None = None
    rows: list[list[str]] = []
    lines: list[str] = []
    with pdfplumber.open(io.BytesIO(data)) as pdf:
        for page in pdf.pages:
            for table in page.extract_tables() or []:
                for row in table:
                    cells = [re.sub(r"\s+", " ", c or "").strip() for c in row]
                    if not any(cells):
                        continue
                    if header is None and any("cas" in c.lower() for c in cells):
                        header = [c or f"Column {i + 1}" for i, c in enumerate(cells)]
                        continue
                    if header is not None and [c.lower() for c in cells] == [h.lower() for h in header]:
                        continue  # repeated header on later pages
                    if header is not None and len(cells) == len(header):
                        rows.append(cells)
            lines.extend((page.extract_text() or "").splitlines())

    if header and rows:
        return pd.DataFrame(rows, columns=_dedupe(header))

    parsed = []
    for line in lines:
        line = re.sub(r"[‐-―−]", "-", line)
        m = CAS_PATTERN.search(line)
        if not m:
            continue
        name = line[: m.start()].strip(" .:-\t")
        rest = line[m.end() :].strip(" .:-\t")
        if not name and rest:
            name, rest = rest, ""
        parsed.append({"Substance": name, "CAS": m.group(0), "Other text": rest})
    return pd.DataFrame(parsed, columns=["Substance", "CAS", "Other text"])


def build_reference(
    df: pd.DataFrame, mapping: dict[str, str | None], source: str, default_category: str = ""
) -> ReferenceList:
    """Turn an imported table into a ReferenceList using the given column mapping."""

    def col(row, key):
        c = mapping.get(key)
        return row[c] if c and c in row else None

    substances: list[Substance] = []
    for _, row in df.iterrows():
        name = _clean(col(row, "name"))
        cas = split_cas_field(col(row, "cas"))
        if not name and not cas:
            continue
        if not name:
            name = f"CAS {cas[0]}"
        substances.append(
            Substance(
                name=name,
                cas=cas,
                category=_clean(col(row, "category")) or default_category,
                synonyms=_split_list(col(row, "synonyms")),
                basis=_clean(col(row, "basis")),
                notes=_clean(col(row, "notes")),
                source=source,
            )
        )
    return ReferenceList(substances=substances, source=source)


def load_reference_file(path: str | Path, mapping: dict[str, str | None] | None = None) -> ReferenceList:
    """Load an imported list from disk, auto-detecting its columns if no mapping is given."""
    path = Path(path)
    df = read_reference_table(path.name, path.read_bytes())
    mapping = mapping or guess_columns(list(df.columns))
    if not mapping.get("name") and not mapping.get("cas"):
        raise ValueError(f"Could not find substance-name or CAS columns in {path.name}: {list(df.columns)}")
    return build_reference(df, mapping, source=path.name)


def load_starter_reference() -> ReferenceList:
    data = resources.files("nas411_tool").joinpath("data/starter_reference.csv").read_bytes()
    df = pd.read_csv(io.BytesIO(data), dtype=str)
    mapping = {
        "name": "Substance",
        "cas": "CAS",
        "category": "Category",
        "synonyms": "Synonyms",
        "basis": "Regulatory Basis",
        "notes": "Notes",
    }
    return build_reference(df, mapping, source=STARTER_SOURCE)


def enrich_with_synonyms(official: ReferenceList, starter: ReferenceList) -> ReferenceList:
    """Copy starter-list synonyms onto official entries that share a CAS number or name.

    Official lists usually give one formal name per row ("Chromium (VI) compounds"),
    while datasheets use shop terms ("hex chrome", "chromate conversion coat").
    """
    for sub in official.substances:
        official_terms = {t.lower() for t in sub.search_terms()}
        for st in starter.substances:
            shared_cas = set(sub.cas) & set(st.cas)
            shared_name = official_terms & {t.lower() for t in [st.name, *name_variants(st.name)]}
            if shared_cas or shared_name:
                for syn in [st.name, *st.synonyms]:
                    if syn.lower() not in official_terms:
                        sub.synonyms.append(syn)
                        official_terms.add(syn.lower())
                if not sub.basis and st.basis:
                    sub.basis = st.basis
    return official


def merge_references(primary: ReferenceList, secondary: ReferenceList) -> ReferenceList:
    """Primary entries plus any secondary entries not already covered by CAS or name."""
    merged = list(primary.substances)
    primary_cas = {c for s in primary.substances for c in s.cas}
    primary_names = {t.lower() for s in primary.substances for t in s.search_terms()}
    for s in secondary.substances:
        if set(s.cas) & primary_cas:
            continue
        if not s.cas and s.name.lower() in primary_names:
            continue
        merged.append(s)
    return ReferenceList(substances=merged, source=f"{primary.source} + {secondary.source}")
