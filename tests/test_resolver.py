import io
import json
import tarfile

import pytest

from stemmata.cache import Cache
from stemmata.errors import CycleError, PromptCliError, ReferenceError_, SchemaError
from stemmata.interp import Layer, interpolate
from stemmata.merge import merge_namespaces
from stemmata.npmrc import NpmConfig
from stemmata.registry import RegistryClient
from stemmata.resolver import Session, layer_order, resolve_graph


class _FakeRegistry(RegistryClient):
    def __init__(self, tarballs: dict[tuple[str, str], bytes]):
        super().__init__(config=NpmConfig(entries={}), offline=False)
        self.tarballs = tarballs
        self.fetch_count = 0

    def fetch_tarball(self, name, version):
        self.fetch_count += 1
        key = (name, version)
        if key not in self.tarballs:
            from stemmata.errors import NetworkError
            raise NetworkError(f"{name}@{version}", 404, "not found")
        return f"fake://{name}/{version}", self.tarballs[key]


def _pack(manifest: dict, files: dict[str, bytes]) -> bytes:
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


def _session(tmp_path, tarballs=None):
    cache = Cache(root=tmp_path / "cache")
    reg = _FakeRegistry(tarballs or {})
    return Session(cache=cache, registry=reg)


def _resolve(tmp_path, target, tarballs=None):
    session = _session(tmp_path, tarballs=tarballs)
    graph = resolve_graph(str(target), session)
    order = layer_order(graph)
    layers_data = [graph.nodes[nid].doc.namespace for nid in order]
    merged = merge_namespaces(layers_data)
    layers = [Layer(canonical_id=nid.canonical, data=graph.nodes[nid].doc.namespace) for nid in order]
    root_file = graph.nodes[graph.root_id].file
    return interpolate(merged, layers, root_file=root_file), graph, order


def test_local_single_file(tmp_path):
    p = tmp_path / "solo.yaml"
    p.write_text("vars:\n  x: 1\nbody: value=${vars.x}\n")
    result, graph, order = _resolve(tmp_path, p)
    assert result["body"] == "value=1"
    assert len(order) == 1


def test_local_with_ancestor(tmp_path):
    base = tmp_path / "base.yaml"
    base.write_text("vars:\n  region: eu\n  timeout: 30\n")
    child = tmp_path / "child.yaml"
    child.write_text("ancestors:\n  - ./base.yaml\nvars:\n  region: us\nbody: ${vars.region}\n")
    result, graph, order = _resolve(tmp_path, child)
    assert result["vars"]["region"] == "us"
    assert result["vars"]["timeout"] == 30
    assert result["body"] == "us"
    assert len(order) == 2


def test_local_cycle_detected(tmp_path):
    a = tmp_path / "a.yaml"
    b = tmp_path / "b.yaml"
    a.write_text("ancestors:\n  - ./b.yaml\n")
    b.write_text("ancestors:\n  - ./a.yaml\n")
    session = _session(tmp_path)
    with pytest.raises(CycleError):
        resolve_graph(str(a), session)


def test_local_missing_reference(tmp_path):
    p = tmp_path / "x.yaml"
    p.write_text("ancestors:\n  - ./nonexistent.yaml\n")
    session = _session(tmp_path)
    with pytest.raises(ReferenceError_):
        resolve_graph(str(p), session)


def test_diamond_inheritance(tmp_path):
    x = tmp_path / "x.yaml"
    x.write_text("vars:\n  color: red\n")
    a = tmp_path / "a.yaml"
    a.write_text("ancestors:\n  - ./x.yaml\nvars:\n  shape: square\n")
    b = tmp_path / "b.yaml"
    b.write_text("ancestors:\n  - ./x.yaml\nvars:\n  size: big\n")
    root = tmp_path / "root.yaml"
    root.write_text("ancestors:\n  - ./a.yaml\n  - ./b.yaml\nbody: ${vars.color}/${vars.shape}/${vars.size}\n")
    result, _, order = _resolve(tmp_path, root)
    assert result["body"] == "red/square/big"
    # Root, A, B, X in BFS order
    assert len(order) == 4


def test_cross_package_fetch(tmp_path):
    base_manifest = {
        "name": "@acme/common",
        "version": "1.0.0",
        "prompts": [{"id": "base", "path": "prompts/base.yaml"}],
    }
    base_yaml = b"vars:\n  region: eu\n  timeout: 30\n"
    tarballs = {("@acme/common", "1.0.0"): _pack(base_manifest, {"prompts/base.yaml": base_yaml})}

    child = tmp_path / "child.yaml"
    child.write_text(
        "ancestors:\n  - package: '@acme/common'\n    version: '1.0.0'\n    prompt: base\n"
        "vars:\n  region: us\nbody: ${vars.region}-${vars.timeout}\n"
    )
    result, graph, order = _resolve(tmp_path, child, tarballs=tarballs)
    assert result["body"] == "us-30"


def test_missing_prompt_id_in_package(tmp_path):
    base_manifest = {
        "name": "@acme/common",
        "version": "1.0.0",
        "prompts": [{"id": "base", "path": "prompts/base.yaml"}],
    }
    tarballs = {("@acme/common", "1.0.0"): _pack(base_manifest, {"prompts/base.yaml": b"foo: 1\n"})}
    child = tmp_path / "child.yaml"
    child.write_text(
        "ancestors:\n  - package: '@acme/common'\n    version: '1.0.0'\n    prompt: does_not_exist\n"
    )
    with pytest.raises(ReferenceError_):
        _resolve(tmp_path, child, tarballs=tarballs)


def test_version_conflict_nearest_wins(tmp_path):
    manifest_v1 = {
        "name": "@x/lib",
        "version": "1.0.0",
        "prompts": [{"id": "base", "path": "prompts/base.yaml"}],
    }
    manifest_v2 = dict(manifest_v1, version="2.0.0")
    tarballs = {
        ("@x/lib", "1.0.0"): _pack(manifest_v1, {"prompts/base.yaml": b"val: one\n"}),
        ("@x/lib", "2.0.0"): _pack(manifest_v2, {"prompts/base.yaml": b"val: two\n"}),
    }
    middle_manifest = {
        "name": "@mid/pkg",
        "version": "1.0.0",
        "prompts": [{"id": "m", "path": "prompts/m.yaml"}],
    }
    m_yaml = (
        b"ancestors:\n"
        b"  - package: '@x/lib'\n"
        b"    version: '1.0.0'\n"
        b"    prompt: base\n"
    )
    tarballs[("@mid/pkg", "1.0.0")] = _pack(middle_manifest, {"prompts/m.yaml": m_yaml})

    child = tmp_path / "child.yaml"
    child.write_text(
        "ancestors:\n"
        "  - package: '@x/lib'\n"
        "    version: '2.0.0'\n"
        "    prompt: base\n"
        "  - package: '@mid/pkg'\n"
        "    version: '1.0.0'\n"
        "    prompt: m\n"
        "body: ${val}\n"
    )
    result, _, _ = _resolve(tmp_path, child, tarballs=tarballs)
    assert result["body"] == "two"


def test_coord_prompt_with_relative_path_ancestor(tmp_path):
    manifest = {
        "name": "@acme/prompts",
        "version": "1.0.0",
        "prompts": [
            {"id": "base", "path": "prompts/base.yaml"},
            {"id": "main", "path": "prompts/main.yaml"},
        ],
    }
    base_yaml = b"vars:\n  region: eu\n  timeout: 30\n"
    main_yaml = (
        b"ancestors:\n  - ./base.yaml\n"
        b"vars:\n  region: us\n"
        b"body: ${vars.region}-${vars.timeout}\n"
    )
    tarballs = {
        ("@acme/prompts", "1.0.0"): _pack(
            manifest,
            {"prompts/base.yaml": base_yaml, "prompts/main.yaml": main_yaml},
        )
    }
    result, graph, order = _resolve(tmp_path, "@acme/prompts@1.0.0#main", tarballs=tarballs)
    assert result["body"] == "us-30"
    assert [nid.canonical for nid in order] == [
        "@acme/prompts@1.0.0#main",
        "@acme/prompts@1.0.0#base",
    ]


def test_coord_prompt_relative_ancestor_escape_rejected(tmp_path):
    manifest = {
        "name": "@acme/prompts",
        "version": "1.0.0",
        "prompts": [{"id": "main", "path": "prompts/main.yaml"}],
    }
    main_yaml = b"ancestors:\n  - ../../outside.yaml\n"
    tarballs = {
        ("@acme/prompts", "1.0.0"): _pack(manifest, {"prompts/main.yaml": main_yaml})
    }
    with pytest.raises(SchemaError):
        _resolve(tmp_path, "@acme/prompts@1.0.0#main", tarballs=tarballs)


def test_coord_prompt_relative_ancestor_missing_manifest_entry(tmp_path):
    manifest = {
        "name": "@acme/prompts",
        "version": "1.0.0",
        "prompts": [{"id": "main", "path": "prompts/main.yaml"}],
    }
    main_yaml = b"ancestors:\n  - ./orphan.yaml\n"
    tarballs = {
        ("@acme/prompts", "1.0.0"): _pack(
            manifest,
            {"prompts/main.yaml": main_yaml, "prompts/orphan.yaml": b"foo: 1\n"},
        )
    }
    with pytest.raises(ReferenceError_):
        _resolve(tmp_path, "@acme/prompts@1.0.0#main", tarballs=tarballs)


def test_cache_hit_avoids_refetch(tmp_path):
    base_manifest = {"name": "@a/b", "version": "1.0.0", "prompts": [{"id": "base", "path": "prompts/base.yaml"}]}
    tarballs = {("@a/b", "1.0.0"): _pack(base_manifest, {"prompts/base.yaml": b"foo: 1\n"})}
    child = tmp_path / "child.yaml"
    child.write_text(
        "ancestors:\n  - package: '@a/b'\n    version: '1.0.0'\n    prompt: base\n"
    )
    session1 = _session(tmp_path, tarballs=tarballs)
    resolve_graph(str(child), session1)
    assert session1.registry.fetch_count == 1
    session2 = _session(tmp_path, tarballs=tarballs)
    resolve_graph(str(child), session2)
    assert session2.registry.fetch_count == 0


def test_offline_without_cache_errors(tmp_path):
    from stemmata.errors import OfflineError
    cache = Cache(root=tmp_path / "cache")
    reg = RegistryClient(config=NpmConfig(entries={"@a:registry": "https://x/"}), offline=True)
    session = Session(cache=cache, registry=reg)
    child = tmp_path / "child.yaml"
    child.write_text(
        "ancestors:\n  - package: '@a/b'\n    version: '1.0.0'\n    prompt: base\n"
    )
    with pytest.raises(OfflineError):
        resolve_graph(str(child), session)


def test_registry_opener_honours_proxy_env(monkeypatch):
    import urllib.request
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy.example.com:8080")
    reg = RegistryClient(config=NpmConfig(entries={}))
    opener = reg._opener()
    assert any(isinstance(h, urllib.request.ProxyHandler) for h in opener.handlers)
    proxy_handler = next(h for h in opener.handlers if isinstance(h, urllib.request.ProxyHandler))
    assert "https" in proxy_handler.proxies
    assert proxy_handler.proxies["https"] == "http://proxy.example.com:8080"
