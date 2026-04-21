import io

from stemmata.cli import run
from stemmata.envelope import failure, to_text
from stemmata.errors import SchemaError


def test_to_text_collapses_single_newline_in_message():
    err = SchemaError("line one\nline two", file="x.yaml", field_name="<f>", reason="r")
    env = failure("resolve", err)
    rendered = to_text(env)
    assert "\n" not in rendered
    assert "line one line two" in rendered


def test_to_text_collapses_multiple_whitespace_runs():
    err = SchemaError("a\n\n\tb   c\r\nd", file="x.yaml", field_name="<f>", reason="r")
    env = failure("resolve", err)
    rendered = to_text(env)
    assert "\n" not in rendered
    assert "\r" not in rendered
    assert "\t" not in rendered
    assert "a b c d" in rendered


def test_to_text_preserves_single_line_messages():
    err = SchemaError("already single line", file="x.yaml", field_name="<f>", reason="r")
    env = failure("resolve", err)
    rendered = to_text(env)
    assert rendered.endswith("already single line")


def test_stderr_summary_is_single_line_for_yaml_parse_error(tmp_path):
    bad = tmp_path / "bad.yaml"
    bad.write_text("a: 1\nb: [unclosed\n")
    out, err = io.StringIO(), io.StringIO()
    code = run(["resolve", str(bad)], stdout=out, stderr=err)
    assert code != 0
    summary = err.getvalue().rstrip("\n")
    assert "\n" not in summary
    assert summary.startswith("error[")
