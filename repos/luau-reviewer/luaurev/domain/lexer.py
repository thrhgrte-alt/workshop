"""A lightweight Luau tokenizer. It is NOT a parser.

It understands exactly enough to keep pattern checks honest: line and block comments (``--``, ``--[[ ]]``,
``--[==[ ]==]``), quoted strings with escapes, long strings (``[[ ]]``, ``[=[ ]=]``), backtick interpolated strings (treated
as one opaque string), numbers, names, keywords and operators. Tokens carry 1-based line numbers. It never raises: bad
input (an unterminated string or comment) ends the token at the end of the line / file so one broken file cannot hide the rest.
"""

from __future__ import annotations

from dataclasses import dataclass

KEYWORDS = frozenset("and break do else elseif end false for function if in local nil not or repeat return then true until while".split())
OPS3 = ("...", "..=", "//=")
OPS2 = ("..", "==", "~=", "<=", ">=", "//", "+=", "-=", "*=", "/=", "%=", "^=", "->", "::")


@dataclass(frozen=True)
class Token:
    kind: str  # name | keyword | number | string | comment | op
    text: str
    line: int
    end_line: int
    pos: int  # offset of the first character
    end: int  # offset just past the last character

    def is_op(self, *texts: str) -> bool:
        return self.kind == "op" and self.text in texts

    def is_kw(self, *texts: str) -> bool:
        return self.kind == "keyword" and self.text in texts

    def is_name(self, *texts: str) -> bool:
        return self.kind == "name" and (not texts or self.text in texts)


def _long_bracket_level(src: str, i: int) -> int | None:
    """If src[i:] starts a long bracket ``[=*[``, return the number of ``=``; else None."""
    if i >= len(src) or src[i] != "[":
        return None
    j = i + 1
    while j < len(src) and src[j] == "=":
        j += 1
    return (j - i - 1) if j < len(src) and src[j] == "[" else None


def _read_long(src: str, i: int, level: int) -> int:
    """Return the offset just past the closing bracket of a long bracket starting at i (or len(src) if unterminated)."""
    close = "]" + "=" * level + "]"
    k = src.find(close, i + level + 2)
    return len(src) if k < 0 else k + len(close)


def tokenize(src: str) -> list[Token]:
    toks: list[Token] = []
    n = len(src)
    i = 0
    line = 1

    def add(kind: str, start: int, end: int, start_line: int) -> None:
        text = src[start:end]
        toks.append(Token(kind, text, start_line, start_line + text.count("\n"), start, end))

    while i < n:
        c = src[i]
        if c == "\n":
            line += 1
            i += 1
        elif c in " \t\r\f\v":
            i += 1
        elif c == "-" and src.startswith("--", i):
            start, sl = i, line
            level = _long_bracket_level(src, i + 2)
            if level is not None:
                end = _read_long(src, i + 2, level)
            else:
                end = src.find("\n", i)
                end = n if end < 0 else end
            add("comment", start, end, sl)
            line += src.count("\n", start, end)
            i = end
        elif c in "'\"":
            start, sl = i, line
            j = i + 1
            while j < n:
                if src[j] == "\\" and j + 1 < n:
                    if src[j + 1] == "\n":
                        line += 1
                    j += 2
                    continue
                if src[j] == c or src[j] == "\n":
                    break
                j += 1
            end = j + 1 if j < n and src[j] == c else j  # unterminated: stop at the newline
            add("string", start, end, sl)
            i = end
        elif c == "`":
            start, sl = i, line
            j = i + 1
            depth = 0
            while j < n:
                ch = src[j]
                if ch == "\\" and j + 1 < n:
                    j += 2
                    continue
                if ch == "{":
                    depth += 1
                elif ch == "}" and depth:
                    depth -= 1
                elif ch == "`" and depth == 0:
                    break
                j += 1
            end = min(j + 1, n)
            add("string", start, end, sl)
            line += src.count("\n", start, end)
            i = end
        elif c == "[" and _long_bracket_level(src, i) is not None:
            start, sl = i, line
            end = _read_long(src, i, _long_bracket_level(src, i))
            add("string", start, end, sl)
            line += src.count("\n", start, end)
            i = end
        elif c.isdigit() or (c == "." and i + 1 < n and src[i + 1].isdigit()):
            start = i
            if c == "0" and i + 1 < n and src[i + 1] in "xXbB":
                i += 2
                while i < n and (src[i].isalnum() or src[i] == "_"):
                    i += 1
            else:
                while i < n and (src[i].isdigit() or src[i] in "._"):
                    if src[i] == "." and src.startswith("..", i):
                        break
                    i += 1
                if i < n and src[i] in "eE":
                    j = i + 1
                    if j < n and src[j] in "+-":
                        j += 1
                    if j < n and src[j].isdigit():
                        i = j
                        while i < n and src[i].isdigit():
                            i += 1
            add("number", start, i, line)
        elif c.isalpha() or c == "_":
            start = i
            while i < n and (src[i].isalnum() or src[i] == "_"):
                i += 1
            word = src[start:i]
            add("keyword" if word in KEYWORDS else "name", start, i, line)
        else:
            if src[i:i + 3] in OPS3:
                text = src[i:i + 3]
            elif src[i:i + 2] in OPS2:
                text = src[i:i + 2]
            else:
                text = c
            add("op", i, i + len(text), line)
            i += len(text)
    return toks


def code_tokens(toks: list[Token]) -> list[Token]:
    return [t for t in toks if t.kind != "comment"]


def mask(src: str, toks: list[Token] | None = None) -> str:
    """The source with every comment and string replaced by spaces (newlines kept), so offsets and line numbers still line up.

    Quote characters are kept for strings (``""``), so ``f("a")`` becomes ``f(" ")`` and the call shape survives."""
    toks = tokenize(src) if toks is None else toks
    out = list(src)
    for t in toks:
        if t.kind == "comment":
            lo, hi = t.pos, t.end
        elif t.kind == "string":
            lo, hi = t.pos + 1, max(t.pos + 1, t.end - 1)
        else:
            continue
        for k in range(lo, hi):
            if out[k] != "\n":
                out[k] = " "
    return "".join(out)


def string_value(tok: Token) -> str:
    """The text of a string token without delimiters (escapes left as written). Good enough for names and keys."""
    t = tok.text
    if t.startswith("["):
        lvl = _long_bracket_level(t, 0) or 0
        return t[lvl + 2: len(t) - lvl - 2]
    if len(t) >= 2 and t[0] == t[-1] and t[0] in "'\"`":
        return t[1:-1]
    return t[1:]
