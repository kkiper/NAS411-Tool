"""Command-line interface.

Examples:
    python -m nas411_tool scan datasheets/*.pdf
    python -m nas411_tool scan part.pdf --reference NAS411-1_HMTL.xlsx --out report.xlsx
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

from .extract import extract_text
from .matcher import CONFIDENCE_RANK, Matcher, hits_to_dataframe, summarize
from .reference import enrich_with_synonyms, load_reference_file, load_starter_reference, merge_references
from .report import DISCLAIMER, build_excel_report


def build_reference(path: str | None, include_starter: bool):
    starter = load_starter_reference()
    if not path:
        return starter
    official = enrich_with_synonyms(load_reference_file(path), starter)
    return merge_references(official, starter) if include_starter else official


def cmd_scan(args: argparse.Namespace) -> int:
    reference = build_reference(args.reference, args.include_starter)
    matcher = Matcher(reference, use_indicators=not args.no_indicators)

    hits, files = [], []
    for name in args.files:
        path = Path(name)
        if not path.is_file():
            print(f"warning: {name} not found", file=sys.stderr)
            continue
        doc = extract_text(path.name, path.read_bytes())
        for w in doc.warnings:
            print(f"warning: {path.name}: {w}", file=sys.stderr)
        files.append({"File": path.name, "Characters extracted": doc.char_count, "Warnings": " ".join(doc.warnings)})
        hits.extend(matcher.scan(doc))

    min_rank = CONFIDENCE_RANK[args.min_confidence]
    hits = [h for h in hits if CONFIDENCE_RANK[h.confidence] >= min_rank]
    summary, details = summarize(hits), hits_to_dataframe(hits)

    print(f"Reference: {reference.source} ({len(reference)} entries)\n")
    if summary.empty:
        print("No reference-list materials were found.")
    else:
        with pd.option_context("display.max_rows", None, "display.width", 200, "display.max_colwidth", 45):
            print(summary[["File", "Substance", "CAS", "Category", "Status", "Confidence", "Matched Terms", "Concentration"]].to_string(index=False))
    print(f"\n{DISCLAIMER}")

    if args.out:
        out = Path(args.out)
        if out.suffix.lower() == ".csv":
            summary.to_csv(out, index=False)
            details.to_csv(out.with_name(out.stem + "_details.csv"), index=False)
        else:
            out.write_bytes(build_excel_report(summary, details, reference, files))
        print(f"\nReport written to {out}")
    return 0


def cmd_reference(args: argparse.Namespace) -> int:
    reference = build_reference(args.reference, args.include_starter)
    df = reference.to_dataframe()
    if args.out:
        df.to_csv(args.out, index=False)
        print(f"Wrote {len(df)} entries to {args.out}")
    else:
        with pd.option_context("display.max_rows", None, "display.width", 200, "display.max_colwidth", 40):
            print(df[["Substance", "CAS", "Category", "Synonyms"]].to_string(index=False))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="nas411_tool", description="Screen hardware datasheets against NAS 411-1.")
    sub = parser.add_subparsers(dest="command", required=True)

    def add_ref_args(p):
        p.add_argument("--reference", "-r", help="Your copy of the NAS 411-1 list (.xlsx, .csv, or .pdf)")
        p.add_argument("--include-starter", action="store_true",
                       help="Also screen against the built-in starter list entries missing from --reference")

    scan = sub.add_parser("scan", help="Scan datasheets")
    scan.add_argument("files", nargs="+")
    add_ref_args(scan)
    scan.add_argument("--out", "-o", help="Write report (.xlsx, or .csv for summary + details CSVs)")
    scan.add_argument("--min-confidence", choices=["High", "Medium", "Low", "Info"], default="Low")
    scan.add_argument("--no-indicators", action="store_true", help="Disable spec/alloy designation indicators")
    scan.set_defaults(func=cmd_scan)

    ref = sub.add_parser("reference", help="Show or export the active reference list")
    add_ref_args(ref)
    ref.add_argument("--out", "-o", help="Write the parsed reference list to CSV")
    ref.set_defaults(func=cmd_reference)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
