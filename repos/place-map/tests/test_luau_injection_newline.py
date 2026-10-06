import pytest

from placemap.domain import collector, places
from placemap.guide_adapter import luau_safety, scope as S


def test_a_trailing_newline_never_reaches_generated_luau(project):
    for bad in ("Workspace\n", "Workspace\nrequire(1)", "\nWorkspace"):
        with pytest.raises(ValueError):
            collector.validate_root(bad)
    with pytest.raises(ValueError):
        luau_safety.validate_path("ReplicatedStorage.Config\n")
    with pytest.raises(ValueError):
        luau_safety.quote_string("a\nb")
    for bad_id in ("dive-and-mine\n", "demo_mine\n"):
        with pytest.raises(S.ScopeError):
            places.resolve(project, bad_id if bad_id.startswith("demo") else "demo_mine", None if bad_id.startswith("demo") else bad_id)
