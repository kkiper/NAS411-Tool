# NAS 411 Hazardous Material Screening Tool

Upload hardware datasheets and screen them against **NAS 411-1, Hazardous Material Target List (HMTL)**. NAS 411-1 is the list used by **NAS 411** Hazardous Materials Management Programs (HMMP). The tool reports which listed materials a part may contain, why it thinks so, and the NAS 411-1 category for each one (for example Prohibited, Restricted, or Tracked).

> This repository also contains a **[Cable Drawing Tool](#cable-drawing-tool)** that turns a wiring list and
> part numbers into a cable assembly drawing.

## What it does

1. **Extracts text** from datasheets, material declarations, and SDSs. It reads PDF (with page numbers), DOCX, XLSX/XLS, CSV, TXT, and HTML files, or text you paste in.
2. **Compares that text with the reference list** in three ways:
   | Match type | Example | Confidence |
   |---|---|---|
   | CAS number (checksum-validated) | `7440-43-9` → Cadmium | High |
   | Substance name / synonym | "cad plated", "hex chrome", "beryllium copper" | High (Medium for short symbols such as `Cd`, `Pb`, `Hg`) |
   | Spec / alloy callout | `QQ-P-416` → cadmium; `MIL-DTL-5541 Type I` → Cr(VI); `UNS C17200` → beryllium; `Sn63Pb37`, `C36000`, `12L14` → lead; `Inconel`, `17-4 PH` → nickel | Medium |
3. **Cuts down false positives.**
   - Negations such as *"lead-free"*, *"non-chromate"* and *"contains no mercury"* are reported as **Stated absent / free**.
   - Non-chemical uses of a word, such as *"lead time"*, *"axial leads"*, *"mm Hg"* or *"Mercury Systems"*, are downgraded to **Low** and shown as **Possible – review**.
   - Element symbols and acronyms only match with exact case, so `CD-ROM` and `NI DAQ` aren't flagged.
4. **Reports** results per datasheet: substance, CAS, NAS 411-1 category, status, confidence, any stated concentration (e.g. `2.1 wt%`), page or sheet location, and a text snippet for each hit. Valid CAS numbers that aren't on the list are also reported for awareness. You can download the results as an Excel workbook (About / Summary / Detailed Hits / Files / Reference Used) or as CSV.

## The reference list: import your NAS 411-1 copy

NAS 411 and NAS 411-1 are published by the Aerospace Industries Association (AIA) and **aren't bundled** with this repository. Download the current NAS 411-1 HMTL from AIA, then load it into the tool:

- **Excel / CSV:** title rows above the header are skipped automatically. Columns for substance name, CAS number, category, synonyms, and notes are auto-detected, and you can change the mapping in the sidebar.
- **PDF:** tables are extracted, with a CAS-per-line fallback. Check the result in the **Reference list** tab, and export it to CSV if you need to clean it up.

When an imported entry shares a CAS number or name with the built-in list, the built-in shop-term synonyms are copied onto it. For example, NAS 411-1's "Chromium (VI) compounds" will also match "hex chrome", "chromate conversion coating", and "Cr(VI)".

**Built-in starter list.** If you haven't imported a list, the tool uses `nas411_tool/data/starter_reference.csv`. It holds about 85 commonly targeted aerospace hazardous materials (cadmium, Cr(VI) and chromates, lead, mercury, beryllium, nickel, cobalt, ODS solvents, PFAS, phthalates, and others), each with a public regulatory basis such as RoHS, REACH, Montreal Protocol, or Stockholm. **It isn't the official NAS 411-1 content and has no NAS 411-1 categories.** Results show "Verify in NAS 411-1" until you import the official list.

## Running it

```bash
pip install -r requirements.txt
streamlit run app.py
```

Then open http://localhost:8501:

1. Import your NAS 411-1 list in the sidebar.
2. Upload the datasheets.
3. Review the findings and download the report.

### Privacy: everything stays on your computer

- The tool makes no internet connections. Datasheets and your NAS 411-1 list are processed in memory on your computer and are never uploaded anywhere. The "upload" button only passes the file from your browser to the app running on the same machine.
- `.streamlit/config.toml` makes the app listen only on `localhost`, so other computers on your network can't open it. It also turns off Streamlit's anonymous usage statistics.
- The command-line tool (below) doesn't start a web server at all.
- Keep your licensed NAS 411-1 file outside the repository, or in a `reference/` folder, which `.gitignore` excludes, so it is never committed.

After running `streamlit run app.py`, open http://localhost:8501 in your browser.

### Command line

```bash
# Screen against the starter list
python -m nas411_tool scan samples/sample_datasheet.txt

# Screen against your NAS 411-1 copy and write an Excel report
python -m nas411_tool scan datasheets/*.pdf -r NAS411-1_HMTL.xlsx -o report.xlsx

# Check how your list was parsed
python -m nas411_tool reference -r NAS411-1_HMTL.xlsx -o parsed.csv
```

Options: `--include-starter` also screens starter entries that are missing from your list. `--min-confidence {High,Medium,Low,Info}` filters the results. `--no-indicators` turns off spec/alloy callouts.

## Customizing

- **Spec/alloy callouts:** `nas411_tool/data/spec_indicators.csv` (regex pattern → implied substance and CAS). Add your company's process specs or approved-supplier finish codes here.
- **Ambiguous words:** `AMBIGUOUS_TERMS` in `nas411_tool/matcher.py`.

## Limitations

This is a **screening aid**, not a compliance determination.

- Datasheets often leave out constituents. Proprietary alloys, platings, primers, sealants, adhesives, and lubricants can contain listed substances that are never named.
- A clean result means "nothing found in the text", not "free of hazardous materials". Confirm with supplier material declarations or SDSs.
- Some spec callouts depend on Type or Class. For example, MIL-DTL-5541 Type I is hexavalent chromium and Type II isn't. Those hits carry a "verify the type" note.
- Scanned (image-only) PDFs must be OCR'd before upload.

## Cable Drawing Tool

Turns a wiring list plus your **connector, backshell, heatshrink, label and wire part numbers** into a cable
assembly drawing package (PDF, or one SVG per sheet):

| Sheet | Contents |
|---|---|
| 1 | Assembly view (connectors, backshells, heatshrink boots, ID labels, lengths, item balloons), bill of materials, notes |
| 2 | Wiring diagram: a pin-out table for every connector, with each wire drawn pin to pin and tagged with wire ID, gauge and color |
| 3+ | Wire list and label schedule (continued onto more sheets if needed) |

Every sheet has a zoned border and a title block (company, title, drawing number, revision, drawn/checked, date, sheet *n* of *m*).
Sheet sizes: ANSI B, C, D and ISO A3, A2, A1.

```bash
streamlit run cable_app.py
```

1. Upload a wiring list (or load one of the samples), and set the title block in the sidebar.
2. Fill in part numbers in the **Connectors** tab. Every connector named in the wire list gets a row automatically.
   Optionally add BOM descriptions in **Part descriptions**, and edit the general notes.
3. Review the checks, preview the sheets, and download the PDF, SVGs, BOM, or **Save project (.xlsx)**.
   Upload the saved project later to continue where you left off.

Command line:

```bash
python -m cable_tool samples/cable_wirelist.csv -c samples/cable_connectors.csv --length 48 \
    --dwg-no W101-001 --title "CABLE ASSY, FCC TO PDU" -o W101-001.pdf --svg out/ --bom bom.csv
python -m cable_tool samples/y_harness_wirelist.csv -c samples/y_harness_connectors.csv --sheet D -o W200.pdf
```

### Input format

**Wiring list** (CSV or Excel, one row per wire). Headers are matched loosely, and title rows above the header are skipped.

| Column | Required | Also accepted as |
|---|---|---|
| From, To | yes | From Conn, Source / Destination. `P1-3`, `P1:3` or `P1.3` are split into connector and pin. |
| From Pin, To Pin | if not in From/To | Pin A / Pin B, From Contact |
| Wire ID | no (W1, W2... assigned) | Wire, Wire No, Circuit |
| Signal, Gauge, Color | no | Function / Net, AWG / Size, Colour |
| Wire P/N | no | Wire Type, Part Number |
| Length | no | Cut Length. Overrides the length worked out from the cable lengths. |
| Wire Label P/N, Wire Heatshrink P/N | no | Marker P/N, Sleeve P/N. Installed at **both** ends of the wire (qty 2 per wire). |
| Notes | no | Remarks |

**Connector table** (one row per connector end): `Ref`, `Description`, `Connector P/N`, `Backshell P/N`,
`Heatshrink P/N` (boot over the backshell), `Label P/N`, `Label Text` (legend; defaults to the ref), and `Length`
(from the connector face to the breakout). An Excel wiring list can carry this on a sheet named *Connectors*, plus optional
*Parts* (P/N, Description), *Title Block* and *Notes* sheets.

### Lengths and quantities

- **Two-connector cable:** enter the overall length (sidebar or `--length`). Every wire is cut to that length.
- **Harness with a breakout:** enter each connector's length to the breakout. A wire from P1 to P3 is cut to P1 + P3.
- A per-wire **Length** overrides both. Wires whose length can't be worked out (for example a jumper within one
  connector) make that wire's BOM quantity **AR** (as required).
- Connectors, backshells, boots and labels are 1 per connector end. Wire markers and wire heatshrink are 2 per wire.
  Parts with the same P/N are combined into one BOM item, which lists where it's used.

### Checks

The tool warns about duplicate wire IDs, pins with more than one wire, missing pins or wire part numbers, connectors
without a part number or without wires, and lengths it can't work out.

The part numbers in the samples are typical examples (D38999 / MS3126 connectors, M85049 backshells, M22759 wire)
used to show the layout. Check every part number against your own design and approved parts list.

## Tests

```bash
pip install -r requirements-dev.txt
pytest
```
