"""NAS 411 / NAS 411-1 hazardous material screening for hardware datasheets."""

from .extract import extract_text
from .matcher import Matcher, hits_to_dataframe, summarize
from .reference import (
    ReferenceList,
    Substance,
    enrich_with_synonyms,
    load_reference_file,
    load_starter_reference,
    merge_references,
)

__all__ = [
    "Matcher",
    "ReferenceList",
    "Substance",
    "enrich_with_synonyms",
    "extract_text",
    "hits_to_dataframe",
    "load_reference_file",
    "load_starter_reference",
    "merge_references",
    "summarize",
]

__version__ = "0.1.0"
