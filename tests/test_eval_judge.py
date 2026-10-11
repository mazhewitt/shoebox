"""Grid relevance judge and pools (design D10) against a mocked HTTP layer only."""

import base64
import io
import json
from datetime import date

import httpx
import pytest
from PIL import Image

from shoebox import config, privacy, store
from shoebox.commands import eval as eval_cmd
from shoebox.evals import judge, search
from shoebox.runners import gemini

FAKE_KEY = "FAKE-gemini-key-0123456789"
BASE = {"model": "gemini-3.8-flash", "media_resolution": "low", "thinking_level": "low",
        "price_input_usd_per_mtok": 0.75, "price_output_usd_per_mtok": 3.75, "price_date": "2026-10-10",
        "price_valid_until": "2026-12-31", "max_output_tokens": 4096}


def aid(i: int) -> str:
    return f"{i:02x}" + "e" * 62


def jpeg_bytes(colour=(200, 120, 40)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (32, 24), colour).save(buf, "JPEG", quality=90)
    return buf.getvalue()


# ---------- pool ----------

QUERIES = [{"query_id": "q-b", "text": "beach", "split": "dev", "relevant": [aid(9)]},
           {"query_id": "q-a", "text": "snow", "split": "test", "relevant": [aid(1)]}]
RANKINGS = {"r1": {"params_hash": "h1", "rankings": {"q-a": [aid(1), aid(2), aid(3)], "q-b": [aid(4), aid(5), aid(6)]}},
            "r2": {"params_hash": "h2", "rankings": {"q-a": [aid(3), aid(7), aid(1)], "q-b": [aid(5), aid(4), aid(8)]}}}


def test_pool_is_union_of_top_k_plus_relevant_and_deterministic():
    rows = judge.build_pool(QUERIES, RANKINGS, k=2)
    assert [r["query_id"] for r in rows] == ["q-a", "q-b"]
    a, b = rows
    assert set(a["asset_ids"]) == {aid(1), aid(2), aid(3), aid(7)}  # aid(1) is r2's #3 but also relevant
    assert set(b["asset_ids"]) == {aid(4), aid(5), aid(9)}  # aid(9): relevant, retrieved by no route
    assert b["asset_ids"][-1] == aid(9) and b["routes"][aid(9)] == []
    assert a["routes"][aid(3)] == ["r2"] and a["routes"][aid(1)] == ["r1"]
    assert b["routes"][aid(4)] == ["r1", "r2"]
    assert judge.build_pool(list(reversed(QUERIES)), dict(reversed(RANKINGS.items())), k=2) == rows
    assert [r["query_id"] for r in judge.build_pool(QUERIES, RANKINGS, k=2, split="dev")] == ["q-b"]


def test_load_rankings_reads_only_routes_of_the_query_file(tmp_path):
    cfg = config.Config(shoebox_root=tmp_path / "sb", sources={})
    d = search.search_dir(cfg, "s")
    d.mkdir(parents=True)
    (d / "a.jsonl").write_text(json.dumps({"query_id": "q-a", "route": "x", "ranking": [aid(1)], "scores": [1]}) + "\n")
    (d / "index.json").write_text(json.dumps({"set": "s", "routes": {
        "x": {"params_hash": "h", "path": str(d / "a.jsonl"), "queries_file": "queries-v1.jsonl"},
        "old": {"params_hash": "o", "path": str(d / "missing.jsonl")}}}))
    got = judge.load_rankings(cfg, "s", "queries-v1.jsonl")
    assert got == {"x": {"params_hash": "h", "rankings": {"q-a": [aid(1)]}}}


# ---------- grids ----------


def test_grids_at_most_ten_and_seeded_shuffle():
    ids = [aid(i) for i in range(23)]
    grids = judge.split_grids(judge.shuffled("q-a", ids, 7), 10)
    assert [len(g) for g in grids] == [8, 8, 7]
    assert sorted(a for g in grids for a in g) == sorted(ids)
    assert judge.shuffled("q-a", list(reversed(ids)), 7) == judge.shuffled("q-a", ids, 7)
    assert judge.shuffled("q-a", ids, 7) != sorted(ids)
    assert judge.shuffled("q-a", ids, 8) != judge.shuffled("q-a", ids, 7)
    assert judge.shuffled("q-b", ids, 7) != judge.shuffled("q-a", ids, 7)
    assert judge.split_grids([], 10) == []


# ---------- runner ----------


@pytest.fixture
def env(tmp_path, monkeypatch):
    checklist = tmp_path / "privacy-checklist.toml"
    checklist.write_text(
        'billing_project_id = "p"\npaid_tier_with_prepay_balance = true\nai_studio_logging_off = true\n'
        f'log_retention_days = 7\nno_datasets_or_feedback = true\nconfirmed_on = "{date.today()}"\n')
    monkeypatch.setattr(privacy, "CHECKLIST_PATH", checklist)
    monkeypatch.setattr(privacy, "NEVER_SEND_PATH", tmp_path / "never-send.txt")
    monkeypatch.setattr(privacy, "SPEND_PATH", tmp_path / "spend.jsonl")
    monkeypatch.setattr(privacy, "SETS_DIR", tmp_path / "sets")
    monkeypatch.setattr(privacy, "limits", lambda path=None: privacy.Limits())
    monkeypatch.setattr(gemini, "ENV_PATH", tmp_path / ".env")
    monkeypatch.setenv("GEMINI_API_KEY", FAKE_KEY)
    monkeypatch.setattr(gemini, "_sleep", lambda s: None)
    src = tmp_path / "src"
    src.mkdir()
    cfg = config.Config(shoebox_root=tmp_path / "shoebox",
                        sources={"importantfiles": config.Source("importantfiles", src, "owner", False, True)})

    class Env:
        pass

    e = Env()
    e.cfg, e.tmp, e.requests = cfg, tmp_path, []
    e.cand = judge.judge_candidate(BASE, max_usd=25.0, prompt="Grade the photos.")
    e.items = []
    for i in range(14):
        a = aid(i)
        p = cfg.derived_dir / "infer-v1" / a[:2] / f"{a}.jpg"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(jpeg_bytes((i * 10, 100, 50)))
        e.items.append({"asset_id": a, "source": "importantfiles", "relpath": f"Pictures/{i}.jpg", "group_id": None})
    e.pool = [{"query_id": "q-a", "text": "snowy walk", "asset_ids": [aid(i) for i in range(12)]},
              {"query_id": "q-b", "text": "beach", "asset_ids": [aid(12), aid(13)]}]

    def serve(fn):
        def handler(request):
            e.requests.append(request)
            return fn(request)
        monkeypatch.setattr(gemini, "_TRANSPORT", httpx.MockTransport(handler))

    e.serve = serve
    return e


def respond(grades_for, usage=None):
    """A handler grading each request's photos with grades_for(n) -> list of grade entries."""
    def fn(request):
        parts = json.loads(request.content)["contents"][0]["parts"]
        n = sum("inlineData" in p for p in parts)
        body = {"candidates": [{"content": {"parts": [{"text": json.dumps({"grades": grades_for(n)})}]},
                                "finishReason": "STOP"}],
                "usageMetadata": usage or {"promptTokenCount": 3000, "candidatesTokenCount": 100,
                                           "thoughtsTokenCount": 0}}
        return httpx.Response(200, json=body)
    return fn


def all_twos(n):
    return [{"photo": i, "grade": 2} for i in range(1, n + 1)]


def records(env):
    return list(store.iter_records(env.cfg, env.cand["model_id"], judge.params_hash(env.cand), judge.KIND))


def test_request_shape(env):
    env.serve(respond(all_twos))
    res = judge.run(env.cfg, env.pool, env.cand, env.items, log=lambda m: None)
    assert res.calls == 3 and res.status == {"ok": 14}
    body = json.loads(env.requests[0].content)
    parts = body["contents"][0]["parts"]
    assert "Grade the photos." in parts[0]["text"] and '"snowy walk"' in parts[0]["text"]
    rest = parts[1:]
    assert len(rest) == 12  # q-a's 12 photos -> grids of 6 and 6
    for i in range(6):
        assert rest[2 * i] == {"text": f"Photo {i + 1}"}
        img = rest[2 * i + 1]
        assert img["mediaResolution"] == {"level": "MEDIA_RESOLUTION_LOW"}
        assert img["inlineData"]["mimeType"] == "image/jpeg"
    gen = body["generationConfig"]
    assert gen["thinkingConfig"] == {"thinkingLevel": "LOW"} and "temperature" not in gen
    sch = gen["responseJsonSchema"]["properties"]["grades"]
    assert sch["minItems"] == sch["maxItems"] == 6
    assert sch["items"]["properties"]["photo"]["maximum"] == 6
    # Photo N in the request is the record's grid position, and its image is that asset's derived JPEG
    recs = {(r["query_id"], r["asset_id"]): r for r in records(env)}
    first = [r for r in recs.values() if r["grid"]["id"] == recs[("q-a", aid(0))]["grid"]["id"]]
    for r in first:
        data = base64.b64decode(rest[2 * (r["grid"]["photo"] - 1) + 1]["inlineData"]["data"])
        assert data == (env.cfg.derived_dir / "infer-v1" / r["asset_id"][:2] / f"{r['asset_id']}.jpg").read_bytes()


@pytest.mark.parametrize("grades,why", [
    (lambda n: all_twos(n)[:-1], "not graded"),
    (lambda n: all_twos(n) + [{"photo": 1, "grade": 0}], "twice"),
    (lambda n: [{"photo": 1, "grade": 3}] + all_twos(n)[1:], "bad grade"),
    (lambda n: [{"photo": 1, "grade": True}] + all_twos(n)[1:], "bad grade"),
    (lambda n: all_twos(n)[:-1] + [{"photo": n + 1, "grade": 1}], "bad photo"),
])
def test_invalid_grid(env, grades, why):
    env.serve(respond(grades))
    pool = [env.pool[1]]
    res = judge.run(env.cfg, pool, env.cand, env.items, log=lambda m: None)
    assert res.status == {"invalid": 2}
    recs = records(env)
    assert len(recs) == 2 and all(r["status"] == "invalid" and r["grade"] is None for r in recs)
    assert why in recs[0]["error"] and json.loads(recs[0]["raw"])["grades"]


def test_spend_logged_per_call(env):
    env.serve(respond(all_twos))
    res = judge.run(env.cfg, env.pool, env.cand, env.items, log=lambda m: None)
    lines = [json.loads(x) for x in (env.tmp / "spend.jsonl").read_text().splitlines()]
    assert len(lines) == 3 and all(x["candidate"] == judge.JUDGE_NAME and x["n"] == 1 for x in lines)
    per = 3000 * 0.75e-6 + 100 * 3.75e-6
    assert sum(x["usd"] for x in lines) == pytest.approx(3 * per) == pytest.approx(res.usd)


def test_cap_stops_the_run_and_unsent_grids_have_no_records(env, monkeypatch):
    env.serve(respond(all_twos))  # ~$0.0026 per call
    monkeypatch.setattr(privacy, "check_budget", lambda *a, **k: None)  # the in-run stop, not the pre-flight
    res = judge.run(env.cfg, env.pool, {**env.cand, "max_usd_per_run": 0.003}, env.items, log=lambda m: None)
    assert len(env.requests) == 2 and res.stopped and "max-usd" in res.stopped
    assert len(records(env)) == 12  # two grids of 6; q-b's grid was never sent
    assert len((env.tmp / "spend.jsonl").read_text().splitlines()) == 2


def test_preflight_estimate_over_cap_refuses_before_any_call(env):
    env.serve(respond(all_twos))
    with pytest.raises(privacy.PrivacyError, match="max_usd_per_run"):
        judge.run(env.cfg, env.pool, {**env.cand, "max_usd_per_run": 0.0001}, env.items, log=lambda m: None)
    assert env.requests == []


def test_privacy_refused_photo_skipped_and_reported(env):
    (env.tmp / "never-send.txt").write_text(aid(13) + "\n")
    env.serve(respond(all_twos))
    logs = []
    res = judge.run(env.cfg, env.pool, env.cand, env.items, log=logs.append)
    assert res.plan.skipped == [{"query_id": "q-b", "asset_id": aid(13), "reason": "never_send"}]
    assert any(aid(13) in m and "never_send" in m for m in logs)
    sent = [json.loads(r.content)["contents"][0]["parts"] for r in env.requests]
    sent_bytes = {p["inlineData"]["data"] for parts in sent for p in parts if "inlineData" in p}
    never = base64.b64encode((env.cfg.derived_dir / "infer-v1" / aid(13)[:2] / f"{aid(13)}.jpg").read_bytes())
    assert never.decode() not in sent_bytes
    assert aid(13) not in {r["asset_id"] for r in records(env)}


def test_cache_hit_on_rerun(env):
    env.serve(respond(all_twos))
    judge.run(env.cfg, env.pool, env.cand, env.items, log=lambda m: None)
    n = len(env.requests)
    res = judge.run(env.cfg, env.pool, env.cand, env.items, log=lambda m: None)
    assert len(env.requests) == n and res.plan.cached == 14 and res.plan.grids == []


def test_same_photo_in_two_queries_is_two_keys(env):
    env.serve(respond(all_twos))
    pool = [{"query_id": "q-a", "text": "a", "asset_ids": [aid(0)]},
            {"query_id": "q-b", "text": "b", "asset_ids": [aid(0)]}]
    judge.run(env.cfg, pool, env.cand, env.items, log=lambda m: None)
    assert len(env.requests) == 2
    assert {(r["query_id"], r["asset_id"]) for r in judge.qrels(env.cfg, env.cand)} == {("q-a", aid(0)), ("q-b", aid(0))}


def test_qrels_export(env):
    env.serve(respond(lambda n: [{"photo": i, "grade": i % 3} for i in range(1, n + 1)]))
    judge.run(env.cfg, env.pool, env.cand, env.items, log=lambda m: None)
    rows = judge.qrels(env.cfg, env.cand)
    assert len(rows) == 14 and rows == sorted(rows, key=lambda r: (r["query_id"], r["asset_id"]))
    recs = {(r["query_id"], r["asset_id"]): r for r in records(env)}
    for r in rows:
        assert set(r) == {"query_id", "asset_id", "grade", "model_id", "params_hash"}
        assert r["grade"] == recs[(r["query_id"], r["asset_id"])]["grid"]["photo"] % 3
        assert r["params_hash"] == judge.params_hash(env.cand) and r["model_id"] == "gemini/gemini-3.8-flash"


def test_params_hash_tracks_settings(env):
    h = judge.params_hash(env.cand)
    for change in ({"prompt_text": "other"}, {"grid_size": 8}, {"shuffle_seed": 1}, {"media_resolution": "medium"},
                   {"thinking_level": "medium"}, {"model": "other"}):
        assert judge.params_hash({**env.cand, **change}) != h


def test_dry_run_makes_no_http_call(env, monkeypatch, capsys):
    set_dir = env.tmp / "sets" / "s"
    set_dir.mkdir(parents=True)
    judge.write_jsonl(set_dir / "pool-v1.jsonl", [{**r, "split": "dev"} for r in env.pool])
    judge.write_jsonl(set_dir / "items.jsonl", env.items)
    env.serve(lambda r: pytest.fail("HTTP call in a dry run"))
    monkeypatch.setattr(eval_cmd, "_cfg", lambda: env.cfg)
    monkeypatch.setattr(eval_cmd, "_judge_cand", lambda max_usd: {**env.cand, "max_usd_per_run": max_usd})
    from shoebox import cli
    rc = cli.main(["eval", "judge", "--set", "s", "--sets-dir", str(env.tmp / "sets"), "--dry-run"])
    out = capsys.readouterr().out
    assert rc == 0 and env.requests == []
    assert "3 grids, 14 images" in out and "$" in out
    assert not (env.tmp / "spend.jsonl").exists()
