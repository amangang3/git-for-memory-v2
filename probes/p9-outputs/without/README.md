# csv2json

A small command-line tool that converts a CSV file (with a header row) to a JSON array of objects.

## Install

```sh
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
```

## Usage

```sh
csv2json data.csv                  # print JSON to stdout
csv2json data.csv -o data.json     # write to a file
csv2json data.csv -d ';' --indent 0
cat data.csv | csv2json -          # read from stdin
```

## Test

```sh
pytest
```
