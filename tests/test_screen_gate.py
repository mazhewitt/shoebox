"""Screen gate (spec content-screen: "screened ... before any stage that sends, shows or captions it").

Every test here is marked `real_screen_gate`: the gate loads real screen records from a synthetic store under
tmp_path through `runners.resolve(SCREEN_CANDIDATE)`, so nothing is stubbed between the store and the refusal.
"""

import pytest
from test_privacy import AID, cfg_held, item
from test_worklist import aid, world  # noqa: F401 - the worklist's synthetic frames fixture

from shoebox import privacy, runners, screened, store, worklist

pytestmark = pytest.mark.real_screen_gate


def screen(cfg, asset_id, status="ok", sensitive=False):
    """A screen record as `runners.screen.screen_items` writes it, under the configured candidate's key."""
    res = runners.resolve(screened.SCREEN_CANDIDATE)
    rec = {"asset_id": asset_id, "model_id": res.model_id, "params_hash": res.params_hash, "status": status,
           "sensitive": None if status == "error" else sensitive}
    if status == "error":
        rec["error"] = "no ok derived image"
    store.append(cfg, rec, res.kind)


@pytest.mark.parametrize("provider", ["gemini", "claude"])
def test_unscreened_asset_refused_for_every_provider(tmp_path, provider):
    cfg = cfg_held(tmp_path, hosted_models=("gemini", "claude"))
    elig = privacy.filter_items([item()], cfg, privacy.NeverSend(), provider=provider)
    assert elig.allowed == []
    assert [(s.asset_id, s.reason) for s in elig.skipped] == [(AID, "unscreened")]


def test_screen_error_record_counts_as_unscreened(tmp_path):
    cfg = cfg_held(tmp_path, hosted_models=("gemini", "claude"))
    screen(cfg, AID, status="error")
    assert [s.reason for s in privacy.filter_items([item()], cfg, privacy.NeverSend()).skipped] == ["unscreened"]


def test_record_under_another_params_hash_counts_as_unscreened(tmp_path):
    cfg = cfg_held(tmp_path, hosted_models=("gemini", "claude"))
    res = runners.resolve(screened.SCREEN_CANDIDATE)
    store.append(cfg, {"asset_id": AID, "model_id": res.model_id, "params_hash": "0" * 16, "status": "ok",
                       "sensitive": False}, res.kind)
    assert [s.reason for s in privacy.filter_items([item()], cfg, privacy.NeverSend()).skipped] == ["unscreened"]


@pytest.mark.parametrize("provider", ["gemini", "claude"])
def test_screened_clean_asset_passes(tmp_path, provider):
    cfg = cfg_held(tmp_path, hosted_models=("gemini", "claude"))
    screen(cfg, AID)
    assert privacy.filter_items([item()], cfg, privacy.NeverSend(), provider=provider).allowed == [item()]


def test_privacy_reasons_come_before_unscreened(tmp_path):
    """`unscreened` means "otherwise eligible": a held-back asset keeps its privacy reason."""
    cfg = cfg_held(tmp_path, held_back=["Pictures"], hosted_models=("gemini",))
    assert [s.reason for s in privacy.filter_items([item()], cfg, privacy.NeverSend()).skipped] == ["held_back"]


def test_screen_index_is_injectable(tmp_path):
    cfg = cfg_held(tmp_path, hosted_models=("gemini",))
    idx = screened.ScreenIndex(frozenset({AID}))
    assert privacy.filter_items([item()], cfg, privacy.NeverSend(), screened=idx).allowed == [item()]
    none = screened.ScreenIndex(frozenset())
    assert privacy.filter_items([item()], cfg, privacy.NeverSend(), screened=none).allowed == []


def test_eval_set_items_already_screened_still_pass(tmp_path):
    """Eval sets were screened (retro-check, 9.3): their items pass once each has an ok record."""
    cfg = cfg_held(tmp_path, hosted_models=("gemini", "claude"))
    its = [item(f"{i:064x}", relpath=f"Pictures/{i}.jpg") for i in range(3)]
    for it in its:
        screen(cfg, it["asset_id"], sensitive=it is its[0])  # sensitive is screened; the quarantine decides
    for p in ("gemini", "claude"):
        assert privacy.filter_items(its, cfg, privacy.NeverSend(), provider=p).allowed == its


# ---------- local caption stage ----------


def test_local_caption_refuses_unscreened(tmp_path, monkeypatch):
    import sys
    import types

    from derived_fixture import write_derived

    from shoebox.config import Config

    d = tmp_path / "config"
    d.mkdir()
    (d / "candidates.toml").write_text('[candidates.fake-a]\nruntime = "fake"\nmodel = "m:1"\nprofile = "infer-v1"\n')
    calls = []
    mod = types.ModuleType("shoebox.runners.fake")
    mod.runtime_version = lambda cand: "fake 1.0"
    mod.run_one = lambda cand, it, image, cfg: calls.append(it["asset_id"]) or {
        "status": "ok", "output": {}, "raw": "", "error": None}
    monkeypatch.setitem(sys.modules, "shoebox.runners.fake", mod)
    cfg = Config(shoebox_root=tmp_path / "shoebox", sources={})
    a, b, c = "1" * 64, "2" * 64, "3" * 64
    for x in (a, b, c):
        write_derived(cfg, x)
    screen(cfg, a)
    screen(cfg, c, status="error")
    tally = runners.run_items(runners.resolve("fake-a", d), [{"asset_id": x} for x in (a, b, c)], cfg)
    assert calls == [a]
    assert sorted(tally.skipped_items) == [(b, "unscreened"), (c, "unscreened")]


# ---------- worklist ----------


def test_worklist_queues_unscreened_captures_as_never_sendable(world):  # noqa: F811
    for a in (2, 3, 4, 5, 6, 7, 8):  # a1 is the only derived, eligible capture; leave it unscreened
        screen(world, aid(a))
    wl = worklist.build(world, provider="gemini")
    c1 = next(c for c in wl.captures if aid(1) in c["members"])
    assert c1["queue"] == "unscreened" and c1["eligible"] is True  # eligible is the privacy verdict alone
    n = wl.counts
    assert (n["hosted"], n["unscreened"]) == (0, 1)
    worklist.check_counts(n)
    md = worklist.summary_md(wl, "t", "gemini-batch-v2", {
        "media_resolution": "medium", "price_input_usd_per_mtok": 0.75, "price_output_usd_per_mtok": 3.75,
        "price_batch_discount": 0.5, "price_date": "2026-10-11", "price_valid_until": "2099-12-31"})
    assert "unscreened 1" in md
    screen(world, aid(1))
    assert next(c for c in worklist.build(world).captures if aid(1) in c["members"])["queue"] == "hosted"


def test_worklist_unscreened_local_capture_is_not_queued_local(world):  # noqa: F811
    """a8 (kyra, no consent) would go to the local 12B queue, which refuses unscreened assets too."""
    for a in (1, 2, 3, 4, 5, 6, 7):
        screen(world, aid(a))
    c8 = next(c for c in worklist.build(world).captures if aid(8) in c["members"])
    assert c8["queue"] == "unscreened"
    assert c8["refusal"] == {"reason": "no_consent", "restricting_source": "kyra"}


# ---------- contact sheet ----------


def test_sheet_leaves_out_unscreened(tmp_path):
    from test_pilot_sheet import AID2, setup

    from shoebox.pilot.sheet import build_data

    cfg, items, labels = setup(tmp_path)
    screen(cfg, AID2)
    data = build_data(cfg, items, labels)
    assert [r["asset_id"] for r in data["items"]] == [AID2]
    assert data["left_out"] == {"quarantined": 0, "unscreened": 1}
