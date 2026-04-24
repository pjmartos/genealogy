from __future__ import annotations

import re
from typing import Any

import yaml

from stemmata.errors import SchemaError, UsageError
from stemmata.prompt_doc import RESERVED_KEYS, _expand_dotted_keys


OVERRIDE_CANONICAL_ID = "<overrides>"

_PATH_RE = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_\-]*(\.[a-zA-Z_][a-zA-Z0-9_\-]*)*$")


def parse_set_flags(raw: list[str]) -> dict[str, Any]:
    if not raw:
        return {}
    flat: dict[str, Any] = {}
    for item in raw:
        if "=" not in item:
            raise UsageError(
                f"--set expects <path>=<value>, got {item!r}",
                argument="--set",
                reason="missing_equals",
            )
        path, _, raw_value = item.partition("=")
        path = path.strip()
        if not _PATH_RE.match(path):
            raise UsageError(
                f"--set path {path!r} is not a valid dotted identifier",
                argument="--set",
                reason="invalid_path",
            )
        head = path.split(".", 1)[0]
        if head in RESERVED_KEYS:
            raise UsageError(
                f"--set cannot target reserved envelope key {head!r}",
                argument="--set",
                reason="reserved_key",
            )
        try:
            value = yaml.safe_load(raw_value)
        except yaml.YAMLError as e:
            raise UsageError(
                f"--set value for {path!r} is not valid YAML: {e}",
                argument="--set",
                reason="invalid_yaml",
            )
        flat[path] = value
    try:
        return _expand_dotted_keys(flat, file=OVERRIDE_CANONICAL_ID)
    except SchemaError as e:
        raise UsageError(
            f"--set produces an intra-override type conflict: {e.message}",
            argument="--set",
            reason="intra_override_conflict",
        )
