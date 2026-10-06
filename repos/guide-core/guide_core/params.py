"""Tunable parameters (layer 3 of the learning layer): named, ranged, versioned, bounded, reversible.

Every threshold, weight, band edge and confidence a repository wants to be able to improve is registered as a
:class:`ParamSpec` with a default, a range and a maximum step. A :class:`ParamStore` keeps, per parameter and per scope
(global, a project, a place), an immutable version history and a pointer to the current version.

* **Resolution order** (:meth:`ParamStore.resolve`): explicit user label, then the project/place value, then the promoted global
  value, then the shipped default. The result always says which source won and lists the others it overrode.
* **Bounded steps**: an update may move a value by at most ``max_step`` (one position for choice parameters) and must stay inside
  ``[min, max]``. Bigger changes are made as several approved small steps, each with its own version.
* **Approval and provenance**: every update needs ``approved_by`` (a person; ``auto``/``system``/``model`` are refused), a reason
  and optionally the run ids that are the evidence. Nothing is applied automatically; this module has no entry point that does.
* **Rollback restores exactly**: :meth:`ParamStore.rollback` moves the pointer back to the previous version (the version that
  was current when the newer one was made, or "no override" if there was none). Versions are never edited or deleted, so after a
  rollback the value, the version number and the history are those of the earlier state.
* **Locked**: a spec with ``locked=True`` never changes. Updates are refused and an explicit user label is ignored (and
  reported as ignored); the shipped default always wins.

Storage is one JSON file (written atomically), schema version in the file.
"""

from __future__ import annotations

import datetime as _dt
import json
import os
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from . import SCHEMA_VERSION
from .scope import GLOBAL, Scope

EPS = 1e-9
AUTO_APPROVERS = {"", "auto", "automatic", "system", "model", "claude", "llm", "ai", "bot", "none"}


class ParamError(ValueError):
    pass


class LockedParameter(ParamError):
    pass


class StepTooLarge(ParamError):
    pass


class OutOfRange(ParamError):
    pass


@dataclass(frozen=True)
class ParamSpec:
    name: str
    default: Any
    min: float | None = None
    max: float | None = None
    max_step: float | None = None
    choices: tuple = ()
    locked: bool = False
    description: str = ""
    verify_against_current_docs: bool = False
    group: str = ""

    def __post_init__(self):
        if not self.name or any(c.isspace() for c in self.name):
            raise ParamError(f"parameter name {self.name!r} must be a non-empty dotted name without spaces")
        if self.choices:
            if self.default not in self.choices:
                raise ParamError(f"{self.name}: default {self.default!r} is not one of {self.choices}")
        else:
            if isinstance(self.default, bool) or not isinstance(self.default, (int, float)):
                raise ParamError(f"{self.name}: default must be a number (or give choices)")
            if self.min is None or self.max is None:
                raise ParamError(f"{self.name}: a numeric parameter needs min and max (its range)")
            if not self.min <= self.default <= self.max:
                raise ParamError(f"{self.name}: default {self.default} is outside [{self.min}, {self.max}]")
            if self.max_step is not None and self.max_step <= 0:
                raise ParamError(f"{self.name}: max_step must be positive")

    @property
    def kind(self) -> str:
        return "choice" if self.choices else ("int" if isinstance(self.default, int) else "number")

    @property
    def step(self) -> float:
        """Largest allowed single update (absolute; 1 position for choices)."""
        if self.choices:
            return 1
        if self.max_step is not None:
            return self.max_step
        rng = float(self.max) - float(self.min)  # type: ignore[arg-type]
        return max(1.0, round(rng * 0.1)) if self.kind == "int" else rng * 0.1

    def check_value(self, value: Any) -> Any:
        if self.choices:
            if value not in self.choices:
                raise OutOfRange(f"{self.name}: {value!r} is not one of {self.choices}")
            return value
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise OutOfRange(f"{self.name}: {value!r} is not a number")
        if self.kind == "int":
            if float(value) != int(value):
                raise OutOfRange(f"{self.name}: {value!r} must be a whole number")
            value = int(value)
        if not (self.min - EPS <= value <= self.max + EPS):  # type: ignore[operator]
            raise OutOfRange(f"{self.name}: {value} is outside the range [{self.min}, {self.max}]")
        return value

    def distance(self, a: Any, b: Any) -> float:
        if self.choices:
            return abs(self.choices.index(a) - self.choices.index(b))
        return abs(float(a) - float(b))

    def to_dict(self) -> dict:
        d = {"name": self.name, "default": self.default, "kind": self.kind, "locked": self.locked, "description": self.description}
        if self.choices:
            d["choices"] = list(self.choices)
        else:
            d.update(min=self.min, max=self.max, max_step=self.step)
        if self.verify_against_current_docs:
            d["verify_against_current_docs"] = True
        return d


@dataclass
class Resolution:
    """The value a parameter has in a scope, which source supplied it, and what it overrode (in order of precedence)."""

    name: str
    value: Any
    source: str  # user_label | project | global | default
    version: int | None
    scope_key: str | None
    locked: bool = False
    overridden: list[dict] = field(default_factory=list)
    ignored: list[dict] = field(default_factory=list)

    def explain(self) -> str:
        won = f"{self.name} = {self.value!r} from {self.source}" + (f" v{self.version} ({self.scope_key})" if self.version is not None else "")
        rest = "; ".join(f"{o['source']}={o['value']!r}" for o in self.overridden)
        return won + (f" (over: {rest})" if rest else "") + (" [locked]" if self.locked else "")


def _now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")


def _check_approval(approved_by: str | None) -> str:
    who = (approved_by or "").strip()
    if who.lower() in AUTO_APPROVERS:
        raise ParamError("a change needs approved_by: the name of the person who approved it (automatic approval is not allowed)")
    return who


class ParamStore:
    """Named parameters and their per-scope version histories, in one JSON file."""

    def __init__(self, path: str | Path, specs: Iterable[ParamSpec] = ()):
        self.path = Path(path)
        self.specs: dict[str, ParamSpec] = {}
        for s in specs:
            self.register(s)

    # registration -----------------------------------------------------------------------------------------------
    def register(self, spec: ParamSpec) -> ParamSpec:
        old = self.specs.get(spec.name)
        if old is not None and old != spec:
            raise ParamError(f"parameter '{spec.name}' is already registered with different settings")
        self.specs[spec.name] = spec
        return spec

    def spec(self, name: str) -> ParamSpec:
        if name not in self.specs:
            raise ParamError(f"unknown parameter '{name}'. Registered: {sorted(self.specs)[:20]}")
        return self.specs[name]

    # state ---------------------------------------------------------------------------------------------------------
    def _load(self) -> dict:
        if not self.path.exists():
            return {"schema": SCHEMA_VERSION, "params": {}, "events": []}
        data = json.loads(self.path.read_text(encoding="utf-8"))
        if data.get("schema") != SCHEMA_VERSION:
            raise ParamError(f"{self.path} has schema {data.get('schema')}, this version of guide-core reads {SCHEMA_VERSION}; see CHANGELOG.md for the migration")
        return data

    def _save(self, state: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=self.path.parent, prefix=".params-", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(state, fh, indent=1, sort_keys=True)
            os.replace(tmp, self.path)
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)

    @staticmethod
    def _entry(state: dict, name: str, key: str) -> dict:
        return state["params"].setdefault(name, {}).setdefault(key, {"current": None, "versions": []})

    @staticmethod
    def _current(entry: dict | None) -> dict | None:
        if not entry or entry["current"] is None:
            return None
        return next(v for v in entry["versions"] if v["version"] == entry["current"])

    # resolution ----------------------------------------------------------------------------------------------------
    def _keys_for(self, scope: Scope | None) -> list[tuple[str, str]]:
        """(source label, scope key) in precedence order: place, project, global."""
        if scope is None or scope.is_global:
            return [("global", GLOBAL)]
        keys = []
        if scope.place_id:
            keys.append(("project", f"{scope.project_id}/{scope.place_id}"))
        keys.append(("project", scope.project_id))
        keys.append(("global", GLOBAL))
        return keys

    def resolve(self, name: str, scope: Scope | None = None, explicit: Any = None, *, _state: dict | None = None) -> Resolution:
        spec = self.spec(name)
        if spec.locked:
            ignored = [] if explicit is None else [{"source": "user_label", "value": explicit, "reason": "locked"}]
            return Resolution(name, spec.default, "default", None, None, True, [], ignored)
        state = _state if _state is not None else self._load()
        cands: list[dict] = []
        if explicit is not None:
            cands.append({"source": "user_label", "value": spec.check_value(explicit), "version": None, "scope_key": None})
        for src, key in self._keys_for(scope):
            cur = self._current(state["params"].get(name, {}).get(key))
            if cur is not None:
                cands.append({"source": src, "value": cur["value"], "version": cur["version"], "scope_key": key})
        cands.append({"source": "default", "value": spec.default, "version": None, "scope_key": None})
        win, rest = cands[0], cands[1:]
        return Resolution(name, win["value"], win["source"], win["version"], win["scope_key"], False,
                          [{"source": c["source"], "value": c["value"], "version": c["version"]} for c in rest])

    def value(self, name: str, scope: Scope | None = None, explicit: Any = None) -> Any:
        return self.resolve(name, scope, explicit).value

    def snapshot(self, scope: Scope | None = None) -> dict[str, dict]:
        """``{name: {value, source, version, locked, default}}`` for every registered parameter."""
        out = {}
        state = self._load()  # read the file once for all parameters
        for n in sorted(self.specs):
            r = self.resolve(n, scope, _state=state)
            out[n] = {"value": r.value, "source": r.source, "version": r.version, "locked": r.locked, "default": self.specs[n].default}
        return out

    def view(self, scope: Scope | None = None, overrides: dict[str, Any] | None = None) -> "ParamView":
        """A read-only view that answers ``value(name)`` as if ``overrides`` were applied (nothing is stored). Used by the gate."""
        return ParamView(self, scope, overrides or {})

    # changes -------------------------------------------------------------------------------------------------------
    def update(self, name: str, value: Any, scope: Scope, *, approved_by: str, reason: str, evidence: Iterable[str] = ()) -> dict:
        """Set ``name`` for ``scope`` to ``value`` as a new version. Refused if locked, out of range, a step larger than
        ``max_step`` from the value currently in force there, or not approved by a person."""
        spec = self.spec(name)
        who = _check_approval(approved_by)
        if not reason or not reason.strip():
            raise ParamError("a change needs a reason")
        if spec.locked:
            raise LockedParameter(f"'{name}' is locked and cannot be changed")
        if scope is None:
            raise ParamError("update needs a scope (a project, a place, or Scope.make_global())")
        value = spec.check_value(value)
        base = self.resolve(name, scope).value
        if spec.distance(value, base) > spec.step + EPS:
            raise StepTooLarge(f"{name}: moving {base!r} -> {value!r} is larger than the allowed step {spec.step}; make it in smaller approved steps")
        if spec.distance(value, base) <= EPS:
            raise ParamError(f"{name}: {value!r} is already the value in force for {scope.label}")
        state = self._load()
        entry = self._entry(state, name, scope.key)
        ver = {"version": max((v["version"] for v in entry["versions"]), default=0) + 1, "value": value, "at": _now(), "approved_by": who,
               "reason": reason.strip(), "evidence": sorted(set(evidence)), "parent": entry["current"], "previous_value": base}
        entry["versions"].append(ver)
        entry["current"] = ver["version"]
        state["events"].append({"at": ver["at"], "event": "update", "param": name, "scope": scope.key, "version": ver["version"], "approved_by": who})
        self._save(state)
        return {**ver, "param": name, "scope": scope.key}

    def nudge(self, name: str, scope: Scope, direction: int | float, *, approved_by: str, reason: str, evidence: Iterable[str] = ()) -> dict:
        """Move one bounded step in ``direction`` (sign only; one position for choices), clamped to the range."""
        spec = self.spec(name)
        base = self.resolve(name, scope).value
        if spec.choices:
            i = spec.choices.index(base) + (1 if direction > 0 else -1)
            if not 0 <= i < len(spec.choices):
                raise OutOfRange(f"{name}: already at the end of {spec.choices}")
            target = spec.choices[i]
        else:
            raw = float(base) + (spec.step if direction > 0 else -spec.step)
            target = min(max(raw, float(spec.min)), float(spec.max))  # type: ignore[arg-type]
            target = int(round(target)) if spec.kind == "int" else round(target, 10)
            if abs(target - float(base)) <= EPS:
                raise OutOfRange(f"{name}: already at the limit of its range")
        return self.update(name, target, scope, approved_by=approved_by, reason=reason, evidence=evidence)

    def history(self, name: str, scope: Scope) -> list[dict]:
        """All versions ever made for this parameter in this exact scope (oldest first), each with ``current``."""
        self.spec(name)
        entry = self._load()["params"].get(name, {}).get(scope.key)
        if not entry:
            return []
        return [{**v, "current": v["version"] == entry["current"]} for v in entry["versions"]]

    def rollback(self, name: str, scope: Scope, *, approved_by: str, reason: str, to_version: int | None = None) -> dict:
        """Point the scope back at the previous version (or ``to_version``); ``None`` means "no override here"."""
        who = _check_approval(approved_by)
        if not reason or not reason.strip():
            raise ParamError("a rollback needs a reason")
        spec = self.spec(name)
        if spec.locked:
            raise LockedParameter(f"'{name}' is locked")
        state = self._load()
        entry = state["params"].get(name, {}).get(scope.key)
        cur = self._current(entry)
        if cur is None:
            raise ParamError(f"{name}: nothing to roll back for {scope.label} (no override is in force)")
        if to_version is None:
            target = cur["parent"]
        else:
            if not any(v["version"] == to_version for v in entry["versions"]):
                raise ParamError(f"{name}: no version {to_version} for {scope.label}")
            target = to_version
        entry["current"] = target
        state["events"].append({"at": _now(), "event": "rollback", "param": name, "scope": scope.key, "from_version": cur["version"],
                                "to_version": target, "approved_by": who, "reason": reason.strip()})
        self._save(state)
        return {"param": name, "scope": scope.key, "from_version": cur["version"], "to_version": target, "value_now": self.resolve(name, scope).value}

    def events(self) -> list[dict]:
        return self._load()["events"]

    def knowledge_version(self) -> int:
        """Number of recorded changes: grows with every update or rollback (used to stamp exported skills)."""
        return len(self._load()["events"])


class ParamView:
    """Read-only access to parameter values, optionally with hypothetical overrides (never stored)."""

    def __init__(self, store: ParamStore, scope: Scope | None, overrides: dict[str, Any]):
        self.store, self.scope, self.overrides = store, scope, dict(overrides)
        for n, v in self.overrides.items():
            store.spec(n).check_value(v)

    def value(self, name: str) -> Any:
        if name in self.overrides and not self.store.spec(name).locked:
            return self.overrides[name]
        return self.store.value(name, self.scope)

    __call__ = value
