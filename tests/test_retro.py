"""The retro-check set (task 9.3): every asset already sent to a hosted model or shown to Claude."""

import json

from shoebox import retro, store
from shoebox.config import Config

A, B, C, D, E = ("a" * 64, "b" * 64, "c" * 64, "d" * 64, "e" * 64)


def _rec(aid, mid, ph="p1"):
    return {"asset_id": aid, "model_id": mid, "params_hash": ph, "status": "ok"}


def test_union_of_hosted_stores_and_shown_sets(tmp_path):
    cfg = Config(shoebox_root=tmp_path / "root", sources={})
    store.append(cfg, _rec(A, "gemini/gemini-3.8-flash"), kind="labels")
    store.append(cfg, _rec(B, "claude/opus-5.5"), kind="judgments")
    store.append(cfg, _rec(C, "ollama/gemma4:12b"), kind="labels")  # local: not sent
    sets = tmp_path / "sets"
    (sets / "eval-1k").mkdir(parents=True)
    (sets / "eval-1k" / "pool.jsonl").write_text(json.dumps({"pairs": [{"asset_id": D}]}) + "\n")
    (sets / "other").mkdir()
    (sets / "other" / "items.jsonl").write_text(json.dumps({"asset_id": E}) + "\n")  # not a shown set
    assert retro.retro_ids(cfg, ("eval-1k",), sets) == {A, B, D}
