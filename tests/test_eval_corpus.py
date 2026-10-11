"""`shoebox eval corpus-draw`: a reproducible, screened draw of an eval corpus (design D8)."""

import hashlib
import json
from pathlib import Path

import pytest
from test_eval_helpers import write_jsonl

from shoebox import config, store
from shoebox.evals import corpus

LB = {"candidate": "fake-12b", "model_id": "ollama/fake:12b", "params_hash": "0123456789abcdef"}


def h(tag: str) -> str:
    return hashlib.sha256(tag.encode()).hexdigest()


def it(tag, role="distractor", relpath=None, group_id=None, source="own"):
    return {"asset_id": h(tag), "source": source, "relpath": relpath or f"Pictures/F{tag[-1]}/{tag}.jpg",
            "ext": "jpg", "bytes": 5, "mtime": 1.0, "bucket": "b", "group_id": group_id, "group_rule": None,
            "role": role}


@pytest.fixture
def env(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    cfg = config.Config(shoebox_root=tmp_path / "root", sources={
        "own": config.Source("own", src, "owner", synced=False, hosted_consent=True,
                             held_back=("Pictures/Held/**",), hosted_models=("gemini", "claude")),
        "gonly": config.Source("gonly", tmp_path / "g", "owner", synced=False, hosted_consent=True,
                               hosted_models=("gemini",)),
    })
    sets = tmp_path / "sets"
    items = [it("t1", "target"), it("t2", "target"),
             it("t-held", "target", relpath="Pictures/Held/t.jpg"),
             *[it(f"d{i}") for i in range(20)],
             it("held", relpath="Pictures/Held/x.jpg"),
             it("gonly", source="gonly"),
             it("excl"), it("pending")]
    write_jsonl(sets / "base" / "items.jsonl", items)
    write_jsonl(sets / "base" / "exclude.jsonl", [{"asset_id": h("excl"), "reason": "x", "decided_by": "t",
                                                  "ts": "2026-10-11"}])
    for x in items:
        status = "error" if x["asset_id"] == h("pending") else "ok"
        store.append(cfg, {"asset_id": x["asset_id"], "model_id": LB["model_id"],
                           "params_hash": LB["params_hash"], "status": status})
    store.append(cfg, {"asset_id": h("held"), "model_id": LB["model_id"], "params_hash": LB["params_hash"],
                       "status": "ok"})
    return cfg, sets, items


def labels(cfg):
    return store.index(cfg, LB["model_id"], LB["params_hash"])


def screen_all(path: Path, items, exclude=(), skip=()):
    rows = []
    for x in items:
        if x["asset_id"] in skip:
            continue
        bad = x["asset_id"] in exclude
        rows.append({"asset_id": x["asset_id"], "verdict": "exclude" if bad else "ok",
                     "category": "medical" if bad else None, "note": "n" if bad else "", "decided_by": "t",
                     "ts": "2026-10-11"})
    write_jsonl(path, rows)
    return path


def run(env, n=8, seed=5, buffer=3, screen=None, out="c1"):
    cfg, sets, _ = env
    return corpus.draw_corpus(cfg, sets / "base", sets / out, labels(cfg), LB, n=n, seed=seed, buffer=buffer,
                              screen_path=screen, others=[], never=corpus.privacy.NeverSend(frozenset(), (), ()))


def read(path):
    return [json.loads(x) for x in path.read_text().splitlines()]


def test_reproducible_byte_identical(env, tmp_path):
    _cfg, sets, items = env
    scr = screen_all(tmp_path / "s.jsonl", items)
    files = ("items.jsonl", "draw.json", "README.md")
    assert run(env, screen=scr, out="a").written
    first = [(sets / "a" / f).read_bytes() for f in files]
    for f in files:
        (sets / "a" / f).unlink()
    assert run(env, screen=scr, out="a").written
    assert [(sets / "a" / f).read_bytes() for f in files] == first
    assert run(env, screen=scr, out="c", seed=6).written
    assert (sets / "c" / "items.jsonl").read_bytes() != (sets / "a" / "items.jsonl").read_bytes()


def test_targets_in_and_rules_failing_target_dropped_held_back_never_drawn(env, tmp_path):
    _cfg, sets, items = env
    res = run(env, n=22, screen=screen_all(tmp_path / "s.jsonl", items))
    out = read(sets / "c1" / "items.jsonl")
    ids = [x["asset_id"] for x in out]
    assert ids[:2] == sorted([h("t1"), h("t2")]) and {x["role"] for x in out[:2]} == {"target"}
    assert h("t-held") not in ids and h("held") not in ids and h("gonly") not in ids
    assert h("excl") not in ids and h("pending") not in ids  # excluded; non-final label
    assert res.written and len(out) == 22
    d = json.loads((sets / "c1" / "draw.json").read_text())
    assert d["counts"]["targets_dropped"] == [{"asset_id": h("t-held"), "reason": "held_back"}]
    assert d["counts"]["rule_skips"] == {"held_back": 1, "no_consent": 1}
    assert d["counts"]["candidates"] == 22


def test_non_final_label_not_candidate(env, tmp_path):
    _cfg, sets, items = env
    run(env, n=22, screen=screen_all(tmp_path / "s.jsonl", items))
    assert h("pending") not in {x["asset_id"] for x in read(sets / "c1" / "items.jsonl")}


def test_screen_exclude_substituted_and_listed(env, tmp_path):
    _cfg, sets, items = env
    scr = screen_all(tmp_path / "s.jsonl", items)
    base = [x["asset_id"] for x in read(sets / "c1" / "items.jsonl")] if run(env, screen=scr).written else []
    drawn = base[2:]
    victim = drawn[2]
    scr2 = screen_all(tmp_path / "s2.jsonl", items, exclude={victim})
    run(env, screen=scr2, out="c2")
    got = [x["asset_id"] for x in read(sets / "c2" / "items.jsonl")][2:]
    assert victim not in got and len(got) == len(drawn)
    assert got[:2] == drawn[:2] and got[2:-1] == drawn[3:]
    d = json.loads((sets / "c2" / "draw.json").read_text())
    assert d["counts"]["substitutions"] == [{"asset_id": victim, "category": "medical", "note": "n",
                                            "replaced_by": drawn[3]}]
    assert d["counts"]["screen_excludes"] == {"medical": 1}
    readme = (sets / "c2" / "README.md").read_text()
    assert "medical | 1" in readme and "/d" not in readme and ".jpg" not in readme


def test_group_mate_of_excluded_item_excluded(env, tmp_path):
    _cfg, sets, items = env
    items = [x | {"group_id": "g1"} if x["asset_id"] in (h("d0"), h("d1"), h("t2")) else x for x in items]
    write_jsonl(sets / "base" / "items.jsonl", items)
    scr = screen_all(tmp_path / "s.jsonl", items, exclude={h("d0")})
    run(env, n=19, screen=scr)
    ids = {x["asset_id"] for x in read(sets / "c1" / "items.jsonl")}
    assert not ids & {h("d0"), h("d1"), h("t2")}
    d = json.loads((sets / "c1" / "draw.json").read_text())
    assert {"asset_id": h("t2"), "reason": "screen_group"} in d["counts"]["targets_dropped"]
    assert d["counts"]["screen_group"] == 1


def test_missing_verdicts_write_queue_and_no_items(env, tmp_path):
    _cfg, sets, items = env
    scr = screen_all(tmp_path / "s.jsonl", items[:1])  # only t1 screened
    res = run(env, n=8, buffer=3, screen=scr)
    assert not res.written
    assert not (sets / "c1" / "items.jsonl").exists()
    q = read(sets / "c1" / "screen-queue.jsonl")
    assert q[0]["asset_id"] == h("t2")  # targets without a verdict first
    assert len(q) == 1 + (8 - 2) + 3
    assert set(q[0]) == {"asset_id", "source", "relpath", "group_id", "role"}
    # once screened, success removes the stale queue
    run(env, n=8, screen=screen_all(tmp_path / "s2.jsonl", items))
    assert (sets / "c1" / "items.jsonl").exists() and not (sets / "c1" / "screen-queue.jsonl").exists()


def test_cli_exit_3_when_screening_needed(env, tmp_path, monkeypatch):
    from shoebox.commands import eval as ev

    cfg, sets, _items = env
    monkeypatch.setattr(ev, "_cfg", lambda: cfg)
    monkeypatch.setattr(corpus, "resolve_labels", lambda cfg, name: (LB, labels(cfg)))
    monkeypatch.setattr(corpus.privacy, "load_never_send", lambda: corpus.privacy.NeverSend(frozenset(), (), ()))
    monkeypatch.setattr(corpus.privacy, "known_items", list)
    from shoebox.cli import main
    rc = main(["eval", "corpus-draw", "--name", "c9", "--base", "base", "--labelled-by", "x", "--n", "5",
               "--seed", "1", "--sets-dir", str(sets)])
    assert rc == 3 and (sets / "c9" / "screen-queue.jsonl").exists()


def test_too_few_candidates_refused(env, tmp_path):
    _cfg, _sets, items = env
    with pytest.raises(ValueError, match="only"):
        run(env, n=23, screen=screen_all(tmp_path / "s.jsonl", items))


def test_order_is_stable_when_items_are_removed():
    from shoebox.evals import corpus
    ids = [f"{i:064x}" for i in range(200)]
    full = corpus.order(ids, 7)
    removed = set(full[3:6]) | {full[50]}
    assert corpus.order([a for a in ids if a not in removed], 7) == [a for a in full if a not in removed]
    assert corpus.order(ids, 8) != full
