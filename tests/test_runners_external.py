"""External label runtime: Opus (Claude Code subagent) labels ingested as a normal candidate. Synthetic only."""

import io
import json
import types

import pytest
from derived_fixture import write_derived
from PIL import Image

from shoebox import config, privacy, runners, store
from shoebox.cli import main
from shoebox.commands import label
from shoebox.runners import external

REAL_CONF = runners.CONFIG_DIR


def aid(i: int) -> str:
    return f"{i:02d}" + "c" * 62


def good(**over) -> dict:
    out = {"caption": "A dog on a beach.", "tags": ["Dog", "beach"], "medium": "digital",
           "digitiser": "camera", "colour_class": "colour",
           "confidence": {"medium": 0.9, "digitiser": 0.8, "colour_class": 0.9}, "rotate_cw": 0}
    return {**out, **over}


@pytest.fixture
def env(tmp_path, monkeypatch):
    conf = tmp_path / "config"
    (conf / "prompts").mkdir(parents=True)
    (conf / "schemas").mkdir()
    (conf / "candidates.d").mkdir()
    (conf / "prompts" / "label-v2.md").write_text((REAL_CONF / "prompts" / "label-v2.md").read_text())
    (conf / "schemas" / "label-v2.json").write_text((REAL_CONF / "schemas" / "label-v2.json").read_text())
    (conf / "candidates.d" / "claude.toml").write_text((REAL_CONF / "candidates.d" / "claude.toml").read_text())
    monkeypatch.setattr(runners, "CONFIG_DIR", conf)
    cfg = config.Config(shoebox_root=tmp_path / "shoebox", sources={})
    monkeypatch.setattr(label.config, "load", lambda *a, **k: cfg)
    its = [{"asset_id": aid(i), "source": "s", "relpath": f"{i}.jpg"} for i in range(4)]
    items = tmp_path / "items.jsonl"
    items.write_text("".join(json.dumps(i) + "\n" for i in its[:3]))
    return types.SimpleNamespace(cfg=cfg, conf=conf, its=its, items=items, tmp=tmp_path)


def write_labels(d, rows, name="labels-001.jsonl"):
    d.mkdir(parents=True, exist_ok=True)
    (d / name).write_text("".join(json.dumps(r) + "\n" for r in rows))


def test_real_candidate_resolves_and_refuses_to_run():
    res = runners.resolve("opus-v2")
    assert res.model_id == "claude/opus-5.5"
    assert res.runtime == res.cand["producer"]
    assert res.kind == "labels"
    with pytest.raises(RuntimeError, match="shoebox label ingest"):
        res.runner.run_one(res.cand, {"asset_id": aid(0)}, None, None)


def test_ingest_valid_invalid_idempotent_and_label_index(env):
    res = runners.resolve("opus-v2", env.conf)
    d = env.tmp / "out"
    write_labels(d, [{"asset_id": aid(0), "output": good()},
                     {"asset_id": aid(1), "output": good(medium="polaroid")},
                     {"asset_id": aid(2), "output": good(caption=None, tags=[])}])
    r = external.ingest(env.cfg, res, d)
    assert (r.ok, r.invalid, r.already) == (1, 2, 0)
    idx = runners.label_index(env.cfg, res)
    assert idx[aid(0)]["status"] == "ok"
    assert idx[aid(0)]["output"]["tags"] == ["dog", "beach"]
    assert idx[aid(0)]["runtime"] == res.cand["producer"]
    assert idx[aid(0)]["latency_ms"] is None
    assert idx[aid(0)]["usage"] == {"input_tokens": None, "output_tokens": None, "thinking_tokens": None}
    bad = idx[aid(1)]
    assert bad["status"] == "invalid" and bad["output"] is None
    assert json.loads(bad["raw"])["medium"] == "polaroid"
    assert "medium" in bad["error"]
    assert idx[aid(2)]["status"] == "invalid" and "caption" in idx[aid(2)]["error"]
    assert (res.store_dir(env.cfg) / "params.json").exists()
    again = external.ingest(env.cfg, res, d)
    assert (again.ok, again.invalid, again.already) == (0, 0, 3)
    assert len(list(store.iter_records(env.cfg, res.model_id, res.params_hash))) == 3


def test_ingest_uppercase_tag_without_normalising_would_be_invalid(env):
    """Guards the normalisation: the schema pattern rejects uppercase tags."""
    import jsonschema

    schema = json.loads((REAL_CONF / "schemas" / "label-v2.json").read_text())
    assert list(jsonschema.Draft202012Validator(schema).iter_errors(good()))


def test_ingest_map_form(env):
    res = runners.resolve("opus-v2", env.conf)
    d = env.tmp / "out"
    write_labels(d, [{"key": "k-0001", "output": good()}])
    (d / "map.json").write_text(json.dumps({"k-0001": aid(1)}))
    r = external.ingest(env.cfg, res, d)
    assert r.ok == 1
    assert runners.label_index(env.cfg, res)[aid(1)]["status"] == "ok"


def test_ingest_unknown_key_and_asset_refused(env):
    res = runners.resolve("opus-v2", env.conf)
    d = env.tmp / "out"
    write_labels(d, [{"asset_id": aid(3), "output": good()}])
    with pytest.raises(external.IngestError, match="not in the items"):
        external.ingest(env.cfg, res, d, items=[{"asset_id": aid(0)}])
    assert not list(store.iter_records(env.cfg, res.model_id, res.params_hash))
    write_labels(d, [{"key": "k-0009", "output": good()}])
    (d / "map.json").write_text(json.dumps({"k-0001": aid(1)}))
    with pytest.raises(external.IngestError, match="k-0009"):
        external.ingest(env.cfg, res, d)


def test_ingest_cli(env, capsys):
    d = env.tmp / "out"
    write_labels(d, [{"asset_id": aid(0), "output": good()}, {"asset_id": aid(3), "output": good()}])
    assert main(["label", "ingest", "--candidate", "opus-v2", "--dir", str(d), "--items", str(env.items)]) == 1
    assert "not in the items" in capsys.readouterr().err
    write_labels(d, [{"asset_id": aid(0), "output": good()}])
    assert main(["label", "ingest", "--candidate", "opus-v2", "--dir", str(d), "--items", str(env.items)]) == 0
    assert "ok 1" in capsys.readouterr().out


def test_other_runtimes_cannot_ingest(env):
    from dataclasses import replace

    res = runners.resolve("opus-v2", env.conf)
    fake = replace(res, cand={**res.cand, "runtime": "gemini"})
    with pytest.raises(external.IngestError, match="external"):
        external.ingest(env.cfg, fake, env.tmp)


# ---------- packets ----------


def jpeg(size=(1600, 1200)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, (10, 100, 200)).save(buf, "JPEG")
    return buf.getvalue()


def test_packets_privacy_skip_map_and_thumbs(env, monkeypatch):
    for it in env.its[:3]:
        write_derived(env.cfg, it["asset_id"], jpeg())

    def fake_filter(items, cfg, never=None, others=(), provider="gemini"):
        assert provider == "claude"
        bad = [privacy.Skip(it["asset_id"], "s", it["relpath"], "never_send") for it in items
               if it["asset_id"] == aid(1)]
        return privacy.Eligibility([it for it in items if it["asset_id"] != aid(1)], bad)

    monkeypatch.setattr(privacy, "filter_items", fake_filter)
    out = env.tmp / "packets"
    res = external.packets(env.cfg, env.its, out, per_batch=1, provider="claude")
    m = json.loads((out / "map.json").read_text())
    assert m == {"k-0001": aid(0), "k-0002": aid(2)}
    assert {(s["asset_id"], s["reason"]) for s in res.skipped} == {(aid(1), "never_send"), (aid(3), "no_derived")}
    b1 = json.loads((out / "batch-001.json").read_text())
    assert [p["key"] for p in b1["photos"]] == ["k-0001"]
    assert aid(0) not in (out / "batch-001.json").read_text()
    assert (out / "batch-002.json").exists() and not (out / "batch-003.json").exists()
    with Image.open(out / "images" / "k-0001.jpg") as im:
        assert max(im.size) == 1024


def test_packets_refuse_evals_dir(env):
    from shoebox.config import REPO_ROOT

    with pytest.raises(ValueError, match="must not"):
        external.packets(env.cfg, env.its, REPO_ROOT / "evals" / "x")


@pytest.mark.parametrize("verdict", ["pending", "private"])
def test_packets_leave_out_quarantined(env, verdict):
    from shoebox import quarantine

    src = env.tmp / "src"
    src.mkdir()
    cfg = config.Config(shoebox_root=env.tmp / "shoebox", sources={
        "s": config.Source("s", src, "owner", synced=False, hosted_consent=True, hosted_models=("claude",))})
    for it in env.its[:2]:
        write_derived(cfg, it["asset_id"], jpeg())
    quarantine.flag([aid(1)], "nudity")
    if verdict != "pending":
        quarantine.record_verdict(aid(1), verdict, "owner")
    out = env.tmp / "packets"
    res = external.packets(cfg, env.its[:2], out, provider="claude")
    assert list(res.keys.values()) == [aid(0)]
    assert res.skipped == [{"asset_id": aid(1), "reason": "quarantined"}]
    assert json.loads((out / "packets.json").read_text())["skipped"] == res.skipped
