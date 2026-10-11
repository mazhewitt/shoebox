import json
import sys
import types

import pytest
from derived_fixture import write_derived

from shoebox import runners, store
from shoebox.config import Config

PROFILES = '[infer-v1]\nlong_edge = 2048\nquality = 92\n'


@pytest.fixture
def confdir(tmp_path):
    d = tmp_path / "config"
    (d / "prompts").mkdir(parents=True)
    (d / "schemas").mkdir()
    (d / "prompts" / "p.md").write_text("Describe it.")
    (d / "schemas" / "s.json").write_text('{"type": "object"}')
    (d / "candidates.toml").write_text(
        '[candidates.fake-a]\nruntime = "fake"\nmodel = "m:1"\nprompt = "p.md"\nschema = "s.json"\n'
        'profile = "infer-v1"\ntemperature = 0.0\nseed = 0\n'
    )
    return d


@pytest.fixture
def cfg(tmp_path):
    return Config(shoebox_root=tmp_path / "shoebox", sources={})


@pytest.fixture
def fake(monkeypatch):
    """A runner module `shoebox.runners.fake` whose run_one is scripted per test."""
    mod = types.ModuleType("shoebox.runners.fake")
    mod.calls = []
    mod.behaviour = {}

    def run_one(cand, item, image_path, cfg):
        mod.calls.append(item["asset_id"])
        b = mod.behaviour.get(item["asset_id"], "ok")
        if isinstance(b, BaseException):
            raise b
        if callable(b):
            b = b(cand)
        if b == "truncated":
            return {"status": "invalid", "reason": "truncated", "output": None, "raw": '{"x', "error": "cap",
                    "latency_ms": 5, "usd": 0.0}
        return {"status": b, "output": {"v": 1} if b == "ok" else None, "raw": "r", "error": None,
                "usage": {"input_tokens": 3, "output_tokens": 4, "thinking_tokens": None},
                "latency_ms": 5, "usd": 0.0}

    mod.run_one = run_one
    mod.runtime_version = lambda cand: "fake 1.0"
    mod.fingerprint = lambda cand: {"model_digest": "sha256:abc"}
    monkeypatch.setitem(sys.modules, "shoebox.runners.fake", mod)
    return mod


def items(n):
    return [{"asset_id": f"{i:02d}" + "a" * 62, "source": "s", "relpath": f"x{i}.jpg", "ext": "jpg"} for i in range(n)]


def make_derived(cfg, its, profile_params=None):
    for it in its:
        write_derived(cfg, it["asset_id"], b"jpeg", profile_params=profile_params)


def test_derived_image_path_formula(cfg):
    aid = "ab" + "c" * 62
    assert runners.derived_image(cfg, "infer-v1", aid) == cfg.shoebox_root / "derived" / "infer-v1" / "ab" / f"{aid}.jpg"


def test_load_candidates_merges_dropins_and_rejects_duplicates(confdir):
    (confdir / "candidates.d").mkdir()
    (confdir / "candidates.d" / "x.toml").write_text('[candidates.fake-b]\nruntime = "fake"\nmodel = "m:2"\n')
    cands = runners.load_candidates(confdir)
    assert set(cands) == {"fake-a", "fake-b"}
    assert cands["fake-b"]["name"] == "fake-b"
    (confdir / "candidates.d" / "y.toml").write_text('[candidates.fake-a]\nruntime = "fake"\n')
    with pytest.raises(ValueError, match="fake-a"):
        runners.load_candidates(confdir)


def test_resolve_hashes_prompt_schema_profile_and_runtime(confdir, cfg, fake):
    (confdir / "profiles.toml").write_text(PROFILES)
    res = runners.resolve("fake-a", confdir)
    assert res.model_id == "fake/m:1"
    assert res.params["prompt_text"] == "Describe it."
    assert res.params["schema_text"] == '{"type": "object"}'
    prof = res.params["profile"]
    assert (prof["name"], prof["settings"]) == ("infer-v1", {"long_edge": 2048, "quality": 92})
    assert res.params["runtime_version"] == "fake 1.0"
    assert res.params["model_digest"] == "sha256:abc"
    assert res.params_hash == store.params_hash(res.params)
    assert res.store_dir(cfg) == store.store_dir(cfg, "fake/m:1", res.params_hash)

    h = res.params_hash
    (confdir / "prompts" / "p.md").write_text("Describe it carefully.")
    assert runners.resolve("fake-a", confdir).params_hash != h
    (confdir / "prompts" / "p.md").write_text("Describe it.")
    assert runners.resolve("fake-a", confdir).params_hash == h
    (confdir / "profiles.toml").write_text(PROFILES.replace("92", "90"))
    assert runners.resolve("fake-a", confdir).params_hash != h
    fake.runtime_version = lambda cand: "fake 1.1"
    assert runners.resolve("fake-a", confdir).params_hash != h


def test_resolve_without_profiles_toml_uses_profile_name_only(confdir, fake):
    res = runners.resolve("fake-a", confdir)
    prof = res.params["profile"]
    assert (prof["name"], prof["settings"], prof["settings_hash"]) == ("infer-v1", None, None)


def test_unknown_candidate(confdir):
    with pytest.raises(KeyError):
        runners.resolve("nope", confdir)


def test_run_writes_records_then_rerun_is_cache_hit(confdir, cfg, fake):
    res = runners.resolve("fake-a", confdir)
    its = items(3)
    make_derived(cfg, its)
    t = runners.run_items(res, its, cfg)
    assert (t.ok, t.cached, t.skipped) == (3, 0, 0)
    assert len(fake.calls) == 3
    recs = list(store.iter_records(cfg, res.model_id, res.params_hash))
    r = recs[0]
    assert r["asset_id"] == its[0]["asset_id"]
    assert r["model_id"] == "fake/m:1" and r["params_hash"] == res.params_hash
    assert r["runtime"] == "fake 1.0" and "," in r["machine"] and r["ts"].endswith("Z")
    assert r["usage"]["input_tokens"] == 3
    params = json.loads((res.store_dir(cfg) / "params.json").read_text())
    assert params["candidate"] == "fake-a" and params["params_hash"] == res.params_hash
    assert params["params"]["prompt_text"] == "Describe it."

    fake.calls.clear()
    t2 = runners.run_items(res, its, cfg)
    assert fake.calls == []
    assert (t2.ok, t2.cached) == (0, 3)


def test_invalid_is_final_error_is_retried(confdir, cfg, fake):
    res = runners.resolve("fake-a", confdir)
    its = items(2)
    make_derived(cfg, its)
    fake.behaviour = {its[0]["asset_id"]: "invalid", its[1]["asset_id"]: "error"}
    t = runners.run_items(res, its, cfg)
    assert (t.invalid, t.error) == (1, 1)
    fake.calls.clear()
    fake.behaviour = {}
    t2 = runners.run_items(res, its, cfg)
    assert fake.calls == [its[1]["asset_id"]]
    assert (t2.cached, t2.ok) == (1, 1)
    assert store.lookup(cfg, res.model_id, res.params_hash, its[1]["asset_id"])["status"] == "ok"


def test_killed_run_resumes_from_first_missing_key(confdir, cfg, fake):
    res = runners.resolve("fake-a", confdir)
    its = items(5)
    make_derived(cfg, its)
    fake.behaviour = {its[2]["asset_id"]: KeyboardInterrupt()}
    with pytest.raises(KeyboardInterrupt):
        runners.run_items(res, its, cfg)
    assert fake.calls == [i["asset_id"] for i in its[:3]]
    fake.calls.clear()
    fake.behaviour = {}
    t = runners.run_items(res, its, cfg)
    assert fake.calls == [i["asset_id"] for i in its[2:]]
    assert (t.cached, t.ok) == (2, 3)


def test_runner_exception_becomes_error_record(confdir, cfg, fake):
    res = runners.resolve("fake-a", confdir)
    its = items(1)
    make_derived(cfg, its)
    fake.behaviour = {its[0]["asset_id"]: RuntimeError("boom")}
    t = runners.run_items(res, its, cfg)
    assert t.error == 1
    r = store.lookup(cfg, res.model_id, res.params_hash, its[0]["asset_id"])
    assert r["status"] == "error" and "boom" in r["error"]


def test_missing_derived_is_skipped_and_records_nothing(confdir, cfg, fake):
    res = runners.resolve("fake-a", confdir)
    its = items(2)
    make_derived(cfg, its[:1])
    t = runners.run_items(res, its, cfg)
    assert (t.ok, t.skipped) == (1, 1)
    assert t.skipped_items == [(its[1]["asset_id"], "no ok derived image (run `shoebox derive`)")]
    assert store.lookup(cfg, res.model_id, res.params_hash, its[1]["asset_id"]) is None


def test_preflight_filters_and_can_refuse(confdir, cfg, fake):
    res = runners.resolve("fake-a", confdir)
    its = items(3)
    make_derived(cfg, its)
    fake.preflight = lambda cand, its_, cfg_: its_[:1]
    t = runners.run_items(res, its, cfg)
    assert (t.ok, t.skipped) == (1, 2)
    assert all(reason == "refused by preflight" for _, reason in t.skipped_items)

    def refuse(cand, its_, cfg_):
        raise RuntimeError("privacy checklist missing")

    fake.preflight = refuse
    with pytest.raises(RuntimeError, match="privacy"):
        runners.run_items(res, its, cfg)


def test_duplicate_items_run_once(confdir, cfg, fake):
    res = runners.resolve("fake-a", confdir)
    its = items(1)
    make_derived(cfg, its)
    runners.run_items(res, its + its, cfg)
    assert len(fake.calls) == 1


def test_label_one_cached_or_runs(confdir, cfg, fake):
    res = runners.resolve("fake-a", confdir)
    its = items(2)
    make_derived(cfg, its)
    rec = runners.label_one(res, its[1]["asset_id"], its, cfg)
    assert rec["status"] == "ok" and fake.calls == [its[1]["asset_id"]]
    fake.calls.clear()
    assert runners.label_one(res, its[1]["asset_id"], its, cfg) == rec
    assert fake.calls == []
    with pytest.raises(LookupError):
        runners.label_one(res, "f" * 64, its, cfg)


def test_get_imports_runtime_module(fake):
    assert runners.get("fake") is fake
    with pytest.raises(ModuleNotFoundError):
        runners.get("no_such_runtime")


def test_budget_keys_and_profile_status_do_not_change_hash(confdir, fake):
    (confdir / "profiles.toml").write_text('[infer-v1]\nstatus = "provisional"\nlong_edge = 2048\n[infer-v1.raw]\ngamma = [2.222, 4.5]\n')
    res = runners.resolve("fake-a", confdir)
    assert res.params["profile"]["settings"] == {"long_edge": 2048, "raw": {"gamma": [2.222, 4.5]}}
    h = res.params_hash
    (confdir / "profiles.toml").write_text('[infer-v1]\nstatus = "frozen"\nlong_edge = 2048\n[infer-v1.raw]\ngamma = [2.222, 4.5]\n')
    toml = (confdir / "candidates.toml").read_text()
    (confdir / "candidates.toml").write_text(toml + 'max_usd_per_run = 2.0\nest_output_tokens = 300\nprice_date = "x"\n')
    assert runners.resolve("fake-a", confdir).params_hash == h
    (confdir / "candidates.toml").write_text(toml + "num_ctx = 4096\n")
    assert runners.resolve("fake-a", confdir).params_hash != h


def test_repo_candidates():
    cands = runners.load_candidates()
    for name, model in [("e4b-bf16", "gemma4:e4b-mlx-bf16"), ("e4b-q4", "gemma4:e4b"), ("g12b-q4", "gemma4:12b"),
                        ("g31b", "gemma4:31b")]:
        c = cands[name]
        assert c["runtime"] == "ollama" and c["model"] == model
        assert (c["think"], c["temperature"], c["seed"], c["image_budget"]) == (False, 0.0, 0, "auto")
        assert c["prompt"] == "label-v1.md" and c["schema"] == "label-v1.json" and c["profile"] == "infer-v1"
        assert c["quant"]
    assert cands["rules-v1"]["runtime"] == "rules"
    assert cands["rules-v1"]["schema"] == "label-v1.json"  # its params_hash covers the output schema
    assert runners.model_id(cands["rules-v1"]) == "rules-v1"
    assert runners.model_id(cands["e4b-bf16"]) == "ollama/gemma4:e4b-mlx-bf16"


def test_profile_hash_matches_derive(confdir, fake, monkeypatch):
    """The label hash covers what changes the derived bytes (settings, derive tools), not derive's check table."""
    from shoebox import derive

    (confdir / "profiles.toml").write_text('[infer-v1]\nlong_edge = 2048\n[infer-v1.check]\nmax_clip_increase_pct = 1.0\n')
    res = runners.resolve("fake-a", confdir)
    prof = res.params["profile"]
    assert prof["settings"] == {"long_edge": 2048}
    assert prof["settings_hash"] == derive.settings_hash({"long_edge": 2048})
    assert prof["tools"] == derive.tool_versions()
    h = res.params_hash
    (confdir / "profiles.toml").write_text('[infer-v1]\nlong_edge = 2048\n[infer-v1.check]\nmax_clip_increase_pct = 5.0\n')
    assert runners.resolve("fake-a", confdir).params_hash == h
    real = derive.tool_versions
    monkeypatch.setattr(derive, "tool_versions", lambda: dict(real(), libvips="9.0.0"))
    assert runners.resolve("fake-a", confdir).params_hash != h


def test_only_ok_current_derived_images_are_labelled(confdir, cfg, fake, monkeypatch):
    """The derive index decides: no ok record, or one made with other settings/tools, is skipped, not labelled."""
    from shoebox import derive

    (confdir / "profiles.toml").write_text(PROFILES)
    res = runners.resolve("fake-a", confdir)
    its = items(4)
    make_derived(cfg, its[:2], res.params["profile"])
    stale = {**res.params["profile"], "tools": {**res.params["profile"]["tools"], "libvips": "8.0.0"}}
    make_derived(cfg, its[2:3], stale)
    p = runners.derived_image(cfg, "infer-v1", its[3]["asset_id"])  # a file with no index record
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"jpeg")
    t = runners.run_items(res, its, cfg)
    assert t.ok == 2 and fake.calls == [it["asset_id"] for it in its[:2]]
    reasons = dict(t.skipped_items)
    assert "other profile settings or tools" in reasons[its[2]["asset_id"]]
    assert "no ok derived image" in reasons[its[3]["asset_id"]]
    assert derive.load_index(cfg, "infer-v1")[its[0]["asset_id"]]["status"] == "ok"


def test_stop_run_stops_the_run_without_a_record(confdir, cfg, fake):
    """A runner's StopRun (e.g. a spend cap) ends the run; it is not recorded as an `error` for every item."""
    res = runners.resolve("fake-a", confdir)
    its = items(3)
    make_derived(cfg, its)
    fake.behaviour[its[1]["asset_id"]] = runners.StopRun("cap reached")
    with pytest.raises(runners.StopRun):
        runners.run_items(res, its, cfg)
    assert fake.calls == [its[0]["asset_id"], its[1]["asset_id"]]
    assert set(store.index(cfg, res.model_id, res.params_hash)) == {its[0]["asset_id"]}


def test_gemini_spend_cap_is_a_stop_run():
    from shoebox.runners import gemini

    assert issubclass(gemini.SpendCapReached, runners.StopRun)


RETRY = '[candidates.fake-a.retry]\nnum_predict = 300\nrepeat_penalty = 1.1\n'


def test_retry_table_resolves_its_own_hash_and_leaves_the_first_attempts_hash(confdir, fake):
    h = runners.resolve("fake-a", confdir).params_hash
    (confdir / "candidates.toml").write_text((confdir / "candidates.toml").read_text() + RETRY)
    res = runners.resolve("fake-a", confdir)
    assert res.params_hash == h and "retry" not in res.params
    assert res.retry.name == "fake-a~retry" and res.retry.model_id == res.model_id
    assert res.retry.params["num_predict"] == 300 and res.retry.params["repeat_penalty"] == 1.1
    assert res.retry.params_hash != h and res.retry.retry is None


def test_truncated_items_are_retried_once_and_evals_read_the_retry(confdir, cfg, fake):
    (confdir / "candidates.toml").write_text((confdir / "candidates.toml").read_text() + RETRY)
    res = runners.resolve("fake-a", confdir)
    its = items(3)
    make_derived(cfg, its)
    a, b = its[0]["asset_id"], its[1]["asset_id"]
    fake.behaviour = {a: lambda c: "ok" if c.get("num_predict") == 300 else "truncated",
                      b: lambda c: "truncated"}
    t = runners.run_items(res, its, cfg)
    assert (t.ok, t.invalid, t.retried, t.recovered) == (1, 2, 2, 1)
    first = store.index(cfg, res.model_id, res.params_hash)
    assert first[a]["status"] == "invalid" and first[a]["reason"] == "truncated"
    view = runners.label_index(cfg, res)
    assert view[a]["status"] == "ok" and view[a]["retry_of"] == res.params_hash
    assert view[a]["params_hash"] == res.retry.params_hash
    assert view[b]["status"] == "invalid" and view[b]["retry_of"] == res.params_hash  # retry failed: a failure
    assert view[its[2]["asset_id"]] == first[its[2]["asset_id"]]
    fake.calls.clear()
    t2 = runners.run_items(res, its, cfg)
    assert fake.calls == [] and (t2.cached, t2.retried) == (3, 0)
    assert runners.label_source(res)["retry_params_hash"] == res.retry.params_hash


def test_truncated_without_a_retry_record_is_awaiting(confdir, cfg, fake):
    its = items(1)
    make_derived(cfg, its)
    fake.behaviour = {its[0]["asset_id"]: "truncated"}
    plain = runners.resolve("fake-a", confdir)
    runners.run_items(plain, its, cfg)  # no retry table yet: the truncated record just sits there
    (confdir / "candidates.toml").write_text((confdir / "candidates.toml").read_text() + RETRY)
    res = runners.resolve("fake-a", confdir)
    assert res.params_hash == plain.params_hash
    assert runners.label_index(cfg, res)[its[0]["asset_id"]]["awaiting_retry"] is True


def test_replay_reparses_into_the_new_hash_without_inference(confdir, cfg, fake, monkeypatch):
    res_old = runners.resolve("fake-a", confdir)
    its = items(3)
    make_derived(cfg, its)
    fake.behaviour = {its[1]["asset_id"]: "invalid", its[2]["asset_id"]: "error"}
    runners.run_items(res_old, its, cfg)
    old_dir = res_old.store_dir(cfg)

    fake.fingerprint = lambda cand: {"model_digest": "sha256:abc", "runner": "fake-runner-2"}
    fake.reparse = lambda cand, rec: {"status": "ok", "output": {"v": 2}, "raw": None, "error": None}
    res = runners.resolve("fake-a", confdir)
    assert res.params_hash != res_old.params_hash
    fake.calls.clear()
    counts = runners.replay(res, old_dir, cfg)
    assert fake.calls == []
    assert counts == {"ok": 2, "invalid": 0, "already": 0, "changed": 2}  # the error record is not replayed
    new = store.index(cfg, res.model_id, res.params_hash)
    assert set(new) == {its[0]["asset_id"], its[1]["asset_id"]}
    r = new[its[1]["asset_id"]]
    assert r["replay_of"] == res_old.params_hash and r["output"] == {"v": 2} and r["usage"]["input_tokens"] == 3
    assert runners.replay(res, old_dir, cfg)["already"] == 2  # idempotent

    toml = (confdir / "candidates.toml").read_text()
    (confdir / "candidates.toml").write_text(toml.replace("seed = 0", "seed = 1"))
    with pytest.raises(ValueError, match="seed"):
        runners.replay(runners.resolve("fake-a", confdir), old_dir, cfg)


@pytest.mark.parametrize("verdict, runs", [("pending", False), ("private", True), ("exclude", False),
                                           ("include", True)])
def test_local_run_honours_quarantine(confdir, cfg, fake, verdict, runs):
    from shoebox import quarantine

    res = runners.resolve("fake-a", confdir)
    its = items(2)
    make_derived(cfg, its)
    quarantine.flag([its[0]["asset_id"]], "nudity")
    if verdict != "pending":
        quarantine.record_verdict(its[0]["asset_id"], verdict, "owner")
    tally = runners.run_items(res, its, cfg)
    assert (its[0]["asset_id"] in fake.calls) is runs
    assert its[1]["asset_id"] in fake.calls
    if not runs:
        assert tally.skipped_items == [(its[0]["asset_id"], "quarantined")]


def test_local_run_refuses_same_pixel_copy_of_pending(confdir, cfg, fake, monkeypatch):
    from shoebox import copies, privacy, quarantine

    res = runners.resolve("fake-a", confdir)
    its = items(2)
    make_derived(cfg, its)
    pixels = {it["asset_id"]: "samepix" for it in its}
    monkeypatch.setattr(privacy, "default_copy_index", lambda c: copies.CopyIndex.build([], pixels))
    quarantine.flag([its[0]["asset_id"]], "nudity")
    runners.run_items(res, its[1:], cfg)
    assert fake.calls == []


def test_exclude_is_never_requeued_and_stays_refused(confdir, cfg, fake):
    from shoebox import quarantine

    res = runners.resolve("fake-a", confdir)
    its = items(1)
    make_derived(cfg, its)
    a = its[0]["asset_id"]
    quarantine.flag([a], "nudity")
    quarantine.record_verdict(a, "exclude", "owner")
    assert quarantine.flag([a], "nudity") == 0
    assert quarantine.flag([a], "document") == 0
    assert quarantine.load()[a]["verdict"] == "exclude"
    runners.run_items(res, its, cfg)
    assert fake.calls == []
    assert quarantine.Checker.load().refused(a, "hosted")
