import pytest

from econbal.domain import luau


@pytest.mark.parametrize("fn,arg", [("validate_path", "A.B\n"), ("validate_name", "Config\n")])
def test_trailing_newline_is_rejected(fn, arg):
    """A '$' anchor accepts a trailing newline; a newline could end a Luau string literal early."""
    with pytest.raises(ValueError):
        getattr(luau, fn)(arg)
