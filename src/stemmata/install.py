from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from stemmata.bundle import build_tarball, collect_members
from stemmata.cache import Cache
from stemmata.errors import GenericError
from stemmata.manifest import parse_manifest


@dataclass
class InstallResult:
    name: str
    version: str
    cache_path: str
    installed: bool


def _ineligible(message: str) -> GenericError:
    return GenericError(message, exception="IneligiblePackage")


def _missing_or_empty(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, (str, list, dict, tuple)) and len(value) == 0:
        return True
    return False


def run_install(path: Path, *, cache: Cache) -> InstallResult:
    base = path.resolve()
    if not base.is_dir():
        raise _ineligible(f"install target {str(path)!r} is not a directory")

    manifest_file = base / "package.json"
    if not manifest_file.is_file():
        raise _ineligible(f"no package.json found at {manifest_file}")

    raw = manifest_file.read_text(encoding="utf-8")
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        raise _ineligible(f"package.json at {manifest_file} is not valid JSON: {e.msg}")
    if not isinstance(data, dict):
        raise _ineligible(f"package.json at {manifest_file} must be a JSON object")

    missing = [key for key in ("name", "version", "prompts") if key not in data or _missing_or_empty(data[key])]
    if missing:
        raise _ineligible(
            f"package.json at {manifest_file} is missing required field(s): {', '.join(missing)}"
        )

    name = data["name"]
    version = data["version"]
    if isinstance(name, str) and isinstance(version, str) and cache.has_package(name, version):
        return InstallResult(
            name=name,
            version=version,
            cache_path=str(cache.package_dir(name, version)),
            installed=False,
        )

    manifest = parse_manifest(raw, file=str(manifest_file))

    extra_files: list[str] = ["package.json"]
    for optional in ("README.md", "LICENSE", "LICENSE.md", "LICENSE.txt"):
        if (base / optional).is_file():
            extra_files.append(optional)
    yaml_paths = [e.path for e in manifest.prompts]
    markdown_paths = [e.path for e in manifest.resources]
    members = collect_members(base, extra_files, yaml_paths, markdown_paths)
    tarball_bytes = build_tarball(members)

    with cache.lock(manifest.name, manifest.version):
        if cache.has_package(manifest.name, manifest.version):
            installed = False
        else:
            cache.install_tarball(manifest.name, manifest.version, tarball_bytes)
            installed = True

    return InstallResult(
        name=manifest.name,
        version=manifest.version,
        cache_path=str(cache.package_dir(manifest.name, manifest.version)),
        installed=installed,
    )
