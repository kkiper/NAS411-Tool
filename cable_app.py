"""Cable assembly drawing tool - Streamlit web app.

Run with:  streamlit run cable_app.py
"""

from __future__ import annotations

import base64
import hashlib
import io
import zipfile
from datetime import date
from pathlib import Path

import pandas as pd
import streamlit as st

from cable_tool.bom import bom_dataframe, build_bom, check_design
from cable_tool.canvas import sheet_to_svg, sheets_to_pdf
from cable_tool.drawing import DEFAULT_SHEET, SHEET_SIZES, build_drawing
from cable_tool.model import DEFAULT_NOTES, CableDesign, TitleBlock
from cable_tool.project import (
    CONNECTOR_COLUMNS,
    CONNECTOR_HEADERS,
    WIRE_HEADERS,
    _read_raw_sheets,
    _with_header,
    add_connectors,
    connectors_from_dataframe,
    connectors_to_dataframe,
    load_design,
    save_design,
    wires_from_dataframe,
    wires_to_dataframe,
)

st.set_page_config(page_title="Cable Drawing Tool", page_icon=":electric_plug:", layout="wide")

SAMPLES = Path(__file__).parent / "samples"
SAMPLE_SETS = {
    "Two-connector cable (W101)": ("cable_wirelist.csv", "cable_connectors.csv", 48.0, "W101-001"),
    "Y-harness with breakout (W200)": ("y_harness_wirelist.csv", "y_harness_connectors.csv", 0.0, "W200-001"),
}
PART_COLS = ["P/N", "Description"]
TB_FIELDS = ("title", "drawing_number", "revision", "company", "drawn_by", "checked_by", "scale")
ss = st.session_state


def read_connector_table(name: str, data: bytes) -> list:
    raw = next(iter(_read_raw_sheets(name, data).values()))
    return connectors_from_dataframe(_with_header(raw, CONNECTOR_COLUMNS, {"ref"}))


def apply_design(design: CableDesign) -> None:
    """Replace all inputs with ``design``. Must run before any widget is created."""
    ss.wires = wires_to_dataframe(design.wires)
    ss.conns = connectors_to_dataframe(design.connectors)
    ss.parts = pd.DataFrame(sorted(design.part_descriptions.items()), columns=PART_COLS)
    ss.notes = "\n".join(design.notes)
    for f in TB_FIELDS:
        ss[f"tb_{f}"] = getattr(design.title_block, f)
    ss.units = design.units if design.units in ("IN", "MM") else "IN"
    ss.tolerance = design.tolerance
    ss.overall_length = float(design.overall_length or 0.0)
    for k in ("wires_rev", "conns_rev", "parts_rev", "notes_rev"):
        ss[k] = ss.get(k, 0) + 1


def queue_design(design: CableDesign) -> None:
    ss.pending_design = design
    st.rerun()


def sample_design(name: str) -> CableDesign:
    wires_file, conns_file, length, dwg_no = SAMPLE_SETS[name]
    design, _ = load_design(wires_file, (SAMPLES / wires_file).read_bytes())
    add_connectors(design, read_connector_table(conns_file, (SAMPLES / conns_file).read_bytes()))
    design.overall_length = length or None
    design.title_block = TitleBlock(drawing_number=dwg_no)
    return design


if "wires" not in ss:
    apply_design(CableDesign())
if "pending_design" in ss:
    apply_design(ss.pop("pending_design"))

# ---------------------------------------------------------------------------
# Sidebar: drawing settings
# ---------------------------------------------------------------------------
st.sidebar.header("Title block")
tb = TitleBlock(
    title=st.sidebar.text_input("Title", key="tb_title"),
    drawing_number=st.sidebar.text_input("Drawing / part number", key="tb_drawing_number"),
    revision=st.sidebar.text_input("Revision", key="tb_revision"),
    company=st.sidebar.text_input("Company", key="tb_company"),
    drawn_by=st.sidebar.text_input("Drawn by", key="tb_drawn_by"),
    checked_by=st.sidebar.text_input("Checked by", key="tb_checked_by"),
    date=st.sidebar.date_input("Date", value=date.today(), key="tb_date").strftime("%Y-%m-%d"),
    scale=st.sidebar.text_input("Scale", key="tb_scale"),
)
st.sidebar.header("Sheet and lengths")
sheet_size = st.sidebar.selectbox("Sheet size", list(SHEET_SIZES), index=list(SHEET_SIZES).index(DEFAULT_SHEET))
units = st.sidebar.selectbox("Units", ["IN", "MM"], key="units")
tolerance = st.sidebar.text_input("Length tolerance (±)", key="tolerance")
overall = st.sidebar.number_input(
    "Overall length (two-connector cables)", min_value=0.0, step=1.0, key="overall_length",
    help="Face-to-face length of a point-to-point cable. For a harness with a breakout, enter each "
         "connector's length to the breakout in the Connectors table instead. Per-wire lengths in the "
         "wire list override both.",
)

# ---------------------------------------------------------------------------
# 1. Inputs
# ---------------------------------------------------------------------------
st.title("Cable Drawing Tool")
st.write(
    "Load a wiring list, enter the **connector, backshell, heatshrink, label and wire part numbers**, and "
    "generate a drawing package: assembly view with item balloons, bill of materials, wiring diagram, "
    "wire list, and label schedule."
)

st.subheader("1. Wiring list")
c1, c2 = st.columns([3, 2])
with c1:
    up = st.file_uploader(
        "Wiring list or saved project (.xlsx, .csv)", type=["xlsx", "xlsm", "xls", "csv", "tsv", "txt"],
        help="Needs From and To columns (P1 and 3, or P1-3). Wire ID, Signal, Gauge, Color, Wire P/N, Length, "
             "Wire Label P/N and Wire Heatshrink P/N are optional. A workbook can also have Connectors, Parts, "
             "Title Block and Notes sheets; the project file this tool saves has all of them.",
    )
    if up is not None:
        digest = hashlib.sha1(up.getvalue()).hexdigest()
        if ss.get("wires_digest") != digest:
            ss.wires_digest = digest
            try:
                loaded, ss.load_msgs = load_design(up.name, up.getvalue())
            except Exception as exc:  # noqa: BLE001
                ss.load_msgs = [f"Could not read {up.name}: {exc}"]
            else:
                if not loaded.title_block.drawing_number:
                    loaded.title_block.drawing_number = ss.tb_drawing_number
                queue_design(loaded)
    for m in ss.get("load_msgs", []):
        st.warning(m)
with c2:
    sample = st.selectbox("...or start from a sample", ["(none)", *SAMPLE_SETS], key="sample")
    if st.button("Load sample", disabled=sample == "(none)"):
        ss.load_msgs = []
        queue_design(sample_design(sample))
    conn_up = st.file_uploader("Connector table (optional .xlsx/.csv)", type=["xlsx", "xls", "csv"],
                               help="Columns: Ref, Description, Connector P/N, Backshell P/N, Heatshrink P/N, "
                                    "Label P/N, Label Text, Length.")
    if conn_up is not None:
        digest = hashlib.sha1(conn_up.getvalue()).hexdigest()
        if ss.get("conn_digest") != digest:
            ss.conn_digest = digest
            current = {c.ref: c for c in connectors_from_dataframe(ss.get(f"conns_current_{ss.conns_rev}", ss.conns))}
            for c in read_connector_table(conn_up.name, conn_up.getvalue()):
                current[c.ref] = c
            ss.conns = connectors_to_dataframe(list(current.values()))
            ss.conns_rev += 1

st.subheader("2. Parts and connections")
tab_wires, tab_conns, tab_parts, tab_notes = st.tabs(["Wire list", "Connectors", "Part descriptions", "Notes"])


def _typed(df: pd.DataFrame) -> pd.DataFrame:
    return df.astype({c: ("float" if c == "Length" else "string") for c in df.columns})


def _columns(headers) -> dict:
    cfg = {h: st.column_config.TextColumn(h) for h in headers}
    cfg["Length"] = st.column_config.NumberColumn("Length", min_value=0.0, format="%.2f")
    return cfg


with tab_wires:
    st.caption("One row per wire. Wire label and heatshrink part numbers are installed at both ends of the wire.")
    wires_df = st.data_editor(_typed(ss.wires), num_rows="dynamic", width="stretch", key=f"wires_{ss.wires_rev}",
                              column_config=_columns(WIRE_HEADERS.values()))
    wires, wire_msgs = wires_from_dataframe(wires_df)

with tab_conns:
    st.caption(
        "One row per connector end; connectors named in the wire list are added automatically. **Length** is "
        "from the connector face to the breakout. For a simple two-connector cable, leave it blank and use "
        "*Overall length* in the sidebar."
    )
    base = ss.get(f"conns_current_{ss.conns_rev}", ss.conns)
    known = set(base["Ref"].dropna().astype(str))
    missing = []
    for w in wires:
        for ref in (w.from_ref, w.to_ref):
            if ref not in known and ref not in missing:
                missing.append(ref)
    if missing:
        ss.conns = pd.concat([base, pd.DataFrame({"Ref": missing})], ignore_index=True)
        ss.conns_rev += 1
    conns_df = st.data_editor(_typed(ss.conns), num_rows="dynamic", width="stretch", key=f"conns_{ss.conns_rev}",
                              column_config=_columns(CONNECTOR_HEADERS.values()))
    ss[f"conns_current_{ss.conns_rev}"] = conns_df
    connectors = connectors_from_dataframe(conns_df)

design = CableDesign(title_block=tb, connectors=connectors, wires=wires, units=units, tolerance=tolerance,
                     overall_length=overall or None)

with tab_parts:
    st.caption("Descriptions for the bill of materials. Blank ones get a generic description (CONNECTOR, BACKSHELL, ...).")
    base = ss.get(f"parts_current_{ss.parts_rev}", ss.parts)
    have = set(base["P/N"].dropna().astype(str))
    new_pns = [b.pn for b in build_bom(design) if b.pn not in have]
    if new_pns:
        ss.parts = pd.concat([base, pd.DataFrame({"P/N": new_pns, "Description": ""})], ignore_index=True)
        ss.parts_rev += 1
    parts_df = st.data_editor(ss.parts.astype("string"), num_rows="dynamic", width="stretch",
                              key=f"parts_{ss.parts_rev}")
    ss[f"parts_current_{ss.parts_rev}"] = parts_df
    design.part_descriptions = {
        str(r["P/N"]).strip(): str(r["Description"]).strip()
        for _, r in parts_df.iterrows()
        if pd.notna(r["P/N"]) and pd.notna(r["Description"]) and str(r["Description"]).strip()
    }

with tab_notes:
    st.caption("One note per line. {UNITS}, {UNITS_NAME} and {TOL} are filled in. Notes for labels, wire markers, "
               "heatshrink and sheet references are added automatically.")
    notes_text = st.text_area("General notes", value=ss.notes or "\n".join(DEFAULT_NOTES), height=160,
                              key=f"notes_{ss.notes_rev}")
    design.notes = [n for n in notes_text.splitlines() if n.strip()]

# ---------------------------------------------------------------------------
# 3. Output
# ---------------------------------------------------------------------------
st.subheader("3. Drawing")
if not design.wires:
    st.info("Load a wiring list (or a sample), or type wires into the Wire list tab, to generate a drawing.")
    st.stop()

bom = build_bom(design)
sheets, layout_msgs = build_drawing(design, sheet_size)
issues = wire_msgs + check_design(design) + layout_msgs
m1, m2, m3, m4 = st.columns(4)
m1.metric("Wires", len(design.wires))
m2.metric("Connectors", len(design.connectors))
m3.metric("BOM items", len(bom))
m4.metric("Sheets", len(sheets))
if issues:
    with st.expander(f"Checks: {len(issues)} item(s) to review", expanded=True):
        for m in issues:
            st.warning(m)
else:
    st.success("No issues found.")

stem = (tb.drawing_number or "cable").replace("/", "_").replace(" ", "_")
svgs = [sheet_to_svg(s) for s in sheets]
zbuf = io.BytesIO()
with zipfile.ZipFile(zbuf, "w", zipfile.ZIP_DEFLATED) as z:
    for i, svg in enumerate(svgs, start=1):
        z.writestr(f"{stem}_sheet{i}.svg", svg)

d1, d2, d3, d4 = st.columns(4)
d1.download_button("Drawing (.pdf)", sheets_to_pdf(sheets, title=f"{tb.drawing_number} {tb.title}".strip()),
                   file_name=f"{stem}.pdf", mime="application/pdf", type="primary")
d2.download_button("Sheets (.svg, zipped)", zbuf.getvalue(), file_name=f"{stem}_svg.zip", mime="application/zip")
d3.download_button("Bill of materials (.csv)", bom_dataframe(bom, units).to_csv(index=False),
                   file_name=f"{stem}_bom.csv", mime="text/csv")
d4.download_button("Save project (.xlsx)", save_design(design), file_name=f"{stem}_project.xlsx",
                   mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                   help="Wire list, connectors, part descriptions, title block and notes in one workbook. "
                        "Upload it in step 1 to continue later.")

for tab, svg in zip(st.tabs([f"Sheet {i}: {s.name}" for i, s in enumerate(sheets, start=1)]), svgs):
    with tab:
        b64 = base64.b64encode(svg.encode("utf-8")).decode("ascii")
        st.markdown(f'<img src="data:image/svg+xml;base64,{b64}" '
                    'style="width:100%;background:#fff;border:1px solid #ccc"/>', unsafe_allow_html=True)

with st.expander("Bill of materials"):
    st.dataframe(bom_dataframe(bom, units), width="stretch", hide_index=True)
