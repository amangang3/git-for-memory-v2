import io
import json

from csv2json import csv_to_json, read_csv_rows


def test_read_csv_rows_basic():
    rows = read_csv_rows(io.StringIO("name,age\nAda,36\nLinus,54\n"))
    assert rows == [
        {"name": "Ada", "age": "36"},
        {"name": "Linus", "age": "54"},
    ]


def test_header_only_gives_empty_list():
    assert read_csv_rows(io.StringIO("a,b\n")) == []


def test_custom_delimiter():
    rows = read_csv_rows(io.StringIO("a;b\n1;2\n"), delimiter=";")
    assert rows == [{"a": "1", "b": "2"}]


def test_quoted_fields():
    rows = read_csv_rows(io.StringIO('a,b\n"x,y","he said ""hi"""\n'))
    assert rows == [{"a": "x,y", "b": 'he said "hi"'}]


def test_csv_to_json_roundtrip():
    out = csv_to_json(io.StringIO("a,b\n1,2\n"))
    assert json.loads(out) == [{"a": "1", "b": "2"}]


def test_csv_to_json_compact():
    out = csv_to_json(io.StringIO("a\n1\n"), indent=None)
    assert out == '[{"a": "1"}]'
