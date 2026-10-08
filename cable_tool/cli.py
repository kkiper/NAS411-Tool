"""Command-line interface.

Examples:
    python -m cable_tool samples/cable_wirelist.csv -c samples/cable_connectors.csv \\
        -g samples/cable_groups.csv -s samples/cable_splices.csv -l samples/parts_library.csv \\
        --length 48 -o W101.pdf --dxf out/ --drc W101_drc.csv
    python -m cable_tool project.xlsx -o W101.pdf --svg out/ --sheet D --strict
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .bom import bom_dataframe, build_bom
from .canvas import sheet_to_svg, sheets_to_pdf
from .drawing import DEFAULT_SHEET, SHEET_SIZES, build_drawing
from .drc import ERROR, INFO, WARNING, run_drc
from .dxf import sheet_to_dxf
from .project import apply_table, load_design, save_design


def _sheet_size(value: str) -> str:
    for name, (letter, _, _) in SHEET_SIZES.items():
        if value.lower() in (name.lower(), letter.lower()):
            return name
    raise argparse.ArgumentTypeError(f"unknown sheet size {value!r}; choose from " + ", ".join(v[0] for v in SHEET_SIZES.values()))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="cable_tool", description="Generate a cable assembly drawing from a wiring list.")
    parser.add_argument("wirelist", help="Wiring list or saved project (.csv, .xlsx)")
    parser.add_argument("--connectors", "-c", help="Connector table (Ref, Connector P/N, Contact P/N, Backshell P/N, ...)")
    parser.add_argument("--groups", "-g", help="Wire groups: twisted pairs, shields, cables (Group, Type, Cable P/N, ...)")
    parser.add_argument("--splices", "-s", help="Splice table (Splice, Splice P/N, Near, Distance)")
    parser.add_argument("--library", "-l", "--parts", "-p", action="append", default=[],
                        help="Parts library with part parameters (repeatable; later files win)")
    parser.add_argument("--out", "-o", default="cable_drawing.pdf", help="PDF drawing to write (default: %(default)s)")
    parser.add_argument("--svg", metavar="DIR", help="Also write one SVG per sheet into DIR")
    parser.add_argument("--dxf", metavar="DIR", help="Also write one DXF (R12, inches) per sheet into DIR")
    parser.add_argument("--bom", help="Also write the bill of materials (.csv or .xlsx)")
    parser.add_argument("--drc", metavar="FILE", help="Write the design rule check report (.csv or .xlsx)")
    parser.add_argument("--strict", action="store_true", help="Exit with status 1 if the design rule check finds errors")
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
    for kind, files in (("parts", args.library), ("connectors", [args.connectors]), ("groups", [args.groups]),
                        ("splices", [args.splices])):
        for f in files:
            if f:
                apply_table(design, kind, Path(f).name, Path(f).read_bytes())
    tb = design.title_block
    for attr, value in (("title", args.title), ("drawing_number", args.dwg_no), ("revision", args.rev),
                        ("company", args.company), ("drawn_by", args.drawn_by), ("date", args.date)):
        if value is not None:
            setattr(tb, attr, value)
    if args.length is not None:
        design.overall_length = args.length
    if args.units:
        design.units = args.units.upper()

    for m in msgs:
        print(f"warning: {m}", file=sys.stderr)
    report = run_drc(design)
    counts = report.counts()
    for f in report.sorted():
        if f.severity != INFO:
            print(f"{f.severity.lower()}: [{f.rule}] {f.item + ': ' if f.item else ''}{f.message}", file=sys.stderr)
    print(f"Design rule check: {counts[ERROR]} error(s), {counts[WARNING]} warning(s), {counts[INFO]} info")

    sheets, layout_msgs = build_drawing(design, args.sheet)
    for m in layout_msgs:
        print(f"warning: {m}", file=sys.stderr)
    out = Path(args.out)
    out.write_bytes(sheets_to_pdf(sheets, title=f"{tb.drawing_number} {tb.title}".strip()))
    print(f"Wrote {len(sheets)}-sheet drawing to {out}")

    stem = (tb.drawing_number or "cable").replace("/", "_")
    for fmt, folder, render in (("SVG", args.svg, sheet_to_svg), ("DXF", args.dxf, sheet_to_dxf)):
        if folder:
            d = Path(folder)
            d.mkdir(parents=True, exist_ok=True)
            for i, sheet in enumerate(sheets, start=1):
                (d / f"{stem}_sheet{i}.{fmt.lower()}").write_text(render(sheet), encoding="utf-8")
            print(f"Wrote {len(sheets)} {fmt} sheets to {d}")
    if args.bom:
        bom = bom_dataframe(build_bom(design), design.units)
        bom.to_csv(args.bom, index=False) if args.bom.lower().endswith(".csv") else bom.to_excel(args.bom, index=False)
        print(f"Wrote bill of materials to {args.bom}")
    if args.drc:
        df = report.to_dataframe()
        df.to_csv(args.drc, index=False) if args.drc.lower().endswith(".csv") else df.to_excel(args.drc, index=False)
        print(f"Wrote design rule check report to {args.drc}")
    if args.save_project:
        Path(args.save_project).write_bytes(save_design(design))
        print(f"Saved project to {args.save_project}")
    return 1 if args.strict and counts[ERROR] else 0


if __name__ == "__main__":
    raise SystemExit(main())
