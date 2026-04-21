from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from stemmata.errors import CycleError, MergeError, ReferenceError_, UnresolvableError
from stemmata.yaml_loader import scalar_meta


_PLACEHOLDER_RE = re.compile(r"\$\{(=)?([^{}]+)\}")
_ESCAPE_TOKEN = "\x00PCLI_ESC_DOLLAR\x00"

_RESOURCE_PREFIX = "resource:"


@dataclass
class Layer:
    canonical_id: str
    data: dict[str, Any]


@dataclass
class ResourceBinding:
    bindings: dict[tuple[str, str], str] = field(default_factory=dict)
    flat_texts: dict[str, str] = field(default_factory=dict)
    prompt_resources: dict[str, list[str]] = field(default_factory=dict)
    resource_children: dict[str, list[str]] = field(default_factory=dict)
    resource_files: dict[str, str] = field(default_factory=dict)


def _resource_body(inner: str) -> str | None:
    stripped = inner.lstrip()
    if not stripped.startswith(_RESOURCE_PREFIX):
        return None
    return stripped[len(_RESOURCE_PREFIX):].strip()


def _resource_lookup(
    binding: ResourceBinding | None,
    file: str | None,
    body: str,
    *,
    line: int | None,
    column: int | None,
) -> str:
    placeholder = f"${{resource:{body}}}"
    searched_in = file or "<local>"
    if binding is not None:
        canonical = binding.bindings.get((file or "", body))
        if canonical is not None:
            text = binding.flat_texts.get(canonical)
            if text is not None:
                return text
            searched_in = canonical
    raise ReferenceError_(
        f"unresolved resource reference {placeholder}",
        file=file,
        line=line,
        column=column,
        reference=placeholder,
        searched_in=searched_in,
        kind="resource",
        reason="missing",
    )


_NOT_FOUND = object()
_NULL_SENTINEL = object()


def _walk_path(root: Any, parts: list[str]) -> tuple[object, bool]:
    cur: Any = root
    for i, p in enumerate(parts):
        if cur is None:
            return _NOT_FOUND, False
        if not isinstance(cur, dict):
            return _NOT_FOUND, False
        if p not in cur:
            return _NOT_FOUND, False
        cur = cur[p]
    if cur is None:
        return _NULL_SENTINEL, True
    return cur, True


def lookup_with_provenance(
    namespace: Any,
    layers: list[Layer],
    path: str,
) -> tuple[Any, str, str | None, list[str]]:
    parts = path.split(".")
    for p in parts:
        if not p:
            return _NOT_FOUND, "not_provided", None, [layer.canonical_id for layer in layers]
    value, found = _walk_path(namespace, parts)
    searched = [layer.canonical_id for layer in layers]
    if not found:
        return _NOT_FOUND, "not_provided", None, searched
    if value is _NULL_SENTINEL:
        provider: str | None = None
        for layer in layers:
            v, f = _walk_path(layer.data, parts)
            if f:
                provider = layer.canonical_id
                break
        return _NULL_SENTINEL, "explicit_null", provider, searched
    return value, "ok", None, searched


def _err_unresolvable(path: str, *, file: str | None, line: int | None, column: int | None, reason: str, searched: list[str], provider: str | None) -> UnresolvableError:
    return UnresolvableError(
        path,
        file=file,
        line=line,
        column=column,
        reason=reason,
        ancestors_searched=searched,
        providing_ancestor=provider,
    )


def _stringify_scalar(v: Any) -> str:
    if v is True:
        return "true"
    if v is False:
        return "false"
    if v is None:
        return ""
    if isinstance(v, float):
        if v != v:
            return ".nan"
        return repr(v)
    return str(v)


def _is_scalar(v: Any) -> bool:
    return v is None or isinstance(v, (bool, int, float, str))


def _parse_placeholder_tokens(text: str) -> list[tuple[str, str]]:
    tokens: list[tuple[str, str]] = []
    i = 0
    while i < len(text):
        if text[i] == "$" and i + 1 < len(text) and text[i + 1] == "$":
            if i + 2 < len(text) and text[i + 2] == "{":
                j = text.find("}", i + 3)
                if j == -1:
                    tokens.append(("text", text[i]))
                    i += 1
                    continue
                tokens.append(("escape", text[i + 2:j + 1]))
                i = j + 1
                continue
            tokens.append(("text", "$"))
            i += 2
            continue
        if text[i] == "$" and i + 1 < len(text) and text[i + 1] == "{":
            j = text.find("}", i + 2)
            if j == -1:
                tokens.append(("text", text[i]))
                i += 1
                continue
            inner = text[i + 2:j]
            tokens.append(("ph", inner))
            i = j + 1
            continue
        tokens.append(("text", text[i]))
        i += 1
    merged: list[tuple[str, str]] = []
    for kind, val in tokens:
        if merged and merged[-1][0] == "text" and kind == "text":
            merged[-1] = ("text", merged[-1][1] + val)
        else:
            merged.append((kind, val))
    return merged


def _exact_placeholder(text: str) -> tuple[bool, bool, str]:
    trimmed = text.strip()
    if not trimmed.startswith("${") or not trimmed.endswith("}"):
        return False, False, ""
    inner = trimmed[2:-1]
    if "${" in inner or "}" in inner:
        return False, False, ""
    non_splat = inner.startswith("=")
    if non_splat:
        inner = inner[1:]
    if not inner.strip():
        return False, False, ""
    return True, non_splat, inner


def interpolate(
    tree: Any,
    layers: list[Layer],
    *,
    root_file: str,
    resources: ResourceBinding | None = None,
) -> Any:
    namespace = tree
    return _interp(
        tree,
        namespace,
        layers,
        parent_is_list=False,
        root_file=root_file,
        visiting=(),
        resources=resources,
    )


_SPLAT_MARKER = object()


class _Splat:
    __slots__ = ("items",)

    def __init__(self, items: list[Any]) -> None:
        self.items = items


def _raise_cycle(chain: list[str], path: str, *, file: str | None, line: int | None, column: int | None) -> None:
    cycle_ids = chain + [path]
    raise CycleError(
        nodes=[{"file": file, "line": line, "column": column}],
        cycle_ids=cycle_ids,
    )


def _interp(
    node: Any,
    namespace: Any,
    layers: list[Layer],
    *,
    parent_is_list: bool,
    root_file: str,
    visiting: tuple[str, ...],
    resources: ResourceBinding | None = None,
) -> Any:
    if isinstance(node, dict):
        return {
            k: _interp(
                v,
                namespace,
                layers,
                parent_is_list=False,
                root_file=root_file,
                visiting=visiting,
                resources=resources,
            )
            for k, v in node.items()
        }
    if isinstance(node, list):
        out: list[Any] = []
        for item in node:
            resolved = _interp(
                item,
                namespace,
                layers,
                parent_is_list=True,
                root_file=root_file,
                visiting=visiting,
                resources=resources,
            )
            if isinstance(resolved, _Splat):
                out.extend(resolved.items)
            else:
                out.append(resolved)
        return out
    if isinstance(node, str):
        file, line, column, is_flow = scalar_meta(node)
        file = file or root_file
        exact, non_splat, inner_path = _exact_placeholder(node)
        if exact and is_flow:
            body = _resource_body(inner_path)
            if body is not None:
                return _resource_lookup(resources, file, body, line=line, column=column)
            path = inner_path.strip()
            if path in visiting:
                _raise_cycle(list(visiting), path, file=file, line=line, column=column)
            value, status, provider, searched = lookup_with_provenance(namespace, layers, path)
            if status == "not_provided":
                raise _err_unresolvable(path, file=file, line=line, column=column, reason="not_provided", searched=searched, provider=None)
            if status == "explicit_null":
                raise _err_unresolvable(path, file=file, line=line, column=column, reason="explicit_null", searched=searched, provider=provider)
            resolved = _interp(
                value,
                namespace,
                layers,
                parent_is_list=False,
                root_file=root_file,
                visiting=visiting + (path,),
                resources=resources,
            )
            if parent_is_list and isinstance(resolved, list) and not non_splat:
                return _Splat(list(resolved))
            return resolved
        tokens = _parse_placeholder_tokens(str(node))
        has_placeholder = any(k == "ph" for k, _ in tokens)
        has_escape = any(k == "escape" for k, _ in tokens)
        if not has_placeholder and not has_escape:
            return node
        parts_out: list[str] = []
        for kind, val in tokens:
            if kind == "text":
                parts_out.append(val)
            elif kind == "escape":
                parts_out.append("$" + val)
            else:
                body = _resource_body(val)
                if body is not None:
                    parts_out.append(_resource_lookup(resources, file, body, line=line, column=column))
                    continue
                inner = val
                if inner.startswith("="):
                    inner = inner[1:]
                inner = inner.strip()
                if inner in visiting:
                    _raise_cycle(list(visiting), inner, file=file, line=line, column=column)
                value, status, provider, searched = lookup_with_provenance(namespace, layers, inner)
                if status == "not_provided":
                    raise _err_unresolvable(inner, file=file, line=line, column=column, reason="not_provided", searched=searched, provider=None)
                if status == "explicit_null":
                    raise _err_unresolvable(inner, file=file, line=line, column=column, reason="explicit_null", searched=searched, provider=provider)
                resolved = _interp(
                    value,
                    namespace,
                    layers,
                    parent_is_list=False,
                    root_file=root_file,
                    visiting=visiting + (inner,),
                    resources=resources,
                )
                if not _is_scalar(resolved):
                    raise MergeError(
                        path=inner,
                        conflict="non_scalar_in_textual",
                        types=[type(resolved).__name__],
                        nodes=[{"file": file, "line": line, "column": column, "ancestor": root_file}],
                    )
                parts_out.append(_stringify_scalar(resolved))
        return "".join(parts_out)
    return node
