import io
import json
import tarfile

import pytest

from stemmata.cache import Cache
from stemmata.errors import SchemaError
from stemmata.interp import Layer, interpolate
from stemmata.merge import merge_namespaces
from stemmata.npmrc import NpmConfig
from stemmata.prompt_doc import parse_prompt
from stemmata.registry import RegistryClient
from stemmata.resolver import Session, layer_order, resolve_graph


class _FakeRegistry(RegistryClient):
    def __init__(self, tarballs):
        super().__init__(config=NpmConfig(entries={}), offline=False)
        self.tarballs = tarballs

    def fetch_tarball(self, name, version):
        key = (name, version)
        if key not in self.tarballs:
            from stemmata.errors import NetworkError
            raise NetworkError(f"{name}@{version}", 404, "not found")
        return f"fake://{name}/{version}", self.tarballs[key]


def _pack(manifest, files):
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        data = json.dumps(manifest).encode()
        ti = tarfile.TarInfo("package/package.json")
        ti.size = len(data)
        ti.mode = 0o644
        tf.addfile(ti, io.BytesIO(data))
        for relpath, content in files.items():
            ti = tarfile.TarInfo(f"package/{relpath}")
            ti.size = len(content)
            ti.mode = 0o644
            tf.addfile(ti, io.BytesIO(content))
    return buf.getvalue()


def _session(tmp_path, tarballs):
    cache = Cache(root=tmp_path / "cache")
    reg = _FakeRegistry(tarballs)
    return Session(cache=cache, registry=reg)


def _resolve(tmp_path, target, tarballs):
    session = _session(tmp_path, tarballs)
    graph = resolve_graph(str(target), session)
    order = layer_order(graph)
    layers_data = [graph.nodes[nid].doc.namespace for nid in order]
    merged = merge_namespaces(layers_data)
    layers = [Layer(canonical_id=nid.canonical, data=graph.nodes[nid].doc.namespace) for nid in order]
    root_file = graph.nodes[graph.root_id].file
    return interpolate(merged, layers, root_file=root_file), graph, order


# --- parse_prompt unit tests ---------------------------------------------------


def test_parse_prompt_content_type_json_overrides_yaml_extension():
    text = '{"foo": "bar"}'
    doc = parse_prompt(text, file="virtual.yaml", content_type="json")
    assert doc.namespace["foo"] == "bar"


def test_parse_prompt_content_type_json_rejects_yaml_only_payload():
    text = "date: 2024-01-15\nitems: [a, b, c]\n"
    with pytest.raises(SchemaError) as exc:
        parse_prompt(text, file="@scope/pkg@1.0.0#j", content_type="json")
    assert exc.value.code == 10
    assert exc.value.details["reason"] == "json_parse_error"


def test_parse_prompt_content_type_yaml_overrides_json_extension():
    text = "foo: bar\n"
    doc = parse_prompt(text, file="virtual.json", content_type="yaml")
    assert doc.namespace["foo"] == "bar"


def test_parse_prompt_default_dispatch_still_uses_extension():
    text = "foo: bar\n"
    doc = parse_prompt(text, file="virtual.yaml")
    assert doc.namespace["foo"] == "bar"
    with pytest.raises(SchemaError) as exc:
        parse_prompt(text, file="virtual.json")
    assert exc.value.code == 10
    assert exc.value.details["reason"] == "json_parse_error"


def test_parse_prompt_not_mapping_error_uses_explicit_content_type():
    with pytest.raises(SchemaError) as exc:
        parse_prompt('"a string"', file="@scope/pkg@1.0.0#s", content_type="json")
    assert "must be a JSON mapping" in str(exc.value)


# --- registry-coordinate integration tests ------------------------------------


def test_coord_json_prompt_parses_as_json_when_manifest_says_json(tmp_path):
    manifest = {
        "name": "@acme/jpkg",
        "version": "1.0.0",
        "prompts": [{"id": "j", "path": "prompts/j.json", "contentType": "json"}],
    }
    payload = b'{"vars": {"region": "eu"}, "body": "v=${vars.region}"}\n'
    tarballs = {("@acme/jpkg", "1.0.0"): _pack(manifest, {"prompts/j.json": payload})}
    result, _, _ = _resolve(tmp_path, "@acme/jpkg@1.0.0#j", tarballs=tarballs)
    assert result["body"] == "v=eu"


def test_coord_json_prompt_rejects_yaml_only_payload(tmp_path):
    """The bug fix: a contentType=json entry whose bytes are valid YAML but
    invalid JSON must hard-fail at parse time when reached via coordinate,
    rather than silently parsing as YAML."""
    manifest = {
        "name": "@acme/jpkg",
        "version": "1.0.0",
        "prompts": [{"id": "j", "path": "prompts/j.json", "contentType": "json"}],
    }
    payload = b"date: 2024-01-15\nitems: [a, b, c]\n"
    tarballs = {("@acme/jpkg", "1.0.0"): _pack(manifest, {"prompts/j.json": payload})}
    with pytest.raises(SchemaError) as exc:
        _resolve(tmp_path, "@acme/jpkg@1.0.0#j", tarballs=tarballs)
    assert exc.value.code == 10
    assert exc.value.details["reason"] == "json_parse_error"


def test_coord_json_prompt_parity_with_local_file_path(tmp_path):
    """B3 (deterministic output): the same physical bytes resolved via local
    path and via registry coordinate must produce the same outcome."""
    manifest = {
        "name": "@acme/jpkg",
        "version": "1.0.0",
        "prompts": [{"id": "j", "path": "prompts/j.json", "contentType": "json"}],
    }
    payload = b"date: 2024-01-15\nitems: [a, b, c]\n"
    tarballs = {("@acme/jpkg", "1.0.0"): _pack(manifest, {"prompts/j.json": payload})}

    local = tmp_path / "j.json"
    local.write_bytes(payload)

    with pytest.raises(SchemaError) as local_exc:
        _resolve(tmp_path, str(local), tarballs=tarballs)
    with pytest.raises(SchemaError) as coord_exc:
        _resolve(tmp_path, "@acme/jpkg@1.0.0#j", tarballs=tarballs)

    assert local_exc.value.code == coord_exc.value.code == 10
    assert (
        local_exc.value.details["reason"]
        == coord_exc.value.details["reason"]
        == "json_parse_error"
    )


def test_coord_yaml_prompt_unaffected(tmp_path):
    """Pure-YAML packages — the dominant case — must keep working unchanged."""
    manifest = {
        "name": "@acme/ypkg",
        "version": "1.0.0",
        "prompts": [{"id": "y", "path": "prompts/y.yaml", "contentType": "yaml"}],
    }
    payload = b"vars:\n  region: eu\nbody: v=${vars.region}\n"
    tarballs = {("@acme/ypkg", "1.0.0"): _pack(manifest, {"prompts/y.yaml": payload})}
    result, _, _ = _resolve(tmp_path, "@acme/ypkg@1.0.0#y", tarballs=tarballs)
    assert result["body"] == "v=eu"


def test_coord_yaml_default_content_type_unaffected(tmp_path):
    """When the manifest omits contentType, the default 'yaml' must be honoured."""
    manifest = {
        "name": "@acme/dpkg",
        "version": "1.0.0",
        "prompts": [{"id": "d", "path": "prompts/d.yaml"}],
    }
    payload = b"vars:\n  region: eu\nbody: v=${vars.region}\n"
    tarballs = {("@acme/dpkg", "1.0.0"): _pack(manifest, {"prompts/d.yaml": payload})}
    result, _, _ = _resolve(tmp_path, "@acme/dpkg@1.0.0#d", tarballs=tarballs)
    assert result["body"] == "v=eu"


def test_coord_json_prompt_with_yaml_extension_still_dispatches_by_manifest(tmp_path):
    """The manifest's contentType is the source of truth, not the on-disk
    extension. A .yaml file declared as contentType=json must be parsed as
    JSON when reached via coordinate."""
    manifest = {
        "name": "@acme/mismatch",
        "version": "1.0.0",
        "prompts": [{"id": "m", "path": "prompts/m.yaml", "contentType": "json"}],
    }
    payload = b"date: 2024-01-15\n"
    tarballs = {("@acme/mismatch", "1.0.0"): _pack(manifest, {"prompts/m.yaml": payload})}
    with pytest.raises(SchemaError) as exc:
        _resolve(tmp_path, "@acme/mismatch@1.0.0#m", tarballs=tarballs)
    assert exc.value.code == 10
    assert exc.value.details["reason"] == "json_parse_error"
