"""Command-line interface.

Examples:
    python -m cable_tool samples/cable_wirelist.csv --connectors samples/cable_connectors.csv -o W101.pdf
    python -m cable_tool project.xlsx -o W101.pdf --svg out/ --sheet "ANSI D (34 x 22 in)"
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

from .bom import bom_dataframe, build_bom, check_design
from .canvas import sheet_to_svg, sheets_to_pdf
from .drawing import DEFAULT_SHEET, SHEET_SIZES, build_drawing
from .project import (
    add_connectors,
    connectors_from_dataframe,
    load_design,
    parts_from_dataframe,
    save_design,
    sync_connectors,
)


def _read_table(path: Path) -> pd.DataFrame:
    from .project import CONNECTOR_COLUMNS, PART_COLUMNS, _read_raw_sheets, _with_header

    raw = next(iter(_read_raw_sheets(path.name, path.read_bytes()).values()))
    for spec, required in ((CONNECTOR_COLUMNS, {"ref"}), (PART_COLUMNS, {"pn", "description"})):
        df = _with_header(raw, spec, required)
        if not df.empty:
            return df
    return pd.DataFrame()


def _sheet_size(value: str) -> str:
    for name, (letter, _, _) in SHEET_SIZES.items():
        if value.lower() in (name.lower(), letter.lower()):
            return name
    raise argparse.ArgumentTypeError(f"unknown sheet size {value!r}; choose from " + ", ".join(v[0] for v in SHEET_SIZES.values()))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="cable_tool", description="Generate a cable assembly drawing from a wiring list.")
    parser.add_argument("wirelist", help="Wiring list or saved project (.csv, .xlsx)")
    parser.add_argument("--connectors", "-c", help="Connector table (Ref, Connector P/N, Backshell P/N, Heatshrink P/N, Label P/N, ...)")
    parser.add_argument("--parts", "-p", help="Part descriptions (P/N, Description)")
    parser.add_argument("--out", "-o", default="cable_drawing.pdf", help="PDF drawing to write (default: %(default)s)")
    parser.add_argument("--svg", metavar="DIR", help="Also write one SVG per sheet into DIR")
    parser.add_argument("--bom", help="Also write the bill of materials (.csv or .xlsx)")
    parser.add_argument("--save-project", help="Save everything as a project workbook (.xlsx) you can edit and reload")
    parser.add_argument("--sheet", type=_sheet_size, default=DEFAULT_SHEET, help="Sheet size: B, C, D, A3, A2, A1 (default B)")
    parser.add_argument("--title")
    parser.add_argument("--dwg-no")
    parser.add_argument("--rev")
    parser.add_argument("--company")
    parser.add_argument("--drawn-by")
    parser.add_argument("--date")
    parser.add_argument("--length", type=float, help="Overall length (two-connector cables)")
    parser.add_argument("--units", help="Length units, e.g. IN or MM")
    args = parser.parse_args(argv)

    path = Path(args.wirelist)
    design, msgs = load_design(path.name, path.read_bytes())
    if args.connectors:
        add_connectors(design, connectors_from_dataframe(_read_table(Path(args.connectors))))
        sync_connectors(design)
    if args.parts:
        design.part_descriptions.update(parts_from_dataframe(_read_table(Path(args.parts))))
    tb = design.title_block
    for attr, value in (("title", args.title), ("drawing_number", args.dwg_no), ("revision", args.rev),
                        ("company", args.company), ("drawn_by", args.drawn_by), ("date", args.date)):
        if value is not None:
            setattr(tb, attr, value)
    if args.length is not None:
        design.overall_length = args.length
    if args.units:
        design.units = args.units.upper()

    for m in msgs + check_design(design):
        print(f"warning: {m}", file=sys.stderr)

    sheets, layout_msgs = build_drawing(design, args.sheet)
    for m in layout_msgs:
        print(f"warning: {m}", file=sys.stderr)
    out = Path(args.out)
    out.write_bytes(sheets_to_pdf(sheets, title=f"{tb.drawing_number} {tb.title}".strip()))
    print(f"Wrote {len(sheets)}-sheet drawing to {out}")

    if args.svg:
        d = Path(args.svg)
        d.mkdir(parents=True, exist_ok=True)
        stem = (tb.drawing_number or "cable").replace("/", "_")
        for i, sheet in enumerate(sheets, start=1):
            (d / f"{stem}_sheet{i}.svg").write_text(sheet_to_svg(sheet), encoding="utf-8")
        print(f"Wrote {len(sheets)} SVG sheets to {d}")
    if args.bom:
        bom = bom_dataframe(build_bom(design), design.units)
        if args.bom.lower().endswith(".csv"):
            bom.to_csv(args.bom, index=False)
        else:
            bom.to_excel(args.bom, index=False)
        print(f"Wrote bill of materials to {args.bom}")
    if args.save_project:
        Path(args.save_project).write_bytes(save_design(design))
        print(f"Saved project to {args.save_project}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
