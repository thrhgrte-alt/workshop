"""Lightweight structure on top of the tokenizer: bracket matching, blocks, functions and calls.

This is deliberately *not* a Luau parser. It recovers just enough shape to answer questions such as "is this call inside a
``pcall``", "is it inside a loop", "which signal does this callback listen to", and "what are this function's parameters".
Unbalanced or unusual code degrades to fewer matches, never to an exception.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .lexer import Token, code_tokens, mask, tokenize

EXPR_PREV_OPS = frozenset(["=", "(", ",", "{", "[", "..", "+", "-", "*", "/", "//", "%", "^", "==", "~=", "<", ">", "<=", ">=",
                           "+=", "-=", "*=", "/=", "..=", "%=", "^=", "//=", "?", ":", "->", "|", "&"])
EXPR_PREV_KW = frozenset(["return", "and", "or", "not", "in"])
PROTECTED_RE = re.compile(r"^(pcall|xpcall|spcall|ypcall|retry\w*|withretry\w*|try\w*|attempt\w*|safe\w*|protected\w*|trycatch\w*)$", re.I)
CONNECT_METHODS = frozenset(["Connect", "Once", "ConnectParallel", "connect"])


@dataclass
class Block:
    kind: str  # function | if | do | while | for | repeat
    start: int  # token index of the opener
    end: int  # token index of the closer (len(toks) when unterminated)
    parent: int  # index into Analysis.blocks, -1 at top level


@dataclass
class Call:
    callee: str  # e.g. "RunService.Heartbeat:Connect"
    method: str  # last name, e.g. "Connect"
    receiver: str  # chain before the last '.'/':' ("" for a plain call)
    is_method: bool  # called with ':'
    start: int  # first token index of the callee chain
    open: int
    close: int
    line: int
    args: list[tuple[int, int]]  # (first, last_exclusive) token ranges of each argument


@dataclass
class Param:
    name: str
    typed: bool


@dataclass
class Func:
    start: int  # index of the 'function' keyword
    end: int  # index of its 'end'
    block: int
    name: str | None  # for declarations: "M.foo" / "M:foo" / "foo"
    assigned_to: str | None  # for "X = function": "X"
    is_local: bool
    params: list[Param]
    returns_typed: bool
    line: int
    end_line: int
    paren_open: int
    paren_close: int
    parent_call: Call | None = None  # the call this function literal is an argument of

    @property
    def label(self) -> str:
        return self.name or self.assigned_to or "<anonymous>"


@dataclass
class Analysis:
    src: str
    path: str = ""
    context: str | None = None  # server | client | shared | None
    all_tokens: list[Token] = field(default_factory=list)
    toks: list[Token] = field(default_factory=list)
    lines: list[str] = field(default_factory=list)
    masked: str = ""
    masked_lines: list[str] = field(default_factory=list)
    match: dict[int, int] = field(default_factory=dict)
    rmatch: dict[int, int] = field(default_factory=dict)
    enclosing: list[int] = field(default_factory=list)
    blocks: list[Block] = field(default_factory=list)
    cur_block: list[int] = field(default_factory=list)
    functions: list[Func] = field(default_factory=list)
    calls: list[Call] = field(default_factory=list)
    balanced: bool = True
    protected_spans: list[tuple[int, int]] = field(default_factory=list)

    # ------------------------------------------------------------------ construction
    @classmethod
    def of(cls, src: str, path: str = "", context: str | None = None) -> "Analysis":
        a = cls(src=src, path=path, context=context)
        a.all_tokens = tokenize(src)
        a.toks = code_tokens(a.all_tokens)
        a.lines = src.split("\n")
        a.masked = mask(src, a.all_tokens)
        a.masked_lines = a.masked.split("\n")
        a._brackets()
        a._blocks()
        a._functions()
        a._calls()
        a.protected_spans = [(c.open + 1, c.close) for c in a.calls if PROTECTED_RE.match(c.method) and not c.is_method]
        return a

    def _brackets(self) -> None:
        pairs = {")": "(", "]": "[", "}": "{"}
        stack: list[int] = []
        self.enclosing = [-1] * len(self.toks)
        for i, t in enumerate(self.toks):
            if t.kind == "op" and t.text in pairs and stack and self.toks[stack[-1]].text == pairs[t.text]:
                o = stack.pop()
                self.match[o] = i
                self.rmatch[i] = o
                self.enclosing[i] = stack[-1] if stack else -1
                continue
            self.enclosing[i] = stack[-1] if stack else -1
            if t.kind == "op" and t.text in "({[":
                stack.append(i)
        if stack:
            self.balanced = False

    def _blocks(self) -> None:
        toks = self.toks
        stack: list[int] = []
        exprs: list[int] = []  # heights of open if-expressions awaiting their 'else'
        pending_loop: str | None = None
        last_else_expr_at = -2
        self.cur_block = [-1] * len(toks)

        def open_block(kind: str, i: int) -> None:
            self.blocks.append(Block(kind, i, len(toks), stack[-1] if stack else -1))
            stack.append(len(self.blocks) - 1)

        for i, t in enumerate(toks):
            self.cur_block[i] = stack[-1] if stack else -1
            if t.kind != "keyword":
                continue
            w = t.text
            if w == "function":
                open_block("function", i)
            elif w in ("while", "for"):
                pending_loop = w
            elif w == "do":
                open_block(pending_loop or "do", i)
                pending_loop = None
            elif w == "repeat":
                open_block("repeat", i)
            elif w == "if":
                prev = toks[i - 1] if i else None
                is_expr = bool(prev and ((prev.kind == "op" and prev.text in EXPR_PREV_OPS) or (prev.kind == "keyword" and prev.text in EXPR_PREV_KW)
                                         or (prev.is_kw("else") and last_else_expr_at == i - 1)))
                if is_expr:
                    exprs.append(len(stack))
                else:
                    open_block("if", i)
            elif w == "else":
                if exprs and exprs[-1] == len(stack):
                    exprs.pop()
                    last_else_expr_at = i
            elif w == "end":
                if stack:
                    self.blocks[stack[-1]].end = i
                    self.cur_block[i] = stack[-1]
                    stack.pop()
                else:
                    self.balanced = False
            elif w == "until":
                if stack and self.blocks[stack[-1]].kind == "repeat":
                    self.blocks[stack[-1]].end = i
                    stack.pop()
        if stack:
            self.balanced = False

    # ------------------------------------------------------------------ chains, functions, calls
    def chain_before(self, end: int) -> tuple[str, int]:
        """The member chain that ends just before token ``end`` (e.g. ``a.b:c`` or ``f().x``). Returns (text, first token index)."""
        j = end - 1
        parts: list[str] = []
        state = "atom"  # atom | sep | after_group
        while j >= 0:
            t = self.toks[j]
            if state in ("atom", "after_group"):
                if t.kind == "name":
                    parts.append(t.text)
                    j -= 1
                    state = "sep"
                elif t.kind == "op" and t.text in ")]}":
                    o = self.rmatch.get(j)
                    if o is None:
                        break
                    parts.append({")": "()", "]": "[]", "}": "{}"}[t.text])
                    j = o - 1
                    state = "after_group"
                elif t.kind == "string" and state == "atom":
                    parts.append('"str"')
                    j -= 1
                    state = "sep"
                else:
                    break
            else:
                if t.kind == "op" and t.text in (".", ":"):
                    parts.append(t.text)
                    j -= 1
                    state = "atom"
                else:
                    break
        parts.reverse()
        return "".join(parts), j + 1

    def _functions(self) -> None:
        toks = self.toks
        for bi, b in enumerate(self.blocks):
            if b.kind != "function":
                continue
            s = b.start
            j = s + 1
            name = None
            if j < len(toks) and toks[j].kind == "name":
                k = j
                while k + 2 < len(toks) and toks[k + 1].is_op(".", ":") and toks[k + 2].kind == "name":
                    k += 2
                name = "".join(t.text for t in toks[j:k + 1])
                j = k + 1
                if j < len(toks) and toks[j].is_op("<") and (m := self._skip_angle(j)):
                    j = m
            if j >= len(toks) or not toks[j].is_op("("):
                continue
            close = self.match.get(j)
            if close is None:
                continue
            params: list[Param] = []
            depth_idx = j
            cur: list[Token] = []
            for k in range(j + 1, close):
                if self.enclosing[k] == depth_idx and toks[k].is_op(","):
                    params.append(self._param(cur))
                    cur = []
                else:
                    cur.append(toks[k])
            if cur:
                params.append(self._param(cur))
            returns_typed = close + 1 < len(toks) and toks[close + 1].is_op(":")
            prev = toks[s - 1] if s else None
            is_local = bool(prev and prev.is_kw("local")) if name else False
            assigned = None
            parent_call = None
            if not name and prev is not None:
                if prev.is_op("="):
                    chain, start = self.chain_before(s - 1)
                    assigned = chain or None
                    if start > 0 and toks[start - 1].is_kw("local"):
                        is_local = True
                elif prev.is_op("(", ","):
                    o = self.enclosing[s]
                    if o >= 0 and toks[o].is_op("("):
                        parent_call = ("pending", o)  # type: ignore[assignment]
            self.functions.append(Func(s, b.end, bi, name, assigned, is_local, params, returns_typed, toks[s].line,
                                       toks[b.end].line if b.end < len(toks) else toks[-1].line, j, close, parent_call))

    def _skip_angle(self, j: int) -> int | None:
        depth = 0
        for k in range(j, min(j + 60, len(self.toks))):
            if self.toks[k].is_op("<"):
                depth += 1
            elif self.toks[k].is_op(">"):
                depth -= 1
                if depth == 0:
                    return k + 1
        return None

    @staticmethod
    def _param(toks: list[Token]) -> Param:
        if not toks:
            return Param("?", False)
        name = toks[0].text
        return Param(name, any(t.is_op(":") for t in toks[1:]))

    def _calls(self) -> None:
        toks = self.toks
        fn_paren = {f.paren_open for f in self.functions}
        for i, t in enumerate(toks):
            if not t.is_op("(") or i == 0 or i in fn_paren:
                continue
            prev = toks[i - 1]
            if not (prev.kind == "name" or prev.is_op(")", "]")):
                continue
            close = self.match.get(i)
            if close is None:
                continue
            chain, start = self.chain_before(i)
            if not chain or (start > 0 and toks[start - 1].is_kw("function")):
                continue
            is_method = False
            method, receiver = chain, ""
            m = re.search(r"([.:])([A-Za-z_][A-Za-z_0-9]*)$", chain)
            if m:
                is_method = m.group(1) == ":"
                method = m.group(2)
                receiver = chain[: m.start()]
            args: list[tuple[int, int]] = []
            lo = i + 1
            for k in range(i + 1, close):
                if self.enclosing[k] == i and toks[k].is_op(","):
                    args.append((lo, k))
                    lo = k + 1
            if lo < close:
                args.append((lo, close))
            self.calls.append(Call(chain, method, receiver, is_method, start, i, close, toks[start].line, args))
        by_open = {c.open: c for c in self.calls}
        for f in self.functions:
            if isinstance(f.parent_call, tuple):
                f.parent_call = by_open.get(f.parent_call[1])

    # ------------------------------------------------------------------ queries used by the rules
    def block_chain(self, i: int) -> list[Block]:
        out = []
        b = self.cur_block[i] if 0 <= i < len(self.cur_block) else -1
        while b >= 0:
            out.append(self.blocks[b])
            b = self.blocks[b].parent
        return out

    def enclosing_function(self, i: int) -> Func | None:
        for blk in self.block_chain(i):
            if blk.kind == "function":
                return next((f for f in self.functions if f.start == blk.start), None)
        return None

    def function_depth(self, i: int) -> int:
        return sum(1 for b in self.block_chain(i) if b.kind == "function")

    def in_loop(self, i: int, cross_functions: bool = False) -> Block | None:
        for blk in self.block_chain(i):
            if blk.kind == "function" and not cross_functions:
                return None
            if blk.kind in ("while", "for", "repeat"):
                return blk
        return None

    def in_protected(self, i: int) -> bool:
        return any(lo <= i < hi for lo, hi in self.protected_spans)

    def in_span(self, i: int, call: Call) -> bool:
        return call.open < i < call.close

    def text(self, lo: int, hi: int) -> str:
        return " ".join(t.text for t in self.toks[lo:hi])

    def names_in(self, lo: int, hi: int) -> set[str]:
        return {t.text for t in self.toks[lo:hi] if t.kind == "name"}

    def func_calls(self, f: Func) -> list[Call]:
        return [c for c in self.calls if f.start < c.start < f.end]

    def line_text(self, line: int) -> str:
        return self.lines[line - 1] if 1 <= line <= len(self.lines) else ""

    def has_name(self, *names: str) -> bool:
        s = set(names)
        return any(t.kind == "name" and t.text in s for t in self.toks)

    def callbacks(self) -> list[tuple[Func, str, str]]:
        """Functions that handle an event: (function, signal text such as 'RunService.Heartbeat', event name such as 'Heartbeat').

        Covers ``X:Connect(function ...)``/``:Once``/``:ConnectParallel`` and ``X.OnServerInvoke = function`` / ``X.ProcessReceipt = function``."""
        out = []
        for f in self.functions:
            pc = f.parent_call
            if pc and pc.method in CONNECT_METHODS and pc.is_method:
                sig = pc.receiver
                ev = re.split(r"[.:]", sig)[-1] if sig else ""
                out.append((f, sig, ev))
            elif f.assigned_to and re.search(r"\.(On\w+|ProcessReceipt)$", f.assigned_to):
                out.append((f, f.assigned_to, f.assigned_to.split(".")[-1]))
        return out

    def file_kind(self) -> str | None:
        """server | client | shared | None from an explicit context, else from the file name or well-known folder names."""
        if self.context in ("server", "client", "shared"):
            return self.context
        p = self.path.replace("\\", "/")
        low = p.lower()
        if re.search(r"\.server\.luau?$", low):
            return "server"
        if re.search(r"\.client\.luau?$", low):
            return "client"
        parts = set(p.split("/"))
        if parts & {"ServerScriptService", "ServerStorage"}:
            return "server"
        if parts & {"StarterPlayerScripts", "StarterCharacterScripts", "StarterGui", "StarterPack", "PlayerScripts"}:
            return "client"
        if "ReplicatedStorage" in parts:
            return "shared"
        return None
