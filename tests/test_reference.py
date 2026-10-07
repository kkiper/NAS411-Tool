import io

import pandas as pd
import pytest

from nas411_tool.extract import extract_text
from nas411_tool.matcher import Matcher, summarize
from nas411_tool.reference import (
    build_reference,
    enrich_with_synonyms,
    guess_columns,
    load_starter_reference,
    merge_references,
    name_variants,
    read_reference_table,
)


def test_name_variants():
    assert name_variants("Cadmium and cadmium compounds") == ["Cadmium"]
    assert name_variants("Lead compounds") == ["Lead"]
    assert "PFOA" in name_variants("Perfluorooctanoic acid (PFOA)")
    v = name_variants("Chromium (VI) compounds")
    assert "Chromium (VI)" in v and "Chromium" not in v


def _official_xlsx() -> bytes:
    """An HMTL-style workbook with title rows above the header and AIA-like column names."""
    rows = [
        ["NAS 411-1 Hazardous Material Target List", None, None, None],
        ["Revision X", None, None, None],
        [None, None, None, None],
        ["Hazardous Material", "CAS Number", "HMTL Category", "Comments"],
        ["Cadmium and cadmium compounds", "7440-43-9", "Prohibited", ""],
        ["Chromium (VI) compounds", "18540-29-9", "Restricted", ""],
        ["Nickel", "7440-02-0", "Tracked", ""],
    ]
    buf = io.BytesIO()
    pd.DataFrame(rows).to_excel(buf, header=False, index=False, sheet_name="HMTL")
    return buf.getvalue()


def test_import_excel_with_title_rows():
    df = read_reference_table("hmtl.xlsx", _official_xlsx())
    mapping = guess_columns(list(df.columns))
    assert mapping["name"] == "Hazardous Material"
    assert mapping["cas"] == "CAS Number"
    assert mapping["category"] == "HMTL Category"
    ref = build_reference(df, mapping, source="hmtl.xlsx")
    assert len(ref) == 3
    assert ref.find_by_cas("7440-43-9")[0].category == "Prohibited"


def test_official_list_categories_flow_to_results():
    df = read_reference_table("hmtl.xlsx", _official_xlsx())
    official = enrich_with_synonyms(build_reference(df, guess_columns(list(df.columns)), "hmtl.xlsx"), load_starter_reference())
    m = Matcher(official)
    text = "Fastener cad plated per QQ-P-416; bracket hex chrome conversion coat; spring Inconel 718"
    summary = summarize(m.scan(extract_text("ds.txt", text.encode())))
    cats = dict(zip(summary["Substance"], summary["Category"]))
    assert cats["Cadmium and cadmium compounds"] == "Prohibited"
    assert cats["Chromium (VI) compounds"] == "Restricted"  # matched via enriched synonym "hex chrome"
    assert cats["Nickel"] == "Tracked"  # matched via Inconel indicator resolved by CAS


def test_merge_keeps_primary_and_adds_missing():
    df = read_reference_table("hmtl.xlsx", _official_xlsx())
    official = build_reference(df, guess_columns(list(df.columns)), "hmtl.xlsx")
    merged = merge_references(official, load_starter_reference())
    assert len(merged.find_by_cas("7440-43-9")) == 1
    assert merged.find_by_cas("7439-97-6")  # mercury added from starter


def test_import_csv():
    csv = "Substance,CAS RN,Status,Synonyms\nBeryllium,7440-41-7,Restricted,BeCu;beryllium copper\n"
    df = read_reference_table("list.csv", csv.encode())
    ref = build_reference(df, guess_columns(list(df.columns)), "list.csv")
    assert ref.substances[0].synonyms == ["BeCu", "beryllium copper"]
    assert ref.substances[0].category == "Restricted"


def test_import_pdf_table():
    reportlab = pytest.importorskip("reportlab")
    from reportlab.lib.pagesizes import letter
    from reportlab.platypus import SimpleDocTemplate, Table, TableStyle

    buf = io.BytesIO()
    data = [["Substance", "CAS No.", "Category"],
            ["Cadmium", "7440-43-9", "Prohibited"],
            ["Mercury", "7439-97-6", "Prohibited"]]
    t = Table(data)
    t.setStyle(TableStyle([("GRID", (0, 0), (-1, -1), 0.5, "black")]))
    SimpleDocTemplate(buf, pagesize=letter).build([t])

    df = read_reference_table("hmtl.pdf", buf.getvalue())
    ref = build_reference(df, guess_columns(list(df.columns)), "hmtl.pdf")
    assert {s.name for s in ref.substances} == {"Cadmium", "Mercury"}
    assert ref.find_by_cas("7439-97-6")[0].category == "Prohibited"
