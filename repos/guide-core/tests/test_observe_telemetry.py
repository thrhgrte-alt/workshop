import json
import re
from pathlib import Path

import pytest

from guide_core import observe, telemetry
from guide_core.mcpkit import ToolSpec
from guide_core.scope import Scope, ScopeError

A, B = Scope("proj-a", "main"), Scope("proj-b", "main")


@pytest.mark.parametrize("text,gone", [
    ("opened /home/alice/games/obby/Main.luau", "alice"),
    ("saved C:\\Users\\Bob\\Documents\\game.rbxl ok", "Bob"),
    ("see ~/secrets/notes.txt", "secrets"),
    ("share \\\\fileserver\\builds\\x.fbx", "fileserver"),
    ("api_key=abcd1234efgh5678", "abcd1234"),
    ("password: hunter2hunter2", "hunter2"),
    ("Authorization: Bearer abcdefghij1234567890", "abcdefghij"),
    ("token ghp_abcdefghijklmnopqrstuvwxyz0123456789", "ghp_abc"),
    ("key sk-abcdefghijklmnopqrstuvwxyz", "sk-abc"),
    ("_|WARNING:-DO-NOT-SHARE-THIS.--abcdef123456", "abcdef123456"),
    ("jwt eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.abcdefghijklmnop", "eyJhbGci"),
    ("mail me at someone@example.com", "someone"),
    ("hash 0123456789abcdef0123456789abcdef01234567", "0123456789abcdef0123"),
    ("-----BEGIN RSA PRIVATE KEY-----\nMIIEabc\n-----END RSA PRIVATE KEY-----", "MIIE"),
])
def test_redaction_removes_sensitive_text(text, gone):
    out = observe.redact(text)
    assert gone not in out and ("<path>" in out or "<secret>" in out or "<email>" in out)


@pytest.mark.parametrize("text", ["rule SEC003 fired at line 42", "price 100/200 ratio a/b", "the vendor sells 3 items", "Workspace.Door.Open = true", "version 1.2.3"])
def test_redaction_leaves_ordinary_text_alone(text):
    assert observe.redact(text) == text


def test_redact_walks_structures_and_keeps_non_strings():
    out = observe.redact({"a": ["/home/x/y/z.lua", 5, None], "n": 1.5, "k": {"p": "email a@b.co"}})
    assert out == {"a": ["<path>", 5, None], "n": 1.5, "k": {"p": "email <email>"}}


def test_log_run_redacts_and_requires_scope(tmp_path):
    log = observe.RunLog(tmp_path / "obs" / "runs.jsonl")
    rid = log.log_run(request="check /home/alice/x/Shop.luau token=abc123abc123", scope=A, tools=["check"], findings={"path": "/home/alice/x"}, output_chars=120, seconds=0.5)
    row = log.rows(kind="run")[0]
    assert row["run_id"] == rid and "alice" not in json.dumps(row) and "abc123abc123" not in json.dumps(row)
    assert (row["project_id"], row["place_id"], row["schema"]) == ("proj-a", "main", 1) and row["output_chars"] == 120
    with pytest.raises(ScopeError):
        log.log_run(request="x", scope=None)
    with pytest.raises(ValueError):
        log.log_run(request="  ", scope=A)
    with pytest.raises(ValueError, match="signal"):
        log.log_run(request="x", scope=A, signals=[{"kind": "override"}])


def test_log_is_append_only_and_actions_are_new_rows(tmp_path):
    log = observe.RunLog(tmp_path / "r.jsonl")
    r1 = log.log_run(request="one", scope=A)
    before = (tmp_path / "r.jsonl").read_bytes()
    log.log_action(r1, "reject", note="wrong at /home/bob/p", targets=["rule:X"])
    after = (tmp_path / "r.jsonl").read_bytes()
    assert after.startswith(before) and len(after.splitlines()) == 2
    run = log.runs()[0]
    assert run["user_action"] == "reject" and run["user_targets"] == ["rule:X"] and "bob" not in after.decode()
    with pytest.raises(ValueError):
        log.log_action(r1, "maybe")
    with pytest.raises(ValueError, match="unknown run_id"):
        log.log_action("obs-nope", "accept")


def test_runs_are_filtered_by_scope(tmp_path):
    log = observe.RunLog(tmp_path / "r.jsonl")
    log.log_run(request="a-main", scope=A)
    log.log_run(request="b-main", scope=B)
    log.log_run(request="a-alt", scope=Scope("proj-a", "alt"))
    log.log_run(request="a-project", scope=Scope("proj-a"))
    log.log_run(request="glob", scope=Scope.make_global())
    names = lambda sc, **kw: sorted(r["request"] for r in log.runs(sc, **kw))  # noqa: E731
    assert names(A) == ["a-main", "a-project", "glob"]
    assert names(A, include_global=False) == ["a-main", "a-project"]
    assert names(B) == ["b-main", "glob"] and len(log.runs()) == 5


def test_torn_line_is_skipped_not_repaired(tmp_path):
    f = tmp_path / "r.jsonl"
    log = observe.RunLog(f)
    log.log_run(request="ok", scope=A)
    with f.open("a") as fh:
        fh.write('{"kind": "run", "run_id": "obs-torn", "requ')
    assert [r["request"] for r in log.rows()] == ["ok"]


def test_observe_module_has_no_network_code():
    src = Path(observe.__file__).read_text()
    imports = re.findall(r"^\s*(?:from|import)\s+([A-Za-z_][\w.]*)", src, re.M)
    assert not {"socket", "urllib", "http", "requests", "httpx", "ssl", "smtplib", "ftplib", "aiohttp"} & {i.split(".")[0] for i in imports}


# --- telemetry -----------------------------------------------------------------------------------------------------------
def test_output_chars_is_compact_json_length():
    assert telemetry.output_chars({"a": [1, 2]}) == len('{"a":[1,2]}') and telemetry.output_chars("hello") == 5


def test_instrument_records_size_and_time_without_changing_behaviour(tmp_path):
    t = telemetry.Telemetry(tmp_path / "t.jsonl")

    def big(n: int = 3) -> dict:
        return {"v": "x" * n}

    spec = t.instrument([ToolSpec("big", big, "d", group="rare", read_only=False)])[0]
    assert spec.group == "rare" and spec.read_only is False and spec.description == "d"
    assert spec.fn(n=10) == {"v": "x" * 10} and spec.fn(n=2) == {"v": "xx"}
    rows = t.rows()
    assert [r["chars"] for r in rows] == [len('{"v":"' + "x" * 10 + '"}'), len('{"v":"xx"}')] and all(r["seconds"] >= 0 and r["ok"] for r in rows)
    import inspect

    assert "n" in inspect.signature(spec.fn).parameters  # FastMCP builds the schema from this


def test_failed_calls_are_counted_as_errors(tmp_path):
    t = telemetry.Telemetry(tmp_path / "t.jsonl")

    def boom() -> dict:
        raise RuntimeError("x")

    with pytest.raises(RuntimeError):
        t.wrap("boom", boom)()
    s = t.summary()["boom"]
    assert s["calls"] == 1 and s["errors"] == 1 and s["chars_median"] == 0


def test_summary_per_tool_and_budgets(tmp_path):
    t = telemetry.Telemetry(tmp_path / "t.jsonl")
    for c in (100, 300, 200):
        t.record("a", c, 0.01)
    t.record("b", 4000, 0.5)
    s = t.summary()
    assert s["a"]["chars_median"] == 200 and s["a"]["chars_max"] == 300 and s["a"]["approx_tokens_median"] == 50 and s["a"]["calls"] == 3
    assert s["b"]["seconds_max"] == 0.5
    assert telemetry.check_budgets(s, {"a": 250, "b": 1000, "zzz": 1}) == ["b: median 4000 chars > budget 1000"]


def test_telemetry_stores_no_arguments_or_results(tmp_path):
    t = telemetry.Telemetry(tmp_path / "t.jsonl")
    t.wrap("x", lambda secret: {"out": secret})("hunter2-the-password")
    assert "hunter2" not in (tmp_path / "t.jsonl").read_text()
