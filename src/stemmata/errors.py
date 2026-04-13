from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


EXIT_OK = 0
EXIT_GENERIC = 1
EXIT_USAGE = 2
EXIT_SCHEMA = 10
EXIT_REFERENCE = 11
EXIT_CYCLE = 12
EXIT_UNRESOLVABLE = 14
EXIT_MERGE = 15
EXIT_NETWORK = 20
EXIT_CACHE = 21
EXIT_OFFLINE = 22


CATEGORIES = {
    EXIT_GENERIC: "internal_error",
    EXIT_USAGE: "usage_error",
    EXIT_SCHEMA: "schema_validation",
    EXIT_REFERENCE: "reference_error",
    EXIT_CYCLE: "cycle_detected",
    EXIT_UNRESOLVABLE: "unresolvable_placeholder",
    EXIT_MERGE: "merge_failure",
    EXIT_NETWORK: "network_error",
    EXIT_CACHE: "cache_error",
    EXIT_OFFLINE: "offline_violation",
}


@dataclass
class PromptCliError(Exception):
    code: int
    message: str
    location: Any = None
    details: dict[str, Any] = field(default_factory=dict)

    def __str__(self) -> str:
        return self.message


class UsageError(PromptCliError):
    def __init__(self, message: str, argument: str | None = None, reason: str | None = None):
        super().__init__(
            EXIT_USAGE,
            message,
            None,
            {"argument": argument or "", "reason": reason or message},
        )


class SchemaError(PromptCliError):
    def __init__(self, message: str, *, file: str | None, line: int | None = None, column: int | None = None, field_name: str = "", reason: str | None = None):
        super().__init__(
            EXIT_SCHEMA,
            message,
            {"file": file, "line": line, "column": column} if file is not None else None,
            {"field": field_name, "reason": reason or message},
        )


class ReferenceError_(PromptCliError):
    def __init__(self, message: str, *, file: str | None, line: int | None, column: int | None, reference: str, searched_in: str):
        super().__init__(
            EXIT_REFERENCE,
            message,
            {"file": file, "line": line, "column": column},
            {"reference": reference, "searched_in": searched_in},
        )


class CycleError(PromptCliError):
    def __init__(self, nodes: list[dict[str, Any]], cycle_ids: list[str]):
        super().__init__(
            EXIT_CYCLE,
            f"Cycle detected: {' -> '.join(cycle_ids) if cycle_ids else ''}",
            nodes,
            {"cycle": cycle_ids},
        )


class UnresolvableError(PromptCliError):
    def __init__(self, placeholder: str, *, file: str | None, line: int | None, column: int | None, reason: str, ancestors_searched: list[str], providing_ancestor: str | None):
        super().__init__(
            EXIT_UNRESOLVABLE,
            f"Placeholder ${{{placeholder}}} in {file}:{line} could not be resolved ({reason})",
            {"file": file, "line": line, "column": column},
            {
                "reason": reason,
                "placeholder": placeholder,
                "ancestors_searched": ancestors_searched,
                "providing_ancestor": providing_ancestor,
            },
        )


class MergeError(PromptCliError):
    def __init__(self, path: str, conflict: str, types: list[str], nodes: list[dict[str, Any]]):
        super().__init__(
            EXIT_MERGE,
            f"Merge failure at '{path}': {conflict} ({', '.join(types)})",
            nodes,
            {"path": path, "conflict": conflict, "types": types},
        )


class NetworkError(PromptCliError):
    def __init__(self, url: str, http_status: int | None, reason: str):
        super().__init__(
            EXIT_NETWORK,
            f"Network error fetching {url}: {reason}",
            None,
            {"url": url, "http_status": http_status, "reason": reason},
        )


class CacheError(PromptCliError):
    def __init__(self, cache_path: str, reason: str):
        super().__init__(
            EXIT_CACHE,
            f"Cache error at {cache_path}: {reason}",
            None,
            {"cache_path": cache_path, "reason": reason},
        )


class OfflineError(PromptCliError):
    def __init__(self, url: str):
        super().__init__(
            EXIT_OFFLINE,
            f"Offline mode: refusing to fetch {url}",
            None,
            {"url": url},
        )


class GenericError(PromptCliError):
    def __init__(self, message: str, exception: str = "", traceback: str = ""):
        super().__init__(
            EXIT_GENERIC,
            message,
            None,
            {"exception": exception, "traceback": traceback},
        )
