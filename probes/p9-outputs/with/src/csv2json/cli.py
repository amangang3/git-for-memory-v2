"""Command-line interface for csv2json."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence

from csv2json.core import csv_to_json


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="csv2json", description="Convert a CSV file to JSON."
    )
    parser.add_argument(
        "input", help="path to the input CSV file, or '-' to read from stdin"
    )
    parser.add_argument(
        "-o", "--output", help="path to write JSON to (default: stdout)"
    )
    parser.add_argument(
        "-d", "--delimiter", default=",", help="CSV delimiter (default: ',')"
    )
    parser.add_argument(
        "--indent",
        type=int,
        default=2,
        help="JSON indent width; use 0 for compact output (default: 2)",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    indent = args.indent if args.indent > 0 else None

    try:
        if args.input == "-":
            result = csv_to_json(sys.stdin, args.delimiter, indent)
        else:
            with open(args.input, newline="", encoding="utf-8") as f:
                result = csv_to_json(f, args.delimiter, indent)

        if args.output:
            with open(args.output, "w", encoding="utf-8") as f:
                f.write(result + "\n")
        else:
            print(result)
    except OSError as e:
        print(f"csv2json: error: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
