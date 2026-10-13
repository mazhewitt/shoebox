"""Anthropic Message Batches runner against a fake transport only: no request leaves the process."""

import json
from dataclasses import replace

import httpx
import jsonschema
import pytest
from derived_fixture import write_derived
from test_gemini import env, jpeg_bytes  # noqa: F401 - env is a fixture

from shoebox import privacy, runners, store
from shoebox.runners import anthropic_batch as ab

GOOD = {
    "caption": "Two adults on a beach at sunset. The sea is calm.",
    "tags": ["Beach", "sunset"],
    "medium": "print",
    "digitiser": "flatbed_scanner",
    "colour_class": "mono",
    "confidence": {"medium": 0.8, "digitiser": 0.7, "colour_class": 0.95},
    "rotate_cw": 0,
}
USAGE = {"input_tokens": 3000, "output_tokens": 250, "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0}


def message(text, stop="end_turn", usage=USAGE):
    return {"id": "msg_x", "type": "message", "role": "assistant", "model": "claude-opus-5-5",
            "content": [{"type": "text", "text": text}], "stop_reason": stop, "usage": usage}


def ok(output=GOOD):
    return {"type": "succeeded", "message": message(json.dumps(output))}


def refused():
    return {"type": "succeeded", "message": message("", stop="refusal", usage={**USAGE, "output_tokens": 5})}


def truncated():
    return {"type": "succeeded", "message": message('{"caption": "Two adu', stop="max_tokens")}


def errored():
    return {"type": "errored", "error": {"type": "error", "error": {"type": "invalid_request_error",
                                                                    "message": "bad image"}}}


def expired():
    return {"type": "expired"}


class FakeTransport:
    """The Message Batches API in memory. `states` scripts what each poll returns per batch (the last entry
    repeats). `listed` are extra batches the provider lists (`{id, created_at, n}`), e.g. another client's."""

    def __init__(self, respond=lambda aid: ok(), states=("ended",), prefix="msgbatch_"):
        self.prefix = prefix
        self.respond, self.states_script = respond, list(states)
        self.batches: dict[str, dict] = {}
        self.submits: list[list[str]] = []
        self.polls: list[str] = []
        self.deleted: list[str] = []
        self.finds: list[tuple[str, int]] = []
        self.timeout_submits = 0
        self.find_fails = False
        self.listed: list[dict] = []

    def submit(self, requests):
        bid = f"{self.prefix}{len(self.submits)}"
        self.submits.append([cid for cid, _ in requests])
        self.batches[bid] = {"requests": requests, "states": list(self.states_script),
                             "created_at": ab.now_iso()}
        if self.timeout_submits:
            self.timeout_submits -= 1
            raise httpx.ReadTimeout("client timed out after the server accepted the batch")
        return bid

    def find(self, created_after, n):
        self.finds.append((created_after, n))
        if self.find_fails:
            raise httpx.ConnectError("cannot list batches")
        rows = [{"id": b, "created_at": v["created_at"], "n": len(v["requests"])} for b, v in self.batches.items()]
        return [r["id"] for r in [*rows, *self.listed] if r["created_at"] >= created_after and r["n"] == n]

    def poll(self, batch_id):
        self.polls.append(batch_id)
        states = self.batches[batch_id]["states"]
        return states.pop(0) if len(states) > 1 else states[0]

    def download(self, batch_id):
        return [{"custom_id": cid, "result": self.respond(cid)} for cid, _ in self.batches[batch_id]["requests"]]

    def delete(self, batch_id):
        self.deleted.append(batch_id)


AIDS = [f"{i:02x}" + "d" * 62 for i in range(5)]


@pytest.fixture
def a(env, monkeypatch):  # noqa: F811
    """`env` from the sync tests with Claude consented on the source, five derived items, the real candidate."""
    src = env.cfg.sources["importantfiles"]
    env.cfg.sources["importantfiles"] = replace(src, hosted_models=("gemini", "claude"))
    its = []
    for aid in AIDS:
        write_derived(env.cfg, aid, jpeg_bytes())
        its.append({"asset_id": aid, "source": "importantfiles", "relpath": f"Pictures/{aid[:4]}.jpg",
                    "group_id": None})
    env.items = its
    env.res = runners.resolve("opus-api-v2")
    env.ledger = env.cfg.results_dir / "batch-jobs" / "opus-api-v2"
    return env


def run(a, transport, items=None, res=None, **kw):
    kw.setdefault("sleep", lambda s: None)
    return ab.run_batch(res or a.res, a.items if items is None else items, a.cfg, transport, **kw)


def spend_lines(a):
    p = a.tmp / "spend.jsonl"
    return [json.loads(x) for x in p.read_text().splitlines()] if p.exists() else []


def stored(a):
    return store.index(a.cfg, a.res.model_id, a.res.params_hash)


PER_IMG = (3000 * 4 + 250 * 20) / 1e6 * 0.5


# ---------- candidate ----------


def test_candidate_resolves_to_the_anthropic_runner():
    res = runners.resolve("opus-api-v2")
    assert res.runner is ab and res.model_id == "anthropic/claude-opus-5-5"
    assert res.cand["budget_provider"] == "anthropic" and "budget_provider" not in res.params
    assert res.params["model"] == "claude-opus-5-5" and res.params["max_tokens"] == 2048
    assert ab.BATCH_RUNNER_VERSION in res.params["runtime_version"]
    assert res.params["schema_text"] == runners.resolve("opus-v2").params["schema_text"]
    assert res.params["prompt_text"] == runners.resolve("opus-v2").params["prompt_text"]


# ---------- happy path ----------


def test_batch_stores_labels_logs_spend_and_sends_a_1024px_thumbnail(a):
    t = FakeTransport()
    tally = run(a, t)
    assert len(t.submits) == 1 and sorted(t.submits[0]) == sorted(AIDS)
    assert (tally.submitted, tally.ok, tally.jobs_submitted) == (5, 5, 1)
    recs = stored(a)
    assert all(recs[x]["status"] == "ok" for x in AIDS)
    assert recs[AIDS[0]]["output"]["tags"] == ["beach", "sunset"]
    assert recs[AIDS[0]]["batch_job"] == "msgbatch_0"
    assert recs[AIDS[0]]["usage"]["input_tokens"] == 3000 and recs[AIDS[0]]["usd"] == pytest.approx(PER_IMG)
    lines = spend_lines(a)
    assert len(lines) == 1 and lines[0]["candidate"] == "opus-api-v2" and lines[0]["n"] == 5
    assert lines[0]["provider"] == "anthropic" and lines[0]["usd"] == pytest.approx(5 * PER_IMG)
    job = json.loads((a.ledger / "msgbatch_0.json").read_text())
    assert job["state"] == "stored" and job["n_requests"] == 5 and job["actual_usd"] == pytest.approx(5 * PER_IMG)
    assert t.deleted == ["msgbatch_0"]
    cid, params = t.batches["msgbatch_0"]["requests"][0]
    assert cid in AIDS and params["model"] == "claude-opus-5-5" and params["max_tokens"] == 2048
    img, text = params["messages"][0]["content"]
    assert img["type"] == "image" and img["source"]["media_type"] == "image/jpeg"
    assert text == {"type": "text", "text": a.res.cand["prompt_text"]}
    import base64
    import io

    from PIL import Image
    sent = Image.open(io.BytesIO(base64.b64decode(img["source"]["data"])))
    assert max(sent.size) == 1024  # the derived image (64x48 here) at the packets' 1024 px long edge
    assert params["output_config"]["format"]["type"] == "json_schema"
    t2 = FakeTransport()
    assert run(a, t2).cached == 5 and t2.submits == []


def test_refusal_truncation_errored_and_expired(a):
    canned = {AIDS[0]: refused(), AIDS[1]: truncated(), AIDS[2]: errored(), AIDS[3]: expired(),
              AIDS[4]: ok({**GOOD, "medium": "vinyl"})}
    tally = run(a, FakeTransport(respond=lambda x: canned[x]))
    got = stored(a)
    assert got[AIDS[0]]["status"] == "invalid" and got[AIDS[0]]["reason"] == "refused"
    assert got[AIDS[1]]["status"] == "invalid" and got[AIDS[1]]["reason"] == "truncated"
    assert got[AIDS[2]]["status"] == "error" and "bad image" in got[AIDS[2]]["error"] and got[AIDS[2]]["usd"] == 0
    assert AIDS[3] not in got and tally.returned == 1  # expired: not processed, pending again
    assert got[AIDS[4]]["status"] == "invalid" and "reason" not in got[AIDS[4]]
    assert runners.refusal_reason(got[AIDS[0]]) == "refused"
    # billed: the three succeeded results (refusal, truncation, schema failure)
    assert spend_lines(a)[0]["n"] == 3
    t2 = FakeTransport()
    run(a, t2)
    assert sorted(t2.submits[0]) == sorted([AIDS[2], AIDS[3]])  # error and expired are retried


def test_spend_is_logged_before_labels_and_once_per_job(a, monkeypatch):
    order = []
    real_spend, real_append = privacy.append_spend, store.append
    monkeypatch.setattr(privacy, "append_spend", lambda *x, **k: (order.append("spend"), real_spend(*x, **k)))
    monkeypatch.setattr(store, "append", lambda *x, **k: (order.append("label"), real_append(*x, **k)))
    calls = {"n": 0}

    def crash_on_third(*x, **k):
        calls["n"] += 1
        if calls["n"] == 3:
            raise RuntimeError("killed while storing")
        return real_append(*x, **k)

    monkeypatch.setattr(store, "append", lambda *x, **k: (order.append("label"), crash_on_third(*x, **k)))
    t = FakeTransport()
    with pytest.raises(RuntimeError):
        run(a, t)
    assert order[0] == "spend" and order.count("spend") == 1
    monkeypatch.setattr(store, "append", real_append)
    tally = run(a, t)  # resumes the downloaded job: no second spend line, no resubmit
    assert len(spend_lines(a)) == 1 and len(t.submits) == 1 and tally.jobs_resumed == 1
    assert all(stored(a)[x]["status"] == "ok" for x in AIDS)


# ---------- intent reconciliation ----------


def test_intent_adopts_the_one_matching_batch(a):
    t = FakeTransport()
    t.timeout_submits = 1
    with pytest.raises(httpx.ReadTimeout):
        run(a, t)
    intent = next(a.ledger.glob("intent_*.json"))
    rec = json.loads(intent.read_text())
    assert rec["state"] == "submitting" and rec["n_requests"] == 5 and rec["submitted_ts"]
    tally = run(a, t)
    assert len(t.submits) == 1 and t.finds and t.finds[0][1] == 5
    assert tally.jobs_resumed == 1 and tally.ok == 5 and not intent.exists()
    assert (a.ledger / "msgbatch_0.json").exists()


def test_intent_with_no_matching_batch_is_failed_and_resubmitted(a):
    t = FakeTransport()
    t.timeout_submits = 1
    with pytest.raises(httpx.ReadTimeout):
        run(a, t)
    t.batches.clear()  # the server never created it
    tally = run(a, t)
    assert tally.returned == 5 and len(t.submits) == 2 and tally.ok == 5
    failed = [json.loads(p.read_text()) for p in a.ledger.glob("intent_*.json")]
    assert [f["state"] for f in failed] == ["failed"]


def test_ambiguous_intent_refuses(a):
    t = FakeTransport()
    t.timeout_submits = 1
    with pytest.raises(httpx.ReadTimeout):
        run(a, t)
    t.listed.append({"id": "msgbatch_other", "created_at": "2999-01-01T00:00:00Z", "n": 5})
    with pytest.raises(ab.LedgerError, match="2 batches"):
        run(a, t)
    assert len(t.submits) == 1


def test_intent_ignores_batches_already_in_the_ledger(a):
    run(a, FakeTransport(), items=a.items[:2])
    t = FakeTransport(prefix="msgbatch_b")
    t.batches["msgbatch_known"] = {"requests": [("x", {})] * 3, "states": ["ended"], "created_at": "2999-01-01"}
    import shoebox.runners.gemini_batch as gb
    gb.save_job(a.cfg, {"job_id": "msgbatch_known", "display_name": "k", "candidate": "opus-api-v2",
                        "items": [], "state": "stored", "params_hash": a.res.params_hash})
    t.timeout_submits = 1
    with pytest.raises(httpx.ReadTimeout):
        run(a, t)  # 3 items left: same count as the known batch
    tally = run(a, t)
    assert tally.ok == 3 and len(t.submits) == 1


def test_failed_lookup_refuses(a):
    t = FakeTransport()
    t.timeout_submits = 1
    with pytest.raises(httpx.ReadTimeout):
        run(a, t)
    t.find_fails = True
    with pytest.raises(ab.LedgerError, match="lookup failed"):
        run(a, t)
    assert len(t.submits) == 1


# ---------- budgets ----------


def test_anthropic_spend_counts_only_against_its_own_budget(a):
    privacy.append_spend("gemini-batch-v2", 1, privacy.DEFAULT_MAX_TOTAL_USD - 0.001, "2026-10-11")
    t = FakeTransport()
    tally = run(a, t)  # the Gemini budget is spent, the Anthropic one is not
    assert tally.refused is None and len(t.submits) == 1
    assert privacy.spent_total() == pytest.approx(privacy.DEFAULT_MAX_TOTAL_USD - 0.001)
    assert privacy.spent_total(provider="anthropic") == pytest.approx(5 * PER_IMG)
    assert privacy.spent_total(provider=None) == pytest.approx(privacy.DEFAULT_MAX_TOTAL_USD - 0.001 + 5 * PER_IMG)


def test_anthropic_budget_refuses_when_spent(a):
    privacy.append_spend("opus-api-v2", 1, 99.999, "2026-10-13", provider="anthropic")
    t = FakeTransport()
    tally = run(a, t)
    assert t.submits == [] and "remaining anthropic budget $0.00" in tally.refused


def test_anthropic_rows_do_not_count_against_the_gemini_budget():
    lim = privacy.Limits()
    assert lim.budget("anthropic") == 100.0 and lim.budget("gemini") == privacy.DEFAULT_MAX_TOTAL_USD
    gem = {"max_usd_per_run": 5.0}
    est = privacy.Estimate(1, 1, 1, 1.0)
    privacy.check_budget(gem, est, already_spent=0.0, lim=lim)
    with pytest.raises(privacy.PrivacyError, match="remaining anthropic budget"):
        privacy.check_budget({**gem, "budget_provider": "anthropic"}, est, already_spent=99.5, lim=lim)
    with pytest.raises(privacy.PrivacyError, match="no budget"):
        privacy.check_budget({**gem, "budget_provider": "openai"}, est, already_spent=0.0, lim=lim)


def test_provider_budgets_from_the_hosted_table(tmp_path):
    p = tmp_path / "s.toml"
    p.write_text('shoebox_root = "/x"\n[hosted]\nmax_usd_total = 20\n[hosted.provider_budgets]\nanthropic = 7\n')
    lim = privacy.limits(p)
    assert lim.budget("anthropic") == 7.0 and lim.budget("gemini") == 20.0


def test_run_cap_refuses(a):
    res = replace(a.res, cand={**a.res.cand, "max_usd_per_run": 0.001})
    t = FakeTransport()
    assert "max_usd_per_run" in run(a, t, res=res).refused and t.submits == []


def test_never_send_item_is_not_sent_and_provider_is_claude(a, monkeypatch):
    (a.tmp / "never-send.txt").write_text(AIDS[0] + "\n")
    seen = []
    real = privacy.filter_items
    monkeypatch.setattr(privacy, "filter_items", lambda *x, **k: (seen.append(k.get("provider")), real(*x, **k))[1])
    t = FakeTransport()
    tally = run(a, t)
    assert AIDS[0] not in t.submits[0] and dict(tally.skipped_items)[AIDS[0]] == "never_send"
    assert seen and set(seen) == {"claude"}


def test_unconfirmed_checklist_refuses(a):
    (a.tmp / "privacy-checklist.toml").write_text('billing_project_id = ""\n')
    t = FakeTransport()
    with pytest.raises(privacy.PrivacyError):
        run(a, t)
    assert t.submits == []


# ---------- schema adapter ----------


def test_schema_adapter_drops_unsupported_keywords_and_closes_objects():
    original = json.loads(runners.resolve("opus-api-v2").cand["schema_text"])
    adapted = ab.anthropic_schema({**original, "properties": {**original["properties"], "extra": {
        "type": "object", "properties": {"x": {"type": "array", "minItems": 3, "items": {"type": "string"}},
                                         "y": {"type": "array", "minItems": 1, "uniqueItems": True}}}}})
    bad = {"minimum", "maximum", "multipleOf", "minLength", "maxLength", "pattern", "maxItems", "uniqueItems"}

    def walk(node):
        if isinstance(node, dict):
            assert not bad & set(node), node
            if node.get("type") == "object" or "properties" in node:
                assert node["additionalProperties"] is False
            assert node.get("minItems", 0) <= 1
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    walk(adapted)
    assert adapted["properties"]["extra"]["properties"]["y"]["minItems"] == 1
    assert adapted["properties"]["caption"]["type"] == "string"
    assert adapted["properties"]["rotate_cw"]["enum"] == [0, 90, 180, 270]
    jsonschema.Draft202012Validator.check_schema(adapted)
    jsonschema.validate(GOOD, ab.anthropic_schema(original))
    assert original["properties"]["caption"]["maxLength"] == 1000  # the original is not modified


def test_local_validation_uses_the_full_schema(a):
    long_tag = {**GOOD, "tags": ["x" * 41]}  # maxLength 40 is dropped from the request schema
    run(a, FakeTransport(respond=lambda x: ok(long_tag)), items=a.items[:1])
    rec = stored(a)[AIDS[0]]
    assert rec["status"] == "invalid" and "schema" in rec["error"]


# ---------- live transport ----------


def test_http_transport_refuses_under_pytest(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-not-a-key")
    with pytest.raises(ab.AnthropicError, match="refused under pytest"):
        ab.HttpTransport().submit([("a", {})])
    with pytest.raises(ab.AnthropicError, match="refused under pytest"):
        ab.HttpTransport().find("2026-01-01T00:00:00Z", 1)


def test_run_one_refuses():
    with pytest.raises(runners.StopRun):
        ab.run_one({}, {}, None, None)


# ---------- tools/claude_batch.py ----------


def test_claude_batch_tool_quotes_and_runs_printing_counts_only(a, monkeypatch, capsys):
    import importlib.util

    spec = importlib.util.spec_from_file_location("claude_batch", runners.REPO_ROOT / "tools" / "claude_batch.py")
    tool = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(tool)
    monkeypatch.setattr(tool.config, "load", lambda: a.cfg)
    items = a.tmp / "items.jsonl"
    items.write_text("".join(json.dumps(it) + "\n" for it in a.items))
    t = FakeTransport()
    assert tool.main(["--items", str(items), "--quote"], transport=t) == 0 and t.submits == []
    quote = json.loads(capsys.readouterr().out)
    assert quote["to_send"] == 5 and quote["budget_provider"] == "anthropic" and quote["budget_usd"] == 100.0
    assert tool.main(["--items", str(items)], transport=t) == 0
    out = capsys.readouterr().out.strip().splitlines()[-1]
    assert json.loads(out)["ok"] == 5 and not any(x in out for x in AIDS)
