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

from cable_tool.bom import bom_dataframe, build_bom
from cable_tool.canvas import sheet_to_svg, sheets_to_pdf
from cable_tool.drawing import DEFAULT_SHEET, SHEET_SIZES, build_drawing
from cable_tool.drc import ERROR, INFO, WARNING, run_drc
from cable_tool.dxf import sheet_to_dxf
from cable_tool.library import LIBRARY_HEADERS, PART_TYPES, library_from_dataframe, load_library
from cable_tool.model import DEFAULT_NOTES, CableDesign, TitleBlock
from cable_tool.project import (
    CONNECTOR_HEADERS,
    GROUP_HEADERS,
    SPLICE_HEADERS,
    WIRE_HEADERS,
    apply_table,
    connectors_from_dataframe,
    connectors_to_dataframe,
    groups_from_dataframe,
    groups_to_dataframe,
    library_with_descriptions,
    load_design,
    save_design,
    splices_from_dataframe,
    splices_to_dataframe,
    wires_from_dataframe,
    wires_to_dataframe,
)

st.set_page_config(page_title="Cable Drawing Tool", page_icon=":electric_plug:", layout="wide")

SAMPLES = Path(__file__).parent / "samples"
SAMPLE_SETS = {
    "Two-connector cable with shielded pair and splice (W101)": dict(
        wires="cable_wirelist.csv", connectors="cable_connectors.csv", groups="cable_groups.csv",
        splices="cable_splices.csv", parts="parts_library.csv", length=48.0, dwg="W101-001"),
    "Y-harness with breakout (W200)": dict(
        wires="y_harness_wirelist.csv", connectors="y_harness_connectors.csv", parts="parts_library.csv",
        length=0.0, dwg="W200-001"),
}
TABLES = ("wires", "conns", "groups", "splices", "parts")
NUMERIC_COLS = {"Length", "Distance", "AWG", "OD", "AWG Min", "AWG Max", "Dia Min", "Dia Max", "CMA Min", "CMA Max",
                "Contacts", "Conductors", "Wall"}
TB_FIELDS = ("title", "drawing_number", "revision", "company", "drawn_by", "checked_by", "scale")
SEVERITY_COLORS = {ERROR: "#f8d7da", WARNING: "#fff3cd", INFO: "#e2e3e5"}
ss = st.session_state


def apply_design(design: CableDesign) -> None:
    """Replace all inputs with ``design``. Must run before any widget is created."""
    ss.wires = wires_to_dataframe(design.wires)
    ss.conns = connectors_to_dataframe(design.connectors)
    ss.groups = groups_to_dataframe(design.groups)
    ss.splices = splices_to_dataframe(design.splices)
    ss.parts = library_with_descriptions(design).to_dataframe()
    ss.notes = "\n".join(design.notes)
    for f in TB_FIELDS:
        ss[f"tb_{f}"] = getattr(design.title_block, f)
    ss.units = design.units if design.units in ("IN", "MM") else "IN"
    ss.tolerance = design.tolerance
    ss.overall_length = float(design.overall_length or 0.0)
    for k in (*TABLES, "notes"):
        ss[f"{k}_rev"] = ss.get(f"{k}_rev", 0) + 1


def queue_design(design: CableDesign) -> None:
    ss.pending_design = design
    st.rerun()


def sample_design(name: str) -> CableDesign:
    spec = SAMPLE_SETS[name]
    design, _ = load_design(spec["wires"], (SAMPLES / spec["wires"]).read_bytes())
    for kind, key in (("parts", "parts"), ("connectors", "connectors"), ("groups", "groups"), ("splices", "splices")):
        if spec.get(key):
            apply_table(design, kind, spec[key], (SAMPLES / spec[key]).read_bytes())
    design.overall_length = spec["length"] or None
    design.title_block = TitleBlock(drawing_number=spec["dwg"])
    return design


def merge_rows(key: str, new: pd.DataFrame, id_col: str) -> None:
    """Add or replace rows (by ``id_col``) in a table, keeping unsaved edits."""
    base = ss.get(f"{key}_current_{ss[f'{key}_rev']}", ss[key])
    keep = base[~base[id_col].astype(str).isin(set(new[id_col].astype(str)))]
    ss[key] = pd.concat([keep, new], ignore_index=True)
    ss[f"{key}_rev"] += 1


def editor(key: str, headers, missing=None, extra_config=None, help_text: str = "") -> pd.DataFrame:
    """Editable table whose edits survive reruns. ``missing(base)`` returns rows to append."""
    rev = ss[f"{key}_rev"]
    base = ss.get(f"{key}_current_{rev}", ss[key])
    if missing is not None:
        add = missing(base)
        if add is not None and not add.empty:
            ss[key] = pd.concat([base, add], ignore_index=True)
            ss[f"{key}_rev"] += 1
    df = ss[key].astype({c: ("float" if c in NUMERIC_COLS else "string") for c in ss[key].columns})
    config = {h: (st.column_config.NumberColumn(h, format="%g") if h in NUMERIC_COLS else st.column_config.TextColumn(h))
              for h in headers}
    config.update(extra_config or {})
    if help_text:
        st.caption(help_text)
    out = st.data_editor(df, num_rows="dynamic", width="stretch", key=f"{key}_{ss[f'{key}_rev']}", column_config=config)
    ss[f"{key}_current_{ss[f'{key}_rev']}"] = out
    return out


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
    "Load a wiring list, enter the **connector, contact, backshell, heatshrink, label and wire part numbers**, "
    "define **twisted / shielded groups** and **splices**, and reference a **parts library** for the design rule "
    "check. The output is a drawing package (PDF, SVG, DXF) with assembly view, bill of materials, wiring diagram, "
    "wire list, group and splice tables, and label schedule."
)

st.subheader("1. Load")
c1, c2, c3 = st.columns(3)
with c1:
    up = st.file_uploader(
        "Wiring list or saved project (.xlsx, .csv)", type=["xlsx", "xlsm", "xls", "csv", "tsv", "txt"],
        help="Needs From and To columns (P1 and 3, or P1-3). Refs like SP1 are splices. Optional: Wire ID, Signal, "
             "Gauge, Color, Wire P/N, Length, Wire Label P/N, Wire Heatshrink P/N, Group. A workbook can also have "
             "Connectors, Groups, Splices, Parts Library, Title Block and Notes sheets.",
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
                if not len(loaded.library):   # keep the parts library already loaded
                    current = ss.get(f"parts_current_{ss.parts_rev}", ss.parts)
                    loaded.library = library_from_dataframe(current, source="Parts library tab")
                queue_design(loaded)
    for m in ss.get("load_msgs", []):
        st.warning(m)
with c2:
    sample = st.selectbox("...or start from a sample", ["(none)", *SAMPLE_SETS], key="sample")
    if st.button("Load sample", disabled=sample == "(none)"):
        ss.load_msgs = []
        queue_design(sample_design(sample))
with c3:
    lib_up = st.file_uploader(
        "Parts library (.xlsx, .csv)", type=["xlsx", "xls", "csv"], key="lib_up",
        help="Your master parts list with part parameters (AWG, OD, wire/diameter ranges, contact P/N, ...). "
             "It's merged into the Parts library tab; rows with the same P/N are replaced.",
    )
    if lib_up is not None:
        digest = hashlib.sha1(lib_up.getvalue()).hexdigest()
        if ss.get("lib_digest") != digest:
            ss.lib_digest = digest
            try:
                lib = load_library(lib_up.name, lib_up.getvalue())
            except Exception as exc:  # noqa: BLE001
                st.error(f"Could not read {lib_up.name}: {exc}")
            else:
                merge_rows("parts", lib.to_dataframe(), "P/N")
                st.success(f"Loaded {len(lib)} parts from {lib_up.name}.")

st.subheader("2. Design")
tabs = st.tabs(["Wire list", "Connectors", "Groups & shields", "Splices", "Parts library", "Notes"])

with tabs[0]:
    wires_df = editor("wires", WIRE_HEADERS.values(), help_text=(
        "One row per wire. Use a splice ref (SP1, SP2...) as From or To to splice wires. Put wires that are twisted, "
        "shielded or part of one cable in the same **Group**. Wire label and heatshrink P/Ns go on both wire ends."))
    wires, wire_msgs = wires_from_dataframe(wires_df)

design = CableDesign(title_block=tb, wires=wires, units=units, tolerance=tolerance, overall_length=overall or None)


def _missing(column: str, wanted: list[str]):
    def fn(base: pd.DataFrame):
        have = set(base[column].dropna().astype(str))
        new = [r for r in dict.fromkeys(wanted) if r and r not in have]
        return pd.DataFrame({column: new}) if new else None
    return fn


with tabs[3]:
    splice_refs = [r for w in wires for r in (w.from_ref, w.to_ref) if design.is_splice(r)]
    splices_df = editor("splices", SPLICE_HEADERS.values(), missing=_missing("Ref", splice_refs), help_text=(
        "Splices named in the wire list are added automatically. **Near** is the connector whose leg the splice is "
        "on and **Distance** is measured from that connector's face; both set the wire lengths to the splice."))
    design.splices = splices_from_dataframe(splices_df)

with tabs[1]:
    conn_refs = [r for w in wires for r in (w.from_ref, w.to_ref) if not design.is_splice(r)]
    conns_df = editor("conns", CONNECTOR_HEADERS.values(), missing=_missing("Ref", conn_refs), help_text=(
        "One row per connector end; connectors named in the wire list are added automatically. **Contact P/N** "
        "overrides the library's default contact for that connector. **Length** is from the connector face to the "
        "breakout. For a simple two-connector cable, leave it blank and use *Overall length* in the sidebar."))
    design.connectors = connectors_from_dataframe(conns_df)

with tabs[2]:
    group_ids = [w.group for w in wires if w.group]
    groups_df = editor("groups", GROUP_HEADERS.values(), missing=_missing("Group", group_ids), help_text=(
        "Groups named in the wire list are added automatically. **Type**: TWISTED PAIR, TWISTED TRIPLE, SHIELDED, "
        "SHIELDED TWISTED PAIR, or JACKETED CABLE. **Cable P/N** makes the group one cable (its wires are its "
        "conductors); **Shield P/N** is braid over a built-up group. **Shield Term From / To**: BACKSHELL, FLOAT, "
        "or a pin (11 or P2-11) at the group's From / To connector."))
    design.groups = groups_from_dataframe(groups_df)

with tabs[4]:
    used = [b.pn for b in build_bom(design)]
    parts_df = editor("parts", LIBRARY_HEADERS.values(), missing=_missing("P/N", used), extra_config={
        "Type": st.column_config.SelectboxColumn("Type", options=PART_TYPES)}, help_text=(
        "Part numbers used in the design are added automatically; fill in the parameters the design rule check "
        "needs. Dimensions in inches. **Dia Min/Max** means: contact or connector = wire sealing range; backshell = "
        "cable clamp range; heatshrink, label or marker = recovered ID / expanded ID; shield termination = range over "
        "the shield. **AWG Min/Max** = accepted wire size; **CMA Min/Max** = total wire area for splices."))
    design.library = library_from_dataframe(parts_df, source="Parts library tab")

with tabs[5]:
    st.caption("One note per line. {UNITS}, {UNITS_NAME} and {TOL} are filled in. Notes for labels, wire markers, "
               "heatshrink, twisted pairs, shields, splices, bundle diameter and sheet references are added automatically.")
    notes_text = st.text_area("General notes", value=ss.notes or "\n".join(DEFAULT_NOTES), height=160,
                              key=f"notes_{ss.notes_rev}")
    design.notes = [n for n in notes_text.splitlines() if n.strip()]

# ---------------------------------------------------------------------------
# 3. Design rule check
# ---------------------------------------------------------------------------
st.subheader("3. Design rule check")
if not design.wires:
    st.info("Load a wiring list (or a sample), or type wires into the Wire list tab, to check and draw the design.")
    st.stop()

report = run_drc(design)
counts = report.counts()
r1, r2, r3, r4 = st.columns(4)
r1.metric("Errors", counts[ERROR])
r2.metric("Warnings", counts[WARNING] + len(wire_msgs))
r3.metric("Info", counts[INFO])
sized = {ref: b.diameter for ref, b in report.bundles.items() if b.diameter}
r4.metric("Largest bundle Ø", f"{max(sized.values()):.3f} in" if sized else "n/a",
          help="Calculated bundle diameter at each connector: " + (", ".join(f"{r} {d:.3f} in" for r, d in sized.items()) or "n/a"))
for m in wire_msgs:
    st.warning(m)
if counts[ERROR] == 0 and counts[WARNING] == 0:
    st.success("No errors or warnings.")
drc_df = report.to_dataframe()
show_info = st.toggle("Show info findings", value=False)
view = drc_df if show_info else drc_df[drc_df["Severity"] != INFO]
if not view.empty:
    st.dataframe(view.style.apply(lambda r: [f"background-color: {SEVERITY_COLORS[r['Severity']]}; color: #000"] * len(r), axis=1),
                 width="stretch", hide_index=True)

# ---------------------------------------------------------------------------
# 4. Drawing
# ---------------------------------------------------------------------------
st.subheader("4. Drawing")
bom = build_bom(design)
sheets, layout_msgs = build_drawing(design, sheet_size)
for m in layout_msgs:
    st.warning(m)

stem = (tb.drawing_number or "cable").replace("/", "_").replace(" ", "_")
svgs = [sheet_to_svg(s) for s in sheets]


def _zip(ext: str, docs: list[str]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for i, doc in enumerate(docs, start=1):
            z.writestr(f"{stem}_sheet{i}.{ext}", doc)
    return buf.getvalue()


d1, d2, d3 = st.columns(3)
d1.download_button("Drawing (.pdf)", sheets_to_pdf(sheets, title=f"{tb.drawing_number} {tb.title}".strip()),
                   file_name=f"{stem}.pdf", mime="application/pdf", type="primary")
d2.download_button("CAD sheets (.dxf, zipped)", _zip("dxf", [sheet_to_dxf(s) for s in sheets]),
                   file_name=f"{stem}_dxf.zip", mime="application/zip",
                   help="AutoCAD R12 DXF, one file per sheet, true size in inches, with named layers.")
d3.download_button("Sheets (.svg, zipped)", _zip("svg", svgs), file_name=f"{stem}_svg.zip", mime="application/zip")
d4, d5, d6 = st.columns(3)
d4.download_button("Bill of materials (.csv)", bom_dataframe(bom, units).to_csv(index=False),
                   file_name=f"{stem}_bom.csv", mime="text/csv")
d5.download_button("Design rule check (.csv)", drc_df.to_csv(index=False), file_name=f"{stem}_drc.csv", mime="text/csv")
d6.download_button("Save project (.xlsx)", save_design(design), file_name=f"{stem}_project.xlsx",
                   mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                   help="Wire list, connectors, groups, splices, parts library, title block and notes in one "
                        "workbook. Upload it in step 1 to continue later.")

for tab, svg in zip(st.tabs([f"Sheet {i}: {s.name}" for i, s in enumerate(sheets, start=1)]), svgs):
    with tab:
        b64 = base64.b64encode(svg.encode("utf-8")).decode("ascii")
        st.markdown(f'<img src="data:image/svg+xml;base64,{b64}" '
                    'style="width:100%;background:#fff;border:1px solid #ccc"/>', unsafe_allow_html=True)

with st.expander("Bill of materials"):
    st.dataframe(bom_dataframe(bom, units), width="stretch", hide_index=True)
