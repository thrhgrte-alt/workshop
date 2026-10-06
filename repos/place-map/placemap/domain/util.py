"""Small shared helpers: tokens, paths, hashing, time, identifier validation. No guide-core imports here (the adapter is the only door)."""

from __future__ import annotations

import datetime as _dt
import hashlib
import os
import re
from functools import lru_cache
from typing import Iterable

# identifiers are validated with fullmatch (never match + "$": that accepts a trailing newline, which would break out of generated Luau)
PLACE_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}")
SEGMENT_BAD = re.compile(r"[\x00-\x1f]")
SEP = "/"
PLACE_SEP = "::"
_CAMEL1 = re.compile(r"([a-z0-9])([A-Z])")
_CAMEL2 = re.compile(r"([A-Z]+)([A-Z][a-z])")
_WORD = re.compile(r"[A-Za-z]+|[0-9]+")


def is_place_id(value: object) -> bool:
    return isinstance(value, str) and PLACE_ID_RE.fullmatch(value) is not None


def norm_token(t: str) -> str:
    """Lowercase token with a trailing plural 's' ignored (same rule on both sides of every match)."""
    t = t.lower()
    return t[:-1] if len(t) > 3 and t.endswith("s") and not t.endswith("ss") else t


@lru_cache(maxsize=200_000)
def _split_cached(text: str) -> tuple[str, ...]:
    s = _CAMEL2.sub(r"\1 \2", _CAMEL1.sub(r"\1 \2", text))
    return tuple(norm_token(w) for w in _WORD.findall(s))


def split_tokens(text: str) -> list[str]:
    """camelCase, underscores, digits and punctuation split; lowercase, plural-normalised, order kept, duplicates kept."""
    if not isinstance(text, str):
        return []
    return list(_split_cached(text))


def token_set(text: str, *, drop_numbers: bool = False) -> set[str]:
    toks = split_tokens(text)
    return {t for t in toks if not (drop_numbers and t.isdigit())}


def jaccard(a: set[str], b: set[str]) -> float:
    if not a and not b:
        return 1.0
    return len(a & b) / len(a | b)


def escape_segment(name: str) -> str:
    """A path segment: '/' and '%' in an instance name are percent-escaped (the collector Luau does the same)."""
    return name.replace("%", "%25").replace("/", "%2F")


def unescape_segment(seg: str) -> str:
    return seg.replace("%2F", "/").replace("%25", "%")


def join_path(*segs: str) -> str:
    return SEP.join(segs)


def split_path(path: str) -> list[str]:
    return [s for s in path.split(SEP) if s != ""]


def parent_path(path: str) -> str:
    return path.rsplit(SEP, 1)[0] if SEP in path else ""


def leaf_name(path: str) -> str:
    return unescape_segment(path.rsplit(SEP, 1)[-1])


def is_under(path: str, root: str) -> bool:
    return path == root or path.startswith(root + SEP)


def clean_path_input(path: str) -> str:
    """Accept ``place::A/B``, ``A/B``, ``A.B`` (dots only when no slash is present) and ``game.A.B``; returns ``A/B``. The place prefix is checked by the caller."""
    p = path.strip()
    if PLACE_SEP in p:
        p = p.split(PLACE_SEP, 1)[1]
    if p.startswith("game."):
        p = p[5:]
    if SEP not in p and "." in p:
        p = p.replace(".", SEP)
    return p.strip(SEP)


def prefixed(place_id: str, path: str) -> str:
    return f"{place_id}{PLACE_SEP}{path}"


def content_hash(source: str) -> str:
    """sha256 of the source with CRLF/CR line endings turned into LF and trailing whitespace removed from every line. Same text in two places gives the same hash."""
    text = source.replace("\r\n", "\n").replace("\r", "\n")
    text = "\n".join(line.rstrip() for line in text.split("\n"))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def fingerprint(source: str) -> str:
    """The quick fingerprint the generated Luau computes without a hash library: ``<bytes>:<checksum hex>`` with checksum = (checksum * 31 + byte) mod 4294967291 over the UTF-8 bytes."""
    data = source.encode("utf-8")
    h = 0
    for b in data:
        h = (h * 31 + b) % 4294967291
    return f"{len(data)}:{h:x}"


def utcnow() -> _dt.datetime:
    """The current time. ``PLACEMAP_NOW`` (an ISO timestamp) overrides it so tests and evals are deterministic; real use never sets it."""
    fixed = os.environ.get("PLACEMAP_NOW")
    return parse_iso(fixed) if fixed else _dt.datetime.now(_dt.timezone.utc)


def now_iso() -> str:
    return utcnow().isoformat(timespec="seconds")


def iso_from_unix(ts: float) -> str:
    return _dt.datetime.fromtimestamp(float(ts), _dt.timezone.utc).isoformat(timespec="seconds")


def parse_iso(s: str) -> _dt.datetime:
    return _dt.datetime.fromisoformat(s.replace("Z", "+00:00"))


def age_hours(taken_at: str, now: _dt.datetime | None = None) -> float:
    now = now or utcnow()
    return max(0.0, (now - parse_iso(taken_at)).total_seconds() / 3600.0)


def median(values: Iterable[float]) -> float:
    xs = sorted(values)
    if not xs:
        return 0.0
    mid = len(xs) // 2
    return float(xs[mid]) if len(xs) % 2 else (xs[mid - 1] + xs[mid]) / 2.0


def clamp(x: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, x))
