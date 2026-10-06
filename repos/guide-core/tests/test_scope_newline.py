import pytest

from guide_core import scope


@pytest.mark.parametrize("pid", ["demo\n", "demo\n\n"])
def test_trailing_newline_ids_are_refused(pid):
    """'$' matches before a trailing newline; ids reach generated Luau and file paths, so fullmatch is required."""
    with pytest.raises(Exception):
        scope.Scope(pid)
