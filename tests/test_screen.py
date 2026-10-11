"""Nudity screen runner (tasks 4.2-4.4): stub model returning fixed scores per synthetic colour."""

import json
import socket
from pathlib import Path

import numpy as np
import pytest
from derived_fixture import write_derived
from PIL import Image

from shoebox import config, quarantine, runners, store
from shoebox.cli import main
from shoebox.config import Config
from shoebox.runners import screen

MEANS_BGR = np.array([104.0, 117.0, 123.0])
# asset id -> (RGB colour of its synthetic derived JPEG, the stub's nsfw score for that colour)
ASSETS = {
    "a" * 64: ((230, 20, 20), 0.05),     # well below
    "b" * 64: ((20, 30, 230), 0.1999),   # just below 0.2
    "c" * 64: ((20, 220, 30), 0.2),      # exactly the threshold
    "d" * 64: ((240, 240, 30), 0.2001),  # just above
    "e" * 64: ((30, 220, 220), 0.95),    # far above
}


class StubModel:
    """Scores by the centre pixel's colour (undoing the BGR/mean preprocessing); counts calls."""

    def __init__(self, assets=ASSETS):
        self.by_colour = [(np.array(rgb, float), s) for rgb, s in assets.values()]
        self.calls = 0

    def predict(self, x: np.ndarray) -> float:
        assert x.shape == (1, 224, 224, 3) and x.dtype == np.float32
        self.calls += 1
        rgb = (x[0, 112, 112] + MEANS_BGR)[::-1]
        return min(self.by_colour, key=lambda cs: np.abs(cs[0] - rgb).sum())[1]


@pytest.fixture
def world(tmp_path, monkeypatch):
    cfg = Config(shoebox_root=tmp_path / "shoebox", sources={})
    monkeypatch.setattr(config, "load", lambda *a, **k: cfg)
    for aid, (rgb, _) in ASSETS.items():
        p = runners.derived_image(cfg, "infer-v1", aid)
        p.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (320, 240), rgb).save(p, quality=95)
        write_derived(cfg, aid)
    return cfg


def items():
    return [{"asset_id": a} for a in ASSETS]


def no_network(monkeypatch):
    def refuse(*a, **k):
        raise AssertionError("network used during screening")
    monkeypatch.setattr(socket, "socket", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)
    monkeypatch.setattr(socket, "getaddrinfo", refuse)


def test_candidate_resolves_with_hashed_threshold():
    res = runners.resolve("nsfw-coreml-v1")
    assert res.cand["runtime"] == "screen" and res.kind == "screen"
    assert res.params["threshold"] == 0.2 and res.params["compute_units"] == "ALL"
    assert res.params["package_sha256"] == "6b935854cc5a3e8c1153377c2020bdf6b46127846dd5386859192f3994bc2d70"
    assert res.params["profile"]["name"] == "infer-v1"
    assert res.store_dir(Config(shoebox_root=Path("/x"), sources={})) == Path(
        "/x/results/screen/nsfw-coreml-v1") / res.params_hash


def test_threshold_boundary_and_boolean_only_records(world, monkeypatch):
    no_network(monkeypatch)
    res = runners.resolve("nsfw-coreml-v1")
    model = StubModel()
    tally = screen.screen_items(res, items(), world, model=model)
    assert (tally.screened, tally.flagged, tally.cached, tally.errors) == (5, 3, 0, 0)
    recs = store.index(world, res.model_id, res.params_hash, "screen")
    got = {aid: r["sensitive"] for aid, r in recs.items()}
    # score >= threshold is sensitive: the boundary errs towards quarantine
    assert got == {"a" * 64: False, "b" * 64: False, "c" * 64: True, "d" * 64: True, "e" * 64: True}
    for r in recs.values():
        assert set(r) == {"asset_id", "model_id", "params_hash", "status", "sensitive"}
        assert r["status"] == "ok"
    raw = "".join(p.read_text() for p in res.store_dir(world).glob("*"))
    assert "0.2001" not in raw and "0.95" not in raw and "score" not in raw


def test_flags_once_and_rerun_is_a_cache_hit(world):
    res = runners.resolve("nsfw-coreml-v1")
    model = StubModel()
    screen.screen_items(res, items(), world, model=model)
    rows = quarantine.load()
    assert set(rows) == {"c" * 64, "d" * 64, "e" * 64}
    assert all(r["verdict"] == "pending" and r["screen"] == "nudity" for r in rows.values())
    again = screen.screen_items(res, items(), world, model=model)
    assert model.calls == 5
    assert (again.screened, again.cached, again.flagged) == (0, 5, 0)
    assert len(quarantine.QUARANTINE_PATH.read_text().splitlines()) == 3


def test_rerun_never_loads_the_model(world, monkeypatch):
    res = runners.resolve("nsfw-coreml-v1")
    screen.screen_items(res, items(), world, model=StubModel())
    monkeypatch.setattr(screen, "load_model", lambda *a, **k: pytest.fail("model loaded on a cache hit"))
    assert screen.screen_items(res, items(), world).cached == 5


def test_threshold_change_rescreens_under_new_key(world):
    res = runners.resolve("nsfw-coreml-v1")
    screen.screen_items(res, items(), world, model=StubModel())
    cand = {k: v for k, v in res.cand.items() if k not in ("name", "model_id", "params_hash")} | {"threshold": 0.5}
    res2 = runners._resolve_cand("nsfw-coreml-v1", cand, runners.CONFIG_DIR)
    assert res2.params_hash != res.params_hash
    model = StubModel()
    t = screen.screen_items(res2, items(), world, model=model)
    assert model.calls == 5 and t.screened == 5 and t.flagged == 1
    recs = store.index(world, res2.model_id, res2.params_hash, "screen")
    assert [a for a, r in recs.items() if r["sensitive"]] == ["e" * 64]


def test_missing_or_stale_derived_is_an_error_record(world):
    res = runners.resolve("nsfw-coreml-v1")
    runners.derived_image(world, "infer-v1", "a" * 64).unlink()
    stale = {**runners.profile_params("infer-v1"), "settings_hash": "0" * 16}
    write_derived(world, "b" * 64, profile_params=stale)
    model = StubModel()
    t = screen.screen_items(res, [*items(), {"asset_id": "f" * 64}], world, model=model)
    assert (t.screened, t.errors) == (6, 3) and model.calls == 3
    recs = store.index(world, res.model_id, res.params_hash, "screen")
    for aid in ("a" * 64, "b" * 64, "f" * 64):
        assert recs[aid]["status"] == "error" and recs[aid]["sensitive"] is None and recs[aid]["error"]
        assert "score" not in recs[aid]


def test_model_failure_is_an_error_record(world):
    class Broken(StubModel):
        def predict(self, x):
            raise RuntimeError("ane fell over")
    res = runners.resolve("nsfw-coreml-v1")
    t = screen.screen_items(res, items()[:1], world, model=Broken())
    assert t.errors == 1 and not quarantine.load()


def test_package_hash_mismatch_is_refused(tmp_path):
    pkg = tmp_path / "m.mlpackage"
    (pkg / "Data").mkdir(parents=True)
    (pkg / "Data" / "w.bin").write_bytes(b"weights")
    with pytest.raises(RuntimeError, match="hash"):
        screen.CoreMLModel.load(pkg, "0" * 64)


def test_preprocess_matches_opennsfw_layout(tmp_path):
    p = tmp_path / "x.jpg"
    Image.new("RGB", (500, 300), (200, 100, 50)).save(p, quality=95)
    x = screen.preprocess(p)
    assert x.shape == (1, 224, 224, 3) and x.dtype == np.float32
    assert np.allclose(x[0, 112, 112] + MEANS_BGR, (50, 100, 200), atol=4)  # BGR


def test_cli_prints_counts_only(world, monkeypatch, capsys, tmp_path):
    no_network(monkeypatch)
    model = StubModel()
    monkeypatch.setattr(screen, "load_model", lambda cand, cfg: model)
    f = tmp_path / "items.jsonl"
    f.write_text("".join(json.dumps(it) + "\n" for it in items()[:3]))
    assert main(["screen", "run", "--items", str(f)]) == 0
    out = capsys.readouterr()
    assert json.loads(out.out.strip().splitlines()[-1]) == {
        "candidate": "nsfw-coreml-v1", "screened": 3, "flagged": 1, "cached": 0, "errors": 0}
    for aid in ASSETS:
        assert aid[:8] not in out.out + out.err
    assert main(["screen", "run", "--all-derived", "--workers", "2"]) == 0
    s = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert (s["screened"], s["cached"], s["flagged"]) == (2, 3, 2)


def test_cli_needs_items_or_all_derived(world):
    with pytest.raises(SystemExit):
        main(["screen", "run"])


REAL_PKG = Path("~/mnt/shoebox/models/opennsfw2-v1-float32.mlpackage").expanduser()


@pytest.mark.skipif(not REAL_PKG.exists(), reason="real Core ML package not present")
def test_real_package_loads_and_scores_a_synthetic_image(tmp_path):
    res = runners.resolve("nsfw-coreml-v1")
    model = screen.CoreMLModel.load(REAL_PKG, res.cand["package_sha256"])
    p = tmp_path / "x.jpg"
    Image.new("RGB", (300, 300), (40, 120, 200)).save(p)
    s = model.predict(screen.preprocess(p))
    assert 0.0 <= s <= 1.0
