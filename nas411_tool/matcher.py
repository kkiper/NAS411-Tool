"""Compare extracted datasheet text against the reference list."""

from __future__ import annotations

import io
import re
from dataclasses import dataclass
from importlib import resources

import pandas as pd

from .cas import find_cas_numbers
from .extract import ExtractedDocument
from .reference import ReferenceList, Substance

HIGH, MEDIUM, LOW, INFO = "High", "Medium", "Low", "Info"
CONFIDENCE_RANK = {HIGH: 3, MEDIUM: 2, LOW: 1, INFO: 0}

STATUS_IDENTIFIED = "Identified"
STATUS_POSSIBLE = "Possible - review"
STATUS_ABSENT = "Stated absent / free"
STATUS_UNLISTED = "CAS not on reference list"

_WORD = r"A-Za-z0-9"

# Words with a common non-chemical meaning. A hit is downgraded to Low when the
# surrounding text matches one of these patterns. ``before`` is matched against
# the text immediately preceding the hit, ``after`` against the matched word plus
# the text that follows it (all lower-cased).
AMBIGUOUS_TERMS: dict[str, dict[str, str]] = {
    "lead": {
        "after": r"^(?:leads\b|lead[\s-]*(?:time|wire|length|spacing|pitch|frame|count|screw|angle|form|bend|"
        r"diameter|dia\b|width|thickness|coplanarity|finish|style|config|span|in\b|out\b|engineer|"
        r"designer|technician|auditor|to\b|the\b|by\b|\d|[a-z]\b))",
        "before": r"(?:axial|radial|test|gull[- ]?wing|j|component|each|per|bent|formed|flexible|"
        r"device|package|pin|signal|ground|battery|positive|negative|probe|wire|will|may|can|could)[\s-]*$",
    },
    "hg": {"after": "", "before": r"(?:mm|in|cm)\s*$"},
    "mercury": {"after": r"^mercury\s+(?:systems|computer|marine|inc\b|corp)", "before": ""},
    "cd": {"after": r"^cd\s*(?:=|\(|\d|pf\b|nf\b)", "before": r"(?:c|capacitance|\d)\s*$"},
}

NEGATION_BEFORE = re.compile(
    r"\b(?:no|not|non|without|free\s+(?:of|from)|absence\s+of|zero|replac\w*|alternatives?\s+to|"
    r"substitutes?\s+for|in\s+lieu\s+of|does\s+not\s+contain|do\s+not\s+contain|contains?\s+no)\b[^.;:!?]*$",
    re.IGNORECASE,
)
NEGATION_AFTER = re.compile(r"^[\w()+/]*\s*-?\s*(?:free|replacement|substitute|alternative)\b", re.IGNORECASE)

CONCENTRATION = re.compile(
    r"(?:[<>≤≥~]=?\s*)?\d+(?:[.,]\d+)?(?:\s*(?:-|to)\s*\d+(?:[.,]\d+)?)?\s*"
    r"(?:wt\.?\s*%|weight\s*%|w/w\s*%|%\s*(?:by\s+)?(?:wt|weight)\.?|%|ppm|ppb|mg/kg)",
    re.IGNORECASE,
)


@dataclass
class Indicator:
    pattern: re.Pattern
    label: str
    implied_substance: str
    implied_cas: list[str]
    note: str


@dataclass
class Hit:
    filename: str
    section: str
    substance: str
    cas: str
    category: str
    match_type: str
    matched_text: str
    confidence: str
    negated: bool
    concentration: str
    context: str
    note: str = ""


def load_indicators() -> list[Indicator]:
    data = resources.files("nas411_tool").joinpath("data/spec_indicators.csv").read_bytes()
    df = pd.read_csv(io.BytesIO(data), dtype=str).fillna("")
    return [
        Indicator(
            pattern=re.compile(row["Pattern"], re.IGNORECASE),
            label=row["Indicator"],
            implied_substance=row["Implied Substance"],
            implied_cas=[c.strip() for c in row["Implied CAS"].split(";") if c.strip()],
            note=row["Note"],
        )
        for _, row in df.iterrows()
    ]


def is_case_sensitive(term: str) -> bool:
    """Short symbols and acronyms (Cd, Pb, PFOA, BeCu) must match case exactly."""
    if " " in term:
        return False
    uppers = sum(1 for c in term if c.isupper())
    return (len(term) <= 3 and uppers >= 1) or (uppers >= 2 and len(term) <= 8)


def _alternation(terms: list[str]) -> str:
    return "|".join(re.escape(t) for t in sorted(terms, key=len, reverse=True))


class Matcher:
    def __init__(self, reference: ReferenceList, use_indicators: bool = True, report_unlisted_cas: bool = True):
        self.reference = reference
        self.indicators = load_indicators() if use_indicators else []
        self.report_unlisted_cas = report_unlisted_cas

        self._ci: dict[str, list[Substance]] = {}
        self._cs: dict[str, list[Substance]] = {}
        self._cas: dict[str, list[Substance]] = {}
        for sub in reference.substances:
            for cas in sub.cas:
                self._cas.setdefault(cas, []).append(sub)
            for term in sub.search_terms():
                if len(term) < 2:
                    continue
                if is_case_sensitive(term):
                    bucket, key = self._cs, term
                else:
                    bucket, key = self._ci, term.lower()
                if sub not in bucket.setdefault(key, []):
                    bucket[key].append(sub)

        self._ci_re = (
            re.compile(rf"(?<![{_WORD}])(?P<t>{_alternation(list(self._ci))})(?P<sfx>e?s)?(?![{_WORD}])", re.IGNORECASE)
            if self._ci
            else None
        )
        self._cs_re = (
            re.compile(rf"(?<![{_WORD}])(?P<t>{_alternation(list(self._cs))})(?![{_WORD}])") if self._cs else None
        )

    # ------------------------------------------------------------------
    def scan(self, doc: ExtractedDocument) -> list[Hit]:
        hits: list[Hit] = []
        for section in doc.sections:
            text = section.text
            hits.extend(self._scan_cas(doc.filename, section.label, text))
            hits.extend(self._scan_terms(doc.filename, section.label, text))
            hits.extend(self._scan_indicators(doc.filename, section.label, text))
        return hits

    def _scan_cas(self, filename: str, label: str, text: str) -> list[Hit]:
        hits = []
        for m in find_cas_numbers(text):
            cas = m.group(0)
            subs = self._cas.get(cas)
            negated = self._is_negated(text, m.start(), m.end())
            if subs:
                for sub in subs:
                    hits.append(self._hit(filename, label, sub, "CAS number", cas, HIGH, negated, text, m))
            elif self.report_unlisted_cas:
                hits.append(
                    Hit(filename, label, f"Unlisted substance (CAS {cas})", cas, "-", "CAS number", cas, INFO,
                        negated, self._concentration(text, m.start(), m.end()), self._context(text, m.start(), m.end()),
                        "Valid CAS number found that is not on the active reference list")
                )
        return hits

    def _scan_terms(self, filename: str, label: str, text: str) -> list[Hit]:
        hits = []
        for regex, table, case_sensitive in ((self._ci_re, self._ci, False), (self._cs_re, self._cs, True)):
            if regex is None:
                continue
            for m in regex.finditer(text):
                term = m.group("t")
                key = term if case_sensitive else term.lower()
                subs = table.get(key, [])
                confidence = MEDIUM if case_sensitive and len(term) <= 4 else HIGH
                note = ""
                amb = AMBIGUOUS_TERMS.get(key.lower())
                if amb:
                    confidence = MEDIUM
                    before = text[max(0, m.start() - 30) : m.start()].lower()
                    after = text[m.start() : m.end() + 30].lower()
                    if (amb["after"] and re.search(amb["after"], after)) or (
                        amb["before"] and re.search(amb["before"], before)
                    ):
                        confidence = LOW
                        note = f"'{term}' is likely used in a non-chemical sense here"
                negated = self._is_negated(text, m.start(), m.end())
                match_type = "Symbol/abbreviation" if case_sensitive and len(term) <= 4 else "Name/synonym"
                for sub in subs:
                    hits.append(self._hit(filename, label, sub, match_type, m.group(0), confidence, negated, text, m, note))
        return hits

    def _scan_indicators(self, filename: str, label: str, text: str) -> list[Hit]:
        hits = []
        for ind in self.indicators:
            for m in ind.pattern.finditer(text):
                subs = self._resolve_indicator(ind)
                negated = self._is_negated(text, m.start(), m.end())
                note = f"{ind.label}. {ind.note}".strip()
                for sub in subs:
                    hits.append(self._hit(filename, label, sub, "Spec/alloy indicator", m.group(0), MEDIUM, negated, text, m, note))
        return hits

    def _resolve_indicator(self, ind: Indicator) -> list[Substance]:
        subs: list[Substance] = []
        for cas in ind.implied_cas:
            for s in self._cas.get(cas, []):
                if s not in subs:
                    subs.append(s)
        if not subs:
            subs = self.reference.find_by_name(ind.implied_substance)
        if not subs:
            subs = [Substance(name=ind.implied_substance, cas=ind.implied_cas, source="Spec indicator")]
        return subs

    # ------------------------------------------------------------------
    def _hit(self, filename, label, sub: Substance, match_type, matched, confidence, negated, text, m, note="") -> Hit:
        return Hit(
            filename=filename,
            section=label,
            substance=sub.name,
            cas="; ".join(sub.cas),
            category=sub.display_category if sub.source != "Spec indicator" else "Not on reference list",
            match_type=match_type,
            matched_text=matched,
            confidence=confidence,
            negated=negated,
            concentration=self._concentration(text, m.start(), m.end()),
            context=self._context(text, m.start(), m.end()),
            note=note,
        )

    @staticmethod
    def _is_negated(text: str, start: int, end: int) -> bool:
        before = text[max(0, start - 60) : start]
        before = re.split(r"[.;:!?]\s|\n\s*\n", before)[-1]
        after = text[end : end + 25]
        return bool(NEGATION_BEFORE.search(before) or NEGATION_AFTER.search(after))

    @staticmethod
    def _concentration(text: str, start: int, end: int) -> str:
        line_end = text.find("\n", end)
        line_end = len(text) if line_end == -1 else line_end
        after = text[end : min(line_end, end + 80)]
        m = CONCENTRATION.search(after)
        if m:
            return m.group(0).strip()
        line_start = text.rfind("\n", 0, start) + 1
        before = text[max(line_start, start - 40) : start]
        found = CONCENTRATION.findall(before)
        return found[-1].strip() if found else ""

    @staticmethod
    def _context(text: str, start: int, end: int, width: int = 90) -> str:
        s = max(0, start - width)
        e = min(len(text), end + width)
        snippet = text[s:start] + "[" + text[start:end] + "]" + text[end:e]
        snippet = re.sub(r"\s+", " ", snippet).strip()
        return ("..." if s > 0 else "") + snippet + ("..." if e < len(text) else "")


# ----------------------------------------------------------------------
# Summaries
# ----------------------------------------------------------------------

def hits_to_dataframe(hits: list[Hit]) -> pd.DataFrame:
    cols = ["File", "Location", "Substance", "CAS", "Category", "Match Type", "Matched Text", "Confidence",
            "Negated", "Concentration", "Context", "Note"]
    rows = [
        [h.filename, h.section, h.substance, h.cas, h.category, h.match_type, h.matched_text, h.confidence,
         "Yes" if h.negated else "", h.concentration, h.context, h.note]
        for h in hits
    ]
    return pd.DataFrame(rows, columns=cols)


def summarize(hits: list[Hit]) -> pd.DataFrame:
    """One row per (file, substance) with an overall status."""
    groups: dict[tuple[str, str], list[Hit]] = {}
    for h in hits:
        groups.setdefault((h.filename, h.substance), []).append(h)

    rows = []
    for (filename, substance), hs in groups.items():
        positive = [h for h in hs if not h.negated]
        best = max((CONFIDENCE_RANK[h.confidence] for h in positive), default=-1)
        if all(h.confidence == INFO for h in hs):
            status = STATUS_UNLISTED
        elif not positive:
            status = STATUS_ABSENT
        elif best >= CONFIDENCE_RANK[MEDIUM]:
            status = STATUS_IDENTIFIED
        else:
            status = STATUS_POSSIBLE
        best_hits = positive or hs
        best_conf = max(best_hits, key=lambda h: CONFIDENCE_RANK[h.confidence]).confidence
        rows.append(
            {
                "File": filename,
                "Substance": substance,
                "CAS": hs[0].cas,
                "Category": hs[0].category,
                "Status": status,
                "Confidence": best_conf,
                "Match Types": ", ".join(sorted({h.match_type for h in hs})),
                "Matched Terms": ", ".join(_unique(h.matched_text for h in hs))[:300],
                "Concentration": ", ".join(_unique(h.concentration for h in hs if h.concentration)),
                "Locations": ", ".join(_unique(h.section for h in hs)),
                "Hits": len(hs),
                "Notes": " | ".join(_unique(h.note for h in hs if h.note))[:500],
            }
        )
    df = pd.DataFrame(
        rows,
        columns=["File", "Substance", "CAS", "Category", "Status", "Confidence", "Match Types", "Matched Terms",
                 "Concentration", "Locations", "Hits", "Notes"],
    )
    if df.empty:
        return df
    status_order = {STATUS_IDENTIFIED: 0, STATUS_POSSIBLE: 1, STATUS_ABSENT: 2, STATUS_UNLISTED: 3}
    df["_s"] = df["Status"].map(status_order)
    df["_c"] = df["Confidence"].map(CONFIDENCE_RANK)
    df = df.sort_values(["File", "_s", "_c", "Substance"], ascending=[True, True, False, True])
    return df.drop(columns=["_s", "_c"]).reset_index(drop=True)


def _unique(values) -> list[str]:
    out: list[str] = []
    for v in values:
        if v not in out:
            out.append(v)
    return out
