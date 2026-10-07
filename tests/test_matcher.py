from pathlib import Path

import pytest

from nas411_tool.extract import extract_text
from nas411_tool.matcher import (
    LOW,
    STATUS_ABSENT,
    STATUS_IDENTIFIED,
    STATUS_POSSIBLE,
    Matcher,
    summarize,
)
from nas411_tool.reference import load_starter_reference

SAMPLE = Path(__file__).resolve().parents[1] / "samples" / "sample_datasheet.txt"


@pytest.fixture(scope="module")
def matcher():
    return Matcher(load_starter_reference())


def scan(matcher, text):
    hits = matcher.scan(extract_text("t.txt", text.encode()))
    return hits, summarize(hits)


def status(summary, substance):
    rows = summary[summary["Substance"] == substance]
    return rows["Status"].iloc[0] if len(rows) else None


def test_sample_datasheet(matcher):
    hits, summary = scan(matcher, SAMPLE.read_text())
    assert status(summary, "Cadmium and cadmium compounds") == STATUS_IDENTIFIED
    assert status(summary, "Beryllium and beryllium compounds") == STATUS_IDENTIFIED
    assert status(summary, "Hexavalent chromium compounds") == STATUS_IDENTIFIED
    assert status(summary, "Strontium chromate") == STATUS_IDENTIFIED
    assert status(summary, "Nickel and nickel compounds") == STATUS_IDENTIFIED
    # "lead-free", "Lead time", "axial leads" must not count as identified lead
    assert status(summary, "Lead and lead compounds") == STATUS_POSSIBLE
    cd = summary[summary["Substance"] == "Cadmium and cadmium compounds"].iloc[0]
    assert "2.1 wt%" in cd["Concentration"]


def test_cas_match_is_high_confidence(matcher):
    hits, _ = scan(matcher, "Contains 1333-82-0 at 3%")
    assert any(h.substance == "Chromium trioxide" and h.confidence == "High" for h in hits)


def test_negation(matcher):
    _, summary = scan(matcher, "RoHS compliant. Lead-free. This part is cadmium free. Non-chromate primer.")
    assert status(summary, "Lead and lead compounds") == STATUS_ABSENT
    assert status(summary, "Cadmium and cadmium compounds") == STATUS_ABSENT
    assert status(summary, "Hexavalent chromium compounds") == STATUS_ABSENT


def test_negation_does_not_cross_sentences(matcher):
    _, summary = scan(matcher, "Contains no mercury. Finish: cadmium plate.")
    assert status(summary, "Mercury and mercury compounds") == STATUS_ABSENT
    assert status(summary, "Cadmium and cadmium compounds") == STATUS_IDENTIFIED


def test_ambiguous_lead(matcher):
    hits, _ = scan(matcher, "Lead time 4 weeks. Lead pitch 0.5 mm. Gull-wing lead.")
    assert hits and all(h.confidence == LOW for h in hits if h.substance.startswith("Lead"))
    _, summary = scan(matcher, "Solder: tin-lead, Sn63Pb37")
    assert status(summary, "Lead and lead compounds") == STATUS_IDENTIFIED


def test_company_name_downgraded(matcher):
    hits, _ = scan(matcher, "Manufactured by Mercury Systems Inc.")
    assert hits and all(h.confidence == LOW for h in hits)


def test_symbols_are_case_sensitive(matcher):
    hits, _ = scan(matcher, "CD-ROM drive, PB size, NI DAQ card")
    assert not [h for h in hits if h.substance.startswith(("Cadmium", "Lead", "Nickel"))]
    hits, _ = scan(matcher, "Plating: Cd per spec. Contacts: Ni underplate.")
    subs = {h.substance for h in hits}
    assert "Cadmium and cadmium compounds" in subs and "Nickel and nickel compounds" in subs


def test_hex_chrome_not_matched_by_plain_chromium(matcher):
    _, summary = scan(matcher, "Composition: Chromium 18%, Nickel 8%, Iron balance")
    assert status(summary, "Hexavalent chromium compounds") is None


@pytest.mark.parametrize(
    "text,substance",
    [
        ("Finish: AMS-QQ-P-416 Type II Class 2", "Cadmium and cadmium compounds"),
        ("Coat per MIL-C-5541E Class 3", "Hexavalent chromium compounds"),
        ("Anodize per MIL-A-8625 Type I", "Hexavalent chromium compounds"),
        ("Material: C36000 brass", "Lead and lead compounds"),
        ("Material: 12L14 steel", "Lead and lead compounds"),
        ("Spring: CDA 17200 / UNS C17200 BeCu", "Beryllium and beryllium compounds"),
        ("Electrode: EWTh-2", "Thorium"),
        ("Magnet: SmCo", "Cobalt and cobalt compounds"),
    ],
)
def test_spec_indicators(matcher, text, substance):
    _, summary = scan(matcher, text)
    assert status(summary, substance) == STATUS_IDENTIFIED


def test_indicators_can_be_disabled():
    m = Matcher(load_starter_reference(), use_indicators=False)
    hits = m.scan(extract_text("t.txt", b"Finish per QQ-P-416"))
    assert not hits


def test_unlisted_cas_reported(matcher):
    hits, summary = scan(matcher, "Copper 7440-50-8 balance")
    assert summary.iloc[0]["Status"] == "CAS not on reference list"
