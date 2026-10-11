"""Gemini batch runner against a fake transport only: no request leaves the process."""

import json
from dataclasses import replace
from datetime import date

import httpx
import pytest
from derived_fixture import write_derived
from test_gemini import env, jpeg_bytes  # noqa: F401 - env is a fixture

from shoebox import config, privacy, runners, store
from shoebox.runners import gemini, gemini_batch

GOOD = {
    "caption": "Two adults on a beach at sunset. The sea is calm.",
    "tags": ["Beach", "sunset", "Eiffel Tower"],
    "medium": "print",
    "digitiser": "flatbed_scanner",
    "colour_class": "colour",
    "confidence": {"medium": 0.8, "digitiser": 0.7, "colour_class": 0.95},
    "rotate_cw": 0,
}
USAGE = {"promptTokenCount": 2600, "candidatesTokenCount": 150, "thoughtsTokenCount": 0}


def ok_body(output=GOOD, finish="STOP", usage=USAGE):
    return {"candidates": [{"content": {"role": "model", "parts": [{"text": json.dumps(output)}]},
                            "finishReason": finish}],
            "usageMetadata": usage, "modelVersion": "gemini-3.8-flash"}


def refused_body():
    return {"promptFeedback": {"blockReason": "PROHIBITED_CONTENT"},
            "usageMetadata": {"promptTokenCount": 2600, "candidatesTokenCount": 0}}


def safety_finish_body():
    return {"candidates": [{"finishReason": "SAFETY"}], "usageMetadata": USAGE}


def truncated_body():
    return {"candidates": [{"content": {"parts": [{"text": '{"caption": "Two adul'}]},
                            "finishReason": "MAX_TOKENS"}], "usageMetadata": USAGE}


class FakeTransport:
    """submit/poll/download/delete in memory. `states` scripts what each poll returns per job (the last
    entry repeats); `crash_polls` makes the next n polls raise, as if the process died while polling."""

    def __init__(self, respond=lambda aid: ok_body(), states=("succeeded",)):
        self.respond, self.states_script = respond, list(states)
        self.jobs: dict[str, dict] = {}
        self.submits: list[list[str]] = []
        self.polls: list[str] = []
        self.deleted: list[str] = []
        self.crash_polls = 0

    def submit(self, model, requests, display_name):
        jid = f"batches/job{len(self.submits)}"
        self.submits.append([key for key, _ in requests])
        self.jobs[jid] = {"requests": requests, "states": list(self.states_script), "model": model}
        return jid

    def poll(self, job_id):
        self.polls.append(job_id)
        if self.crash_polls:
            self.crash_polls -= 1
            raise RuntimeError("process killed while polling")
        states = self.jobs[job_id]["states"]
        return states.pop(0) if len(states) > 1 else states[0]

    def download(self, job_id):
        return [{"key": key, "response": self.respond(key)} for key, _ in self.jobs[job_id]["requests"]]

    def delete(self, job_id):
        self.deleted.append(job_id)


AIDS = [f"{i:02x}" + "c" * 62 for i in range(5)]


@pytest.fixture
def b(env, monkeypatch):  # noqa: F811
    """`env` from the sync tests, plus derived index records for five items and the real candidate."""
    its = []
    for aid in AIDS:
        write_derived(env.cfg, aid, jpeg_bytes())
        its.append({"asset_id": aid, "source": "importantfiles", "relpath": f"Pictures/{aid[:4]}.jpg",
                    "group_id": None})
    env.items = its
    env.res = runners.resolve("gemini-batch-v2")
    env.ledger = env.cfg.results_dir / "batch-jobs" / "gemini-batch-v2"
    return env


def run(b, transport, items=None, res=None, **kw):
    kw.setdefault("sleep", lambda s: None)
    return gemini_batch.run_batch(res or b.res, b.items if items is None else items, b.cfg, transport, **kw)


def spend_lines(b):
    p = b.tmp / "spend.jsonl"
    return [json.loads(x) for x in p.read_text().splitlines()] if p.exists() else []


def stored(b, res=None):
    res = res or b.res
    return store.index(b.cfg, res.model_id, res.params_hash)


# ---------- candidate ----------


def test_candidate_copies_medium_v2_but_is_its_own_store_key():
    med, bat = runners.resolve("gemini-medium-v2"), runners.resolve("gemini-batch-v2")
    for k in ("model", "media_resolution", "thinking_level", "prompt_text", "schema_text", "max_output_tokens",
              "schema_field", "profile"):
        assert med.params[k] == bat.params[k], k
    assert bat.params["runtime"] == "gemini-batch" and "price_batch_discount" not in bat.params
    assert bat.cand["price_batch_discount"] == 0.5
    assert gemini_batch.BATCH_RUNNER_VERSION in bat.params["runtime_version"]
    assert (bat.model_id, bat.params_hash) != (med.model_id, med.params_hash)
    assert bat.runner is gemini_batch


def test_price_discount_is_not_hashed(monkeypatch):
    h = runners.resolve("gemini-batch-v2").params_hash
    cands = runners.load_candidates()
    cands["gemini-batch-v2"]["price_batch_discount"] = 0.4
    monkeypatch.setattr(runners, "load_candidates", lambda config_dir=None: cands)
    assert runners.resolve("gemini-batch-v2").params_hash == h


# ---------- happy path, parity, spend ----------


def test_job_stores_labels_logs_spend_and_writes_the_ledger(b):
    t = FakeTransport()
    tally = run(b, t)
    assert len(t.submits) == 1 and sorted(t.submits[0]) == sorted(AIDS)
    assert (tally.submitted, tally.ok, tally.jobs_submitted) == (5, 5, 1)
    recs = stored(b)
    assert all(recs[a]["status"] == "ok" for a in AIDS)
    assert recs[AIDS[0]]["output"]["tags"] == ["beach", "sunset", "eiffel tower"]  # normalised as sync
    assert recs[AIDS[0]]["batch_job"] == "batches/job0"
    lines = spend_lines(b)
    assert len(lines) == 1 and lines[0]["n"] == 5 and lines[0]["candidate"] == "gemini-batch-v2"
    per_img = (2600 * 0.75 + 150 * 3.75) / 1e6 * 0.5
    assert lines[0]["usd"] == pytest.approx(5 * per_img)
    job = json.loads((b.ledger / "batches_job0.json").read_text())
    assert job["job_id"] == "batches/job0" and job["state"] == "stored" and sorted(job["items"]) == sorted(AIDS)
    assert job["actual_usd"] == pytest.approx(5 * per_img) and job["est_usd"] > job["actual_usd"]
    assert t.deleted == ["batches/job0"]
    # the request is the sync runner's request
    _key, body = t.jobs["batches/job0"]["requests"][0]
    assert body["generationConfig"]["thinkingConfig"]["thinkingLevel"] == "LOW"
    assert body["contents"][0]["parts"][0]["mediaResolution"]["level"] == "MEDIA_RESOLUTION_MEDIUM"
    assert "tools" not in body
    # a second run makes no request
    t2 = FakeTransport()
    assert run(b, t2).cached == 5 and t2.submits == []


def test_parse_parity_with_sync_on_canned_responses(b, monkeypatch):
    sync = runners.resolve("gemini-medium-v2")
    canned = {AIDS[0]: ok_body(), AIDS[1]: ok_body(output={**GOOD, "medium": "vinyl"}),
              AIDS[2]: refused_body(), AIDS[3]: truncated_body()}
    run(b, FakeTransport(respond=lambda aid: canned[aid]), items=b.items[:4])
    got = stored(b)
    for aid, body in canned.items():
        monkeypatch.setattr(gemini, "_TRANSPORT",
                            httpx.MockTransport(lambda req, body=body: httpx.Response(200, json=body)))
        gemini.preflight(sync.cand, [b.items[AIDS.index(aid)]], b.cfg)
        s = runners.run_and_store(sync, b.items[AIDS.index(aid)], runners.derived_image(b.cfg, "infer-v1", aid),
                                  b.cfg)
        for k in ("status", "output", "raw", "error", "usage"):
            assert got[aid][k] == s[k], (aid, k)
        assert got[aid]["usd"] == pytest.approx(s["usd"] * 0.5)
        assert (got[aid]["model_id"], got[aid]["params_hash"]) != (s["model_id"], s["params_hash"])
    assert got[AIDS[0]]["status"] == "ok" and got[AIDS[1]]["status"] == "invalid"


def test_refused_and_truncated_reasons(b):
    canned = {AIDS[0]: refused_body(), AIDS[1]: safety_finish_body(), AIDS[2]: truncated_body(),
              AIDS[3]: ok_body(output={**GOOD, "medium": "vinyl"})}
    run(b, FakeTransport(respond=lambda aid: canned[aid]), items=b.items[:4])
    got = stored(b)
    assert got[AIDS[0]]["reason"] == "refused" and got[AIDS[0]]["status"] == "invalid"
    assert got[AIDS[1]]["reason"] == "refused"
    assert got[AIDS[2]]["reason"] == "truncated" and got[AIDS[2]]["status"] == "invalid"
    assert "reason" not in got[AIDS[3]]  # a schema failure is not routed


def test_spend_is_appended_before_labels_are_stored(b, monkeypatch):
    order = []
    real_spend, real_append = privacy.append_spend, store.append
    monkeypatch.setattr(privacy, "append_spend", lambda *a, **k: (order.append("spend"), real_spend(*a, **k)))
    monkeypatch.setattr(store, "append", lambda *a, **k: (order.append("label"), real_append(*a, **k)))
    run(b, FakeTransport())
    assert order[0] == "spend" and order.count("spend") == 1 and order.count("label") == 5


# ---------- guards ----------


def test_unconfirmed_checklist_refuses(b, monkeypatch):
    (b.tmp / "privacy-checklist.toml").write_text('billing_project_id = ""\n')
    t = FakeTransport()
    with pytest.raises(privacy.PrivacyError):
        run(b, t)
    assert t.submits == []


def test_expired_price_refuses(b, monkeypatch):
    monkeypatch.setattr(privacy, "_today", lambda: date(2027, 1, 1))
    t = FakeTransport()
    with pytest.raises(privacy.PrivacyError, match="expired"):
        run(b, t)
    assert t.submits == []


@pytest.mark.parametrize("guard", ["checklist", "price"])
def test_guards_refuse_before_anything_is_polled(b, monkeypatch, guard):
    t = FakeTransport(states=("running",))
    run(b, t, max_poll_rounds=1)
    polls = len(t.polls)
    if guard == "price":
        monkeypatch.setattr(privacy, "_today", lambda: date(2027, 1, 1))
    else:
        (b.tmp / "privacy-checklist.toml").write_text('billing_project_id = ""\n')
    with pytest.raises(privacy.PrivacyError):
        run(b, t, items=[], max_poll_rounds=1)
    assert len(t.polls) == polls and len(t.submits) == 1


def test_missing_batch_discount_refuses(b):
    res = replace(b.res, cand={k: v for k, v in b.res.cand.items() if k != "price_batch_discount"})
    t = FakeTransport()
    with pytest.raises(privacy.PrivacyError, match="price_batch_discount"):
        run(b, t, res=res)
    assert t.submits == []


def test_never_send_and_held_back_items_are_not_sent(b):
    (b.tmp / "never-send.txt").write_text(AIDS[0] + "\n")
    b.cfg.sources["importantfiles"] = config.Source("importantfiles", b.cfg.sources["importantfiles"].root,
                                                    "owner", False, True, held_back=("Medical/**",))
    b.items[1]["relpath"] = "Medical/x.jpg"
    t = FakeTransport()
    tally = run(b, t)
    sent = t.submits[0]
    assert AIDS[0] not in sent and AIDS[1] not in sent and len(sent) == 3
    assert dict(tally.skipped_items)[AIDS[0]] == "never_send"
    assert dict(tally.skipped_items)[AIDS[1]] == "held_back"


def test_a_skip_from_filter_items_prevents_the_request(b, monkeypatch):
    """Quarantine (and any later reason) lives in filter_items: whatever it skips is never sent."""
    real = privacy.filter_items

    def quarantining(items, cfg, *a, **k):
        assert k.get("provider") == "gemini"
        elig = real(items, cfg, *a, **k)
        keep = [it for it in elig.allowed if it["asset_id"] != AIDS[2]]
        return privacy.Eligibility(keep, [*elig.skipped, privacy.Skip(AIDS[2], "importantfiles", "x", "quarantined")])

    monkeypatch.setattr(privacy, "filter_items", quarantining)
    t = FakeTransport()
    tally = run(b, t)
    assert AIDS[2] not in t.submits[0] and dict(tally.skipped_items)[AIDS[2]] == "quarantined"


def test_all_items_filtered_makes_no_request(b, monkeypatch):
    monkeypatch.setattr(privacy, "filter_items",
                        lambda items, cfg, *a, **k: privacy.Eligibility([], [privacy.Skip(
                            it["asset_id"], "importantfiles", "x", "quarantined") for it in items]))
    t = FakeTransport()
    assert run(b, t).skipped == 5 and t.submits == []


def test_derived_image_with_exif_is_not_sent(b):
    write_derived(b.cfg, AIDS[3], jpeg_bytes(exif=True))
    t = FakeTransport()
    tally = run(b, t)
    assert AIDS[3] not in t.submits[0] and "metadata" in dict(tally.skipped_items)[AIDS[3]]


def test_over_cap_estimate_submits_nothing(b):
    res = replace(b.res, cand={**b.res.cand, "max_usd_per_run": 0.001})
    t = FakeTransport()
    tally = run(b, t, res=res)
    assert t.submits == [] and "max_usd_per_run" in tally.refused


def test_over_project_budget_submits_nothing(b):
    privacy.append_spend("earlier", 1, privacy.DEFAULT_MAX_TOTAL_USD - 0.001, "2026-10-10")
    t = FakeTransport()
    tally = run(b, t)
    assert t.submits == [] and "remaining project budget" in tally.refused


def test_cap_is_checked_between_jobs(b):
    one = privacy.estimate(gemini_batch.batch_priced(b.res.cand), 2).usd
    res = replace(b.res, cand={**b.res.cand, "max_usd_per_run": one * 1.5})
    per_item = privacy.estimate(b.res.cand, 1).input_tokens
    t = FakeTransport()
    tally = run(b, t, res=res, queue_tokens=2 * per_item)  # 2 items per job
    assert len(t.submits) == 1 and len(t.submits[0]) == 2 and tally.refused
    assert tally.ok == 2


def test_chunks_to_the_queue_limit(b):
    per_item = privacy.estimate(b.res.cand, 1).input_tokens
    t = FakeTransport()
    tally = run(b, t, queue_tokens=2 * per_item)
    assert [len(s) for s in t.submits] == [2, 2, 1] and tally.ok == 5
    assert len(spend_lines(b)) == 3  # one spend line per job


def test_queue_limit_waits_for_open_jobs_before_submitting_more(b):
    per_item = privacy.estimate(b.res.cand, 1).input_tokens
    t = FakeTransport(states=("running", "succeeded"))
    events = []
    real_submit, real_poll = t.submit, t.poll
    t.submit = lambda *a: (events.append("submit"), real_submit(*a))[1]
    t.poll = lambda j: (events.append("poll"), real_poll(j))[1]
    run(b, t, queue_tokens=3 * per_item)
    assert events[:4] == ["submit", "poll", "poll", "submit"]  # second job only once the first left the queue


# ---------- resume ----------


def test_crash_mid_poll_then_restart_polls_and_does_not_resubmit(b):
    t = FakeTransport(states=("running", "running", "succeeded"))
    t.crash_polls = 1
    with pytest.raises(RuntimeError, match="killed"):
        run(b, t)
    assert len(t.submits) == 1
    job = json.loads((b.ledger / "batches_job0.json").read_text())
    assert job["state"] == "submitted" and stored(b) == {}
    tally = run(b, t)
    assert len(t.submits) == 1  # no resubmit
    assert tally.ok == 5 and tally.jobs_resumed == 1
    assert json.loads((b.ledger / "batches_job0.json").read_text())["state"] == "stored"


def test_stopped_polling_leaves_the_job_open_for_the_next_run(b):
    t = FakeTransport(states=("running",))
    tally = run(b, t, max_poll_rounds=2)
    assert len(t.submits) == 1 and tally.open_jobs == 1 and stored(b) == {}
    t.jobs["batches/job0"]["states"] = ["succeeded"]
    assert run(b, t).ok == 5 and len(t.submits) == 1


def test_downloaded_but_not_stored_does_not_log_spend_twice(b, monkeypatch):
    t = FakeTransport()
    real_append = store.append
    calls = {"n": 0}

    def flaky(*a, **k):
        calls["n"] += 1
        if calls["n"] == 3:
            raise OSError("disk full")
        return real_append(*a, **k)

    monkeypatch.setattr(store, "append", flaky)
    with pytest.raises(OSError):
        run(b, t)
    monkeypatch.setattr(store, "append", real_append)
    tally = run(b, t)
    assert len(t.submits) == 1 and len(spend_lines(b)) == 1 and tally.ok == 3  # 2 were stored before the crash
    assert all(stored(b)[a]["status"] == "ok" for a in AIDS)


@pytest.mark.parametrize("state", ["expired", "failed", "cancelled"])
def test_expired_or_failed_job_returns_items_to_pending(b, state):
    t = FakeTransport(states=(state,))
    tally = run(b, t)
    assert tally.returned == 5 and stored(b) == {} and spend_lines(b) == []
    assert json.loads((b.ledger / "batches_job0.json").read_text())["state"] == state
    t.states_script = ["succeeded"]
    tally = run(b, t)
    assert len(t.submits) == 2 and sorted(t.submits[1]) == sorted(AIDS) and tally.ok == 5


def test_per_item_error_is_an_error_record_and_retried_later(b):
    t = FakeTransport()
    real = t.download
    t.download = lambda j: [{"key": r["key"], "error": {"code": 13, "message": "internal"}}
                            if r["key"] == AIDS[0] else r for r in real(j)]
    tally = run(b, t)
    assert tally.error == 1 and stored(b)[AIDS[0]]["status"] == "error"
    t2 = FakeTransport()
    run(b, t2)
    assert t2.submits == [[AIDS[0]]]


# ---------- framework hooks ----------


def test_run_one_stops_the_framework_loop():
    with pytest.raises(runners.StopRun, match="run_batch"):
        gemini_batch.run_one({}, {}, None, None)


def test_http_transport_refuses_under_pytest():
    with pytest.raises(gemini.GeminiError, match="pytest"):
        gemini_batch.HttpTransport("k").submit("gemini-3.8-flash", [], "x")
