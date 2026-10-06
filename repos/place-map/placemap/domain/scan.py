"""Static analysis of one Luau script: requires, remotes fired and handled, DataStore names and keys, instance-name references, string tokens, a short deterministic summary.

This is a lexer plus regular expressions, NOT a Luau parser: it understands comments, strings (including long brackets and backticks), `local x = <path expression>` aliases and
the usual path expressions (`script.Parent.Foo`, `game:GetService("S").A.B`, `S:WaitForChild("A"):WaitForChild("B")`, `S.A["B"]`). Anything it cannot resolve is kept as raw text and flagged,
never guessed. The result depends only on the source text (never on where the script lives), so it is cached by content hash and shared across places.
"""

from __future__ import annotations

import bisect
import re
from collections import Counter

from .secrets import SecretFilter, default_filter
from .util import split_tokens

ANALYSIS_VERSION = 2
SERVICES = {"Workspace", "ReplicatedStorage", "ReplicatedFirst", "ServerScriptService", "ServerStorage", "StarterGui", "StarterPack", "StarterPlayer", "Players", "Lighting",
            "SoundService", "Teams", "CollectionService", "RunService", "Debris", "TweenService", "UserInputService", "DataStoreService", "MarketplaceService", "HttpService",
            "TeleportService", "MessagingService", "ProximityPromptService", "ContextActionService", "PathfindingService", "PhysicsService", "BadgeService", "TextService"}
PROP_NOISE = {"Parent", "Name", "ClassName", "Position", "Size", "CFrame", "Value", "Text", "Enabled", "Visible", "Anchored", "CanCollide", "Transparency", "Character", "LocalPlayer",
              "UserId", "Humanoid", "Health", "Touched", "Triggered", "Connect", "Destroy", "Clone", "X", "Y", "Z", "Magnitude", "Unit", "Color", "Material",
              "OnServerEvent", "OnClientEvent", "OnServerInvoke", "OnClientInvoke", "Event", "FireServer", "FireClient", "FireAllClients"}
IDENT = r"[A-Za-z_][A-Za-z0-9_]*"
_STR = r"""(?:"(?:[^"\\\n]|\\.)*"|'(?:[^'\\\n]|\\.)*')"""
_CALLS = r"GetService|WaitForChild|FindFirstChild|FindFirstChildOfClass|FindFirstChildWhichIsA|FindFirstDescendant"
CHAIN_RE = re.compile(rf"(?<![\w.:])({IDENT})((?:\s*(?:\.\s*{IDENT}|\[\s*{_STR}\s*\]|:\s*(?:{_CALLS})\s*\(\s*{_STR}[^)\n]*\)))*)")
SEG_RE = re.compile(rf"\.\s*({IDENT})|\[\s*({_STR})\s*\]|:\s*({_CALLS})\s*\(\s*({_STR})")
FIRE_RE = re.compile(r"\s*:\s*(FireServer|FireClient|FireAllClients|InvokeServer|InvokeClient|Fire|Invoke)\s*\(")
HANDLE_NAMES = {"OnServerEvent": "event", "OnClientEvent": "event", "OnServerInvoke": "invoke", "OnClientInvoke": "invoke", "Event": "bindable"}
HANDLE_AFTER = re.compile(r"\s*(?::\s*(?:Connect|Once|Wait|ConnectParallel)\s*\(|=(?!=))")
ALIAS_RE = re.compile(rf"\blocal\s+({IDENT})\s*(?::\s*[\w.?<>]+)?\s*=\s*")
REQUIRE_RE = re.compile(r"\brequire\s*\(\s*")
DS_GET_RE = re.compile(rf"\bGetDataStore\s*\(\s*({_STR})|\bGetOrderedDataStore\s*\(\s*({_STR})")
DS_KEY_RE = re.compile(rf"""[:.]\s*(?:Get|Set|Update|Remove|Increment)Async\s*\(\s*(?:({_STR})\s*(\.\.)?|({IDENT}))""")
FUNC_RE = re.compile(rf"\bfunction\s+((?:{IDENT}[.:])*{IDENT})\s*\(")
NUM_RE = re.compile(r"\d+")
MAX_REFS = 400


def _unquote(lit: str) -> str:
    body = lit[1:-1]
    return re.sub(r"\\(.)", r"\1", body)


def lex(src: str) -> tuple[str, list[str], list[str], list[tuple[int, int]]]:
    """(code with comments blanked but strings and line breaks kept, comment texts, string literal contents, string spans in ``code`` coordinates). Long strings and backtick strings are
    kept in the code as plain quoted text."""
    out: list[str] = []
    comments: list[str] = []
    strings: list[str] = []
    spans: list[tuple[int, int]] = []
    length = 0
    i, n = 0, len(src)
    def emit(text: str, is_string: bool = False) -> None:
        nonlocal length
        if is_string:
            spans.append((length, length + len(text)))
        out.append(text)
        length += len(text)

    while i < n:
        c = src[i]
        if c == "-" and src.startswith("--", i):
            m = re.match(r"--\[(=*)\[", src[i:])
            if m:
                close = "]" + m.group(1) + "]"
                j = src.find(close, i + m.end())
                j = n if j < 0 else j + len(close)
                comments.append(src[i + m.end(): max(i + m.end(), j - len(close))])
                emit(re.sub(r"[^\n]", " ", src[i:j]))
                i = j
            else:
                j = src.find("\n", i)
                j = n if j < 0 else j
                comments.append(src[i + 2:j])
                emit(" " * (j - i))
                i = j
        elif c in "\"'":
            j = i + 1
            while j < n and src[j] != c and src[j] != "\n":
                j += 2 if src[j] == "\\" else 1
            j = min(j + 1, n)
            lit = src[i:j]
            emit(lit, True)
            if len(lit) >= 2 and lit[-1] == c:
                strings.append(_unquote(lit))
            i = j
        elif c == "`":
            j = src.find("`", i + 1)
            j = n if j < 0 else j + 1
            strings.append(src[i + 1:j - 1])
            emit('"' + re.sub(r'[\n"]', " ", src[i + 1:j - 1]) + '"', True)
            i = j
        elif c == "[" and re.match(r"\[=*\[", src[i:]):
            m = re.match(r"\[(=*)\[", src[i:])
            close = "]" + m.group(1) + "]"
            j = src.find(close, i + m.end())
            body_end = n if j < 0 else j
            j = n if j < 0 else j + len(close)
            strings.append(src[i + m.end():body_end])
            emit('"' + re.sub(r'[\n"]', " ", src[i + m.end():body_end]) + '"', True)
            i = j
        else:
            emit(c)
            i += 1
    return "".join(out), comments, strings, spans


def _segments(tail: str) -> list[str]:
    segs: list[str] = []
    for m in SEG_RE.finditer(tail):
        if m.group(1):
            segs.append(m.group(1))
        elif m.group(2):
            segs.append(_unquote(m.group(2)))
        else:
            name = _unquote(m.group(4))
            segs.append(f"@{name}" if m.group(3) == "GetService" else name)
    return segs


def _expand(base: str, segs: list[str], aliases: dict[str, list[str]]) -> list[str] | None:
    """Canonical chain: first token 'script', '@Service' or '?var' (unresolved); then names and '..' for Parent. None if the first token cannot be resolved."""
    if base == "script":
        toks = ["script"]
    elif base == "workspace":
        toks = ["@Workspace"]
    elif base == "game":
        toks = ["@game"]
    elif base in aliases:
        toks = list(aliases[base])
    elif base in SERVICES:
        toks = [f"@{base}"]
    else:
        return None
    for s in segs:
        if s == "Parent" and toks[-1] != "@game":
            toks.append("..")
        elif toks == ["@game"] and not s.startswith("@"):
            toks = [f"@{s}"]  # game.ReplicatedStorage
        else:
            toks.append(s)
    return [t for t in toks if t != "@game"] or None


def analyze(source: str, class_name: str = "ModuleScript", flt: SecretFilter | None = None) -> dict:
    flt = flt or default_filter()
    code, comments, strings, spans = lex(source)
    starts = [a for a, _ in spans]

    def in_string(pos: int) -> bool:
        k = bisect.bisect_right(starts, pos) - 1
        return k >= 0 and spans[k][0] <= pos < spans[k][1]

    aliases: dict[str, list[str]] = {}
    for m in ALIAS_RE.finditer(code):
        if in_string(m.start()):
            continue
        cm = CHAIN_RE.match(code, m.end())
        if cm and not code[cm.end():].lstrip().startswith("("):
            exp = _expand(cm.group(1), _segments(cm.group(2)), aliases)
            if exp and (cm.group(2) or cm.group(1) in aliases or cm.group(1) in SERVICES or cm.group(1) in ("script", "workspace", "game")):
                aliases.setdefault(m.group(1), exp)
    requires, fired, handled, name_refs = [], [], [], []
    seen_req: set[str] = set()
    for m in REQUIRE_RE.finditer(code):
        if in_string(m.start()):
            continue
        if NUM_RE.match(code, m.end()):
            nm = NUM_RE.match(code, m.end())
            key = f"asset:{nm.group(0)}"
            if key not in seen_req:
                seen_req.add(key)
                requires.append({"expr": nm.group(0), "chain": None, "asset_id": int(nm.group(0))})
            continue
        cm = CHAIN_RE.match(code, m.end())
        if not cm:
            tail = code[m.end():m.end() + 60].split(")")[0].strip()
            if tail and f"?{tail}" not in seen_req:
                seen_req.add(f"?{tail}")
                requires.append({"expr": tail[:120], "chain": None, "asset_id": None})
            continue
        chain = _expand(cm.group(1), _segments(cm.group(2)), aliases)
        expr = re.sub(r"\s+", " ", code[cm.start():cm.end()])[:120]
        key = "/".join(chain) if chain else f"?{expr}"
        if key not in seen_req:
            seen_req.add(key)
            requires.append({"expr": expr, "chain": chain, "asset_id": None})
    for cm in CHAIN_RE.finditer(code):
        if in_string(cm.start()):
            continue
        base, tail = cm.group(1), cm.group(2)
        segs = _segments(tail)
        for s in segs:
            if not s.startswith("@") and s not in PROP_NOISE and s not in SERVICES and s != "..":
                name_refs.append(s)
        rest = code[cm.end():cm.end() + 40]
        fm = FIRE_RE.match(rest)
        if fm and segs:
            fired.append({"name": segs[-1].lstrip("@"), "via": fm.group(1), "chain": _expand(base, segs, aliases)})
        elif fm and base in aliases:
            fired.append({"name": aliases[base][-1].lstrip("@"), "via": fm.group(1), "chain": list(aliases[base])})
        elif fm:
            fired.append({"name": base, "via": fm.group(1), "chain": None, "name_guess": True})
        if segs and segs[-1] in HANDLE_NAMES and HANDLE_AFTER.match(rest):
            prefix = segs[:-1]
            if prefix:
                handled.append({"name": prefix[-1].lstrip("@"), "via": segs[-1], "chain": _expand(base, prefix, aliases)})
            elif base in aliases:
                handled.append({"name": aliases[base][-1].lstrip("@"), "via": segs[-1], "chain": list(aliases[base])})
            else:
                handled.append({"name": base, "via": segs[-1], "chain": None, "name_guess": True})
        if base in aliases and not segs:
            name_refs.append(aliases[base][-1].lstrip("@"))
        if base not in aliases and base not in SERVICES and base not in ("script", "game", "workspace") and not segs:
            pass
    services = sorted({_unquote(m.group(1)) for m in re.finditer(rf"GetService\s*\(\s*({_STR})", code) if not in_string(m.start())})
    datastores = sorted({_unquote(m.group(1) or m.group(2)) for m in DS_GET_RE.finditer(code) if not in_string(m.start())})
    ds_keys: set[str] = set()
    for dm in DS_KEY_RE.finditer(code):
        if in_string(dm.start()):
            continue
        lit, concat, ident = dm.group(1), dm.group(2), dm.group(3)
        if lit:
            k = _unquote(lit) + ("*" if concat else "")
            if not flt.value_is_secret(k):
                ds_keys.add(k)
        elif ident:
            ds_keys.add(f"<{ident}>")
    safe_strings, secret_n = [], 0
    for s in strings:
        if flt.value_is_secret(s):
            secret_n += 1
        else:
            safe_strings.append(s)
    toks = Counter(t for s in safe_strings for t in split_tokens(s) if len(t) > 1 and not t.isdigit())
    short = [s for s in safe_strings if 0 < len(s) <= 40 and "\n" not in s and len(s.split()) <= 5 and s not in SERVICES]
    refs = sorted(set(name_refs) | set(short))[:MAX_REFS]
    funcs = []
    for f in FUNC_RE.findall(code):
        if f not in funcs:
            funcs.append(f)
    first = ""
    for c in comments:
        line = next((ln.strip(" -=*\t") for ln in c.splitlines() if ln.strip(" -=*\t")), "")
        if line and not line.lower().startswith(("!strict", "!nonstrict", "!nocheck")):
            first = line[:140]
            break
    res = {
        "analysis_version": ANALYSIS_VERSION,
        "lines": source.count("\n") + 1 if source else 0,
        "requires": requires,
        "remotes_fired": _uniq(fired),
        "remotes_handled": _uniq(handled),
        "datastores": datastores,
        "datastore_keys": sorted(ds_keys)[:40],
        "services": services,
        "functions": funcs[:12],
        "name_refs": refs,
        "string_tokens": sorted(toks)[:MAX_REFS],
        "first_comment": first,
        "secret_like_strings_skipped": secret_n,
    }
    res["summary"] = summarize(res, class_name)
    return res


def _uniq(items: list[dict]) -> list[dict]:
    seen, out = set(), []
    for it in items:
        key = (it["name"], it["via"])
        if key not in seen:
            seen.add(key)
            out.append(it)
    return out


def kind_of(class_name: str, run_context: str | None = None) -> str:
    if class_name == "ModuleScript":
        return "module"
    if class_name == "LocalScript":
        return "client"
    if class_name == "Script":
        return "client" if run_context == "Client" else "server"
    return "other"


def summarize(a: dict, class_name: str, max_chars: int = 320) -> str:
    """A short deterministic summary built from the analysis (no model call). The first comment, what it requires, fires, handles and stores."""
    parts: list[str] = []
    if a["first_comment"]:
        parts.append(a["first_comment"].rstrip(".") + ".")
    parts.append(f"{class_name}, {a['lines']} lines.")
    if a["requires"]:
        names = [(r["chain"][-1] if r["chain"] else r["expr"]) for r in a["requires"]][:5]
        parts.append(f"Requires {', '.join(str(n) for n in names)}" + (f" (+{len(a['requires']) - 5} more)" if len(a["requires"]) > 5 else "") + ".")
    if a["remotes_fired"]:
        parts.append("Fires " + ", ".join(sorted({r["name"] for r in a["remotes_fired"]})[:5]) + ".")
    if a["remotes_handled"]:
        parts.append("Handles " + ", ".join(sorted({r["name"] for r in a["remotes_handled"]})[:5]) + ".")
    if a["datastores"] or a["datastore_keys"]:
        parts.append("DataStore " + ", ".join((a["datastores"] + a["datastore_keys"])[:4]) + ".")
    if a["functions"]:
        parts.append("Defines " + ", ".join(a["functions"][:5]) + ".")
    text = " ".join(parts)
    return text if len(text) <= max_chars else text[: max_chars - 3].rstrip() + "..."
