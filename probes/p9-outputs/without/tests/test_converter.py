import io
import json

from csv2json.converter import convert, csv_to_records


def test_csv_to_records_basic():
    rows = ["name,age", "Ada,36", "Linus,54"]
    assert csv_to_records(rows) == [
        {"name": "Ada", "age": "36"},
        {"name": "Linus", "age": "54"},
    ]


def test_custom_delimiter():
    assert csv_to_records(["a;b", "1;2"], delimiter=";") == [{"a": "1", "b": "2"}]


def test_quoted_fields():
    rows = ['name,note', '"Doe, Jane","said ""hi"""']
    assert csv_to_records(rows) == [{"name": "Doe, Jane", "note": 'said "hi"'}]


def test_empty_input():
    assert csv_to_records([]) == []


def test_header_only():
    assert csv_to_records(["a,b"]) == []


def test_convert_writes_json_and_returns_count():
    out = io.StringIO()
    n = convert(io.StringIO("x,y\n1,2\n3,4\n"), out)
    assert n == 2
    assert json.loads(out.getvalue()) == [{"x": "1", "y": "2"}, {"x": "3", "y": "4"}]


def test_convert_compact():
    out = io.StringIO()
    convert(io.StringIO("x\n1\n"), out, indent=0)
    assert out.getvalue().strip() == '[{"x": "1"}]'
