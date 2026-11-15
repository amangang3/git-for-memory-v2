import io
import json

from csv2json.cli import main


def test_file_to_stdout(tmp_path, capsys):
    src = tmp_path / "in.csv"
    src.write_text("name,age\nAda,36\n")

    assert main([str(src)]) == 0
    assert json.loads(capsys.readouterr().out) == [{"name": "Ada", "age": "36"}]


def test_file_to_output_file(tmp_path):
    src = tmp_path / "in.csv"
    dst = tmp_path / "out.json"
    src.write_text("a,b\n1,2\n")

    assert main([str(src), "-o", str(dst)]) == 0
    assert json.loads(dst.read_text()) == [{"a": "1", "b": "2"}]


def test_stdin(monkeypatch, capsys):
    monkeypatch.setattr("sys.stdin", io.StringIO("a\n1\n"))

    assert main(["-", "--indent", "0"]) == 0
    assert capsys.readouterr().out.strip() == '[{"a": "1"}]'


def test_delimiter_option(tmp_path, capsys):
    src = tmp_path / "in.csv"
    src.write_text("a;b\n1;2\n")

    assert main([str(src), "-d", ";"]) == 0
    assert json.loads(capsys.readouterr().out) == [{"a": "1", "b": "2"}]


def test_missing_file_returns_error(tmp_path, capsys):
    assert main([str(tmp_path / "nope.csv")]) == 1
    assert "error" in capsys.readouterr().err
