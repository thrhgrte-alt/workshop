"""Never store a secret: attribute keys and string values that look like keys or tokens are skipped (rules/secrets.yaml). Heuristics, not a guarantee."""

from __future__ import annotations

import re
from pathlib import Path

import yaml

from .util import split_tokens

ROOT = Path(__file__).resolve().parents[2]
_WORD_OPAQUE = re.compile(r"[A-Za-z0-9+/=_-]+")


class SecretFilter:
    def __init__(self, rules: dict):
        self.key_tokens = {t.lower() for t in rules.get("key_name_tokens", [])}
        self.key_compact = {re.sub(r"[^a-z0-9]", "", t.lower()) for t in rules.get("key_name_tokens", [])}
        self.key_exact = {t.lower() for t in rules.get("key_name_exact", [])}
        self.patterns = [re.compile(p) for p in rules.get("value_patterns", [])]
        self.opaque_min = int(rules.get("long_opaque_min_chars", 32))

    @classmethod
    def load(cls, root: Path | str | None = None) -> "SecretFilter":
        path = Path(root or ROOT) / "rules" / "secrets.yaml"
        return cls(yaml.safe_load(path.read_text(encoding="utf-8")) or {})

    def key_is_secret(self, key: str) -> bool:
        low = str(key).lower()
        if low in self.key_exact:
            return True
        compact = re.sub(r"[^a-z0-9]", "", low)
        if compact in self.key_compact:
            return True
        return any(t in self.key_tokens for t in split_tokens(str(key)) + [low])

    def value_is_secret(self, value: object) -> bool:
        if not isinstance(value, str):
            return False
        v = value.strip()
        if any(p.search(v) for p in self.patterns):
            return True
        for word in v.split():
            if len(word) >= self.opaque_min and _WORD_OPAQUE.fullmatch(word) and re.search(r"\d", word) and re.search(r"[A-Za-z]", word):
                return True
        return False

    def attr_is_secret(self, key: str, value: object) -> bool:
        return self.key_is_secret(key) or self.value_is_secret(value)


_DEFAULT: SecretFilter | None = None


def default_filter() -> SecretFilter:
    global _DEFAULT
    if _DEFAULT is None:
        _DEFAULT = SecretFilter.load()
    return _DEFAULT


def luau_key_patterns(flt: SecretFilter) -> tuple[list[str], list[str]]:
    """(substring tokens, exact keys) for the Luau-side pre-filter: keys are lowercased and stripped of non-alphanumerics before the test."""
    return sorted({re.sub(r"[^a-z0-9]", "", t) for t in flt.key_tokens}), sorted(flt.key_exact)
