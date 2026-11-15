"""Command-line interface."""

import argparse
import sys
from typing import List, Optional

from .converter import convert


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="csv2json", description="Convert a CSV file to JSON.")
    p.add_argument("input", help="path to input CSV file, or '-' for stdin")
    p.add_argument("-o", "--output", help="output JSON file (default: stdout)")
    p.add_argument("-d", "--delimiter", default=",", help="CSV delimiter (default: ',')")
    p.add_argument("--indent", type=int, default=2, help="JSON indent; 0 for compact (default: 2)")
    return p


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        infile = sys.stdin if args.input == "-" else open(args.input, newline="", encoding="utf-8")
        try:
            if args.output:
                with open(args.output, "w", encoding="utf-8") as out:
                    convert(infile, out, args.delimiter, args.indent)
            else:
                convert(infile, sys.stdout, args.delimiter, args.indent)
        finally:
            if infile is not sys.stdin:
                infile.close()
    except OSError as e:
        print(f"csv2json: error: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
