"""The ONE Luau safety lint, plus helpers that keep generated Luau injection-safe.

Every guide repository that emits Luau for the hub's ``execute_luau`` (or Studio's own MCP) passes it through :func:`lint`
before returning it. The lint is a deny-list over the text: it is a tripwire for generated code, not a Luau parser and not a
sandbox. What it blocks:

* publishing and saving (``Publish``, ``SavePlace``, ``SaveInstance`` ...), marketplace/asset/teleport/datastore/messaging services;
* network calls (``RequestAsync``, ``GetAsync``, ``PostAsync``, ``HttpService:Get``/``Post``, ``HttpGet``, web sockets) - ``HttpService``
  may only be used for ``JSONEncode``;
* code loading (``loadstring``, ``require(``, ``getfenv``/``setfenv``, ``LoadAsset``, ``InsertService``);
* destruction (``:Destroy(``, ``ClearAllChildren``, ``Parent = nil``) unless ``allow_destroy`` names a guard the caller verifies.

Embedding helpers (:func:`validate_path`, :func:`validate_name`, :func:`quote_string`, :func:`check_long_bracket_safe`,
:func:`place_guard`) refuse anything that could break out of a generated string literal or path.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# NOTE: all of these are used with ``fullmatch``. The first repositories used ``match`` with a trailing ``$``, which also accepts a
# trailing newline ("Name\n"): a newline inside a generated path or name is exactly the kind of injection this module exists to stop.
ID_RE = re.compile(r"[A-Za-z][A-Za-z0-9_]{0,39}")
SEGMENT_RE = re.compile(r"[A-Za-z][A-Za-z0-9_]{0,39}")
VALUE_KEY_RE = re.compile(r"[A-Za-z0-9_.]+")
PLACE_NAME_RE = re.compile(r"[A-Za-z0-9 _.()\-]{1,60}")
LABEL_RE = re.compile(r"[A-Za-z0-9_/]{1,90}")

#: Tokens that must never appear in generated Luau (superset of the lists the first three repositories used).
FORBIDDEN: tuple[str, ...] = (
    "Publish", "SavePlace", "SaveToRoblox", "SaveInstance", "SavePlaceAsync",
    "MarketplaceService", "AssetService", "TeleportService", "DataStoreService", "MessagingService", "InsertService", "LoadAsset",
    "RequestAsync", "GetAsync", "PostAsync", "HttpService:Get", "HttpService:Post", "HttpGet", "WebSocket", "CreateWebStreamClient",
    "loadstring", "require(", "getfenv", "setfenv",
    ":Destroy(", "ClearAllChildren", "game:Destroy", "workspace:Destroy", "Parent = nil", "Parent=nil",
)
#: Destruction tokens that a caller may allow when it guards them itself (``lint(code, allow=...)``).
GUARDABLE = (":Destroy(", "ClearAllChildren", "Parent = nil", "Parent=nil")


@dataclass(frozen=True)
class LintIssue:
    token: str
    message: str


def lint(code: str, *, allow: tuple[str, ...] = (), extra_forbidden: tuple[str, ...] = ()) -> list[str]:
    """Problems found in ``code`` (empty list = nothing blocked). Messages are stable strings.

    ``allow`` lifts specific tokens from :data:`GUARDABLE` only (anything else cannot be allowed); ``extra_forbidden`` adds tokens.
    """
    bad = [a for a in allow if a not in GUARDABLE]
    if bad:
        raise ValueError(f"only {GUARDABLE} can be allowed, not {bad}")
    issues = [f"forbidden token '{t}'" for t in (*FORBIDDEN, *extra_forbidden) if t in code and t not in allow]
    if "HttpService" in code:
        rest = code.replace('game:GetService("HttpService"):JSONEncode', "")
        if "HttpService" in rest.replace("-- ", ""):
            issues.append("HttpService may only be used for JSONEncode")
    return issues


def check(code: str, **kw) -> list[LintIssue]:
    return [LintIssue(m.split("'")[1] if "'" in m else "HttpService", m) for m in lint(code, **kw)]


def assert_safe(code: str, **kw) -> str:
    """Return ``code`` if the lint passes, else raise ``ValueError`` (what generators call before returning a script)."""
    problems = lint(code, **kw)
    if problems:
        raise ValueError("generated Luau failed the safety lint: " + "; ".join(problems))
    return code


# --- injection-safe embedding ------------------------------------------------------------------------------------
def validate_path(path: str, what: str = "path") -> str:
    """A dotted instance path of plain names (``ReplicatedStorage.Config``). Refuses '..', slashes, quotes, brackets, spaces."""
    parts = (path or "").split(".")
    if not path or not all(SEGMENT_RE.fullmatch(p) for p in parts):
        raise ValueError(f"{what} '{path}' must be a dotted path of plain names such as ReplicatedStorage.Config (letters, digits, underscore)")
    return path


def validate_name(name: str, what: str = "name") -> str:
    if not ID_RE.fullmatch(name or ""):
        raise ValueError(f"{what} '{name}' must match {ID_RE.pattern}")
    return name


def quote_string(value: str) -> str:
    """A double-quoted Luau string literal for ``value``; control characters and quotes are escaped, newlines refused."""
    if not isinstance(value, str):
        raise ValueError("only strings can be quoted")
    if any(ord(c) < 32 for c in value):
        raise ValueError("control characters (including newlines) cannot be embedded in a generated string")
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def check_long_bracket_safe(source: str, level: int = 2) -> str:
    """``source`` may be embedded in ``[==[ ... ]==]`` only if it cannot terminate the literal."""
    if "]" + "=" * level + "]" in source:
        raise ValueError("source contains a Lua long-bracket terminator; refusing to embed it")
    return source


def place_guard(place_number: int, place_name: str, label: str) -> list[str]:
    """Luau lines that stop the script unless the open place is the named one (PlaceId when published, otherwise the place name)."""
    if not PLACE_NAME_RE.fullmatch(place_name or ""):
        raise ValueError(f"place name {place_name!r} cannot be embedded safely")
    if not LABEL_RE.fullmatch(label or ""):
        raise ValueError(f"place label {label!r} cannot be embedded safely")
    if not isinstance(place_number, int) or isinstance(place_number, bool) or place_number < 0:
        raise ValueError("place number must be a non-negative integer")
    return [f'local EXPECTED_PLACE_ID = {place_number}', f'local EXPECTED_NAME = "{place_name}"', f'local EXPECTED_LABEL = "{label}"',
            "if EXPECTED_PLACE_ID ~= 0 then", "\tif game.PlaceId ~= EXPECTED_PLACE_ID then",
            "\t\terror(\"wrong place: this script is for \" .. EXPECTED_LABEL .. \" (PlaceId \" .. EXPECTED_PLACE_ID .. \") but PlaceId \" .. tostring(game.PlaceId) .. \" is open\")",
            "\tend", "elseif game.Name ~= EXPECTED_NAME then",
            "\terror(\"wrong place: this script is for \" .. EXPECTED_LABEL .. \" (\" .. EXPECTED_NAME .. \") but \" .. tostring(game.Name) .. \" is open\")", "end", ""]
