"""CAS Registry Number helpers: normalization, checksum validation, and text search."""

from __future__ import annotations

import re

# A CAS RN is 2-7 digits, a hyphen, 2 digits, a hyphen, and a single check digit.
CAS_PATTERN = re.compile(r"(?<![\d-])(\d{2,7})-(\d{2})-(\d)(?![\d-])")


def is_valid_cas(cas: str) -> bool:
    """Return True if ``cas`` is well formed and its check digit is correct."""
    m = re.fullmatch(r"(\d{2,7})-(\d{2})-(\d)", cas.strip())
    if not m:
        return False
    digits = (m.group(1) + m.group(2))[::-1]
    total = sum((i + 1) * int(d) for i, d in enumerate(digits))
    return total % 10 == int(m.group(3))


def normalize_cas(value: str) -> str | None:
    """Normalize a single CAS number string, returning None if it is not valid."""
    if value is None:
        return None
    s = str(value).strip()
    # Spreadsheets sometimes store CAS numbers without hyphens or with odd dashes.
    s = re.sub(r"[‐-―−]", "-", s)
    s = re.sub(r"\s+", "", s)
    if re.fullmatch(r"\d{5,10}", s):
        s = f"{s[:-3]}-{s[-3:-1]}-{s[-1]}"
    return s if is_valid_cas(s) else None


def split_cas_field(value) -> list[str]:
    """Extract every valid CAS number from a reference-list cell (may hold several)."""
    if value is None:
        return []
    text = str(value)
    if text.strip().lower() in {"", "nan", "none", "n/a", "na", "various", "-"}:
        return []
    found = [m.group(0) for m in CAS_PATTERN.finditer(re.sub(r"[‐-―−]", "-", text))]
    if not found:
        single = normalize_cas(text)
        found = [single] if single else []
    out: list[str] = []
    for cas in found:
        if is_valid_cas(cas) and cas not in out:
            out.append(cas)
    return out


def find_cas_numbers(text: str) -> list[re.Match]:
    """Return regex matches for checksum-valid CAS numbers in ``text``."""
    return [m for m in CAS_PATTERN.finditer(text) if is_valid_cas(m.group(0))]
