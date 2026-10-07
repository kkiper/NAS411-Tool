import pandas as pd
from importlib import resources

from nas411_tool.cas import find_cas_numbers, is_valid_cas, normalize_cas, split_cas_field


def test_checksum():
    assert is_valid_cas("7440-43-9")  # cadmium
    assert is_valid_cas("18540-29-9")  # Cr(VI)
    assert not is_valid_cas("7440-43-8")
    assert not is_valid_cas("744043-9")


def test_normalize_and_split():
    assert normalize_cas("7440439") == "7440-43-9"
    assert normalize_cas("7440–43–9") == "7440-43-9"
    assert split_cas_field("7440-43-9; 1306-19-0\n1306-23-6") == ["7440-43-9", "1306-19-0", "1306-23-6"]
    assert split_cas_field("Various") == []
    assert split_cas_field(None) == []


def test_find_ignores_invalid_and_embedded_numbers():
    text = "Cd 7440-43-9, part 1234-56-0, date 2020-01-01, PN 12-7440-43-9-A"
    assert [m.group(0) for m in find_cas_numbers(text)] == ["7440-43-9"]


def test_builtin_data_cas_numbers_are_valid():
    for name in ["starter_reference.csv", "spec_indicators.csv"]:
        df = pd.read_csv(resources.files("nas411_tool").joinpath("data", name), dtype=str).fillna("")
        col = "CAS" if "CAS" in df.columns else "Implied CAS"
        for value in df[col]:
            for cas in filter(None, (v.strip() for v in value.split(";"))):
                assert is_valid_cas(cas), f"{name}: {cas}"
