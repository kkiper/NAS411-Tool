"""NAS 411 Hazardous Material Screening - Streamlit web app.

Run with:  streamlit run app.py
"""

from __future__ import annotations

import pandas as pd
import streamlit as st

from nas411_tool.extract import SUPPORTED_EXTENSIONS, extract_text
from nas411_tool.matcher import (
    CONFIDENCE_RANK,
    STATUS_ABSENT,
    STATUS_IDENTIFIED,
    STATUS_POSSIBLE,
    STATUS_UNLISTED,
    Matcher,
    hits_to_dataframe,
    summarize,
)
from nas411_tool.reference import (
    build_reference,
    enrich_with_synonyms,
    guess_columns,
    load_starter_reference,
    merge_references,
    read_reference_table,
)
from nas411_tool.report import DISCLAIMER, build_excel_report

st.set_page_config(page_title="NAS 411 Material Screening", page_icon=":mag:", layout="wide")

STATUS_COLORS = {
    STATUS_IDENTIFIED: "#f8d7da",
    STATUS_POSSIBLE: "#fff3cd",
    STATUS_ABSENT: "#d1e7dd",
    STATUS_UNLISTED: "#e2e3e5",
}


@st.cache_data(show_spinner=False)
def _read_table(name: str, data: bytes) -> pd.DataFrame:
    return read_reference_table(name, data)


@st.cache_data(show_spinner=False)
def _extract(name: str, data: bytes):
    return extract_text(name, data)


def _style_status(df: pd.DataFrame):
    def color(row):
        c = STATUS_COLORS.get(row.get("Status"), "")
        return [f"background-color: {c}; color: #000" if c else "" for _ in row]

    return df.style.apply(color, axis=1)


# ---------------------------------------------------------------------------
# Sidebar: reference list
# ---------------------------------------------------------------------------
st.sidebar.header("1. Reference list")
st.sidebar.caption(
    "Import your copy of the **NAS 411-1 Hazardous Materials Target List** (Excel, CSV, or PDF). "
    "The official list is published by the Aerospace Industries Association (AIA) and isn't bundled with this tool."
)
ref_file = st.sidebar.file_uploader("NAS 411-1 list", type=["xlsx", "xlsm", "xls", "csv", "pdf"], key="ref")

starter = load_starter_reference()
reference = starter

if ref_file is not None:
    try:
        table = _read_table(ref_file.name, ref_file.getvalue())
    except Exception as exc:  # noqa: BLE001
        st.sidebar.error(f"Could not read {ref_file.name}: {exc}")
        table = None

    if table is not None and not table.empty:
        cols = list(table.columns)
        guess = guess_columns(cols)
        options = ["(none)", *cols]

        with st.sidebar.expander("Column mapping", expanded=not (guess.get("name") and guess.get("cas"))):
            st.caption("Check that each field points to the right column in your list.")
            mapping = {}
            for field, label in [
                ("name", "Substance name"),
                ("cas", "CAS number"),
                ("category", "Category (Prohibited / Restricted / Tracked)"),
                ("synonyms", "Synonyms"),
                ("basis", "Regulatory basis / driver"),
                ("notes", "Notes"),
            ]:
                default = guess.get(field)
                choice = st.selectbox(label, options, index=options.index(default) if default in cols else 0, key=f"map_{field}")
                mapping[field] = None if choice == "(none)" else choice

        if not mapping.get("name") and not mapping.get("cas"):
            st.sidebar.error("Map at least the substance-name or CAS column.")
        else:
            official = build_reference(table, mapping, source=ref_file.name)
            if st.sidebar.checkbox("Add common shop-term synonyms (recommended)", value=True,
                                   help="Copies synonyms such as 'hex chrome' and 'cad plated' from the built-in "
                                        "list onto your entries that share a CAS number or name."):
                official = enrich_with_synonyms(official, load_starter_reference())
            if st.sidebar.checkbox("Also screen starter-list substances not in my list", value=False):
                reference = merge_references(official, starter)
            else:
                reference = official
            st.sidebar.success(f"Loaded {len(official)} entries from {ref_file.name}")
            if ref_file.name.lower().endswith(".pdf"):
                st.sidebar.warning("Lists parsed from a PDF should be reviewed (see the Reference list tab).")
else:
    st.sidebar.warning(
        "Using the built-in **starter list** of commonly targeted aerospace hazardous materials. "
        "It isn't the official NAS 411-1 list and has no NAS 411-1 categories. Import your NAS 411-1 copy above "
        "for an authoritative comparison."
    )

st.sidebar.header("2. Options")
use_indicators = st.sidebar.checkbox(
    "Detect spec and alloy callouts", value=True,
    help="Flags designations that imply a substance, e.g. QQ-P-416 (cadmium plating), "
         "MIL-DTL-5541 Type I (hexavalent chromium), UNS C17200 (beryllium copper), Sn63Pb37 (lead).",
)
min_conf = st.sidebar.select_slider("Minimum confidence to show", options=["Info", "Low", "Medium", "High"], value="Low")
show_absent = st.sidebar.checkbox("Show 'stated absent / free' findings", value=True)

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
st.title("NAS 411 Hazardous Material Screening")
st.write(
    "Upload hardware datasheets, material declarations, or SDSs. The tool extracts their text and compares it with "
    "the NAS 411-1 Hazardous Materials Target List by **CAS number**, **substance name and synonyms**, and "
    "**spec/alloy callouts** that imply a listed material."
)

tab_scan, tab_ref, tab_about = st.tabs(["Screen datasheets", "Reference list", "About NAS 411"])

with tab_scan:
    uploads = st.file_uploader(
        "Hardware datasheets", type=[e.lstrip(".") for e in SUPPORTED_EXTENSIONS], accept_multiple_files=True
    )
    pasted = st.text_area("...or paste datasheet text", height=120, placeholder="Paste material/finish callouts here")

    docs = []
    for up in uploads or []:
        docs.append(_extract(up.name, up.getvalue()))
    if pasted.strip():
        docs.append(extract_text("Pasted text.txt", pasted.encode("utf-8")))

    if not docs:
        st.info("Upload one or more datasheets to begin. Supported types: " + ", ".join(SUPPORTED_EXTENSIONS))
    else:
        matcher = Matcher(reference, use_indicators=use_indicators)
        hits = []
        file_info = []
        for doc in docs:
            hits.extend(matcher.scan(doc))
            file_info.append({"File": doc.filename, "Characters extracted": doc.char_count, "Warnings": " ".join(doc.warnings)})
            for w in doc.warnings:
                st.warning(f"**{doc.filename}**: {w}")

        hits = [h for h in hits if CONFIDENCE_RANK[h.confidence] >= CONFIDENCE_RANK[min_conf]]
        summary = summarize(hits)
        details = hits_to_dataframe(hits)
        if not show_absent and not summary.empty:
            summary = summary[summary["Status"] != STATUS_ABSENT]

        # Overview metrics
        identified = summary[summary["Status"] == STATUS_IDENTIFIED] if not summary.empty else summary
        possible = summary[summary["Status"] == STATUS_POSSIBLE] if not summary.empty else summary
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Datasheets", len(docs))
        c2.metric("Identified materials", len(identified))
        c3.metric("Possible (review)", len(possible))
        c4.metric("Reference entries", len(reference))

        if summary.empty:
            st.success("No reference-list materials were found in the uploaded text.")
        else:
            st.subheader("Findings by datasheet")
            if len(docs) > 1 and not identified.empty:
                matrix = pd.crosstab(identified["Substance"], identified["File"]).astype(bool)
                matrix = matrix.replace({True: "X", False: ""})
                with st.expander("Cross-reference matrix (identified materials x datasheets)", expanded=False):
                    st.dataframe(matrix, width="stretch")

            for doc in docs:
                part = summary[summary["File"] == doc.filename]
                n_id = int((part["Status"] == STATUS_IDENTIFIED).sum()) if not part.empty else 0
                with st.expander(f"{doc.filename} - {n_id} identified, {len(part)} total findings", expanded=True):
                    if part.empty:
                        st.write("No findings.")
                        continue
                    st.dataframe(
                        _style_status(part.drop(columns=["File"])),
                        width="stretch",
                        hide_index=True,
                    )
                    d = details[details["File"] == doc.filename].drop(columns=["File"])
                    if st.toggle("Show every hit with context", key=f"ctx_{doc.filename}"):
                        st.dataframe(d, width="stretch", hide_index=True)

            st.subheader("Download report")
            col_a, col_b, col_c = st.columns(3)
            col_a.download_button(
                "Excel report (.xlsx)",
                build_excel_report(summary, details, reference, file_info),
                file_name="nas411_screening_report.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )
            col_b.download_button("Summary (.csv)", summary.to_csv(index=False), file_name="nas411_summary.csv", mime="text/csv")
            col_c.download_button("All hits (.csv)", details.to_csv(index=False), file_name="nas411_hits.csv", mime="text/csv")

        st.caption(DISCLAIMER)

with tab_ref:
    st.subheader(f"Active reference list: {reference.source}")
    st.write(f"{len(reference)} entries. Use this tab to check that your imported list was parsed correctly.")
    ref_df = reference.to_dataframe()
    query = st.text_input("Filter", placeholder="Name, CAS, or synonym")
    if query:
        mask = ref_df.apply(lambda r: r.astype(str).str.contains(query, case=False, regex=False).any(), axis=1)
        ref_df = ref_df[mask]
    st.dataframe(ref_df, width="stretch", hide_index=True)
    st.download_button("Download parsed list (.csv)", reference.to_dataframe().to_csv(index=False),
                       file_name="nas411_reference_parsed.csv", mime="text/csv")

with tab_about:
    st.markdown(
        """
**NAS 411 - Hazardous Materials Management Program (HMMP).** An AIA National Aerospace Standard that sets out
how a contractor plans and runs a program to identify, track, reduce, and eliminate hazardous materials in the
systems it delivers. It defines the HMMP process, including reporting and the selection of hazardous materials
that are to be avoided.

**NAS 411-1 - Hazardous Material Target List (HMTL).** The companion list of hazardous materials (identified by
name and CAS number) that NAS 411 programs target. Each entry has a category (for example *Prohibited*,
*Restricted*, or *Tracked*) that sets how it is to be handled in a program.

**How this tool uses them**

* Datasheet text is compared with the NAS 411-1 list you import (name, synonyms, and CAS numbers).
* Engineering callouts that imply a listed material are also flagged. For example, *QQ-P-416* is cadmium
  plating, *MIL-DTL-5541 Type I* is a hexavalent chromium conversion coating, and *UNS C17200* is beryllium copper.
* Statements such as *"lead-free"*, *"non-chromate"* or *"contains no mercury"* are reported as
  **Stated absent / free** so you can see them, but they don't count as identifications.
* Non-chemical uses of words, such as *"lead time"*, *"axial leads"* or *"mm Hg"*, are downgraded to **Low** confidence.

**Limitations.** Datasheets often don't list every constituent. Proprietary alloys, platings, primers, sealants,
adhesives, and lubricants may contain listed substances that are never named. Treat a clean result as "nothing
found in the text", not as proof that the hardware is free of hazardous materials. Request material declarations
or SDSs from suppliers to confirm. Scanned PDFs need OCR before upload.
"""
    )
