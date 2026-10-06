"""Domain behaviour checked independently of the eval tasks: numbers are derived by hand or from first principles."""
import colorsys
import json
import os
import re
import sys
from pathlib import Path

import pytest
import yaml

from conceptai.domain import adapters, analysis, direction as D, lora, prompts, runs, synthetic

ENV = {"subject": "a ruined lighthouse", "kind": "environment"}
PROP = {"subject": "an iron lantern", "kind": "prop"}


def hue(hex_):
    r, g, b = (int(hex_[i:i + 2], 16) / 255 for i in (1, 3, 5))
    return colorsys.rgb_to_hsv(r, g, b)[0] * 360


def hue_gap(a, b):
    d = abs(hue(a) - hue(b)) % 360
    return min(d, 360 - d)


# --- directions -------------------------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("seed", range(6))
def test_directions_always_differ_on_several_axes(seed):
    res = D.generate_directions(ENV, 5, seed)
    assert res["min_axes_differing"] >= 3 and not res["warnings"]
    for i, a in enumerate(res["directions"]):
        for b in res["directions"][i + 1:]:
            assert D.axes_differing("environment", a, b) >= 3
            assert D.distance("environment", a, b) >= 3.0


def test_distance_is_a_weighted_hamming_distance():
    a = {"axes": {"silhouette": "a", "palette_scheme": "x", "lighting": "l", "composition": "c", "materials": "m", "mood": "d"}}
    b = {"axes": {**a["axes"], "palette_scheme": "y", "mood": "e"}}
    assert D.distance("environment", a, b) == D.WEIGHTS["environment"]["palette_scheme"] + D.WEIGHTS["environment"]["mood"] == 3.0
    assert D.distance("environment", a, a) == 0.0 and D.axes_differing("environment", a, b) == 2


def test_farthest_point_beats_taking_the_first_candidates():
    """The greedy choice must separate directions at least as well as an arbitrary pick of the same size."""
    res = D.generate_directions(ENV, 4, 7)
    arbitrary = []
    import itertools
    opts = {a: list(o) for a, o in D.AXES["environment"].items()}
    for combo in itertools.islice(itertools.product(*opts.values()), 4):
        arbitrary.append({"axes": dict(zip(opts, combo))})
    worst = min(D.distance("environment", x, y) for i, x in enumerate(arbitrary) for y in arbitrary[i + 1:])
    assert res["min_pairwise_distance"] > worst


def test_every_option_in_every_incompatible_pair_exists():
    for kind, pairs in D.INCOMPATIBLE.items():
        for a, oa, b, ob in pairs:
            assert oa in D.AXES[kind][a] and ob in D.AXES[kind][b], (kind, a, oa, b, ob)


def test_palette_schemes_follow_colour_theory():
    pal = D.palette_for("complementary", 30)
    assert 150 <= hue_gap(pal[0]["hex"], pal[2]["hex"]) <= 210  # accent roughly opposite the dominant
    ana = D.palette_for("analogous_warm", 30)
    assert hue_gap(ana[0]["hex"], ana[2]["hex"]) <= 60
    assert [p["role"] for p in pal] == ["dominant", "secondary", "accent", "shadow", "highlight"]
    # shadows are tinted and darker, highlights lighter than the dominant
    lum = lambda h: colorsys.rgb_to_hsv(*(int(h[i:i + 2], 16) / 255 for i in (1, 3, 5)))[2]
    assert lum(pal[3]["hex"]) < lum(pal[0]["hex"]) < lum(pal[4]["hex"])


def test_scheme_decides_hue_family_over_lighting():
    for seed in range(20):
        for d in D.generate_directions({**ENV, "locked": {"axes": {"palette_scheme": "analogous_warm"}}}, 3, seed)["directions"]:
            assert 0 <= hue(d["palette"][0]["hex"]) <= 80 or hue(d["palette"][0]["hex"]) >= 330, d["palette"][0]


def test_stable_ids_depend_only_on_axes():
    a = D.generate_directions(ENV, 3, 1)["directions"][0]
    again = [d for d in D.generate_directions(ENV, 8, 2)["directions"] if d["id"] == a["id"]]
    assert all(d["axes"] == a["axes"] for d in again)


# --- prompts ----------------------------------------------------------------------------------------------------------------------------
def test_direction_and_brief_kinds_must_match():
    d = D.generate_directions(PROP, 1, 0)["directions"][0]
    with pytest.raises(ValueError, match="direction is for kind"):
        prompts.build_prompt(ENV, d)


def test_prompt_is_deterministic_and_pure_text():
    d = D.generate_directions(ENV, 1, 0)["directions"][0]
    a, b = prompts.build_prompt(ENV, d), prompts.build_prompt(ENV, d)
    assert a == b and "{" not in a["positive"] and a["status"] == "concept_only"


def test_negative_prompt_has_no_duplicates():
    d = D.generate_directions(ENV, 1, 0)["directions"][0]
    neg = prompts.build_prompt({**ENV, "avoid": ["Watermark", "crowds", "crowds"]}, d, {"exclusions": ["watermark", "crowds"]})["negative"].split(", ")
    assert len({x.lower() for x in neg}) == len(neg)


# --- adapters ---------------------------------------------------------------------------------------------------------------------------
SIDECAR = ("import sys, pathlib\nfrom PIL import Image\nout = pathlib.Path(sys.argv[1])\n"
           "Image.new('RGB', (64, 64), (1, 2, 3)).save(out)\nout.with_suffix('.txt').write_text(pathlib.Path(sys.argv[2]).read_text() + '|' + sys.argv[3] + '|' + sys.argv[4])\n")


def run_script(tmp_path, body, **kw):
    script = tmp_path / "g.py"
    script.write_text(body)
    ad = adapters.CommandAdapter([sys.executable, str(script), "{out}", "{prompt_file}", "{seed}", "{width}"], model="m", **kw)
    return ad


def test_command_adapter_passes_prompt_by_file_and_never_uses_a_shell(tmp_path):
    nasty = "a lantern; touch HACKED $(echo hi) `id`"
    files = run_script(tmp_path, SIDECAR).generate({"prompt": nasty, "width": 128, "height": 64, "seed": 9, "stem": "x"}, tmp_path / "out")
    seen = Path(files[0]["path"]).with_suffix(".txt").read_text()
    assert seen == f"{nasty}|9|128"  # verbatim, uninterpreted
    assert not (tmp_path / "HACKED").exists() and not (tmp_path / "out" / "HACKED").exists() and not Path("HACKED").exists()


def test_command_adapter_seed_increments_per_image(tmp_path):
    files = run_script(tmp_path, SIDECAR).generate({"prompt": "p", "width": 64, "height": 64, "seed": 10, "count": 3, "stem": "s"}, tmp_path / "o")
    assert [f["seed"] for f in files] == [10, 11, 12]
    assert [Path(f["path"]).with_suffix(".txt").read_text().split("|")[1] for f in files] == ["10", "11", "12"]


def test_command_adapter_times_out(tmp_path):
    ad = run_script(tmp_path, "import time\ntime.sleep(5)\n", timeout=1)
    with pytest.raises(ValueError, match="timed out"):
        ad.generate({"prompt": "p", "width": 64, "height": 64}, tmp_path / "o")


def test_command_adapter_missing_program(tmp_path):
    with pytest.raises(ValueError, match="command not found"):
        adapters.CommandAdapter(["definitely-not-a-program-xyz", "{out}"]).generate({"prompt": "p", "width": 64, "height": 64}, tmp_path)


def test_command_adapter_needs_out_token():
    ok, why = adapters.CommandAdapter(["python", "x.py"]).available()
    assert not ok and "{out}" in why


def test_command_from_env_accepts_json_and_plain(monkeypatch):
    monkeypatch.setenv("CONCEPTAI_IMAGE_COMMAND", json.dumps(["python", "g.py", "{out}"]))
    monkeypatch.setenv("CONCEPTAI_IMAGE_MODEL", "my-model-1")
    ad = adapters.CommandAdapter.from_env()
    assert ad.argv == ["python", "g.py", "{out}"] and ad.model == "my-model-1" and ad.available()[0]
    monkeypatch.setenv("CONCEPTAI_IMAGE_COMMAND", "python g.py {out}")
    assert adapters.CommandAdapter.from_env().argv == ["python", "g.py", "{out}"]


def test_placeholder_images_are_labelled_and_sized(tmp_path):
    from PIL import Image

    d = D.generate_directions(ENV, 1, 0)["directions"][0]
    f = adapters.PlaceholderAdapter().generate({"prompt": "p", "width": 200, "height": 120, "seed": 1, "settings": {"direction": d}}, tmp_path)[0]
    im = Image.open(f["path"])
    assert im.size == (200, 120)
    assert im.getpixel((2, 2)) == (0, 0, 0)  # the black PLACEHOLDER banner
    assert adapters.sha256_file(f["path"]) == f["sha256"]


def test_placeholder_needs_a_direction(tmp_path):
    with pytest.raises(ValueError, match="settings.direction"):
        adapters.PlaceholderAdapter().generate({"prompt": "p", "width": 64, "height": 64}, tmp_path)


# --- runs -------------------------------------------------------------------------------------------------------------------------------
def test_generation_ids_cannot_traverse(project):
    for bad in ("../etc", "gen-1", "gen-20260101-000000-zzzzzz", ""):
        with pytest.raises(ValueError, match="not a generation id"):
            runs.run_dir(project, bad)
    assert runs.run_dir(project, runs.new_id()).parent == runs.runs_dir(project)


def test_feedback_is_marked_subjective_and_validated():
    rec = runs.make_record("gen-20260101-000000-abcdef", brief=ENV, adapter={"name": "x"}, directions=[], requests=[], outputs=[{"output_id": "o1"}], reference_ids=[], core_run_id=None)
    row = runs.add_feedback(rec, output_id="o1", decision="accept", scores={"brief_adherence": 0.8}, notes="", corrections=[])
    assert row["subjective"] is True and rec["status"] == "concept_only" and len(rec["feedback"]) == 1
    with pytest.raises(ValueError, match="decision must be"):
        runs.add_feedback(rec, output_id="o1", decision="maybe", scores=None, notes="", corrections=[])
    with pytest.raises(ValueError, match="unknown output"):
        runs.add_feedback(rec, output_id="nope", decision="accept", scores=None, notes="", corrections=[])


# --- analysis ---------------------------------------------------------------------------------------------------------------------------
def test_feature_cache_notices_a_changed_file(tmp_path):
    a, b = tmp_path / "a.png", tmp_path / "b.png"
    synthetic.from_spec({"kind": "box", "bg": "#204060", "fg": "#f0c040", "box": [0.1, 0.1, 0.4, 0.4]}, b)
    synthetic.from_spec({"kind": "box", "bg": "#204060", "fg": "#f0c040", "box": [0.1, 0.1, 0.4, 0.4]}, a)
    assert analysis.similarity(a, b) == pytest.approx(1.0, abs=1e-6)
    os.utime(a, (1, 1))  # different mtime even if the rewrite lands in the same clock tick
    synthetic.from_spec({"kind": "box", "bg": "#c04020", "fg": "#20c0a0", "box": [0.6, 0.6, 0.9, 0.9]}, a)
    assert analysis.similarity(a, b) < 0.7


def test_value_structure_adds_up_and_matches_construction(tmp_path):
    p = synthetic.from_spec({"kind": "split", "left": "#000000", "right": "#ffffff"}, tmp_path / "bw.png")
    vs = analysis.measure(p)["value_structure"]
    assert vs["dark"] == pytest.approx(0.5, abs=0.02) and vs["light"] == pytest.approx(0.5, abs=0.02) and vs["mid"] < 0.02
    assert sum(vs.values()) == pytest.approx(1.0, abs=1e-6)


def test_readability_rewards_range_and_penalises_noise(tmp_path):
    flat = analysis.measure(synthetic.from_spec({"kind": "split", "left": "#707070", "right": "#757575"}, tmp_path / "f.png"))
    good = analysis.measure(synthetic.from_spec({"kind": "box", "bg": "#101010", "fg": "#f0f0f0", "box": [0.3, 0.3, 0.7, 0.7]}, tmp_path / "g.png"))
    noisy = analysis.measure(synthetic.from_spec({"kind": "noise", "seed": 3, "size": [96, 96]}, tmp_path / "n.png"))
    r = {"value_range": {"min": 0.35}, "edge_density": {"min": 0.01, "max": 0.35}}
    assert analysis.readability(good, r)["score"] > analysis.readability(noisy, r)["score"] > 0
    assert analysis.readability(good, r)["score"] > analysis.readability(flat, r)["score"]
    assert analysis.readability(flat, r)["parts"]["value_range"] < 0.1 and analysis.readability(noisy, r)["parts"]["edge_density"] < 1.0


def test_novelty_never_claims_one_for_an_empty_library(tmp_path):
    p = synthetic.from_spec({"kind": "solid", "color": "#336699"}, tmp_path / "x.png")
    res = analysis.novelty_vs_library(p, [])
    assert res["novelty"] is None and res["compared"] == 0


def test_box_validation():
    for bad in ([0.5, 0.5, 0.4, 0.6], [0, 0, 2, 1], [0.1, 0.1], "abc", None):
        with pytest.raises(ValueError, match="focal_box must be"):
            analysis.check_box(bad)
    assert analysis.check_box([0.1, 0.2, 0.3, 0.4]) == [0.1, 0.2, 0.3, 0.4]


# --- lora -------------------------------------------------------------------------------------------------------------------------------
def test_lora_module_cannot_start_a_process():
    src = Path(lora.__file__).read_text()
    assert not re.search(r"\b(subprocess|os\.system|Popen|os\.exec|os\.spawn)\b", src), "the LoRA module must only prepare and validate"
    assert not any(hasattr(lora, n) for n in ("train", "run_training", "launch"))


def test_lora_policy_file_matches_defaults():
    pol = lora.load_policy(Path(__file__).resolve().parents[1] / "style" / "lora_policy.yaml")
    assert pol == lora.DEFAULT_POLICY


def test_lora_dataset_needs_a_manifest(tmp_path):
    with pytest.raises(ValueError, match="dataset.yaml"):
        lora.validate_dataset(tmp_path)
    (tmp_path / "dataset.yaml").write_text("dataset: {id: x}\n")
    with pytest.raises(ValueError, match="items"):
        lora.validate_dataset(tmp_path)


def test_overfit_check_flags_a_copy_of_a_training_image(tmp_path):
    ds = tmp_path / "ds"
    (ds / "img").mkdir(parents=True)
    specs = [{"kind": "box", "bg": "#204060", "fg": "#f0c040", "box": [0.1 * i, 0.1, 0.1 * i + 0.3, 0.5], "size": [64, 64]} for i in range(1, 4)]
    items = []
    for i, s in enumerate(specs):
        synthetic.from_spec(s, ds / "img" / f"{i}.png")
        items.append({"file": f"img/{i}.png"})
    (ds / "dataset.yaml").write_text(yaml.safe_dump({"dataset": {"id": "d"}, "items": items}))
    copy = synthetic.from_spec(specs[1], tmp_path / "copy.png")
    other = synthetic.from_spec({"kind": "box", "bg": "#c04020", "fg": "#20c0a0", "box": [0.6, 0.6, 0.9, 0.9], "size": [64, 64]}, tmp_path / "other.png")
    res = lora.check_overfit([str(copy), str(other)], ds)
    assert not res["ok"] and [f["output"] for f in res["flagged"]] == [str(copy)] and res["flagged"][0]["training_image"] == "img/1.png"


def test_synthetic_specs_are_validated(tmp_path):
    with pytest.raises(ValueError, match="unknown image spec"):
        synthetic.from_spec({"kind": "plasma"}, tmp_path / "x.png")
