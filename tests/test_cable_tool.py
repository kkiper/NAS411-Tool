import io
from pathlib import Path

import pandas as pd
import pytest

from cable_tool.bom import build_bom, check_design, compress_refs
from cable_tool.canvas import sheet_to_svg, sheets_to_pdf
from cable_tool.cli import main
from cable_tool.drawing import SHEET_SIZES, assign_sides, build_drawing
from cable_tool.model import CableDesign, ConnectorEnd, Wire
from cable_tool.project import load_design, map_columns, save_design, WIRE_COLUMNS

ROOT = Path(__file__).resolve().parents[1]
SAMPLES = ROOT / "samples"


def two_connector_design() -> CableDesign:
    design, msgs = load_design("cable_wirelist.csv", (SAMPLES / "cable_wirelist.csv").read_bytes())
    assert not msgs
    from cable_tool.cli import _read_table
    from cable_tool.project import add_connectors, connectors_from_dataframe

    add_connectors(design, connectors_from_dataframe(_read_table(SAMPLES / "cable_connectors.csv")))
    design.overall_length = 48
    return design


def test_header_aliases():
    m = map_columns(["Wire", "From", "Pin A", "To", "Pin B", "AWG", "Colour", "Wire Type", "Marker P/N"], WIRE_COLUMNS)
    assert m == {"wire_id": "Wire", "from_ref": "From", "from_pin": "Pin A", "to_ref": "To", "to_pin": "Pin B",
                 "gauge": "AWG", "color": "Colour", "wire_pn": "Wire Type", "label_pn": "Marker P/N"}


def test_title_rows_and_combined_ref_pin():
    design, msgs = load_design("y.csv", (SAMPLES / "y_harness_wirelist.csv").read_bytes())
    assert not msgs
    w1 = design.wires[0]
    assert (w1.wire_id, w1.from_ref, w1.from_pin, w1.to_ref, w1.to_pin) == ("W1", "P1", "A", "P2", "A")
    assert w1.signal == "+28V SENSOR PWR" and w1.gauge == "22" and w1.wire_pn == "M22759/16-22-2"
    assert [c.ref for c in design.connectors] == ["P1", "P2", "P3"]


def test_bom_quantities():
    design = two_connector_design()
    bom = {b.pn: b for b in build_bom(design)}
    assert bom["D38999/26WD35SN"].qty == 1 and bom["D38999/26WD35SN"].category == "connector"
    assert bom["M85049/38S15W"].qty == 2 and bom["M85049/38S15W"].used_on == ["P1", "P2"]
    assert bom["TMS-SCE-1/2-2.0-9"].category == "label"
    assert bom["M22759/16-22-6"].qty == 96 and bom["M22759/16-22-6"].unit == "IN"  # W4 + W6 at 48 in
    assert bom["M22759/16-22-9"].qty_text() == "AR"  # includes the P1 jumper with no length
    assert bom["TMS-SCE-1/8-2.0-9"].qty == 22  # wire markers: 11 wires x 2 ends
    items = [b.item for b in build_bom(design)]
    assert items == list(range(1, len(items) + 1))


def test_leg_lengths_for_breakout():
    design = CableDesign(
        connectors=[ConnectorEnd("P1", length=10), ConnectorEnd("P2", length=20), ConnectorEnd("P3")],
        wires=[Wire("W1", "P1", "1", "P2", "1", wire_pn="X"), Wire("W2", "P1", "2", "P3", "1", wire_pn="X")],
    )
    assert design.wire_length(design.wires[0]) == 30
    assert design.wire_length(design.wires[1]) is None
    assert build_bom(design)[0].qty_text() == "AR"


def test_checks_flag_problems():
    design = CableDesign(
        connectors=[ConnectorEnd("P1", connector_pn="C1")],
        wires=[Wire("W1", "P1", "1", "P2", "1"), Wire("W1", "P1", "1", "P2", "2", wire_pn="X")],
    )
    msgs = "\n".join(check_design(design))
    assert "Wire ID W1 is used 2 times" in msgs
    assert "connector P2 isn't in the connector table" in msgs
    assert "P1 pin 1 has 2 wires" in msgs
    assert "Wire W1: no wire part number" in msgs


def test_compress_refs():
    assert compress_refs(["W3", "W1", "W2", "W5"]) == "W1-W3, W5"
    assert compress_refs(["P2", "P3"]) == "P2, P3"


def test_side_assignment():
    design, _ = load_design("y.csv", (SAMPLES / "y_harness_wirelist.csv").read_bytes())
    left, right = assign_sides(design)
    assert [c.ref for c in left] == ["P1"] and [c.ref for c in right] == ["P2", "P3"]


@pytest.mark.parametrize("size", list(SHEET_SIZES))
def test_drawing_sheets(size):
    design = two_connector_design()
    design.title_block.drawing_number = "W101-001"
    sheets, warnings = build_drawing(design, size)
    assert len(sheets) == 3 and not warnings
    svg1 = sheet_to_svg(sheets[0])
    for text in ("D38999/26WD35SN", "BILL OF MATERIALS", "48 IN", "W101-001", "1 OF 3", "W101-P1"):
        assert text in svg1
    svg2 = sheet_to_svg(sheets[1])
    assert "RS422 TX+" in svg2 and "W3  22 AWG  WHT" in svg2
    assert "LABEL SCHEDULE" in sheet_to_svg(sheets[2])
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(sheets_to_pdf(sheets)))
    assert len(reader.pages) == 3
    w, h = SHEET_SIZES[size][1:]
    assert abs(float(reader.pages[0].mediabox.width) - w) < 1 and abs(float(reader.pages[0].mediabox.height) - h) < 1
    assert "BILL OF MATERIALS" in reader.pages[0].extract_text()


def test_long_wire_list_continues_on_more_sheets():
    wires = [Wire(f"W{i}", "P1", str(i), "P2", str(i), wire_pn="M22759/16-22-9", label_pn="M1") for i in range(1, 121)]
    design = CableDesign(connectors=[ConnectorEnd("P1", "C1"), ConnectorEnd("P2", "C2")], wires=wires, overall_length=24)
    sheets, _ = build_drawing(design)
    assert len(sheets) > 3
    joined = "".join(sheet_to_svg(s) for s in sheets[2:])
    assert "WIRE LIST (CONTINUED)" in joined and ">W120<" in joined
    assert f"{len(sheets)} OF {len(sheets)}" in sheet_to_svg(sheets[-1])


def test_project_round_trip():
    design = two_connector_design()
    design.title_block.drawing_number = "W101-001"
    design.part_descriptions["D38999/26WD35SN"] = "CONN, PLUG, 37 SKT"
    reloaded, msgs = load_design("project.xlsx", save_design(design))
    assert not msgs
    assert reloaded.title_block.drawing_number == "W101-001"
    assert reloaded.overall_length == 48
    assert [(w.wire_id, w.from_ref, w.from_pin, w.to_ref, w.to_pin, w.wire_pn) for w in reloaded.wires] == \
        [(w.wire_id, w.from_ref, w.from_pin, w.to_ref, w.to_pin, w.wire_pn) for w in design.wires]
    assert reloaded.connector("P2").backshell_pn == "M85049/38S15W"
    assert reloaded.connector("P2").label_text == "W101-P2"
    assert reloaded.part_descriptions["D38999/26WD35SN"] == "CONN, PLUG, 37 SKT"
    assert reloaded.notes == design.notes


def test_excel_wirelist_with_connectors_sheet():
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as xw:
        pd.DataFrame({"From": ["J1-1", "J1-2"], "To": ["J2-1", "J2-2"], "Gauge": [22, 22]}).to_excel(xw, sheet_name="Wiring", index=False)
        pd.DataFrame({"Ref Des": ["J1", "J2"], "Connector PN": ["A", "B"], "Backshell": ["BS", "BS"]}).to_excel(xw, sheet_name="Connectors", index=False)
    design, msgs = load_design("list.xlsx", buf.getvalue())
    assert not msgs
    assert [w.wire_id for w in design.wires] == ["W1", "W2"] and design.wires[0].gauge == "22"
    assert design.connector("J2").connector_pn == "B" and design.connector("J1").backshell_pn == "BS"


def test_cli(tmp_path, capsys):
    out, svg_dir, bom = tmp_path / "w.pdf", tmp_path / "svg", tmp_path / "bom.csv"
    rc = main([str(SAMPLES / "y_harness_wirelist.csv"), "-c", str(SAMPLES / "y_harness_connectors.csv"),
               "-o", str(out), "--svg", str(svg_dir), "--bom", str(bom), "--dwg-no", "W200", "--sheet", "D"])
    assert rc == 0
    assert out.read_bytes().startswith(b"%PDF")
    assert sorted(p.name for p in svg_dir.iterdir()) == ["W200_sheet1.svg", "W200_sheet2.svg", "W200_sheet3.svg"]
    bom_df = pd.read_csv(bom)
    assert bom_df.loc[bom_df["Part Number"] == "M22759/16-22-2", "Qty"].item() == 108.5  # W1 18+30, W4 18+42.5
    assert "Wrote 3-sheet drawing" in capsys.readouterr().out


def test_cable_app_runs():
    pytest.importorskip("streamlit")
    from streamlit.testing.v1 import AppTest

    at = AppTest.from_file(str(ROOT / "cable_app.py"), default_timeout=60)
    at.run()
    assert not at.exception
    at.selectbox(key="sample").select("Y-harness with breakout (W200)").run()
    at.button[0].click().run()
    assert not at.exception
    assert [m.value for m in at.metric] == ["8", "3", "11", "3"]
