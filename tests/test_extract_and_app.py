import io
from pathlib import Path

import pytest

from nas411_tool.cli import main
from nas411_tool.extract import extract_text

ROOT = Path(__file__).resolve().parents[1]


def test_docx_extraction():
    docx = pytest.importorskip("docx")
    d = docx.Document()
    d.add_paragraph("Finish: cadmium plate")
    table = d.add_table(rows=1, cols=2)
    table.rows[0].cells[0].text = "Lead"
    table.rows[0].cells[1].text = "7439-92-1"
    buf = io.BytesIO()
    d.save(buf)
    doc = extract_text("ds.docx", buf.getvalue())
    assert "cadmium plate" in doc.sections[0].text
    assert "7439-92-1" in doc.sections[0].text


def test_pdf_extraction_with_pages():
    pytest.importorskip("reportlab")
    from reportlab.pdfgen import canvas

    buf = io.BytesIO()
    c = canvas.Canvas(buf)
    c.drawString(72, 720, "Page one: aluminum")
    c.showPage()
    c.drawString(72, 720, "Page two: beryllium copper spring")
    c.save()
    doc = extract_text("ds.pdf", buf.getvalue())
    assert [s.label for s in doc.sections] == ["Page 1", "Page 2"]
    assert "beryllium" in doc.sections[1].text


def test_empty_pdf_warns():
    pytest.importorskip("reportlab")
    from reportlab.pdfgen import canvas

    buf = io.BytesIO()
    c = canvas.Canvas(buf)
    c.showPage()
    c.save()
    doc = extract_text("scan.pdf", buf.getvalue())
    assert any("OCR" in w for w in doc.warnings)


def test_cli_scan(tmp_path, capsys):
    out = tmp_path / "report.xlsx"
    assert main(["scan", str(ROOT / "samples" / "sample_datasheet.txt"), "-o", str(out)]) == 0
    assert out.stat().st_size > 0
    assert "Cadmium and cadmium compounds" in capsys.readouterr().out


def test_streamlit_app_runs():
    pytest.importorskip("streamlit")
    from streamlit.testing.v1 import AppTest

    at = AppTest.from_file(str(ROOT / "app.py"), default_timeout=60)
    at.run()
    assert not at.exception
    at.text_area[0].input((ROOT / "samples" / "sample_datasheet.txt").read_text()).run()
    assert not at.exception
    assert at.metric[1].value == "5"  # identified materials
