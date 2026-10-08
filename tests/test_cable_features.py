"""Parts library, wire groups (twisted / shielded), splices, design rule check, and DXF output."""

import io
from pathlib import Path

import pandas as pd
import pytest

from cable_tool.bom import build_bom, check_design
from cable_tool.canvas import sheet_to_svg
from cable_tool.cli import main
from cable_tool.drawing import build_drawing
from cable_tool.drc import ERROR, INFO, WARNING, run_drc
from cable_tool.dxf import _clean_text, _offset_polyline, sheet_to_dxf
from cable_tool.library import (
    Part,
    PartsLibrary,
    bundle_diameter,
    circular_mils,
    library_from_dataframe,
    load_library,
    normalize_type,
    parse_awg,
)
from cable_tool.model import CableDesign, ConnectorEnd, Splice, Wire, WireGroup, parse_shield_term

SAMPLES = Path(__file__).resolve().parents[1] / "samples"


def lib(*parts: Part) -> PartsLibrary:
    return PartsLibrary({p.pn: p for p in parts})


def findings(design, rule=None, severity=None):
    return [f for f in run_drc(design).findings
            if (rule is None or f.rule == rule) and (severity is None or f.severity == severity)]


# --- Parts library ------------------------------------------------------------------------------
def test_library_columns_units_and_ranges():
    df = pd.DataFrame({
        "Part Number": ["C1", "BS1", "W22"],
        "Category": ["Pin", "Strain Relief", "Hookup wire"],
        "Accepted AWG": ["20-24", "", ""],
        "Clamp Min (mm)": ["", "5.08", ""],
        "Clamp Max (mm)": ["", "12.7", ""],
        "Insulation OD": ["", "", "0.052"],
        "Gauge": ["", "", "22"],
    })
    library = library_from_dataframe(df)
    assert library.get("C1").type == "contact" and library.get("C1").awg_range == (20, 24)
    bs = library.get("BS1")
    assert bs.type == "backshell" and bs.dia_range == pytest.approx((0.2, 0.5))   # mm converted to inches
    assert library.get("W22").type == "wire" and library.get("W22").od == 0.052 and library.get("W22").awg == 22


def test_load_sample_library():
    library = load_library("parts_library.csv", (SAMPLES / "parts_library.csv").read_bytes())
    assert len(library) == 27
    assert library.get("D38999/26WD18SN").contact_pn == "M39029/56-351"
    assert library.get("M81824/1-1").cma_max == 2100
    assert library.get("202K153-25/225-0").dia_range == (0.22, 0.6)


def test_wire_size_helpers():
    assert parse_awg("22 AWG") == 22 and parse_awg("4/0") == -3 and parse_awg("") is None
    assert circular_mils(22) == pytest.approx(642, rel=0.01)
    assert circular_mils(10) == pytest.approx(10380, rel=0.01)
    assert bundle_diameter([0.1]) == 0.1
    assert bundle_diameter([0.05] * 4) == pytest.approx(1.2 * 0.1)
    assert normalize_type("Solder Sleeve") == "splice" and normalize_type("Overbraid") == "shield"


# --- Groups and splices ---------------------------------------------------------------------------
def test_shield_term_parsing():
    assert parse_shield_term("backshell", "P1") == ("BACKSHELL", "")
    assert parse_shield_term("Float", "P1") == ("FLOAT", "")
    assert parse_shield_term("P2-11", "P2") == ("PIN", "11")
    assert parse_shield_term("11", "P2") == ("PIN", "11")
    assert parse_shield_term("", "P2") == ("", "")
    assert WireGroup("S1", "SHIELDED TWISTED PAIR").shielded and WireGroup("S1", "STP").twisted
    assert not WireGroup("T1", "TWISTED PAIR").shielded


def test_splice_lengths_and_detection():
    design = CableDesign(
        connectors=[ConnectorEnd("P1", length=10), ConnectorEnd("P2", length=20), ConnectorEnd("P3", length=30)],
        wires=[Wire("W1", "P1", "1", "SP1", ""), Wire("W2", "SP1", "", "P2", "1"), Wire("W3", "SP1", "", "P3", "1")],
        splices=[Splice("SP1", near="P2", distance=5)],
    )
    assert [design.wire_length(w) for w in design.wires] == [25, 5, 45]   # P1 leg + (P2 leg - 5), 5, ...
    assert design.is_splice("SP1") and design.is_splice("SP2") and not design.is_splice("P1")
    assert not any("isn't in the connector table" in m or "pin" in m for m in check_design(design))


def test_groups_in_wire_list_and_tables():
    design, _ = load_design_with_groups()
    assert {g.group_id for g in design.groups} == {"TSP1", "TP2"}
    assert [w.wire_id for w in design.group_members("TSP1")] == ["W3", "W4"]
    assert design.splice("SP1").near == "P2"
    assert design.pins_used("P2")[-3:] == ["10", "11", "12"]   # 11 is the TSP1 shield drain


def load_design_with_groups():
    from cable_tool.project import apply_table, load_design

    design, msgs = load_design("w.csv", (SAMPLES / "cable_wirelist.csv").read_bytes())
    for kind, name in (("parts", "parts_library.csv"), ("connectors", "cable_connectors.csv"),
                       ("groups", "cable_groups.csv"), ("splices", "cable_splices.csv")):
        apply_table(design, kind, name, (SAMPLES / name).read_bytes())
    design.overall_length = 48
    return design, msgs


def test_wiring_diagram_shows_groups_shields_and_splices():
    design, _ = load_design_with_groups()
    sheets, _ = build_drawing(design)
    svg = sheet_to_svg(sheets[1])
    assert ">SP1<" in svg and ">M81824/1-1<" in svg       # splice node
    assert ">TSP1<" in svg and ">TP2<" in svg             # group labels
    assert ">TSP1 SHIELD<" in svg and ">TSP1 SHLD<" in svg  # shield drain row on P2-11
    assert "stroke-dasharray" in svg                       # dashed shield oval
    assert "LEGEND:" in svg


# --- Design rule check ------------------------------------------------------------------------------
def base_design(**overrides) -> CableDesign:
    library = lib(
        Part("CONN", "connector", contact_pn="CT22", contacts=4),
        Part("CT22", "contact", awg_min=22, awg_max=28, dia_min=0.03, dia_max=0.054),
        Part("BS", "backshell", dia_min=0.10, dia_max=0.20),
        Part("BOOT", "heatshrink", dia_min=0.08, dia_max=0.40),
        Part("LBL", "label", dia_min=0.05, dia_max=0.30),
        Part("W22", "wire", awg=22, od=0.052),
        Part("W20", "wire", awg=20, od=0.062),
        Part("MK", "marker", dia_min=0.03, dia_max=0.06),
    )
    conn = dict(connector_pn="CONN", backshell_pn="BS", heatshrink_pn="BOOT", label_pn="LBL")
    design = CableDesign(
        connectors=[ConnectorEnd("P1", **conn), ConnectorEnd("P2", **conn)],
        wires=[Wire(f"W{i}", "P1", str(i), "P2", str(i), gauge="22", wire_pn="W22", label_pn="MK") for i in (1, 2, 3)],
        library=library, overall_length=24,
    )
    for k, v in overrides.items():
        setattr(design, k, v)
    return design


def test_clean_design_passes():
    design = base_design()
    report = run_drc(design)
    assert not report.errors and not report.warnings, report.findings
    assert report.bundles["P1"].diameter == pytest.approx(1.2 * (3 * 0.052 ** 2) ** 0.5)


def test_contact_wire_size_and_sealing():
    design = base_design()
    design.wires[0].gauge, design.wires[0].wire_pn = "20", "W20"
    errs = findings(design, "Contact wire size", ERROR)
    assert {f.item for f in errs} == {"P1-1", "P2-1"} and "accepts 22 to 28 AWG" in errs[0].message
    assert {f.item for f in findings(design, "Contact sealing range", WARNING)} == {"P1-1", "P2-1"}


def test_contact_count():
    design = base_design()
    design.wires += [Wire(f"W{i}", "P1", str(i), "P2", str(i), gauge="22", wire_pn="W22") for i in (4, 5)]
    assert [f.item for f in findings(design, "Contact count", ERROR)] == ["P1", "P2"]


def test_bundle_vs_backshell_boot_and_label():
    design = base_design()
    design.wires += [Wire(f"W{i}", "P1", str(i), "P2", str(i), gauge="22", wire_pn="W22") for i in range(4, 40)]
    design.library.get("CONN").contacts = 50
    msgs = {f.rule: f for f in findings(design, severity=ERROR)}
    assert "Backshell fit" in msgs and "won't pass through the backshell clamp" in msgs["Backshell fit"].message
    assert "Label fit" in msgs
    small = base_design(wires=[Wire("W1", "P1", "1", "P2", "1", gauge="22", wire_pn="W22")])
    rules = {f.rule for f in findings(small, severity=WARNING)}
    assert {"Backshell fit", "Boot fit"} <= rules    # 0.052 in is below clamp and boot minimums


def test_marker_fit():
    design = base_design()
    design.library.get("MK").dia_max = 0.04
    assert len(findings(design, "Wire marker/sleeve fit", ERROR)) == 3


def test_splice_ranges():
    design = base_design()
    design.library.add(Part("SPL", "splice", awg_min=20, awg_max=26, cma_min=300, cma_max=1500))
    design.wires = [Wire("W1", "P1", "1", "SP1", "", gauge="22", wire_pn="W22"),
                    Wire("W2", "SP1", "", "P2", "1", gauge="22", wire_pn="W22"),
                    Wire("W3", "SP1", "", "P2", "2", gauge="22", wire_pn="W22")]
    design.splices = [Splice("SP1", "SPL")]
    errs = findings(design, "Splice wire size", ERROR)
    assert len(errs) == 1 and "exceeds SPL max 1,500 CMA" in errs[0].message      # 3 x 22 AWG = 1,926 CMA
    design.wires[2].gauge = "28"
    errs = findings(design, "Splice wire size", ERROR)
    assert any("28 AWG; SPL accepts 20 to 26 AWG" in f.message for f in errs)


def test_group_rules():
    design = base_design()
    design.wires[0].group = design.wires[1].group = design.wires[2].group = "TP1"
    design.groups = [WireGroup("TP1", "TWISTED PAIR")]
    assert any("has 3 wires" in f.message for f in findings(design, "Groups", WARNING))
    design.wires[2].group = "S1"
    design.groups.append(WireGroup("S1", "SHIELDED", shield_term_pn="ST", term_from="FLOAT", term_to="FLOAT"))
    assert any("floats at both ends" in f.message for f in findings(design, "Shield termination", WARNING))
    design.groups[1].term_from, design.groups[1].term_to = "BACKSHELL", ""
    assert any("isn't specified" in f.message for f in findings(design, "Shield termination", WARNING))
    design.groups[1].term_to = "P2-2"     # pin already used by W2, which isn't in the group
    assert any("also carries W2" in f.message for f in findings(design, "Shield termination", WARNING))


def test_cable_group_od_and_conductor_count():
    design = base_design()
    design.library.add(Part("STP", "cable", awg=22, od=0.135, conductors=2))
    for w in design.wires[:2]:
        w.group, w.wire_pn = "C1", ""
    design.groups = [WireGroup("C1", "SHIELDED TWISTED PAIR", cable_pn="STP", term_from="BACKSHELL", term_to="FLOAT")]
    report = run_drc(design)
    assert report.bundles["P1"].diameter == pytest.approx(1.2 * (0.135 ** 2 + 0.052 ** 2) ** 0.5)
    assert report.bundles["P1"].count == 2
    design.wires[2].group, design.wires[2].wire_pn = "C1", ""
    assert any("2 conductors but 3 wires" in f.message for f in findings(design, "Groups", WARNING))
    assert {b.pn for b in build_bom(design) if b.category == "cable"} == {"STP"}
    assert not any(b.pn == "W22" for b in build_bom(design))


def test_part_type_mismatch_and_missing_parts():
    design = base_design()
    design.connectors[0].backshell_pn = "W22"          # a wire used as a backshell
    design.connectors[1].heatshrink_pn = "UNKNOWN-BOOT"
    assert any("is a 'wire' in the library but is used as a backshell" in f.message for f in findings(design, "Part type"))
    assert any(f.item == "UNKNOWN-BOOT" for f in findings(design, "Parts library", INFO))
    assert findings(CableDesign(wires=design.wires), "Parts library", INFO)[0].message.startswith("No parts library")


# --- DXF -------------------------------------------------------------------------------------------
def test_dxf_structure():
    design, _ = load_design_with_groups()
    sheets, _ = build_drawing(design)
    text = sheet_to_dxf(sheets[1])
    assert text.startswith("0\nSECTION\n2\nHEADER") and text.rstrip().endswith("EOF")
    assert "AC1009" in text and "\nSHIELDS\n" in text and "\nDASHED\n" in text
    ezdxf = pytest.importorskip("ezdxf")
    from ezdxf import recover

    doc, auditor = recover.read(io.BytesIO(text.encode()))
    assert not auditor.has_errors
    msp = doc.modelspace()
    texts = {e.dxf.text for e in msp.query("TEXT")}
    assert {"P1", "P2", "SP1", "TSP1", "WIRING DIAGRAM"} <= texts
    xs = [v[0] for e in msp.query("LINE") for v in (e.dxf.start, e.dxf.end)]
    assert 0 < min(xs) and max(xs) < 17     # true size on an ANSI B (17 x 11 in) sheet
    assert ezdxf.__version__


def test_dxf_helpers():
    assert _clean_text("±0.5 Ø1 …") == "%%p0.5 %%c1 ..."
    left = _offset_polyline([(0, 0), (10, 0), (20, 10)], 1)
    assert left[0] == pytest.approx((0, 1)) and len(left) == 3


# --- CLI ---------------------------------------------------------------------------------------------
def test_cli_dxf_drc_and_strict(tmp_path, capsys):
    args = [str(SAMPLES / "cable_wirelist.csv"), "-c", str(SAMPLES / "cable_connectors.csv"),
            "-g", str(SAMPLES / "cable_groups.csv"), "-s", str(SAMPLES / "cable_splices.csv"),
            "-l", str(SAMPLES / "parts_library.csv"), "--length", "48", "--dwg-no", "W101",
            "-o", str(tmp_path / "w.pdf"), "--dxf", str(tmp_path / "dxf"), "--drc", str(tmp_path / "drc.csv"), "--strict"]
    assert main(args) == 0
    assert sorted(p.name for p in (tmp_path / "dxf").iterdir()) == ["W101_sheet1.dxf", "W101_sheet2.dxf", "W101_sheet3.dxf"]
    drc = pd.read_csv(tmp_path / "drc.csv")
    assert "ERROR" not in set(drc["Severity"])
    # Swap in a connector whose contacts don't take 20 AWG: --strict now fails
    bad = (SAMPLES / "parts_library.csv").read_text().replace("M39029/56-351,contact,\"CONTACT, SOCKET, SIZE 20\",,,20,24",
                                                              "M39029/56-351,contact,\"CONTACT, SOCKET, SIZE 22D\",,,22,28")
    (tmp_path / "bad.csv").write_text(bad)
    args[args.index(str(SAMPLES / "parts_library.csv"))] = str(tmp_path / "bad.csv")
    assert main(args) == 1
    assert "error: [Contact wire size] P1-1: Wire W1 is 20 AWG" in capsys.readouterr().err
