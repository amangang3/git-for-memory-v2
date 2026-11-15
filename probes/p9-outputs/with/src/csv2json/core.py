"""Core CSV -> JSON conversion logic."""

from __future__ import annotations

import csv
import json
from collections.abc import Iterable
from typing import TextIO


def read_csv_rows(
    source: TextIO | Iterable[str], delimiter: str = ","
) -> list[dict[str, str]]:
    """Read CSV text (first row is the header) into a list of dicts."""
    reader = csv.DictReader(source, delimiter=delimiter)
    return [dict(row) for row in reader]


def csv_to_json(
    source: TextIO | Iterable[str],
    delimiter: str = ",",
    indent: int | None = 2,
) -> str:
    """Convert CSV text to a JSON array of objects."""
    return json.dumps(read_csv_rows(source, delimiter), indent=indent)
