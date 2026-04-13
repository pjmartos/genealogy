import pytest

from stemmata.errors import MergeError
from stemmata.merge import merge_namespaces, merge_pair


def test_nearest_wins_scalar():
    assert merge_pair("near", "far") == "near"


def test_deep_merge_maps():
    near = {"a": {"b": 1}}
    far = {"a": {"c": 2}}
    assert merge_pair(near, far) == {"a": {"b": 1, "c": 2}}


def test_deep_merge_nearest_wins_at_leaves():
    near = {"a": {"b": 1}}
    far = {"a": {"b": 99, "c": 2}}
    assert merge_pair(near, far) == {"a": {"b": 1, "c": 2}}


def test_null_shadows_map_wholesale():
    near = {"a": None}
    far = {"a": {"b": 1, "c": 2}}
    assert merge_pair(near, far) == {"a": None}


def test_nearer_list_replaces_farther_list():
    near = {"xs": [1, 2]}
    far = {"xs": [3, 4, 5]}
    assert merge_pair(near, far) == {"xs": [1, 2]}


def test_nearer_scalar_vs_farther_map_is_type_conflict():
    near = {"x": "scalar"}
    far = {"x": {"a": 1}}
    with pytest.raises(MergeError):
        merge_pair(near, far)


def test_nearer_list_vs_farther_map_is_type_conflict():
    near = {"x": [1, 2]}
    far = {"x": {"a": 1}}
    with pytest.raises(MergeError):
        merge_pair(near, far)


def test_multiple_layers():
    layers = [
        {"a": 1, "b": 2},
        {"b": 20, "c": 30},
        {"c": 300, "d": 400},
    ]
    merged = merge_namespaces(layers)
    assert merged == {"a": 1, "b": 2, "c": 30, "d": 400}


def test_order_preserves_nearer_keys_first():
    layers = [{"z": 1, "a": 2}, {"m": 3, "b": 4}]
    merged = merge_namespaces(layers)
    assert list(merged.keys()) == ["z", "a", "m", "b"]


def test_empty_layers():
    assert merge_namespaces([]) == {}


def test_single_layer():
    assert merge_namespaces([{"a": 1}]) == {"a": 1}


def test_merge_error_without_provenance_has_empty_nodes():
    layers = [{"x": "scalar"}, {"x": {"a": 1}}]
    with pytest.raises(MergeError) as exc:
        merge_namespaces(layers)
    assert exc.value.location == []


def test_merge_error_with_provenance_populates_contributing_ancestors():
    layers = [
        {"db": {"host": "near"}},
        {"db": {"host": "mid"}},
        {"db": "conflict"},
    ]
    provenance = [
        ("@pkg/a@1.0.0#root", "/path/to/root.yaml"),
        ("@pkg/a@1.0.0#mid", "/path/to/mid.yaml"),
        ("@pkg/a@1.0.0#far", "/path/to/far.yaml"),
    ]
    with pytest.raises(MergeError) as exc:
        merge_namespaces(layers, provenance=provenance)
    nodes = exc.value.location
    ancestors = [n["ancestor"] for n in nodes]
    files = [n["file"] for n in nodes]
    assert ancestors == [
        "@pkg/a@1.0.0#root",
        "@pkg/a@1.0.0#mid",
        "@pkg/a@1.0.0#far",
    ]
    assert files == [
        "/path/to/root.yaml",
        "/path/to/mid.yaml",
        "/path/to/far.yaml",
    ]
    assert exc.value.details["path"] == "db"
    assert exc.value.details["conflict"] == "type_mismatch"


def test_merge_error_provenance_excludes_layers_without_value_at_conflict_path():
    layers = [
        {"db": {"host": "a"}},
        {"unrelated": 1},
        {"db": "conflict"},
    ]
    provenance = [
        ("layer-near", ""),
        ("layer-mid", ""),
        ("layer-far", ""),
    ]
    with pytest.raises(MergeError) as exc:
        merge_namespaces(layers, provenance=provenance)
    ancestors = [n["ancestor"] for n in exc.value.location]
    assert ancestors == ["layer-near", "layer-far"]


def test_merge_namespaces_provenance_length_mismatch():
    with pytest.raises(ValueError):
        merge_namespaces([{"a": 1}, {"a": 2}], provenance=[("only", "")])
