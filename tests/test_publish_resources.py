"""Publish-time validation for Markdown resources (publish pre-checks)."""
import json

import pytest

from stemmata.errors import (
    AggregatedError,
    EXIT_CYCLE,
    EXIT_REFERENCE,
    EXIT_SCHEMA,
)
from stemmata.npmrc import NpmConfig
from stemmata.publish import PublishOptions, run_publish


def _write_pkg(tmp_path, manifest_data, files: dict):
    (tmp_path / "package.json").write_text(json.dumps(manifest_data))
    for rel, content in files.items():
        full = tmp_path / rel
        full.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(content, bytes):
            full.write_bytes(content)
        else:
            full.write_text(content)


def _opts(tmp_path, **overrides):
    base = dict(
        package_root=tmp_path,
        dry_run=True,
        config=NpmConfig(entries={"registry": "https://registry.example.com/"}),
        cache_root=tmp_path / ".cache",
    )
    base.update(overrides)
    return PublishOptions(**base)


def test_publish_with_resource_succeeds(tmp_path):
    _write_pkg(tmp_path, {
        "name": "@acme/p",
        "version": "1.0.0",
        "prompts": [{"id": "base", "path": "prompts/base.yaml"}],
        "resources": [{"id": "footer", "path": "resources/footer.md", "contentType": "markdown"}],
    }, {
        "prompts/base.yaml": b'body: "${resource:../resources/footer.md}"\n',
        "resources/footer.md": b"hello\n",
    })
    result = run_publish(_opts(tmp_path))
    assert result.uploaded is False
    assert result.tarball_size > 0


def test_publish_detects_undeclared_local_resource_ref(tmp_path):
    _write_pkg(tmp_path, {
        "name": "@acme/p",
        "version": "1.0.0",
        "prompts": [{"id": "base", "path": "prompts/base.yaml"}],
        "resources": [{"id": "f", "path": "resources/f.md", "contentType": "markdown"}],
    }, {
        "prompts/base.yaml": b'body: "${resource:../resources/other.md}"\n',
        "resources/f.md": b"hello\n",
    })
    with pytest.raises(AggregatedError) as ei:
        run_publish(_opts(tmp_path))
    codes = {e["code"] for e in ei.value.details["errors"]}
    assert EXIT_SCHEMA in codes or EXIT_REFERENCE in codes


def test_publish_detects_resource_cycle(tmp_path):
    _write_pkg(tmp_path, {
        "name": "@acme/p",
        "version": "1.0.0",
        "prompts": [{"id": "base", "path": "prompts/base.yaml"}],
        "resources": [
            {"id": "a", "path": "resources/a.md", "contentType": "markdown"},
            {"id": "b", "path": "resources/b.md", "contentType": "markdown"},
        ],
    }, {
        "prompts/base.yaml": b'body: "${resource:../resources/a.md}"\n',
        "resources/a.md": b"${resource:b.md}\n",
        "resources/b.md": b"${resource:a.md}\n",
    })
    with pytest.raises(AggregatedError) as ei:
        run_publish(_opts(tmp_path))
    codes = {e["code"] for e in ei.value.details["errors"]}
    assert EXIT_CYCLE in codes


def test_publish_tarball_includes_resource_files(tmp_path):
    import io, tarfile
    _write_pkg(tmp_path, {
        "name": "@acme/p",
        "version": "1.0.0",
        "prompts": [{"id": "base", "path": "prompts/base.yaml"}],
        "resources": [{"id": "footer", "path": "resources/footer.md", "contentType": "markdown"}],
    }, {
        "prompts/base.yaml": b'body: "${resource:../resources/footer.md}"\n',
        "resources/footer.md": b"hello\n",
    })
    result = run_publish(_opts(tmp_path, tarball_out=tmp_path / "out.tgz"))
    with tarfile.open(tmp_path / "out.tgz", mode="r:gz") as tf:
        names = {m.name for m in tf.getmembers() if m.isfile()}
    assert "package/resources/footer.md" in names
    assert "package/prompts/base.yaml" in names


def test_publish_rejects_empty_resource_body(tmp_path):
    _write_pkg(tmp_path, {
        "name": "@acme/p",
        "version": "1.0.0",
        "prompts": [{"id": "base", "path": "prompts/base.yaml"}],
        "resources": [{"id": "f", "path": "resources/f.md", "contentType": "markdown"}],
    }, {
        "prompts/base.yaml": b'body: "${resource:}"\n',
        "resources/f.md": b"hello\n",
    })
    with pytest.raises(AggregatedError) as ei:
        run_publish(_opts(tmp_path))
    errs = ei.value.details["errors"]
    assert any(e["code"] == EXIT_SCHEMA and e["details"].get("reason") == "resource_empty_body"
               for e in errs)


def test_publish_rejects_resource_pointing_at_prompt(tmp_path):
    _write_pkg(tmp_path, {
        "name": "@acme/p",
        "version": "1.0.0",
        "prompts": [
            {"id": "base", "path": "prompts/base.yaml"},
            {"id": "other", "path": "prompts/other.yaml"},
        ],
    }, {
        "prompts/base.yaml": b'body: "${resource:@acme/p@1.0.0#other}"\n',
        "prompts/other.yaml": b"x: 1\n",
    })
    with pytest.raises(AggregatedError) as ei:
        run_publish(_opts(tmp_path))
    codes = {e["code"] for e in ei.value.details["errors"]}
    assert EXIT_REFERENCE in codes
    ref_errs = [e for e in ei.value.details["errors"] if e["code"] == EXIT_REFERENCE]
    assert any(e["details"].get("kind") == "resource" and e["details"].get("reason") == "type_mismatch"
               for e in ref_errs)
