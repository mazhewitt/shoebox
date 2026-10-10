import json

import pytest
from derived_fixture import write_derived
from PIL import Image
from test_embed_runner import FakeEmbedder

from shoebox import config, embed, runners, store
from shoebox.cli import main
from shoebox.config import Config

COLOURS = {"red": (230, 20, 20), "blue": (20, 30, 230), "green": (20, 220, 30)}


@pytest.fixture
def world(tmp_path, monkeypatch):
    cfg = Config(shoebox_root=tmp_path / "shoebox", sources={})
    monkeypatch.setattr(config, "load", lambda *a, **k: cfg)
    fake = FakeEmbedder(digest=runners.load_candidates()["eg2-bf16"]["model_digest"])
    monkeypatch.setattr(embed, "embedder_for", lambda cand: (embed.check_precision(cand), fake)[1])
    items = []
    for name, rgb in COLOURS.items():
        aid = name[0] * 64
        p = runners.derived_image(cfg, "infer-v1", aid)
        p.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (64, 64), rgb).save(p, quality=95)
        write_derived(cfg, aid)  # its ok index record
        items.append({"asset_id": aid, "source": "importantfiles", "relpath": f"syn/{name}.jpg"})
    items.append({"asset_id": "z" * 64, "source": "importantfiles", "relpath": "syn/missing.jpg"})
    f = tmp_path / "items.jsonl"
    f.write_text("".join(json.dumps(it) + "\n" for it in items))
    return cfg, f, fake


def run_json(capsys, argv) -> tuple[int, dict]:
    rc = main(argv)
    return rc, json.loads(capsys.readouterr().out.strip().splitlines()[-1])


def test_run_then_search_then_cache_hit(world, capsys):
    cfg, items, _ = world
    rc, summary = run_json(capsys, ["embed", "run", "--candidate", "eg2-bf16", "--items", str(items)])
    assert rc == 0
    assert (summary["ok"], summary["skipped"], summary["cached"]) == (3, 1, 0)
    assert summary["model_id"] == "ollama/embeddinggemma-2:740m-bf16"

    # embeddings are not label-v1 records: they live under results/embeddings/, never results/labels/
    assert summary["store_dir"] == str(store.store_dir(cfg, summary["model_id"], summary["params_hash"],
                                                       "embeddings"))
    assert not (cfg.results_dir / "labels").exists()
    recs = store.index(cfg, summary["model_id"], summary["params_hash"], "embeddings")
    assert len(recs) == 3
    r = recs["r" * 64]
    assert r["status"] == "ok" and r["runtime"] == "ollama 0.40.2" and r["machine"] and r["ts"]
    assert r["output"]["image"]["dim"] == 768 and r["output"]["caption"] is None
    params = json.loads((store.store_dir(cfg, r["model_id"], r["params_hash"], "embeddings")
                         / "params.json").read_text())
    p = params["params"]
    for key in ("model", "model_digest", "dtype", "image_budget", "query_prompt", "document_prompt",
                "runtime_version", "profile", "tensor_types"):
        assert key in p, key
    assert p["profile"]["settings"]["long_edge"] == 2048

    assert main(["embed", "search", "--candidate", "eg2-bf16", "--items", str(items), "a red square"]) == 0
    lines = capsys.readouterr().out.splitlines()
    assert lines[0].startswith("# eg2-bf16")
    rank, score, aid, relpath = lines[1].split()
    assert (rank, aid, relpath) == ("1", "r" * 64, "syn/red.jpg") and float(score) > 0.9
    assert len(lines) == 4  # header + the three embedded assets (k=5)

    _, again = run_json(capsys, ["embed", "run", "--candidate", "eg2-bf16", "--items", str(items)])
    assert (again["ok"], again["cached"], again["params_hash"]) == (0, 3, summary["params_hash"])


def test_search_dim_and_k(world, capsys):
    _, items, _ = world
    main(["embed", "run", "--candidate", "eg2-bf16", "--items", str(items)])
    capsys.readouterr()
    assert main(["embed", "search", "--dim", "256", "--k", "1", "--items", str(items), "blue"]) == 0
    lines = capsys.readouterr().out.splitlines()
    assert "dim=256" in lines[0] and len(lines) == 2 and lines[1].split()[2] == "b" * 64


def test_search_without_run_fails_cleanly(world, capsys):
    assert main(["embed", "search", "red"]) == 1
    assert "embed run" in capsys.readouterr().err


def test_caption_from_embeds_captions_under_a_new_params_hash(world, capsys, monkeypatch):
    cfg, items, fake = world
    source = {"candidate": "e4b-bf16", "model_id": "ollama/gemma4:e4b-mlx-bf16", "params_hash": "c" * 16}
    monkeypatch.setattr(embed, "caption_source", lambda name: source)
    captions = {"r" * 64: "A green field.", "b" * 64: "A blue wall."}  # red's caption deliberately says green
    for aid, cap in captions.items():
        store.append(cfg, {"asset_id": aid, "model_id": source["model_id"], "params_hash": source["params_hash"],
                           "status": "ok", "output": {"caption": cap, "tags": []}})
    store.append(cfg, {"asset_id": "g" * 64, "model_id": source["model_id"], "params_hash": source["params_hash"],
                       "status": "invalid", "output": None, "raw": "{"})

    _, plain = run_json(capsys, ["embed", "run", "--items", str(items)])
    _, capd = run_json(capsys, ["embed", "run", "--items", str(items), "--caption-from", "e4b-bf16"])
    assert capd["params_hash"] != plain["params_hash"] and capd["ok"] == 3
    recs = store.index(cfg, capd["model_id"], capd["params_hash"], "embeddings")
    assert recs["r" * 64]["output"]["caption"]["source"] == source
    assert recs["g" * 64]["output"]["caption"] is None
    assert "title: none | text: A green field." in fake.texts

    capsys.readouterr()
    assert main(["embed", "search", "--caption-from", "e4b-bf16", "--mode", "caption", "--items", str(items),
                 "green"]) == 0
    lines = capsys.readouterr().out.splitlines()
    assert [ln.split()[2] for ln in lines[1:]] == ["r" * 64, "b" * 64]  # only captioned assets
    assert main(["embed", "search", "--caption-from", "e4b-bf16", "--mode", "blend", "--items", str(items),
                 "green"]) == 0
    assert len(capsys.readouterr().out.splitlines()) == 4
    assert main(["embed", "search", "--mode", "caption", "red"]) == 1  # the plain run has no captions


def test_fp16_candidate_refused_by_cli(world, capsys, monkeypatch, tmp_path):
    _, items, _ = world
    bad = {"name": "eg2-fp16", "runtime": "embed", "backend": "ollama", "model": "embeddinggemma-2:740m-bf16",
           "dtype": "float16", "dim": 768, "query_prompt": "{text}", "document_prompt": "{text}",
           "profile": "infer-v1"}
    real = runners.load_candidates
    monkeypatch.setattr(runners, "load_candidates", lambda *a, **k: {**real(*a, **k), "eg2-fp16": bad})
    with pytest.raises(embed.PrecisionError, match="float16"):
        main(["embed", "run", "--candidate", "eg2-fp16", "--items", str(items)])


def test_query_uses_query_prompt(world, capsys):
    _, items, fake = world
    main(["embed", "run", "--items", str(items)])
    main(["embed", "search", "--items", str(items), "a red square"])
    assert fake.texts[-1] == "task: search result | query: a red square"


def test_caption_from_waits_for_final_label_records(world, capsys, monkeypatch):
    """An asset whose label record is missing or `error` is not embedded yet: its caption may still come."""
    cfg, items, _ = world
    source = {"candidate": "e4b-bf16", "model_id": "ollama/gemma4:e4b-mlx-bf16", "params_hash": "c" * 16}
    monkeypatch.setattr(embed, "caption_source", lambda name: source)

    def label(aid, status, caption=None):
        store.append(cfg, {"asset_id": aid, "model_id": source["model_id"], "params_hash": source["params_hash"],
                           "status": status, "output": {"caption": caption, "tags": []} if caption else None,
                           "error": "timeout" if status == "error" else None})

    label("r" * 64, "ok", "A red door.")
    label("g" * 64, "error")  # blue has no label record at all
    argv = ["embed", "run", "--items", str(items), "--caption-from", "e4b-bf16"]
    rc, first = run_json(capsys, argv)
    assert rc == 0
    assert (first["ok"], first["cached"], first["skipped"], first["total"]) == (1, 0, 3, 4)

    label("b" * 64, "ok", "A blue wall.")
    label("g" * 64, "invalid")
    rc, second = run_json(capsys, argv)
    assert (second["ok"], second["cached"], second["skipped"]) == (2, 1, 1)
    recs = store.index(cfg, second["model_id"], second["params_hash"], "embeddings")
    assert recs["b" * 64]["output"]["caption"]["source"] == source
    assert recs["g" * 64]["output"]["caption"] is None  # final invalid label: no caption will ever come
    assert recs["r" * 64]["output"]["caption"] is not None


def test_summary_latency_covers_only_this_run(world, capsys):
    _, items, _ = world
    _, first = run_json(capsys, ["embed", "run", "--items", str(items)])
    _, again = run_json(capsys, ["embed", "run", "--items", str(items)])
    assert first["mean_latency_ms"] >= 0 and again["mean_latency_ms"] is None
