"""Core conversion logic."""

import csv
import json
from typing import Dict, Iterable, List, TextIO


def csv_to_records(rows: Iterable[str], delimiter: str = ",") -> List[Dict[str, str]]:
    """Parse CSV lines (with a header row) into a list of dicts."""
    reader = csv.DictReader(rows, delimiter=delimiter)
    return [dict(row) for row in reader]


def convert(
    infile: TextIO,
    outfile: TextIO,
    delimiter: str = ",",
    indent: int = 2,
) -> int:
    """Read CSV from infile, write JSON array to outfile. Returns row count."""
    records = csv_to_records(infile, delimiter=delimiter)
    json.dump(records, outfile, indent=indent or None, ensure_ascii=False)
    outfile.write("\n")
    return len(records)
