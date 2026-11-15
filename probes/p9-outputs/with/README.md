# csv2json

Small command-line tool that converts a CSV file to JSON (an array of objects keyed by the header row).

## Usage

```sh
uv run csv2json data.csv                 # print JSON to stdout
uv run csv2json data.csv -o data.json    # write to a file
cat data.csv | uv run csv2json -         # read from stdin
uv run csv2json data.csv -d ';' --indent 0
```

## Development

```sh
uv sync
uv run pytest
uv run ruff check .
uv run ruff format .
```
