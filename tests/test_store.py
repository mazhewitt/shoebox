import json

import pytest

from shoebox import store
from shoebox.config import Config


@pytest.fixture
def cfg(tmp_path):
    return Config(shoebox_root=tmp_path / "shoebox", sources={})


def rec(asset_id, status="ok", model_id="ollama/gemma4:e4b-mlx-bf16", ph="0123456789abcdef", **kw):
    r = {"asset_id": asset_id, "model_id": model_id, "params_hash": ph, "status": status,
         "output": {"x": 1} if status == "ok" else None, "raw": "", "error": None}
    r.update(kw)
    return r


def test_params_hash_is_16_hex_and_stable_under_key_order():
    a = store.params_hash({"model": "m", "temperature": 0.0, "prompt_text": "hi"})
    b = store.params_hash({"prompt_text": "hi", "temperature": 0.0, "model": "m"})
    assert a == b
    assert len(a) == 16 and int(a, 16) >= 0


def test_params_hash_changes_with_any_param():
    base = {"model": "m", "prompt_text": "hi", "image_budget": "auto"}
    h = store.params_hash(base)
    assert store.params_hash({**base, "prompt_text": "hi!"}) != h
    assert store.params_hash({**base, "image_budget": 560}) != h


def test_params_hash_matches_documented_formula():
    import hashlib
    p = {"b": [1, 2], "a": "é"}
    canon = json.dumps(p, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    assert store.params_hash(p) == hashlib.sha256(canon.encode()).hexdigest()[:16]


def test_store_dir_layout(cfg):
    d = store.store_dir(cfg, "ollama/gemma4:e4b-mlx-bf16", "abcdabcdabcdabcd")
    assert d == cfg.shoebox_root / "results" / "labels" / "ollama__gemma4_e4b-mlx-bf16" / "abcdabcdabcdabcd"
    assert store.store_dir(cfg, "rules-v1", "x").parent.name == "rules-v1"


def test_append_then_lookup_and_iter(cfg):
    path = store.append(cfg, rec("a1"))
    assert path.name == "part-0000.jsonl"
    store.append(cfg, rec("a2", status="invalid", raw="{bad"))
    got = store.lookup(cfg, "ollama/gemma4:e4b-mlx-bf16", "0123456789abcdef", "a2")
    assert got["status"] == "invalid" and got["raw"] == "{bad"
    assert store.lookup(cfg, "ollama/gemma4:e4b-mlx-bf16", "0123456789abcdef", "nope") is None
    ids = [r["asset_id"] for r in store.iter_records(cfg, "ollama/gemma4:e4b-mlx-bf16", "0123456789abcdef")]
    assert ids == ["a1", "a2"]


def test_append_requires_key_fields(cfg):
    with pytest.raises(ValueError):
        store.append(cfg, {"asset_id": "a", "status": "ok"})


def test_iter_records_on_missing_dir_is_empty(cfg):
    assert list(store.iter_records(cfg, "rules-v1", "ffff")) == []


def test_final_record_wins_over_error_and_first_final_is_kept(cfg):
    store.append(cfg, rec("a", status="error", error="timeout"))
    assert not store.is_final(store.lookup(cfg, "ollama/gemma4:e4b-mlx-bf16", "0123456789abcdef", "a"))
    store.append(cfg, rec("a", status="ok", output={"n": 1}))
    store.append(cfg, rec("a", status="error", error="later"))
    store.append(cfg, rec("a", status="ok", output={"n": 2}))
    got = store.lookup(cfg, "ollama/gemma4:e4b-mlx-bf16", "0123456789abcdef", "a")
    assert got["status"] == "ok" and got["output"] == {"n": 1}
    assert store.is_final(got)


def test_parts_rotate(cfg, monkeypatch):
    monkeypatch.setattr(store, "PART_LINES", 3)
    paths = [store.append(cfg, rec(f"a{i}")) for i in range(7)]
    assert [p.name for p in paths] == ["part-0000.jsonl"] * 3 + ["part-0001.jsonl"] * 3 + ["part-0002.jsonl"]
    assert len(list(store.iter_records(cfg, "ollama/gemma4:e4b-mlx-bf16", "0123456789abcdef"))) == 7


def test_truncated_last_line_is_skipped_and_not_glued(cfg):
    p = store.append(cfg, rec("a1"))
    with open(p, "a") as f:
        f.write('{"asset_id": "half')  # a write killed mid-line
    store.append(cfg, rec("a2"))
    ids = [r["asset_id"] for r in store.iter_records(cfg, "ollama/gemma4:e4b-mlx-bf16", "0123456789abcdef")]
    assert ids == ["a1", "a2"]


def test_params_json_written_once(cfg):
    d = store.write_params(cfg, "rules-v1", "abcd", {"candidate": "rules-v1", "params": {"v": 1}})
    store.write_params(cfg, "rules-v1", "abcd", {"candidate": "other", "params": {"v": 1}})
    doc = json.loads((d / "params.json").read_text())
    assert doc["candidate"] == "rules-v1"


def test_append_refuses_volumes(tmp_path):
    from pathlib import Path

    cfg = Config(shoebox_root=Path("/Volumes/no-such-share/shoebox"), sources={})
    with pytest.raises(ValueError, match="/Volumes"):
        store.append(cfg, rec("a" * 64))
    with pytest.raises(ValueError, match="/Volumes"):
        store.write_params(cfg, "m", "0123456789abcdef", {})


def test_append_and_write_params_refuse_a_source_root(tmp_path):
    from shoebox.config import Source

    src = tmp_path / "src"
    src.mkdir()
    cfg = Config(shoebox_root=src / "shoebox", sources={"s": Source("s", src, "owner", False, True)})
    with pytest.raises(ValueError, match="source root"):
        store.append(cfg, rec("a" * 64))
    with pytest.raises(ValueError, match="source root"):
        store.write_params(cfg, "m", "0123456789abcdef", {})
    assert not (src / "shoebox").exists()


def test_append_refuses_a_source_root_reached_through_a_symlink(tmp_path):
    from shoebox.config import Source

    src = tmp_path / "src"
    (src / "inner").mkdir(parents=True)
    (tmp_path / "link").symlink_to(src / "inner")
    cfg = Config(shoebox_root=tmp_path / "link", sources={"s": Source("s", src, "owner", False, True)})
    with pytest.raises(ValueError, match="source root"):
        store.append(cfg, rec("a" * 64))
    assert list((src / "inner").iterdir()) == []
