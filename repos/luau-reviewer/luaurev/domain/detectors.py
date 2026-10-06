"""Own pattern checks. Each detector looks at an :class:`Analysis` and returns :class:`Hit` objects; the rule YAML supplies everything else.

These are heuristics over a lightweight tokenizer, not a Luau parser. They are written to stay quiet inside strings and comments (the tokenizer
removes them) and to prefer a missed bug over a noisy false alarm where the two conflict. Known blind spots are listed in the README.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Callable

from .structure import Analysis, Call, Func, PROTECTED_RE

FRAME_EVENTS = frozenset(["Heartbeat", "RenderStepped", "Stepped", "PreRender", "PreSimulation", "PostSimulation", "PreAnimation"])
REPEAT_EVENTS = frozenset(["PlayerAdded", "CharacterAdded", "CharacterAppearanceLoaded", "Touched", "TouchEnded", "Changed", "ChildAdded", "DescendantAdded",
                           "OnServerEvent", "OnClientEvent", "Activated", "MouseButton1Click", "MouseClick", "InputBegan", "Triggered", "Died", "Equipped",
                           "Heartbeat", "RenderStepped", "Stepped", "PreRender", "PreSimulation", "PostSimulation", "PreAnimation"])
LONG_LIVED_RE = re.compile(r"^(RunService|UserInputService|ContextActionService|GuiService|Players|workspace|Workspace|game|ReplicatedStorage|ServerStorage|"
                           r"Lighting|StarterGui|HttpService|MarketplaceService|TweenService|SoundService|Teams|CollectionService|MessagingService|"
                           r"ProximityPromptService|BadgeService|TextChatService|PhysicsService)\b")
SENSITIVE_SUBSTR = ("price", "cost", "damage", "dmg", "coin", "gold", "cash", "money", "gem", "reward", "payout", "currency", "xp")
SENSITIVE_EXACT = frozenset(["amount", "value", "total", "score", "health", "heal", "multiplier", "salary", "bounty", "tip"])
VALIDATOR_RE = re.compile(r"^(typeof|type|tonumber|assert|clamp|min|max|floor|find|IsA|IsDescendantOf|isA|isValid\w*|validate\w*|check\w*|verify\w*|sanitize\w*|"
                          r"is[A-Z]\w*|has[A-Z]\w*|expect\w*|ensure\w*)$")
COOLDOWN_RE = re.compile(r"cooldown|debounce|ratelimit|throttle|lastuse|lastfire|lastrequest|lastclaim|lastbuy|canuse|permission|isadmin|hasrank|rank|whitelist|"
                         r"allowed|authorized|ratelimiter|ispermitted|owner|getrankingroup|isingroup", re.I)
TIME_CALLS = frozenset(["os.clock", "os.time", "tick", "time", "workspace:GetServerTimeNow", "DateTime.now"])
DS_METHODS = frozenset(["GetAsync", "SetAsync", "UpdateAsync", "IncrementAsync", "RemoveAsync", "GetSortedAsync", "ListKeysAsync", "ListDataStoresAsync",
                        "GetVersionAsync"])
DS_WRITE = frozenset(["SetAsync", "UpdateAsync", "IncrementAsync", "RemoveAsync"])
DS_MENTION = frozenset(["DataStoreService", "GetDataStore", "GetOrderedDataStore", "GetGlobalDataStore", "DataStore", "OrderedDataStore"])
YIELD_METHODS = frozenset(["wait", "Wait", "yield", "WaitForChild", "InvokeServer", "InvokeClient", "sleep", "WaitForPlayer", "Stepped"])
SERVER_ONLY = ("DataStoreService", "ServerStorage", "ServerScriptService", "OnServerEvent", "OnServerInvoke", "FireClient", "FireAllClients", "ProcessReceipt")
CLIENT_ONLY = ("LocalPlayer", "UserInputService", "ContextActionService", "RenderStepped", "OnClientEvent", "FireServer", "InvokeServer")
LOCK_RE = re.compile(r"lock|session|jobid|messagingservice", re.I)


@dataclass
class Hit:
    line: int
    data: dict = field(default_factory=dict)  # values for the {placeholders} in the rule's problem text
    confidence: str | None = None
    severity: str | None = None
    problem: str | None = None
    fix: str | None = None
    col: int = 0


Detector = Callable[[Analysis, dict], list[Hit]]
DETECTORS: dict[str, Detector] = {}


def detector(rule_id: str):
    def wrap(fn: Detector) -> Detector:
        DETECTORS[rule_id] = fn
        return fn
    return wrap


# ---------------------------------------------------------------------------------------------------------------------------------
# shared helpers
# ---------------------------------------------------------------------------------------------------------------------------------
def is_sensitive(name: str) -> bool:
    low = name.lower()
    return low in SENSITIVE_EXACT or any(s in low for s in SENSITIVE_SUBSTR)


def body_range(a: Analysis, f: Func) -> tuple[int, int]:
    return f.paren_close + 1, f.end


def discarded(a: Analysis, c: Call) -> bool:
    """True when the call is a bare statement (its value is thrown away)."""
    if c.start == 0:
        return True
    prev = a.toks[c.start - 1]
    from .structure import EXPR_PREV_KW, EXPR_PREV_OPS
    if prev.kind == "op" and prev.text in EXPR_PREV_OPS:
        return False
    if prev.kind == "keyword" and prev.text in EXPR_PREV_KW:
        return False
    if prev.is_kw("local"):
        return False
    return True


def own_returns(a: Analysis, f: Func) -> list[int]:
    """Indexes of `return` keywords that belong to ``f`` itself (not to a nested function)."""
    lo, hi = body_range(a, f)
    out = []
    for i in range(lo, min(hi, len(a.toks))):
        if a.toks[i].is_kw("return"):
            fn = a.enclosing_function(i)
            if fn is not None and fn.start == f.start:
                out.append(i)
    return out


def statement_tokens(a: Analysis, i: int) -> list[int]:
    """Token indexes of the statement that contains token i, found by line: all tokens on the same line."""
    line = a.toks[i].line
    return [k for k, t in enumerate(a.toks) if t.line == line]


def guard_lines(a: Analysis, lo: int, hi: int) -> set[int]:
    """Lines inside [lo, hi) that start with if/elseif/assert (a guard or check)."""
    out = set()
    for i in range(lo, hi):
        t = a.toks[i]
        first = i == lo or a.toks[i - 1].line != t.line
        if first and (t.is_kw("if", "elseif") or (t.kind == "name" and t.text == "assert") or t.is_kw("while", "until")):
            out.add(t.line)
    return out


def param_validated(a: Analysis, f: Func, pname: str) -> bool:
    lo, hi = body_range(a, f)
    guards = guard_lines(a, lo, hi)
    for i in range(lo, min(hi, len(a.toks))):
        t = a.toks[i]
        if t.kind != "name" or t.text != pname:
            continue
        if i > 0 and a.toks[i - 1].is_op(".", ":"):
            continue
        # inside a validator-style call: typeof(p), tonumber(p), math.clamp(p, ...), assert(...), p:IsA(...)
        for c in a.calls:
            if c.open < i < c.close and VALIDATOR_RE.match(c.method):
                return True
        if i + 2 < len(a.toks) and a.toks[i + 1].is_op(":") and a.toks[i + 2].kind == "name" and VALIDATOR_RE.match(a.toks[i + 2].text):
            return True
        if t.line in guards:
            return True
    return False


def param_used(a: Analysis, f: Func, pname: str) -> list[int]:
    lo, hi = body_range(a, f)
    return [i for i in range(lo, min(hi, len(a.toks))) if a.toks[i].kind == "name" and a.toks[i].text == pname
            and not (i > 0 and a.toks[i - 1].is_op(".", ":"))]


def remote_handlers(a: Analysis) -> list[dict]:
    """Server-side remote handlers: OnServerEvent:Connect(function(player, ...)) and X.OnServerInvoke = function(player, ...)."""
    out = []
    for f, signal, event in a.callbacks():
        if event not in ("OnServerEvent", "OnServerInvoke"):
            continue
        out.append({"func": f, "signal": signal, "event": event, "kind": "event" if event == "OnServerEvent" else "invoke", "line": f.line,
                    "params": [p.name for p in f.params], "remote": re.sub(r"\.(OnServerEvent|OnServerInvoke)$", "", signal)})
    return out


def has_loop_ancestor(a: Analysis, i: int) -> list:
    """Loop blocks enclosing token i, walking out through function wrappers that are just pcall/retry/task.spawn style helpers."""
    out = []
    for blk in a.block_chain(i):
        if blk.kind == "function":
            f = next((x for x in a.functions if x.start == blk.start), None)
            pc = f.parent_call if f else None
            if pc and (PROTECTED_RE.match(pc.method) or pc.callee in ("task.spawn", "task.defer", "spawn", "coroutine.wrap", "coroutine.wrap")):
                continue
            break
        if blk.kind in ("while", "for", "repeat"):
            out.append(blk)
    return out


def loop_min_wait(a: Analysis, blk) -> float | None:
    waits = []
    for c in a.calls:
        if blk.start < c.start < blk.end and c.callee in ("task.wait", "wait") and c.args:
            lo, hi = c.args[0]
            if hi - lo == 1 and a.toks[lo].kind == "number":
                try:
                    waits.append(float(a.toks[lo].text.replace("_", "")))
                except ValueError:
                    pass
    return min(waits) if waits else None


def ds_mentioned(a: Analysis) -> bool:
    return any(t.kind == "name" and t.text in DS_MENTION for t in a.toks)


def ds_calls(a: Analysis) -> list[Call]:
    mention = ds_mentioned(a)
    out = []
    for c in a.calls:
        if not c.is_method or c.method not in DS_METHODS:
            continue
        recv = c.receiver.split(".")[-1].split(":")[-1].lower()
        if "httpservice" in c.receiver.lower() or recv in ("http", "httpservice"):
            continue
        if mention or re.search(r"store|data", recv):
            out.append(c)
    return out


def enclosing_callbacks(a: Analysis, i: int) -> list[tuple[Func, str, str]]:
    cbs = {f.start: (f, s, e) for f, s, e in a.callbacks()}
    out = []
    for blk in a.block_chain(i):
        if blk.kind == "function" and blk.start in cbs:
            out.append(cbs[blk.start])
    return out


def is_top_level_decl_table(a: Analysis) -> dict[str, int]:
    """Names declared at file level as `local NAME [: type] = {}` (empty constructor): name -> token index of the name."""
    out = {}
    toks = a.toks
    for i, t in enumerate(toks):
        if not t.is_kw("local") or i + 1 >= len(toks) or toks[i + 1].kind != "name" or a.function_depth(i) != 0 or a.block_chain(i):
            continue
        j = i + 2
        while j < len(toks) and not (toks[j].is_op("=") and a.enclosing[j] == a.enclosing[i + 1]):
            if toks[j].line != t.line and not toks[j - 1].is_op(":", ",", "{", "|", "->", "<"):
                break
            j += 1
        if j + 2 < len(toks) and toks[j].is_op("=") and toks[j + 1].is_op("{") and toks[j + 2].is_op("}"):
            out[toks[i + 1].text] = i + 1
    return out


# ---------------------------------------------------------------------------------------------------------------------------------
# security
# ---------------------------------------------------------------------------------------------------------------------------------
@detector("SEC001")
def sec001(a: Analysis, p: dict) -> list[Hit]:
    hits = []
    for h in remote_handlers(a):
        f: Func = h["func"]
        bad = [q.name for q in f.params[1:] if q.name not in ("...", "_") and not is_sensitive(q.name) and param_used(a, f, q.name) and not param_validated(a, f, q.name)]
        if bad:
            hits.append(Hit(h["line"], {"args": ", ".join(f"`{b}`" for b in bad), "remote": h["remote"]}))
    return hits


@detector("SEC002")
def sec002(a: Analysis, p: dict) -> list[Hit]:
    hits = []
    for h in remote_handlers(a):
        f: Func = h["func"]
        for q in f.params[1:]:
            if not is_sensitive(q.name):
                continue
            uses = param_used(a, f, q.name)
            if not uses:
                continue
            strong = False
            for i in uses:
                line_words = {t.text for t in a.toks if t.line == a.toks[i].line}
                if line_words & {"Value", "TakeDamage", "Health", "Coins", "Cash", "Gold", "Money", "Gems", "Increment", "AddMoney", "AddCoins", "+=", "-="}:
                    strong = True
            hits.append(Hit(a.toks[uses[0]].line, {"arg": q.name}, confidence="high" if strong else "medium"))
    return hits


@detector("SEC003")
def sec003(a: Analysis, p: dict) -> list[Hit]:
    hits = []
    for h in remote_handlers(a):
        f: Func = h["func"]
        lo, hi = body_range(a, f)
        names = {t.text for t in a.toks[lo:hi] if t.kind == "name"}
        if any(COOLDOWN_RE.search(n) for n in names):
            continue
        if any(lo < c.start < hi and c.callee in TIME_CALLS for c in a.calls):
            continue
        hits.append(Hit(h["line"], {"remote": h["remote"]}))
    return hits


@detector("SEC004")
def sec004(a: Analysis, p: dict) -> list[Hit]:
    hits = []
    for i, t in enumerate(a.toks):
        if t.kind == "name" and t.text == "loadstring" and not (i > 0 and a.toks[i - 1].is_op(".", ":") and a.toks[i - 2].text not in ("_G", "shared", "getfenv")):
            hits.append(Hit(t.line))
    return hits


@detector("SEC005")
def sec005(a: Analysis, p: dict) -> list[Hit]:
    hits = []
    for c in a.calls:
        if c.callee != "require" or not c.args:
            continue
        lo, hi = c.args[0]
        first = a.toks[lo]
        if hi - lo == 1 and ((first.kind == "number") or (first.kind == "string" and first.text.strip("\"'").isdigit())):
            hits.append(Hit(c.line))
        elif first.kind == "name" and first.text == "tonumber":
            hits.append(Hit(c.line))
    return hits


@detector("SEC006")
def sec006(a: Analysis, p: dict) -> list[Hit]:
    if a.file_kind() == "client":
        return []
    return [Hit(c.line) for c in a.calls if c.is_method and c.method == "InvokeClient"]


# ---------------------------------------------------------------------------------------------------------------------------------
# data
# ---------------------------------------------------------------------------------------------------------------------------------
@detector("DAT001")
def dat001(a: Analysis, p: dict) -> list[Hit]:
    return [Hit(c.line, {"call": f"{c.receiver}:{c.method}"}) for c in ds_calls(a) if not a.in_protected(c.start)]


def _protecting_call(a: Analysis, i: int) -> Call | None:
    best = None
    for c in a.calls:
        if PROTECTED_RE.match(c.method) and not c.is_method and c.open < i < c.close:
            if best is None or c.open > best.open:
                best = c
    return best


@detector("DAT002")
def dat002(a: Analysis, p: dict) -> list[Hit]:
    hits = []
    for c in ds_calls(a):
        pc = _protecting_call(a, c.start)
        if pc is None:
            continue
        if re.search(r"retry|attempt|backoff", pc.method, re.I):
            continue
        fn = a.enclosing_function(c.start)
        # walk outward through wrapper functions: any enclosing loop (through pcall/retry helpers) or retry-named function counts
        if has_loop_ancestor(a, pc.start) or has_loop_ancestor(a, c.start):
            continue
        outer = a.enclosing_function(pc.start)
        if outer and outer.label and re.search(r"retry|attempt|backoff", outer.label, re.I):
            continue
        hits.append(Hit(c.line, {"call": f"{c.receiver}:{c.method}"}))
    return hits


@detector("DAT003")
def dat003(a: Analysis, p: dict) -> list[Hit]:
    has_get = any(c.method == "GetAsync" for c in ds_calls(a))
    hits = []
    for c in ds_calls(a):
        if c.method != "SetAsync" or re.search(r"ordered|leaderboard|ranking|top", c.receiver, re.I):
            continue
        hits.append(Hit(c.line, confidence="medium" if has_get else "low"))
    return hits


@detector("DAT004")
def dat004(a: Analysis, p: dict) -> list[Hit]:
    if a.file_kind() == "client" or a.has_name("BindToClose") or not a.has_name("PlayerRemoving"):
        return []
    writes = [c for c in ds_calls(a) if c.method in DS_WRITE]
    return [Hit(writes[0].line)] if writes else []


@detector("DAT005")
def dat005(a: Analysis, p: dict) -> list[Hit]:
    limit = float(p.get("min_seconds", 30))
    hits = []
    for c in ds_calls(a):
        if c.method not in DS_WRITE:
            continue
        frame = [cb for cb in enclosing_callbacks(a, c.start) if cb[2] in FRAME_EVENTS or cb[2] in ("Changed",)]
        if frame:
            hits.append(Hit(c.line, {"where": f"a {frame[0][2]} handler (every frame or change)"}, confidence="high"))
            continue
        for blk in has_loop_ancestor(a, c.start):
            w = loop_min_wait(a, blk)
            if w is not None and w < limit:
                hits.append(Hit(c.line, {"where": f"a loop that waits only {w:g}s (limit {limit:g}s)"}))
                break
    return hits


@detector("DAT006")
def dat006(a: Analysis, p: dict) -> list[Hit]:
    if a.file_kind() == "client" or not (a.has_name("PlayerAdded") or a.has_name("PlayerRemoving")):
        return []
    writes = [c for c in ds_calls(a) if c.method in DS_WRITE]
    if not writes:
        return []
    if any(LOCK_RE.search(t.text) for t in a.toks if t.kind in ("name",)) or any(LOCK_RE.search(t.text) for t in a.toks if t.kind == "string"):
        return []
    return [Hit(writes[0].line)]


def _receipt_funcs(a: Analysis) -> list[Func]:
    return [f for f in a.functions if f.assigned_to and f.assigned_to.split(".")[-1] == "ProcessReceipt"]


@detector("DAT007")
def dat007(a: Analysis, p: dict) -> list[Hit]:
    hits = []
    for f in _receipt_funcs(a):
        rets = own_returns(a, f)
        if not rets:
            hits.append(Hit(f.line, {"what": "no return at all (nil)"}))
        for r in rets:
            j = r + 1
            end = j
            while end < len(a.toks) and a.toks[end].line == a.toks[r].line and not a.toks[end].is_kw("end", "else", "elseif", "until"):
                end += 1
            expr = a.toks[j:end]
            texts = [t.text for t in expr]
            if not expr:
                hits.append(Hit(a.toks[r].line, {"what": "nothing (nil)"}))
            elif {"PurchaseGranted", "NotProcessedYet", "ProductPurchaseDecision"} & set(texts):
                continue
            elif len(expr) == 1 and (expr[0].is_kw("true", "false", "nil") or expr[0].kind in ("number", "string")):
                hits.append(Hit(a.toks[r].line, {"what": f"`{expr[0].text}`"}))
    return hits


@detector("DAT008")
def dat008(a: Analysis, p: dict) -> list[Hit]:
    hits = []
    for f in _receipt_funcs(a):
        lo, hi = body_range(a, f)
        names = {t.text for t in a.toks[lo:hi] if t.kind == "name"}
        if "PurchaseGranted" in names and "NotProcessedYet" not in names:
            hits.append(Hit(f.line))
    return hits


@detector("DAT009")
def dat009(a: Analysis, p: dict) -> list[Hit]:
    hits = []
    for c in ds_calls(a):
        if not c.args:
            continue
        lo, hi = c.args[0]
        for i in range(lo, hi):
            t = a.toks[i]
            if t.kind == "name" and t.text in ("Name", "DisplayName") and i > 0 and a.toks[i - 1].is_op(".") and i > 1 and a.toks[i - 2].kind == "name" \
                    and re.search(r"player|plr|user|^p$", a.toks[i - 2].text, re.I):
                hits.append(Hit(c.line))
                break
    return hits


# ---------------------------------------------------------------------------------------------------------------------------------
# performance
# ---------------------------------------------------------------------------------------------------------------------------------
def _infinite_loops(a: Analysis):
    for blk in a.blocks:
        if blk.kind == "while":
            # while true do / while 1 do
            j = blk.start - 1
            if j >= 1 and a.toks[j].kind in ("keyword", "number") and a.toks[j].text in ("true", "1") and a.toks[j - 1].is_kw("while"):
                yield blk, "while"
        elif blk.kind == "repeat" and blk.end < len(a.toks) and blk.end + 1 < len(a.toks) and a.toks[blk.end].is_kw("until") and a.toks[blk.end + 1].is_kw("false"):
            yield blk, "repeat"


def _loop_has_exit(a: Analysis, blk) -> bool:
    """Any break/return/error() inside the loop (even conditional or nested) means we cannot call it an unconditional spin."""
    for i in range(blk.start + 1, min(blk.end, len(a.toks))):
        t = a.toks[i]
        if t.is_kw("break", "return") or (t.kind == "name" and t.text == "error" and i + 1 < len(a.toks) and a.toks[i + 1].is_op("(")):
            return True
    return False


def _loop_calls(a: Analysis, blk) -> list[Call]:
    return [c for c in a.calls if blk.start < c.start < blk.end]


@detector("PERF001")
def perf001(a: Analysis, p: dict) -> list[Hit]:
    hits = []
    for blk, _ in _infinite_loops(a):
        calls = _loop_calls(a, blk)
        if any(c.method in YIELD_METHODS or c.callee in ("task.wait", "wait", "coroutine.yield", "task.yield") for c in calls) or _loop_has_exit(a, blk):
            continue
        safe = re.compile(r"^(print|warn|math\.\w+|string\.\w+|table\.\w+|Vector3\.new|CFrame\.new|Color3\.\w+|tostring|tonumber|typeof|type|select|ipairs|pairs|next|#)$")
        conf = "high" if all(safe.match(c.callee) for c in calls) else "medium"
        hits.append(Hit(a.toks[blk.start].line, confidence=conf))
    return hits


@detector("PERF002")
def perf002(a: Analysis, p: dict) -> list[Hit]:
    hits = []
    for blk, _ in _infinite_loops(a):
        if any(c.callee == "wait" for c in _loop_calls(a, blk)):
            hits.append(Hit(next(c.line for c in _loop_calls(a, blk) if c.callee == "wait")))
    # while wait() do ...
    for i, t in enumerate(a.toks):
        if t.is_kw("while") and i + 2 < len(a.toks) and a.toks[i + 1].text == "wait" and a.toks[i + 2].is_op("("):
            hits.append(Hit(t.line))
    return hits


def _frame_funcs(a: Analysis):
    for f, sig, ev in a.callbacks():
        if ev in FRAME_EVENTS:
            yield f, ev
    for c in a.calls:
        if c.method == "BindToRenderStep":
            for lo, hi in c.args[2:3]:
                f = next((x for x in a.functions if lo <= x.start < hi), None)
                if f:
                    yield f, "RenderStep"


@detector("PERF003")
def perf003(a: Analysis, p: dict) -> list[Hit]:
    hits = []
    seen = set()
    for f, ev in _frame_funcs(a):
        for c in a.func_calls(f):
            if not c.is_method:
                continue
            if c.method in ("GetChildren", "GetDescendants"):
                conf = "high"
            elif c.method in ("FindFirstChild", "FindFirstChildOfClass", "FindFirstChildWhichIsA", "FindFirstAncestor", "FindFirstAncestorOfClass"):
                conf = "medium"
            else:
                continue
            if c.open in seen:
                continue
            seen.add(c.open)
            hits.append(Hit(c.line, {"call": f":{c.method}()", "event": ev}, confidence=conf))
    return hits


@detector("PERF004")
def perf004(a: Analysis, p: dict) -> list[Hit]:
    hits = []
    seen = set()
    for f, ev in _frame_funcs(a):
        for c in a.func_calls(f):
            if c.open in seen:
                continue
            if c.callee == "Instance.new":
                seen.add(c.open)
                hits.append(Hit(c.line, {"call": "Instance.new", "event": ev}))
            elif c.is_method and c.method == "Clone":
                seen.add(c.open)
                hits.append(Hit(c.line, {"call": ":Clone()", "event": ev}, confidence="medium"))
    return hits


DEBOUNCE_RE = re.compile(r"debounce|deb\b|cooldown|busy|once|touching|alreadyhit|canhit|hitonce|touched\b|ontouch|lasthit|isactive|active", re.I)


@detector("PERF005")
def perf005(a: Analysis, p: dict) -> list[Hit]:
    hits = []
    for f, sig, ev in a.callbacks():
        if ev != "Touched":
            continue
        lo, hi = body_range(a, f)
        names = {t.text for t in a.toks[lo:hi] if t.kind == "name"}
        if any(DEBOUNCE_RE.search(n) for n in names):
            continue
        hits.append(Hit(f.line))
    return hits


# ---------------------------------------------------------------------------------------------------------------------------------
# leaks
# ---------------------------------------------------------------------------------------------------------------------------------
@detector("LEAK001")
def leak001(a: Analysis, p: dict) -> list[Hit]:
    if a.has_name("Disconnect"):
        return []
    hits = []
    for c in a.calls:
        if not (c.is_method and c.method in ("Connect", "connect", "Once")) or c.method == "Once":
            continue
        if not discarded(a, c):
            continue
        cbs = enclosing_callbacks(a, c.start)
        in_frame = [cb for cb in cbs if cb[2] in FRAME_EVENTS]
        repeating = [cb for cb in cbs if cb[2] in REPEAT_EVENTS]
        loop = a.in_loop(c.start)
        long_lived = bool(LONG_LIVED_RE.match(c.receiver))
        if in_frame:
            hits.append(Hit(c.line, {"signal": c.receiver or c.callee}, confidence="high"))
        elif long_lived and (repeating or loop):
            hits.append(Hit(c.line, {"signal": c.receiver}))
    return hits


@detector("LEAK002")
def leak002(a: Analysis, p: dict) -> list[Hit]:
    tables = is_top_level_decl_table(a)
    hits = []
    for name, idx in tables.items():
        appends = []
        for c in a.calls:
            if c.callee == "table.insert" and c.args and a.toks[c.args[0][0]].text == name and c.args[0][1] - c.args[0][0] == 1:
                appends.append(c.start)
        if not appends:
            continue
        removed = any(c.callee in ("table.remove", "table.clear") and c.args and a.toks[c.args[0][0]].text == name for c in a.calls)
        reassigned = any(t.kind == "name" and t.text == name and k + 3 < len(a.toks) and a.toks[k + 1].is_op("=") and a.toks[k + 2].is_op("{") and k != idx
                         for k, t in enumerate(a.toks))
        nil_set = any(t.kind == "name" and t.text == name and k + 1 < len(a.toks) and a.toks[k + 1].is_op("[") and
                      (m := a.match.get(k + 1)) and m + 2 < len(a.toks) and a.toks[m + 1].is_op("=") and a.toks[m + 2].is_kw("nil") for k, t in enumerate(a.toks))
        if removed or reassigned or nil_set:
            continue
        repeating = [i for i in appends if a.in_loop(i, cross_functions=True) or [cb for cb in enclosing_callbacks(a, i) if cb[2] in REPEAT_EVENTS]]
        if repeating:
            hits.append(Hit(a.toks[repeating[0]].line, {"table": name}))
    return hits


PLAYER_KEY_RE = re.compile(r"^(player|plr|userid|character|char|characterModel|owner)$", re.I)


@detector("LEAK003")
def leak003(a: Analysis, p: dict) -> list[Hit]:
    tables = is_top_level_decl_table(a)
    hits = []
    for name in tables:
        assigns = []
        cleaned = False
        for k, t in enumerate(a.toks):
            if t.kind != "name" or t.text != name or k + 1 >= len(a.toks) or not a.toks[k + 1].is_op("["):
                continue
            if k > 0 and a.toks[k - 1].is_op(".", ":"):
                continue
            m = a.match.get(k + 1)
            if m is None or m + 1 >= len(a.toks) or not a.toks[m + 1].is_op("="):
                continue
            if a.toks[m + 2].is_kw("nil"):
                cleaned = True
                continue
            key_names = {x.text for x in a.toks[k + 2:m] if x.kind == "name"}
            if any(PLAYER_KEY_RE.match(n) for n in key_names) and a.function_depth(k) > 0:
                assigns.append(k)
        if any(c.callee in ("table.remove", "table.clear") and c.args and a.toks[c.args[0][0]].text == name for c in a.calls):
            cleaned = True
        if assigns and not cleaned:
            hits.append(Hit(a.toks[assigns[0]].line, {"table": name}))
    return hits


@detector("LEAK004")
def leak004(a: Analysis, p: dict) -> list[Hit]:
    if a.has_name("PlayerRemoving", "Destroy", "Debris", "AddItem") or a.file_kind() == "client":
        return []
    hits = []
    for f, sig, ev in a.callbacks():
        if ev not in ("PlayerAdded", "CharacterAdded"):
            continue
        for c in a.func_calls(f):
            if c.callee == "Instance.new" or (c.is_method and c.method == "Clone"):
                # instances parented under the player/character clean themselves up; look for workspace-style parents on the same function
                lo, hi = body_range(a, f)
                for k in range(lo, min(hi, len(a.toks) - 3)):
                    if a.toks[k].is_op(".") and a.toks[k + 1].text == "Parent" and a.toks[k + 2].is_op("=") and a.toks[k + 3].text in ("workspace", "Workspace", "ReplicatedStorage"):
                        hits.append(Hit(c.line))
                        break
                break
    return hits


# ---------------------------------------------------------------------------------------------------------------------------------
# deprecated / poor API
# ---------------------------------------------------------------------------------------------------------------------------------
def _defines(a: Analysis, name: str) -> bool:
    for i, t in enumerate(a.toks):
        if t.is_kw("local") and i + 1 < len(a.toks):
            nxt = a.toks[i + 1]
            if nxt.text == name or (nxt.is_kw("function") and i + 2 < len(a.toks) and a.toks[i + 2].text == name):
                return True
    return any(f.name == name for f in a.functions)


def _bare_call(a: Analysis, name: str) -> list[Call]:
    if _defines(a, name):
        return []
    return [c for c in a.calls if c.callee == name]


@detector("API001")
def api001(a: Analysis, p: dict) -> list[Hit]:
    return [Hit(c.line) for c in _bare_call(a, "wait")]


@detector("API002")
def api002(a: Analysis, p: dict) -> list[Hit]:
    return [Hit(c.line) for c in _bare_call(a, "spawn")]


@detector("API003")
def api003(a: Analysis, p: dict) -> list[Hit]:
    return [Hit(c.line) for c in _bare_call(a, "delay")]


@detector("API004")
def api004(a: Analysis, p: dict) -> list[Hit]:
    return [Hit(c.line) for c in a.calls if c.callee == "Instance.new" and len(c.args) >= 2]


@detector("API005")
def api005(a: Analysis, p: dict) -> list[Hit]:
    hits = []
    for c in a.calls:
        if not (c.is_method and c.method == "LoadAnimation"):
            continue
        head = " ".join(t.text for t in a.toks[c.start:c.open]).lower()
        if "animator" in head:
            continue
        last = re.split(r"[.:]", c.receiver)[-1].lower() if c.receiver else ""
        if last.endswith("humanoid") or last in ("hum", "h") or "humanoid" in head:
            hits.append(Hit(c.line, confidence="high" if last.endswith("humanoid") or last in ("hum",) else "medium"))
    return hits


@detector("API006")
def api006(a: Analysis, p: dict) -> list[Hit]:
    if a.file_kind() == "server" or any("CurrentCamera" in t.text and t.kind == "string" for t in a.toks):
        return []
    hits = []
    for i, t in enumerate(a.toks[:-1]):
        if t.is_op(".") and a.toks[i + 1].text == "CurrentCamera" and i >= 1 and a.toks[i - 1].text in ("workspace", "Workspace"):
            if i >= 3 and a.toks[i - 2].is_op("=") and a.toks[i - 3].kind == "name" and a.function_depth(i) == 0:
                hits.append(Hit(t.line))
    return hits


@detector("API007")
def api007(a: Analysis, p: dict) -> list[Hit]:
    allowed = {float(x) for x in p.get("allowed", [0, 1, 2, -1])}
    cap = int(p.get("max_per_file", 5))
    hits = []
    cmp_ops = {"<", ">", "<=", ">=", "==", "~="}
    for i, t in enumerate(a.toks):
        if t.kind != "number":
            continue
        try:
            v = float(t.text.replace("_", ""), ) if not t.text.lower().startswith(("0x", "0b")) else float(int(t.text.replace("_", ""), 0))
        except ValueError:
            continue
        if v in allowed:
            continue
        prev = a.toks[i - 1] if i else None
        nxt = a.toks[i + 1] if i + 1 < len(a.toks) else None
        in_cmp = (prev is not None and prev.kind == "op" and prev.text in cmp_ops) or (nxt is not None and nxt.kind == "op" and nxt.text in cmp_ops)
        # unary minus: `x < -5`
        tunable = prev is not None and prev.is_op("=") and i >= 3 and a.toks[i - 2].kind == "name" and a.toks[i - 2].text in ("WalkSpeed", "JumpPower", "JumpHeight", "MaxHealth", "Health")
        if in_cmp or tunable:
            hits.append(Hit(t.line, {"number": t.text}))
            if len(hits) >= cap:
                break
    return hits


@detector("API008")
def api008(a: Analysis, p: dict) -> list[Hit]:
    hits = []
    for c in a.calls:
        if c.is_method and c.method in ("connect", "disconnect"):
            hits.append(Hit(c.line, {"method": c.method, "fixed": c.method.capitalize()}))
    return hits


@detector("API009")
def api009(a: Analysis, p: dict) -> list[Hit]:
    return [Hit(c.line) for c in _bare_call(a, "tick")]


@detector("API010")
def api010(a: Analysis, p: dict) -> list[Hit]:
    return [Hit(c.line, {"call": c.callee}) for c in a.calls if c.callee in ("table.getn", "table.foreach", "table.foreachi", "gcinfo")]


BODY_MOVERS = ("BodyVelocity", "BodyPosition", "BodyGyro", "BodyForce", "BodyAngularVelocity", "BodyThrust", "RocketPropulsion")


@detector("API011")
def api011(a: Analysis, p: dict) -> list[Hit]:
    hits = []
    for c in a.calls:
        if c.callee == "Instance.new" and c.args:
            lo, hi = c.args[0]
            t = a.toks[lo]
            if hi - lo == 1 and t.kind == "string" and t.text.strip("\"'") in BODY_MOVERS:
                hits.append(Hit(c.line, {"class": t.text.strip("\"'")}))
    return hits


# ---------------------------------------------------------------------------------------------------------------------------------
# structure
# ---------------------------------------------------------------------------------------------------------------------------------
@detector("STR001")
def str001(a: Analysis, p: dict) -> list[Hit]:
    if not a.toks:
        return []
    first = a.toks[0]
    for t in a.all_tokens:
        if t.kind != "comment":
            break
        m = re.match(r"--!\s*(strict|nonstrict|nocheck)\b", t.text)
        if m:
            if m.group(1) == "strict":
                return []
            return [Hit(t.line, problem=f"`--!{m.group(1)}` weakens or disables type checking; use `--!strict`")]
    return [Hit(1)]


def module_table(a: Analysis) -> str | None:
    """The name returned by the last top-level `return NAME`."""
    for i in range(len(a.toks) - 1, -1, -1):
        t = a.toks[i]
        if t.is_kw("return") and not a.block_chain(i):
            nxt = a.toks[i + 1] if i + 1 < len(a.toks) else None
            return nxt.text if nxt is not None and nxt.kind == "name" else None
    return None


@detector("STR002")
def str002(a: Analysis, p: dict) -> list[Hit]:
    mod = module_table(a)
    if not mod:
        return []
    hits = []
    for f in a.functions:
        label = f.name or f.assigned_to or ""
        if not (label.startswith(mod + ".") or label.startswith(mod + ":")):
            continue
        params = [q for q in f.params if q.name not in ("self", "...")]
        untyped = [q.name for q in params if not q.typed]
        lo, hi = body_range(a, f)
        returns_value = any(a.toks[r + 1].line == a.toks[r].line and not a.toks[r + 1].is_kw("end") for r in own_returns(a, f) if r + 1 < len(a.toks))
        if untyped or (returns_value and not f.returns_typed):
            hits.append(Hit(f.line, {"name": label}))
    return hits


@detector("STR004")
def str004(a: Analysis, p: dict) -> list[Hit]:
    if a.file_kind() != "client":
        return []
    hits, seen = [], set()
    for t in a.toks:
        word = t.text if t.kind == "name" else (t.text.strip("\"'") if t.kind == "string" else "")
        if word in SERVER_ONLY and word not in seen:  # a service named in game:GetService("X") is a string token
            seen.add(word)
            hits.append(Hit(t.line, {"name": word}))
    return hits[:3]


@detector("STR005")
def str005(a: Analysis, p: dict) -> list[Hit]:
    if a.file_kind() != "server":
        return []
    hits, seen = [], set()
    for i, t in enumerate(a.toks):
        word = t.text if t.kind == "name" else (t.text.strip("\"'") if t.kind == "string" else "")
        if word in CLIENT_ONLY and word not in seen:
            seen.add(word)
            hits.append(Hit(t.line, {"name": word}))
        if t.kind == "name" and t.text == "CurrentCamera" and i >= 2 and a.toks[i - 1].is_op(".") and a.toks[i - 2].text in ("workspace", "Workspace") and "CurrentCamera" not in seen:
            seen.add("CurrentCamera")
            hits.append(Hit(t.line, {"name": "workspace.CurrentCamera"}))
    return hits[:3]


# ---------------------------------------------------------------------------------------------------------------------------------
# correctness
# ---------------------------------------------------------------------------------------------------------------------------------
@detector("COR001")
def cor001(a: Analysis, p: dict) -> list[Hit]:
    return [Hit(c.line) for c in a.calls if c.callee in ("pcall", "xpcall") and discarded(a, c)]
