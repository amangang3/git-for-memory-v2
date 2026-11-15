import json

from csv2json.cli import main


def test_cli_stdout(tmp_path, capsys):
    src = tmp_path / "in.csv"
    src.write_text("a,b\n1,2\n", encoding="utf-8")
    assert main([str(src)]) == 0
    assert json.loads(capsys.readouterr().out) == [{"a": "1", "b": "2"}]


def test_cli_output_file(tmp_path):
    src = tmp_path / "in.csv"
    dst = tmp_path / "out.json"
    src.write_text("a|b\n1|2\n", encoding="utf-8")
    assert main([str(src), "-o", str(dst), "-d", "|"]) == 0
    assert json.loads(dst.read_text(encoding="utf-8")) == [{"a": "1", "b": "2"}]


def test_cli_missing_file(tmp_path, capsys):
    assert main([str(tmp_path / "nope.csv")]) == 1
    assert "error" in capsys.readouterr().err
