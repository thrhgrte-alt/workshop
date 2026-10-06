"""Tokenizer, structure, detectors, backends, learning, ruleset and patches, with hand-computed expectations."""
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from luaurev.domain import backends as B
from luaurev.domain import detectors as D
from luaurev.domain import learning, patch as P, ruleset as RS
from luaurev.domain.lexer import mask, string_value, tokenize
from luaurev.domain.review import analyse_source, find_cycles, get_rules
from luaurev.domain.structure import Analysis

PRE = "--!strict\n"


def rules_of(project):
    return get_rules(project)[0]


def ids(project, code, strict=False, context=None, name="x.server.luau"):
    found, _ = analyse_source(code, name, rules_of(project), strict=strict, context=context)
    return sorted(f["rule_id"] for f in found)


# ---------------------------------------------------------------- lexer and structure
def test_mask_keeps_offsets_and_blanks_comments_and_strings():
    src = 'local s = "wait(1)" -- wait(2)\nwait(3)'
    m = mask(src)
    assert len(m) == len(src) and m.count("\n") == 1
    assert "wait(3)" in m and "wait(1)" not in m and "wait(2)" not in m


def test_long_string_values_and_levels():
    t = tokenize("x = [==[a]]b]==]")[2]
    assert t.kind == "string" and string_value(t) == "a]]b"


def test_block_matching_handles_if_expressions():
    a = Analysis.of("local x = if a then 1 else 2\nif y then z() end\nlocal f = function() return if q then 1 else if r then 2 else 3 end")
    assert [b.kind for b in a.blocks] == ["if", "function"] and a.balanced


def test_chain_before_and_callbacks():
    a = Analysis.of("workspace:FindFirstChild('x').Touched:Connect(function(hit) end)")
    c = next(c for c in a.calls if c.method == "Connect")
    assert c.callee == "workspace:FindFirstChild().Touched:Connect" and c.receiver == "workspace:FindFirstChild().Touched"
    assert a.callbacks()[0][2] == "Touched"


def test_file_kind_from_name_and_folder():
    assert Analysis.of("", "a/b.server.luau").file_kind() == "server"
    assert Analysis.of("", "a/StarterGui/b.luau").file_kind() == "client"
    assert Analysis.of("", "a/ReplicatedStorage/b.luau").file_kind() == "shared"
    assert Analysis.of("", "b.luau").file_kind() is None and Analysis.of("", "b.luau", "client").file_kind() == "client"


# ---------------------------------------------------------------- detectors
@pytest.mark.parametrize("code,expected", [
    ("wait(1)", ["API001"]), ("task.wait(1)", []), ("local s = 'wait(1)'", []), ("-- wait(1)", []), ("x.wait(1)", []), ("spawn(f)", ["API002"]), ("delay(1, f)", ["API003"]),
    ("local p = Instance.new('Part', workspace)", ["API004"]), ("local p = Instance.new('Part')", []), ("local t = tick()", ["API009"]), ("local n = table.getn(t)", ["API010"]),
    ("x.Touched:connect(f)", ["API008"]), ("loadstring('x')()", ["SEC004"]), ("local s = 'loadstring(x)'", []), ("require(123456)", ["SEC005"]),
    ("require(script.Parent.Mod)", []), ("pcall(f)", ["COR001"]), ("local ok = pcall(f)", []),
])
def test_one_liners(project, code, expected):
    assert ids(project, PRE + code + "\n") == expected


def test_remote_validation_forms(project):
    base = PRE + "r.OnServerEvent:Connect(function(player, a)\n\tlocal cooldown = 1\n%s\nend)\n"
    assert ids(project, base % "\tprint(a)") == ["SEC001"]
    for guard in ("\tif typeof(a) ~= 'string' then return end print(a)", "\tassert(type(a) == 'number') print(a)", "\tlocal n = math.clamp(a, 1, 5) print(n)",
                  "\tif not a then return end print(a)", "\tif a:IsA('Part') then print(a) end"):
        assert ids(project, base % guard) == [], guard


def test_sensitive_parameter_names(project):
    assert D.is_sensitive("price") and D.is_sensitive("itemPrice") and D.is_sensitive("damageAmount") and D.is_sensitive("amount")
    assert not D.is_sensitive("itemId") and not D.is_sensitive("target") and not D.is_sensitive("slot")


def test_datastore_pcall_retry_and_loop_rules(project):
    ds = PRE + "local store = game:GetService('DataStoreService'):GetDataStore('x')\n"
    assert ids(project, ds + "local v = store:GetAsync('k')\n") == ["DAT001"]
    assert ids(project, ds + "local ok, v = pcall(function() return store:GetAsync('k') end)\n") == ["DAT002"]
    loop = "for i = 1, 3 do\n local ok, v = pcall(function() return store:GetAsync('k') end)\n if ok then break end\n task.wait(i)\nend\n"
    assert ids(project, ds + loop) == []
    assert ids(project, PRE + "local ok = pcall(function() return http:GetAsync('k') end)\n") == []  # no DataStore in sight
    assert ids(project, PRE + "local ok = HttpService:GetAsync('u')\n") == []  # HttpService is not a DataStore


def test_dat005_threshold_comes_from_the_rule_file(project):
    code = PRE + ("local store = game:GetService('DataStoreService'):GetDataStore('x')\nwhile true do\n task.wait(%s)\n"
                  " local ok = pcall(function() store:UpdateAsync('k', function(o) return o end) end)\nend\n")
    assert "DAT005" in ids(project, code % "5") and "DAT005" not in ids(project, code % "120")


def test_receipt_returns(project):
    mk = lambda body: PRE + "game:GetService('MarketplaceService').ProcessReceipt = function(r)\n" + body + "\nend\n"
    assert ids(project, mk("return true")) == ["DAT007"]
    assert ids(project, mk("return Enum.ProductPurchaseDecision.NotProcessedYet")) == []
    assert ids(project, mk("local f = function() return true end f() return Enum.ProductPurchaseDecision.PurchaseGranted")) == ["DAT008"]


def test_perf001_confidence_depends_on_calls(project):
    found, _ = analyse_source(PRE + "while true do\n step()\nend\n", "x.luau", rules_of(project))
    assert [(f["rule_id"], f["confidence"]) for f in found] == [("PERF001", "medium")]
    found, _ = analyse_source(PRE + "while true do\n n += 1\nend\n", "x.luau", rules_of(project))
    assert [(f["rule_id"], f["confidence"]) for f in found] == [("PERF001", "high")]


def test_perf002_supersedes_api001_on_the_same_line(project):
    assert ids(project, PRE + "while true do\n wait(1)\nend\n") == ["PERF002"]
    assert ids(project, PRE + "wait(1)\nwhile true do\n wait(1)\nend\n") == ["API001", "PERF002"]


def test_cycles_hand_computed():
    assert find_cycles({"A": {"B"}, "B": {"A"}, "C": {"A"}}) == [["A", "B"]]
    assert find_cycles({"A": {"A"}}) == [["A"]]
    assert find_cycles({"A": {"B"}, "B": {"C"}}) == []


def test_clean_review_of_strict_directive_variants(project):
    assert ids(project, "print(1)\n") == ["STR001"]
    assert ids(project, "--!nocheck\nprint(1)\n") == ["STR001"]
    assert ids(project, "-- c\n--!strict\nprint(1)\n") == []


# ---------------------------------------------------------------- backends: parsing
def test_parse_luau_analyze_and_garbage():
    f, ignored = B.parse_luau_analyze("a.lua(1,2): TypeError: x\nb.lua(3): SyntaxError: y\nc.lua(4,5-9,10): LocalUnused: z\nnoise\n")
    assert [(x.line, x.col, x.rule_id, x.severity) for x in f] == [(1, 2, "luau-analyze:TypeError", "error"), (3, 0, "luau-analyze:SyntaxError", "error"), (4, 5, "luau-analyze:LocalUnused", "warning")]
    assert ignored == 1


def test_parse_selene_json_and_quiet():
    f, _ = B.parse_selene('{"type":"Diagnostic","severity":"Error","code":"c","message":"m","primary_label":{"filename":"a.lua","span":{"start_line":0,"start_column":2}}}\n'
                          'a.lua:3:1: note[n]: hint\n{broken')
    assert [(x.line, x.col, x.severity) for x in f] == [(1, 3, "error"), (3, 1, "info")]


def test_parse_stylua():
    f, _ = B.parse_stylua_check("Diff in a.lua at line 3:\n-x\n+y\nDiff in a.lua at line 9:\n", 1, ["a.lua"])
    assert [x.line for x in f] == [3, 9]
    assert B.parse_stylua_check("", 0, ["a.lua"])[0] == []
    assert len(B.parse_stylua_check("???", 1, ["a.lua", "b.lua"])[0]) == 2


# ---------------------------------------------------------------- backends: stub executables on a temp PATH
def run_with_path(monkeypatch, dirs, fn):
    monkeypatch.setenv("PATH", os.pathsep.join(str(d) for d in dirs))
    return fn()


def test_stub_backends_run_without_a_shell(monkeypatch, stubs, tmp_path):
    f = tmp_path / "a.lua"
    f.write_text("print(1)\n")
    stubs("luau-analyze", stderr=f"{f}(2,3): TypeError: boom\n", exit_code=1)
    seen = {}
    real = subprocess.run

    def spy(cmd, **kw):
        seen.update(cmd=cmd, **kw)
        return real(cmd, **kw)

    monkeypatch.setattr(B.subprocess, "run", spy)
    res, grouped = run_with_path(monkeypatch, [stubs.dir, "/bin", "/usr/bin"], lambda: B.run_backend("luau-analyze", [str(f)], timeout=5))
    assert res.status == "ran" and grouped[str(f)][0].line == 2
    assert isinstance(seen["cmd"], list) and seen["shell"] is False and seen["timeout"] == 5 and seen["stdin"] == subprocess.DEVNULL


def test_missing_backend_is_reported_missing(monkeypatch, tmp_path):
    empty = tmp_path / "empty"
    empty.mkdir()
    res, grouped = run_with_path(monkeypatch, [empty], lambda: B.run_backend("selene", ["/x/a.lua"]))
    assert res.status == "missing" and "NOT run" in res.message and grouped == {}
    assert B.available("selene") is None if shutil.which("selene") is None else True


def test_backend_timeout_and_crash_and_unparsed(monkeypatch, stubs, tmp_path):
    f = tmp_path / "a.lua"
    f.write_text("x\n")
    d = stubs.dir
    (d / "selene").write_text("#!/bin/sh\nexec sleep 5\n")
    (d / "selene").chmod(0o755)
    r, _ = run_with_path(monkeypatch, [d, "/bin", "/usr/bin"], lambda: B.run_backend("selene", [str(f)], timeout=0.3))
    assert r.status == "timeout"
    stubs("luau-analyze", stderr="fatal", exit_code=3)
    r, _ = run_with_path(monkeypatch, [d, "/bin", "/usr/bin"], lambda: B.run_backend("luau-analyze", [str(f)]))
    assert r.status == "error" and r.exit_code == 3
    stubs("luau-analyze", stdout="weird text\n", exit_code=1)
    r, _ = run_with_path(monkeypatch, [d, "/bin", "/usr/bin"], lambda: B.run_backend("luau-analyze", [str(f)]))
    assert r.status == "unparsed"


def test_backend_paths_are_mapped_back_even_when_relative(monkeypatch, stubs, tmp_path):
    f = tmp_path / "sub" / "a.lua"
    f.parent.mkdir()
    f.write_text("x\n")
    stubs("selene", stdout="a.lua:2:1: warning[unused_variable]: x\n", exit_code=1)
    _, grouped = run_with_path(monkeypatch, [stubs.dir, "/bin", "/usr/bin"], lambda: B.run_backend("selene", [str(f)], cwd=str(tmp_path)))
    assert list(grouped) == [str(f)]


@pytest.mark.skipif(not shutil.which("luau-analyze"), reason="real luau-analyze not installed")
def test_real_luau_analyze_if_present(tmp_path):
    f = tmp_path / "a.lua"
    f.write_text("--!strict\nlocal x: number = 'a'\n")
    res, grouped = B.run_backend("luau-analyze", [str(f)], timeout=30)
    assert res.status in ("ran", "unparsed") and (grouped or res.status == "unparsed")


@pytest.mark.skipif(not shutil.which("selene"), reason="real selene not installed")
def test_real_selene_if_present(tmp_path):
    f = tmp_path / "a.lua"
    f.write_text("local x = 1\n")
    res, _ = B.run_backend("selene", [str(f)], timeout=30, cwd=str(tmp_path))
    assert res.status in ("ran", "unparsed", "error")


@pytest.mark.skipif(not shutil.which("stylua"), reason="real stylua not installed")
def test_real_stylua_if_present(tmp_path):
    f = tmp_path / "a.lua"
    f.write_text("local x    =   1\n")
    res, grouped = B.run_backend("stylua", [str(f)], timeout=30, cwd=str(tmp_path))
    assert res.status == "ran" and grouped


# ---------------------------------------------------------------- learning
def test_normalise_and_similarity_hand_computed():
    assert learning.normalise("player.Coins.Value -= price") == ["ID", ".", "Coins", ".", "Value", "-=", "ID"]
    assert learning.similarity(learning.normalise("player.Coins.Value -= price"), learning.normalise("player.Coins.Value += price")) == pytest.approx(3 / 7)
    assert learning.similarity(learning.normalise("a = f(x) -- c"), learning.normalise("b = f(y)")) == 1.0
    assert learning.similarity([], ["a"]) == 0.0
    assert learning.shingles(["a", "b", "c", "d"], 3) == {("a", "b", "c"), ("b", "c", "d")}


def test_confidence_steps_and_floor():
    from luaurev.domain.rules import lower

    assert lower("high") == "medium" and lower("high", 2) == "low" and lower("low", 5) == "low"


def test_apply_learning_two_matches_two_steps_and_other_place_ignored():
    f = {"source": "own", "file": "a", "line": 1, "rule_id": "R", "confidence": "high", "place": "lobby"}
    lines = {"a": ["x.Remote:Fire(a, b, c)", ""]}
    mk = lambda i, code, place=None, g=False: {"id": i, "rule_id": "R", "tokens": learning.normalise(code), "window_lines": 1, "reason": "r", "place_id": place, "global": g}
    rows = [mk("1", "x.Remote:Fire(a, b, c)"), mk("2", "y.Remote:Fire(q, r, s)"), mk("3", "z.Remote:Fire(q, r, s)", place="stage")]
    out = learning.apply_learning([f], lines, rows, 0.6, 2)
    assert f["confidence"] == "low" and len(f["learned"]) == 2 and out[0]["from"] == "high"  # row 3 belongs to another place
    g = {"source": "own", "file": "a", "line": 1, "rule_id": "R", "confidence": "high", "place": "lobby"}
    learning.apply_learning([g], lines, [mk("3", "z.Remote:Fire(q, r, s)", place="stage", g=True)], 0.6, 2)
    assert g["confidence"] == "medium"  # a global row applies in every place


def test_too_short_pattern_refused(tmp_path):
    with pytest.raises(ValueError, match="too short"):
        learning.FalsePositiveStore(tmp_path / "f.jsonl").make_row("R", "x", "a long enough reason")


# ---------------------------------------------------------------- ruleset
def test_ruleset_merge_apply_and_usage():
    g = {"suppressions": [{"rule": "A", "reason": "global reason ok"}], "disabled_rules": ["D"]}
    p = {"suppressions": [{"rule": "B", "file": "x.luau", "reason": "project reason ok"}], "severity_overrides": {"C": "error"}}
    m = RS.merge([("global", "g.yaml", RS.empty() | g), ("project", "p.yaml", RS.empty() | p)])
    fs = [{"rule_id": r, "file": "x.luau", "line": 1, "severity": "warning", "line_fingerprint": ""} for r in "ABCD"]
    kept, sup, dropped, usage = RS.apply(fs, m)
    assert [f["rule_id"] for f in kept] == ["C"] and kept[0]["severity"] == "error"
    assert {s["rule_id"]: s["suppressed_by"] for s in sup} == {"A": "global", "B": "project"} and [d["rule_id"] for d in dropped] == ["D"]
    assert [u["matched"] for u in usage] == [1, 1]


def test_ruleset_validation():
    assert RS.validate({"suppressions": [{"rule": "A", "reason": "no"}]}) and RS.validate({"suppressions": [{"reason": "x" * 20}]})
    assert RS.validate({"suppressions": [{"rule": "A", "reason": "long enough reason"}]}, {"A"}) == []
    assert RS.validate({"severity_overrides": {"A": "loud"}})


# ---------------------------------------------------------------- patches
def test_patch_apply_edits_and_diff_roundtrip(project):
    src = "--!strict\nwait(1)\nlocal p = Instance.new('Part', workspace)\n"
    for rid, expect in (("API001", "task.wait(1)"), ("API004", "p.Parent = workspace")):
        res = P.suggest(src, "x.luau", rid, rules_of(project))
        assert res["supported"] and expect in res["diff"] and res["hits_after"] == 0
    assert P.apply_edits("abc", [(1, 2, "XY")]) == "aXYc"


@pytest.mark.skipif(not shutil.which("patch"), reason="patch utility not installed")
def test_patch_diff_applies_with_the_patch_tool(project, tmp_path):
    src = "--!strict\nwait(1)\nprint(1)\n"
    res = P.suggest(src, "x.luau", "API001", rules_of(project))
    (tmp_path / "x.luau").write_text(src)
    (tmp_path / "p.diff").write_text(res["diff"])
    subprocess.run(["patch", "-p1", "-i", "p.diff"], cwd=tmp_path, check=True, capture_output=True)
    assert (tmp_path / "x.luau").read_text() == "--!strict\ntask.wait(1)\nprint(1)\n"
