"""Config: YAML loading, schema validation, locked values and layered resolution.

Limits and thresholds live in YAML, not in code. A node that comes from Roblox's documentation carries
``verify_against_current_docs: true`` so tools can list what still needs checking (:meth:`Config.unverified`).

Locked values. A value is locked either by name in a top-level list::

    locked: [limits.max_triangles, rules.SEC001]

or by marking a mapping: ``{value: 21845, locked: true}`` / ``max_name_length: {locked: true, value: 50}`` locks that path and
everything below it. Locks come from the base layer and later layers can neither lift them nor change the value: :func:`layer`
raises :class:`LockedValueError` (or, with ``strict=False``, records the attempt in ``rejected`` and keeps the locked value).
The learning layer applies the same rule to tunable parameters (:mod:`guide_core.params`).

The style and rubric loaders of the vendored kit are re-exported here so a repository needs one import for configuration.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator

import yaml

from .rubric import load_rubric, score_rubric, validate_rubric  # noqa: F401
from .style import check_ranges, load_style, style_brief, validate_style  # noqa: F401


class ConfigError(ValueError):
    pass


class LockedValueError(ConfigError):
    pass


def load_yaml(path: str | Path, *, must_exist: bool = True) -> dict:
    p = Path(path)
    if not p.exists():
        if must_exist:
            raise ConfigError(f"{p} does not exist")
        return {}
    try:
        data = yaml.safe_load(p.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ConfigError(f"{p}: invalid YAML: {exc}") from exc
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise ConfigError(f"{p}: expected a mapping at the top level, got {type(data).__name__}")
    return data


def validate(data: Any, schema: dict) -> list[str]:
    """Problems found validating ``data`` against a JSON Schema (empty list = valid). Messages name the path."""
    from jsonschema import Draft202012Validator

    out = []
    for err in sorted(Draft202012Validator(schema).iter_errors(data), key=lambda e: list(map(str, e.absolute_path))):
        where = ".".join(str(p) for p in err.absolute_path) or "<root>"
        out.append(f"{where}: {err.message}")
    return out


def dotted_items(data: Any, prefix: str = "") -> Iterator[tuple[str, Any]]:
    """Leaf values of nested mappings as ``(dotted.path, value)``. Lists are leaves."""
    if isinstance(data, dict) and data:
        for k, v in data.items():
            yield from dotted_items(v, f"{prefix}.{k}" if prefix else str(k))
    else:
        yield prefix, data


def dig(data: Any, dotted: str, default: Any = None) -> Any:
    cur = data
    for part in dotted.split("."):
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        else:
            return default
    return cur


def _set(data: dict, dotted: str, value: Any) -> None:
    parts = dotted.split(".")
    cur = data
    for p in parts[:-1]:
        cur = cur.setdefault(p, {})
    cur[parts[-1]] = value


def _collect_locks(data: dict) -> set[str]:
    locks = {str(x) for x in (data.get("locked") or []) if isinstance(data.get("locked"), list)}

    def walk(node: Any, prefix: str) -> None:
        if isinstance(node, dict):
            if node.get("locked") is True and prefix:
                locks.add(prefix)
            for k, v in node.items():
                if k != "locked":
                    walk(v, f"{prefix}.{k}" if prefix else str(k))

    walk(data, "")
    return locks


def _is_locked(path: str, locks: set[str]) -> str | None:
    for lock in locks:
        if path == lock or path.startswith(lock + "."):
            return lock
    return None


@dataclass
class Config:
    data: dict
    source: str = ""
    locks: set[str] = field(default_factory=set)

    def get(self, dotted: str, default: Any = None) -> Any:
        return dig(self.data, dotted, default)

    def is_locked(self, dotted: str) -> bool:
        return _is_locked(dotted, self.locks) is not None

    def unverified(self) -> list[str]:
        """Dotted paths of mappings flagged ``verify_against_current_docs: true``."""
        out: list[str] = []

        def walk(node: Any, prefix: str) -> None:
            if isinstance(node, dict):
                if node.get("verify_against_current_docs") is True and prefix:
                    out.append(prefix)
                for k, v in node.items():
                    walk(v, f"{prefix}.{k}" if prefix else str(k))

        walk(self.data, "")
        return sorted(out)


def load_config(path: str | Path, schema: dict | None = None) -> Config:
    """Load a YAML config, validate it against ``schema`` if given (all problems reported at once) and collect its locks."""
    data = load_yaml(path)
    if schema is not None:
        problems = validate(data, schema)
        if problems:
            raise ConfigError(f"{path}: " + "; ".join(problems))
    return Config(data, str(path), _collect_locks(data))


@dataclass
class Resolved:
    """The result of layering configs: final ``values`` plus, for every leaf path, which layer set it."""

    values: dict
    sources: dict[str, str]
    locks: set[str]
    rejected: list[dict] = field(default_factory=list)

    def get(self, dotted: str, default: Any = None) -> Any:
        return dig(self.values, dotted, default)

    def source_of(self, dotted: str) -> str | None:
        return self.sources.get(dotted)


def layer(base: Config | dict, *overrides: tuple[str, dict], base_name: str = "default", strict: bool = True) -> Resolved:
    """Apply override layers (``(name, mapping)``, in order, last wins) on a base. Locked paths of the base cannot change."""
    base_data = base.data if isinstance(base, Config) else base
    base_name = base.source or base_name if isinstance(base, Config) else base_name
    locks = set(base.locks) if isinstance(base, Config) else _collect_locks(base_data)
    values = copy.deepcopy(base_data)
    sources = {p: base_name for p, _ in dotted_items(values)}
    rejected: list[dict] = []
    for name, over in overrides:
        for path, value in dotted_items(over):
            if path in ("locked",) or path.endswith(".locked"):
                continue  # a layer cannot lift or add locks by editing the lock markers
            lock = _is_locked(path, locks)
            if lock is not None:
                if dig(values, path) == value:
                    continue
                msg = f"'{path}' is locked (by '{lock}') and cannot be changed by layer '{name}'"
                if strict:
                    raise LockedValueError(msg)
                rejected.append({"path": path, "layer": name, "attempted": value, "kept": dig(values, path), "reason": msg})
                continue
            _set(values, path, copy.deepcopy(value))
            sources[path] = name
    return Resolved(values, sources, locks, rejected)
