import io
import json
from pathlib import Path

import pytest

from stemmata.cache import Cache
from stemmata.cli import run
from stemmata.errors import EXIT_OFFLINE, EXIT_OK, EXIT_SCHEMA, EXIT_USAGE


class _Capture:
    def __init__(self):
        self.out = io.StringIO()
        self.err = io.StringIO()


def _write_valid_package(root: Path, *, name: str = "@acme/pkg", version: str = "1.2.3") -> None:
    (root / "prompts").mkdir(parents=True, exist_ok=True)
    (root / "prompts" / "base.yaml").write_text("body: hello\n", encoding="utf-8")
    manifest = {
        "name": name,
        "version": version,
        "prompts": [{"id": "base", "path": "prompts/base.yaml", "contentType": "yaml"}],
    }
    (root / "package.json").write_text(json.dumps(manifest), encoding="utf-8")


def test_install_fresh_package_populates_cache(tmp_path):
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    _write_valid_package(pkg)
    cache_dir = tmp_path / "cache"
    cap = _Capture()
    code = run(
        ["--cache-dir", str(cache_dir), "--output", "json", "install", str(pkg)],
        stdout=cap.out, stderr=cap.err,
    )
    assert code == EXIT_OK, cap.err.getvalue()
    env = json.loads(cap.out.getvalue())
    assert env["result"]["installed"] is True
    assert env["result"]["name"] == "@acme/pkg"
    assert env["result"]["version"] == "1.2.3"
    cache = Cache(root=cache_dir)
    assert cache.has_package("@acme/pkg", "1.2.3")
    installed = cache.package_dir("@acme/pkg", "1.2.3")
    assert (installed / "package.json").is_file()
    assert (installed / "prompts" / "base.yaml").read_bytes() == b"body: hello\n"


def test_install_is_noop_when_already_cached(tmp_path):
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    _write_valid_package(pkg)
    cache_dir = tmp_path / "cache"

    cap1 = _Capture()
    assert run(
        ["--cache-dir", str(cache_dir), "--output", "json", "install", str(pkg)],
        stdout=cap1.out, stderr=cap1.err,
    ) == EXIT_OK
    env1 = json.loads(cap1.out.getvalue())
    assert env1["result"]["installed"] is True

    cap2 = _Capture()
    assert run(
        ["--cache-dir", str(cache_dir), "--output", "json", "install", str(pkg)],
        stdout=cap2.out, stderr=cap2.err,
    ) == EXIT_OK
    env2 = json.loads(cap2.out.getvalue())
    assert env2["result"]["installed"] is False
    assert env2["result"]["cache_path"] == env1["result"]["cache_path"]


def test_install_refresh_re_fetches_already_cached_package(tmp_path):
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    _write_valid_package(pkg)
    cache_dir = tmp_path / "cache"

    cap1 = _Capture()
    assert run(
        ["--cache-dir", str(cache_dir), "--output", "json", "install", str(pkg)],
        stdout=cap1.out, stderr=cap1.err,
    ) == EXIT_OK
    assert json.loads(cap1.out.getvalue())["result"]["installed"] is True

    (pkg / "prompts" / "base.yaml").write_bytes(b"body: refreshed\n")

    cap2 = _Capture()
    assert run(
        ["--cache-dir", str(cache_dir), "--refresh", "--output", "json",
         "install", str(pkg)],
        stdout=cap2.out, stderr=cap2.err,
    ) == EXIT_OK, cap2.err.getvalue()
    env2 = json.loads(cap2.out.getvalue())
    assert env2["result"]["installed"] is True

    cache = Cache(root=cache_dir)
    installed = cache.package_dir("@acme/pkg", "1.2.3")
    assert (installed / "prompts" / "base.yaml").read_bytes() == b"body: refreshed\n"


def test_install_without_refresh_does_not_overwrite_cached_payload(tmp_path):
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    _write_valid_package(pkg)
    cache_dir = tmp_path / "cache"

    cap1 = _Capture()
    assert run(
        ["--cache-dir", str(cache_dir), "--output", "json", "install", str(pkg)],
        stdout=cap1.out, stderr=cap1.err,
    ) == EXIT_OK

    (pkg / "prompts" / "base.yaml").write_bytes(b"body: changed_locally\n")

    cap2 = _Capture()
    assert run(
        ["--cache-dir", str(cache_dir), "--output", "json", "install", str(pkg)],
        stdout=cap2.out, stderr=cap2.err,
    ) == EXIT_OK
    env2 = json.loads(cap2.out.getvalue())
    assert env2["result"]["installed"] is False

    cache = Cache(root=cache_dir)
    installed = cache.package_dir("@acme/pkg", "1.2.3")
    assert (installed / "prompts" / "base.yaml").read_bytes() == b"body: hello\n"


def test_install_defaults_to_current_dir(tmp_path, monkeypatch):
    _write_valid_package(tmp_path, name="@x/here", version="0.0.1")
    cache_dir = tmp_path / "cache"
    monkeypatch.chdir(tmp_path)
    cap = _Capture()
    code = run(
        ["--cache-dir", str(cache_dir), "--output", "json", "install"],
        stdout=cap.out, stderr=cap.err,
    )
    assert code == EXIT_OK, cap.err.getvalue()
    env = json.loads(cap.out.getvalue())
    assert env["result"]["name"] == "@x/here"
    assert env["result"]["installed"] is True


def test_install_fails_with_exit_10_when_package_json_missing(tmp_path):
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    cache_dir = tmp_path / "cache"
    cap = _Capture()
    code = run(
        ["--cache-dir", str(cache_dir), "--output", "json", "install", str(pkg)],
        stdout=cap.out, stderr=cap.err,
    )
    assert code == EXIT_SCHEMA
    env = json.loads(cap.out.getvalue())
    assert env["status"] == "error"
    assert env["error"]["code"] == EXIT_SCHEMA
    assert env["error"]["details"]["reason"] == "missing_manifest"


@pytest.mark.parametrize("drop", ["name", "version", "prompts"])
def test_install_fails_with_exit_10_when_required_field_missing(tmp_path, drop):
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / "prompts").mkdir()
    (pkg / "prompts" / "base.yaml").write_text("k: v\n", encoding="utf-8")
    manifest = {
        "name": "@acme/pkg",
        "version": "1.0.0",
        "prompts": [{"id": "base", "path": "prompts/base.yaml", "contentType": "yaml"}],
    }
    del manifest[drop]
    (pkg / "package.json").write_text(json.dumps(manifest), encoding="utf-8")

    cache_dir = tmp_path / "cache"
    cap = _Capture()
    code = run(
        ["--cache-dir", str(cache_dir), "--output", "json", "install", str(pkg)],
        stdout=cap.out, stderr=cap.err,
    )
    assert code == EXIT_SCHEMA
    env = json.loads(cap.out.getvalue())
    assert env["error"]["code"] == EXIT_SCHEMA
    assert drop in env["error"]["message"]


def test_install_fails_with_exit_10_when_prompts_empty(tmp_path):
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    manifest = {"name": "@acme/pkg", "version": "1.0.0", "prompts": []}
    (pkg / "package.json").write_text(json.dumps(manifest), encoding="utf-8")
    cache_dir = tmp_path / "cache"
    cap = _Capture()
    code = run(
        ["--cache-dir", str(cache_dir), "--output", "json", "install", str(pkg)],
        stdout=cap.out, stderr=cap.err,
    )
    assert code == EXIT_SCHEMA
    env = json.loads(cap.out.getvalue())
    assert env["error"]["details"]["reason"] == "empty_prompts"


def test_install_fails_with_exit_10_when_package_json_invalid_json(tmp_path):
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / "package.json").write_text("{not json", encoding="utf-8")
    cache_dir = tmp_path / "cache"
    cap = _Capture()
    code = run(
        ["--cache-dir", str(cache_dir), "--output", "json", "install", str(pkg)],
        stdout=cap.out, stderr=cap.err,
    )
    assert code == EXIT_SCHEMA
    env = json.loads(cap.out.getvalue())
    assert env["error"]["details"]["reason"] == "invalid_json"


def test_install_fails_with_exit_10_when_package_json_not_object(tmp_path):
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / "package.json").write_text("[1, 2, 3]", encoding="utf-8")
    cache_dir = tmp_path / "cache"
    cap = _Capture()
    code = run(
        ["--cache-dir", str(cache_dir), "--output", "json", "install", str(pkg)],
        stdout=cap.out, stderr=cap.err,
    )
    assert code == EXIT_SCHEMA
    env = json.loads(cap.out.getvalue())
    assert env["error"]["details"]["reason"] == "not_object"


def test_install_fails_with_exit_2_when_target_not_a_directory(tmp_path):
    missing = tmp_path / "no-such-dir"
    cache_dir = tmp_path / "cache"
    cap = _Capture()
    code = run(
        ["--cache-dir", str(cache_dir), "--output", "json", "install", str(missing)],
        stdout=cap.out, stderr=cap.err,
    )
    assert code == EXIT_USAGE
    env = json.loads(cap.out.getvalue())
    assert env["error"]["details"]["reason"] == "not_a_directory"


def test_install_enables_offline_resolution_of_cached_package(tmp_path):
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    _write_valid_package(pkg, name="@acme/cached", version="2.0.0")
    cache_dir = tmp_path / "cache"
    assert run(
        ["--cache-dir", str(cache_dir), "install", str(pkg)],
        stdout=io.StringIO(), stderr=io.StringIO(),
    ) == EXIT_OK

    cap = _Capture()
    code = run(
        ["--offline", "--cache-dir", str(cache_dir), "--output", "json",
         "describe", "@acme/cached@2.0.0"],
        stdout=cap.out, stderr=cap.err,
    )
    assert code == EXIT_OK, cap.out.getvalue() + cap.err.getvalue()
