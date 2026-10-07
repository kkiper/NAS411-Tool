"""Report export (Excel / CSV)."""

from __future__ import annotations

import io
from datetime import datetime

import pandas as pd

from .reference import ReferenceList

DISCLAIMER = (
    "This report is an automated screening aid. Text matching can miss substances that are not named in "
    "a datasheet (for example, constituents of proprietary alloys or coatings) and can flag terms used in "
    "a non-chemical sense. Confirm every finding against the current revision of NAS 411-1 and with the "
    "supplier (e.g. via a material declaration or SDS) before making HMMP decisions."
)


def build_excel_report(
    summary: pd.DataFrame,
    details: pd.DataFrame,
    reference: ReferenceList,
    files: list[dict],
) -> bytes:
    buf = io.BytesIO()
    about = pd.DataFrame(
        [
            ("Generated", datetime.now().strftime("%Y-%m-%d %H:%M")),
            ("Reference list", reference.source),
            ("Reference entries", len(reference)),
            ("Datasheets scanned", len(files)),
            ("Disclaimer", DISCLAIMER),
        ],
        columns=["Item", "Value"],
    )
    file_df = pd.DataFrame(files, columns=["File", "Characters extracted", "Warnings"])
    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
        about.to_excel(writer, sheet_name="About", index=False)
        summary.to_excel(writer, sheet_name="Summary", index=False)
        details.to_excel(writer, sheet_name="Detailed Hits", index=False)
        file_df.to_excel(writer, sheet_name="Files", index=False)
        reference.to_dataframe().to_excel(writer, sheet_name="Reference Used", index=False)
        for ws in writer.book.worksheets:
            ws.freeze_panes = "A2"
            for col in ws.columns:
                width = max((len(str(c.value)) for c in col if c.value is not None), default=8)
                ws.column_dimensions[col[0].column_letter].width = min(max(width + 2, 10), 70)
    return buf.getvalue()
