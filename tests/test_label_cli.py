import json
import sys
import types

import pytest
from derived_fixture import write_derived

from shoebox import runners, store
from shoebox.cli import main
from shoebox.commands import label
from shoebox.config import Config


@pytest.fixture
def env(tmp_path, monkeypatch):
    conf = tmp_path / "config"
    (conf / "prompts").mkdir(parents=True)
    (conf / "prompts" / "p.md").write_text("Describe.")
    (conf / "candidates.toml").write_text(
        '[candidates.fake-a]\nruntime = "fake"\nmodel = "m:1"\nprompt = "p.md"\nprofile = "infer-v1"\n'
        '[candidates.fake-b]\nruntime = "fake"\nmodel = "m:2"\n')
    monkeypatch.setattr(runners, "CONFIG_DIR", conf)
    cfg = Config(shoebox_root=tmp_path / "shoebox", sources={})
    monkeypatch.setattr(label.config, "load", lambda *a, **k: cfg)

    mod = types.ModuleType("shoebox.runners.fake")
    mod.calls = []
    mod.runtime_version = lambda cand: "fake 1"

    def run_one(cand, item, image_path, cfg_):
        mod.calls.append(item["asset_id"])
        return {"status": "ok", "output": {"caption": "x"}, "raw": None, "error": None,
                "usage": {"input_tokens": 1, "output_tokens": 2, "thinking_tokens": None}, "latency_ms": 7, "usd": 0.0}

    mod.run_one = run_one
    monkeypatch.setitem(sys.modules, "shoebox.runners.fake", mod)

    its = [{"asset_id": f"{i:02d}" + "b" * 62, "source": "s", "relpath": f"{i}.jpg", "ext": "jpg"} for i in range(3)]
    for it in its[:2]:  # the third has no derived image
        write_derived(cfg, it["asset_id"], b"jpg")
    items_file = tmp_path / "items.jsonl"
    items_file.write_text("".join(json.dumps(i) + "\n" for i in its))
    return types.SimpleNamespace(cfg=cfg, mod=mod, items=its, items_file=items_file)


def test_list(env, capsys):
    assert main(["label", "list"]) == 0
    assert capsys.readouterr().out.split() == ["fake-a", "fake-b"]


def test_resolve_prints_contract_json(env, capsys):
    assert main(["label", "resolve", "--candidate", "fake-a"]) == 0
    doc = json.loads(capsys.readouterr().out)
    assert set(doc) == {"candidate", "model_id", "params_hash", "store_dir"}
    assert doc["model_id"] == "fake/m:1"
    assert doc["store_dir"] == str(store.store_dir(env.cfg, "fake/m:1", doc["params_hash"]))


def test_resolve_unknown_candidate(env, capsys):
    assert main(["label", "resolve", "--candidate", "nope"]) == 2


def test_run_then_rerun_is_cached(env, capsys):
    assert main(["label", "run", "--candidate", "fake-a", "--items", str(env.items_file)]) == 0
    out = capsys.readouterr()
    assert "ok 2" in out.out and "skipped 1" in out.out
    assert "no ok derived image" in out.err and "img/h" in out.err
    assert len(env.mod.calls) == 2
    env.mod.calls.clear()
    assert main(["label", "run", "--candidate", "fake-a", "--items", str(env.items_file)]) == 0
    assert env.mod.calls == [] and "cached 2" in capsys.readouterr().out


def test_run_limit(env, capsys):
    main(["label", "run", "--candidate", "fake-a", "--items", str(env.items_file), "--limit", "1"])
    assert env.mod.calls == [env.items[0]["asset_id"]]


def test_one_prints_only_the_record(env, capsys):
    aid = env.items[1]["asset_id"]
    assert main(["label", "one", "--candidate", "fake-a", "--asset-id", aid, "--items", str(env.items_file)]) == 0
    out = capsys.readouterr().out
    rec = json.loads(out)
    assert out.count("\n") == 1 and rec["asset_id"] == aid and rec["status"] == "ok"
    env.mod.calls.clear()
    main(["label", "one", "--candidate", "fake-a", "--asset-id", aid, "--items", str(env.items_file)])
    assert env.mod.calls == [] and json.loads(capsys.readouterr().out) == rec


def test_one_missing_derived_or_unknown_asset(env, capsys):
    aid = env.items[2]["asset_id"]
    assert main(["label", "one", "--candidate", "fake-a", "--asset-id", aid, "--items", str(env.items_file)]) == 3
    assert main(["label", "one", "--candidate", "fake-a", "--asset-id", "e" * 64, "--items", str(env.items_file)]) == 3
    assert capsys.readouterr().out == ""
