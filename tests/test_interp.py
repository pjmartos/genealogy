import pytest

from stemmata.errors import CycleError, MergeError, UnresolvableError
from stemmata.interp import Layer, interpolate
from stemmata.yaml_loader import attach_file, load_with_positions


def _load(text, file="x.yaml"):
    data, _ = load_with_positions(text, file=file)
    attach_file(data, file)
    return data


def _interp(text, layers_data, file="x.yaml"):
    data = _load(text, file=file)
    merged = data
    for layer in layers_data:
        from stemmata.merge import merge_pair
        merged = merge_pair(merged, layer)
    layers = [Layer(canonical_id=f"layer{i}", data=l) for i, l in enumerate([data] + layers_data)]
    return interpolate(merged, layers, root_file=file)


def test_simple_textual():
    result = _interp("body: hello ${name}\n", [{"name": "world"}])
    assert result["body"] == "hello world"


def test_exact_structural_scalar():
    result = _interp("x: ${val}\n", [{"val": 42}])
    assert result["x"] == 42


def test_exact_structural_map():
    result = _interp("x: ${cfg}\n", [{"cfg": {"a": 1}}])
    assert result["x"] == {"a": 1}


def test_list_splat_structural():
    result = _interp("xs:\n  - ${items}\n  - tail\n", [{"items": [1, 2, 3]}])
    assert result["xs"] == [1, 2, 3, "tail"]


def test_non_splat_form():
    result = _interp("xs:\n  - ${=items}\n", [{"items": [1, 2, 3]}])
    assert result["xs"] == [[1, 2, 3]]


def test_empty_list_splat_vanishes():
    result = _interp("xs:\n  - ${items}\n  - tail\n", [{"items": []}])
    assert result["xs"] == ["tail"]


def test_block_scalar_is_textual():
    result = _interp("body: |\n  ${val}\n", [{"val": "abc"}])
    assert result["body"] == "abc\n"


def test_dollar_escape():
    result = _interp('body: "$${literal}"\n', [])
    assert result["body"] == "${literal}"


def test_not_provided_raises():
    with pytest.raises(UnresolvableError) as exc:
        _interp("x: ${missing.path}\n", [])
    assert exc.value.details["reason"] == "not_provided"


def test_explicit_null_raises_with_provider():
    with pytest.raises(UnresolvableError) as exc:
        _interp("x: ${val}\n", [{"val": None}])
    assert exc.value.details["reason"] == "explicit_null"


def test_null_intermediate_is_not_provided():
    with pytest.raises(UnresolvableError) as exc:
        _interp("x: ${a.b}\n", [{"a": None}])
    assert exc.value.details["reason"] == "not_provided"


def test_non_scalar_in_textual_raises():
    with pytest.raises(MergeError):
        _interp("x: prefix ${val} suffix\n", [{"val": [1, 2, 3]}])


def test_multiple_placeholders_in_one_string():
    result = _interp("x: ${a}/${b}\n", [{"a": "one", "b": "two"}])
    assert result["x"] == "one/two"


def test_dotted_path():
    result = _interp("x: ${a.b.c}\n", [{"a": {"b": {"c": 7}}}])
    assert result["x"] == 7


def test_boolean_stringified_textual():
    result = _interp("x: ssl=${flag}\n", [{"flag": True}])
    assert result["x"] == "ssl=true"


def test_null_stringified_textual_errors():
    with pytest.raises(UnresolvableError):
        _interp("x: value=${val}\n", [{"val": None}])


def test_chained_textual():
    result = _interp("x: ${a}\n", [{"a": "hello ${b}", "b": "world"}])
    assert result["x"] == "hello world"


def test_chained_structural_scalar():
    result = _interp("x: ${a}\n", [{"a": "${b}", "b": 42}])
    assert result["x"] == 42


def test_chained_structural_to_list_splat():
    result = _interp(
        "xs:\n  - ${a}\n  - tail\n",
        [{"a": "${b}", "b": [1, 2, 3]}],
    )
    assert result["xs"] == [1, 2, 3, "tail"]


def test_chained_map_with_inner_placeholder():
    result = _interp(
        "x: ${a}\n",
        [{"a": {"name": "${b}"}, "b": "ok"}],
    )
    assert result["x"] == {"name": "ok"}


def test_chained_list_splat_inside_resolved_list():
    result = _interp(
        "xs: ${outer}\n",
        [{"outer": ["head", "${inner}", "tail"], "inner": [1, 2]}],
    )
    assert result["xs"] == ["head", 1, 2, "tail"]


def test_cycle_direct_self_reference():
    with pytest.raises(CycleError) as exc:
        _interp("x: ${a}\n", [{"a": "${a}"}])
    assert exc.value.code == 12
    assert "a" in exc.value.details["cycle"]


def test_cycle_two_step():
    with pytest.raises(CycleError) as exc:
        _interp("x: ${a}\n", [{"a": "${b}", "b": "${a}"}])
    assert exc.value.details["cycle"] == ["a", "b", "a"]


def test_cycle_via_map_value():
    with pytest.raises(CycleError):
        _interp(
            "x: ${a}\n",
            [{"a": {"loop": "${a}"}}],
        )


def test_cycle_in_textual_context():
    with pytest.raises(CycleError):
        _interp(
            "x: hi ${a}\n",
            [{"a": "ho ${a}"}],
        )


def test_non_scalar_in_textual_via_chain():
    with pytest.raises(MergeError):
        _interp(
            "x: prefix ${a} suffix\n",
            [{"a": "${b}", "b": [1, 2, 3]}],
        )
