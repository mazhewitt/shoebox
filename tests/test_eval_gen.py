import json

import pytest
from test_eval_helpers import make_world, write_jsonl

from shoebox import config
from shoebox.cli import main
from shoebox.evals import gen


def _resolver(world):
    table = json.loads(world["resolutions"].read_text())
    return lambda name: table[name]


def _stimuli(spec: str) -> list[dict]:
    """Minimal reader for the generated spec: names, attachments, tags and grader configs."""
    out: list[dict] = []
    for line in spec.splitlines():
        s = line.strip()
        if s.startswith("- name: "):
            out.append({"name": json.loads(s[8:]), "graders": []})
        elif out and s.startswith("attachments: "):
            out[-1]["attachments"] = json.loads(s[13:])
        elif out and s.startswith("tags: "):
            out[-1]["tags"] = json.loads(s[6:])
        elif out and s.startswith("- type: "):
            out[-1]["graders"].append({"type": json.loads(s[8:])})
        elif out and s.startswith("config: ") and out[-1]["graders"]:
            out[-1]["graders"][-1]["config"] = json.loads(s[8:])
    return out


def _gen(world, tmp_path, **kw):
    cfg = config.load(world["cfg_path"])
    return gen.generate(
        cfg,
        "pilot-v0",
        kw.pop("candidates", ["candA", "candB"]),
        sets_dir=world["sets"],
        out_dir=tmp_path / "gen",
        resolver=_resolver(world),
        config_dir=tmp_path / "no-config",
        log=lambda m: None,
        **kw,
    )


def test_one_stimulus_per_ok_derived_item(tmp_path):
    world = make_world(tmp_path)
    res = _gen(world, tmp_path)
    spec = res.spec_path.read_text()
    assert spec.startswith("# generated — do not edit")
    stims = _stimuli(spec)
    assert [s["name"] for s in stims] == world["ok_ids"]
    assert res.skipped_items == {world["ids"][-1]: "no ok derived image"}
    for s in stims:
        (att,) = s["attachments"]
        assert att.startswith(str(world["root"])) and att.endswith(s["name"] + ".jpg")
        assert set(s["tags"]) == {"bucket", "ext", "group_rule", "labelled"}
        assert s["tags"]["labelled"] == "no" and s["tags"]["group_rule"] == "none"
        types = [g["type"] for g in s["graders"]]
        assert types == ["label-status", "field-match", "field-match", "field-match"]
        assert all("expected" not in g.get("config", {}) for g in s["graders"])
    assert 'mode: "replay"' in spec and str(res.candidates_path.resolve()) in spec


def test_labels_become_expected_values(tmp_path):
    world = make_world(tmp_path)
    a0 = world["ok_ids"][0]
    write_jsonl(
        world["sets"] / "pilot-v0" / "labels.jsonl",
        [
            {
                "asset_id": a0,
                "medium": "print",
                "digitiser": "flatbed_scanner",
                "colour_class": "",
                "notes": "",
            }
        ],
    )
    stims = {
        s["name"]: s for s in _stimuli(_gen(world, tmp_path).spec_path.read_text())
    }
    s0 = stims[a0]
    assert s0["tags"]["labelled"] == "yes"
    cfgs = {
        g["config"]["field"]: g["config"]
        for g in s0["graders"]
        if g["type"] == "field-match"
    }
    assert cfgs["medium"]["expected"] == "print"
    assert cfgs["digitiser"]["expected"] == "flatbed_scanner"
    assert (
        "expected" not in cfgs["colour_class"]
    )  # empty label value = unlabelled field
    assert stims[world["ok_ids"][1]]["tags"]["labelled"] == "no"


def test_candidates_json_resolves_and_skips_embed(tmp_path):
    world = make_world(tmp_path)
    toml = tmp_path / "cands" / "candidates.toml"
    toml.parent.mkdir()
    toml.write_text('[candidates.candA]\nruntime = "rules"\n')
    (toml.parent / "candidates.d").mkdir()
    (toml.parent / "candidates.d" / "embed.toml").write_text(
        '[candidates.eg2]\nruntime = "embed"\n'
    )
    msgs: list[str] = []
    res = gen.generate(
        config.load(world["cfg_path"]),
        "pilot-v0",
        ["candA", "eg2", "candC"],
        sets_dir=world["sets"],
        out_dir=tmp_path / "gen",
        resolver=_resolver(world),
        config_dir=toml.parent,
        log=msgs.append,
    )
    doc = json.loads(res.candidates_path.read_text())
    assert doc["set"] == "pilot-v0" and doc["mode"] == "replay"
    assert set(doc["candidates"]) == {"candA", "candC"}
    assert doc["candidates"]["candA"]["model_id"] == "rules-v1"
    # relative store_dir is resolved against shoebox_root
    assert doc["candidates"]["candC"]["store_dir"] == str(
        world["root"] / "results/labels/rules-v1/pc"
    )
    assert "eg2" in res.skipped_candidates and any("eg2" in m for m in msgs)


def test_live_mode_and_bad_mode(tmp_path):
    world = make_world(tmp_path)
    spec = _gen(world, tmp_path, mode="live").spec_path.read_text()
    assert 'mode: "live"' in spec and 'timeout: "10m"' in spec
    with pytest.raises(ValueError):
        _gen(world, tmp_path, mode="bogus")


def test_cli_gen(tmp_path, monkeypatch, capsys):
    world = make_world(tmp_path)
    monkeypatch.setenv("SHOEBOX_CONFIG", str(world["cfg_path"]))
    out = tmp_path / "cli-gen"
    rc = main(
        [
            "eval", "gen", "--set", "pilot-v0", "--candidates", "candA,candB",
            "--sets-dir", str(world["sets"]), "--out-dir", str(out), "--resolutions", str(world["resolutions"]),
        ]
    )  # fmt: skip
    assert rc == 0
    assert (out / "eval.yaml").exists() and (out / "candidates.json").exists()
    assert "3 stimuli" in capsys.readouterr().out


def test_candidates_json_records_set_items_and_skips(tmp_path):
    world = make_world(tmp_path)
    res = _gen(world, tmp_path)
    doc = json.loads(res.candidates_path.read_text())
    assert (
        doc["items"] == world["ids"]
    )  # every set item, in set order, including the skipped one
    assert doc["skipped_items"] == {world["ids"][-1]: "no ok derived image"}
    assert len(doc["items_sha256"]) == 64
    spec = res.spec_path.read_text()
    assert "3 of 4 set items" in spec and "1 skipped" in spec


def test_owner_unknown_label_passed_through_for_grader(tmp_path):
    world = make_world(tmp_path)
    a0 = world["ok_ids"][0]
    write_jsonl(
        world["sets"] / "pilot-v0" / "labels.jsonl",
        [
            {
                "asset_id": a0,
                "medium": "unknown",
                "digitiser": "camera",
                "colour_class": "colour",
            }
        ],
    )
    s0 = {s["name"]: s for s in _stimuli(_gen(world, tmp_path).spec_path.read_text())}[
        a0
    ]
    cfgs = {
        g["config"]["field"]: g["config"]
        for g in s0["graders"]
        if g["type"] == "field-match"
    }
    # kept as expected="unknown"; the field-match grader turns it into unlabelled with owner_unknown=true
    assert cfgs["medium"]["expected"] == "unknown"
