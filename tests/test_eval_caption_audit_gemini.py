"""Gemini caption auditor and the two-auditor report, against a mocked HTTP layer only."""

import base64
import json
from datetime import date

import httpx
import pytest
from test_eval_caption_audit import (
    REF,
    aid,
    fake_resolve,
    hand_example,
    s,
    setup_packets,
)

from shoebox import config, privacy, store
from shoebox.evals import caption_audit as ca
from shoebox.evals import caption_audit_gemini as cg
from shoebox.runners import gemini

FAKE_KEY = "FAKE-gemini-key-0123456789"
BASE = {"model": "gemini-3.8-flash", "media_resolution": "low", "thinking_level": "low",
        "price_input_usd_per_mtok": 0.75, "price_output_usd_per_mtok": 3.75, "price_date": "2026-10-10",
        "price_valid_until": "2026-12-31", "max_output_tokens": 4096}
ARMS6 = (*ca.ARMS, ca.OPUS_ARM)


def put_opus(cfg, a):
    r = fake_resolve(ca.OPUS_ARM)
    out = {"caption": "SECRET CAPTION opus", "tags": ["y"], "medium": "print", "digitiser": "flatbed_scanner",
           "colour_class": "mono", "rotate_cw": 90, "confidence": {}}
    store.append(cfg, {"asset_id": a, "model_id": r.model_id, "params_hash": r.params_hash, "status": "ok",
                       "output": out})


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
    # "own" allows gemini and claude; "cl" only claude (in the Opus packets, refused for Gemini)
    cfg = config.Config(shoebox_root=tmp_path / "shoebox", sources={
        "own": config.Source("own", src, "owner", False, True, hosted_models=("gemini", "claude")),
        "gem": config.Source("gem", src, "owner", False, True),
        "cl": config.Source("cl", src, "owner", False, True, hosted_models=("claude",))})
    rows, items = setup_packets(cfg)
    items[4]["source"] = "cl"  # c-005: packeted for Opus, refused for Gemini
    for i in range(1, 7):
        put_opus(cfg, aid(i))
    labels, sources = ca.load_arms(cfg, ARMS6, resolve=fake_resolve)
    d = tmp_path / "pk6"
    ca.packets(cfg, rows, items, labels, d, per_batch=2, seed=5, sources=sources)

    class Env:
        pass

    e = Env()
    e.cfg, e.tmp, e.rows, e.items, e.dir, e.requests = cfg, tmp_path, rows, items, d, []
    e.cand = cg.candidate(BASE, max_usd=3.0, prompt="Audit the captions.")
    e.mapping = json.loads((d / "map.json").read_text())

    def serve(fn):
        def handler(request):
            e.requests.append(request)
            return fn(request)
        monkeypatch.setattr(gemini, "_TRANSPORT", httpx.MockTransport(handler))

    e.serve = serve
    return e


def reply(labels, useful=lambda lab: 3, **over):
    out = {"reference": REF, "sets": {lab: {"wrong": i % 2, "invented": 0, "missing": 1, "useful": useful(lab),
                                            "notes": "SECRET NOTE"} for i, lab in enumerate(labels)}}
    out.update(over)
    return out


def respond(make=reply, usage=None):
    def fn(request):
        parts = json.loads(request.content)["contents"][0]["parts"]
        labels = [c["label"] for c in json.loads(parts[2]["text"].split("\n", 1)[1])]
        body = {"candidates": [{"content": {"parts": [{"text": json.dumps(make(labels))}]}, "finishReason": "STOP"}],
                "usageMetadata": usage or {"promptTokenCount": 2000, "candidatesTokenCount": 400,
                                           "thoughtsTokenCount": 100}}
        return httpx.Response(200, json=body)
    return fn


# ---------- packets ----------


def test_six_arm_packets_labels_a_to_f_and_blind(env):
    for m in env.mapping.values():
        assert sorted(m) == list("ABCDEF") and sorted(m.values()) == sorted(ARMS6)
    assert len({tuple(m.values()) for m in env.mapping.values()}) > 1
    text = "".join(p.read_text() for p in env.dir.glob("batch-*.json"))
    assert ca.OPUS_ARM not in text and "e4b-bf16" not in text
    meta = json.loads((env.dir / "packets.json").read_text())
    assert sorted(meta["arms"]) == sorted(ARMS6)
    # the default five arms still use A-E exactly as before
    assert ca.ARMS == ("e4b-bf16", "g12b-q4", "e4b-bf16-v2", "g12b-q4-v2", "gemini-medium-v2")
    assert ca.LABELS[:5] == "ABCDE"


def test_seven_arms_refused(env, tmp_path):
    labels = {f"a{i}": {} for i in range(7)}
    with pytest.raises(ValueError, match="only 6 labels"):
        ca.packets(env.cfg, [], [], labels, tmp_path / "x")


# ---------- request ----------


def test_request_shape_image_medium_sets_text_and_schema(env):
    _, _, photos = cg.read_packets(env.dir)
    photo = photos[0]
    body = cg.build_request(env.cand, photo, b"\xff\xd8jpeg")
    parts = body["contents"][0]["parts"]
    assert parts[0] == {"text": "Audit the captions."}
    assert parts[1]["mediaResolution"] == {"level": "MEDIA_RESOLUTION_MEDIUM"}
    assert base64.b64decode(parts[1]["inlineData"]["data"]) == b"\xff\xd8jpeg"
    shown = json.loads(parts[2]["text"].split("\n", 1)[1])
    assert [c["label"] for c in shown] == list("ABCDEF")
    assert shown[0]["caption"] == photo["captions"][0]["caption"]
    assert all(arm not in parts[2]["text"] for arm in ARMS6)
    gen = body["generationConfig"]
    assert gen["thinkingConfig"] == {"thinkingLevel": "LOW"} and "temperature" not in gen
    sch = gen["responseJsonSchema"]
    assert gemini.gemini_schema(sch) == sch  # only keywords Gemini accepts
    assert sch["properties"]["sets"]["required"] == list("ABCDEF")
    assert sch["properties"]["reference"]["properties"]["medium"]["enum"] == ca.enums()["medium"]
    assert set(sch["properties"]["sets"]["properties"]["F"]["required"]) == {"wrong", "invented", "missing",
                                                                            "useful", "notes"}


def test_validation(env):
    labels = list("ABCDEF")
    ok = {"candidates": [{"content": {"parts": [{"text": json.dumps(reply(labels))}]}, "finishReason": "STOP"}]}
    assert cg.parse_response(ok, labels)[0] == "ok"
    bad = reply(labels)
    bad["sets"]["C"]["useful"] = 7
    st, out, raw, err = cg.parse_response(
        {"candidates": [{"content": {"parts": [{"text": json.dumps(bad)}]}, "finishReason": "STOP"}]}, labels)
    assert st == "invalid" and out is None and "useful" in err and raw == json.dumps(bad)
    short = reply(list("ABCDE"))
    st, _, _, err = cg.parse_response(
        {"candidates": [{"content": {"parts": [{"text": json.dumps(short)}]}, "finishReason": "STOP"}]}, labels)
    assert st == "invalid" and "sets" in err
    rot = reply(labels, reference=dict(REF, rotate_cw=45))
    assert cg.parse_response({"candidates": [{"content": {"parts": [{"text": json.dumps(rot)}]}}]},
                             labels)[0] == "invalid"
    assert cg.parse_response({"candidates": [{"content": {"parts": [{"text": "nope"}]}}]}, labels)[0] == "invalid"
    trunc = {"candidates": [{"content": {"parts": [{"text": json.dumps(reply(labels))}]}, "finishReason": "MAX_TOKENS"}]}
    assert cg.parse_response(trunc, labels)[0] == "invalid"


# ---------- run ----------


def test_run_stores_unblinded_records_spend_and_cache(env):
    env.serve(respond())
    res = cg.run(env.cfg, env.dir, env.rows, env.items, env.cand, log=lambda m: None)
    # c-002 was never packeted (no consent); c-005 is refused for Gemini
    assert {s["key"]: s["reason"] for s in res.plan.skipped} == {"c-005": "no_consent"}
    assert res.calls == 4 and len(env.requests) == 4 and res.status == {"ok": 4}
    spend = [json.loads(x) for x in privacy.SPEND_PATH.read_text().splitlines()]
    per = (2000 * 0.75 + 500 * 3.75) / 1e6
    assert len(spend) == 4 and all(x["candidate"] == cg.NAME for x in spend)
    assert res.usd == pytest.approx(4 * per)
    ph, recs = ca.audit_records(env.cfg, "gemini/gemini-3.8-flash")
    assert ph == res.params_hash and [r["key"] for r in recs] == ["c-001", "c-003", "c-004", "c-006"]
    for r in recs:
        m = env.mapping[r["key"]]
        assert sorted(r["sets"]) == sorted(ARMS6) and r["reference"] == REF
        for lab, arm in m.items():
            i = "ABCDEF".index(lab)
            assert r["sets"][arm]["label"] == lab and r["sets"][arm]["wrong"] == i % 2
        missing = r["key"] == "c-003"
        assert (r["sets"]["g12b-q4"]["shown"] is None) == missing
        assert r["sets"][ca.OPUS_ARM]["shown"] == {"medium": "print", "digitiser": "flatbed_scanner",
                                                    "colour_class": "mono", "rotate_cw": 90}
    # cache: a second run makes no calls
    again = cg.run(env.cfg, env.dir, env.rows, env.items, env.cand, log=lambda m: None)
    assert again.calls == 0 and again.plan.cached == 4 and len(env.requests) == 4
    # the report reads them
    rep = ca.build_report(recs, {}, b=50, self_audit_arm=cg.SELF_AUDIT["gemini/gemini-3.8-flash"])
    assert set(rep["arms"]) == set(ARMS6) and rep["self_audit"] == {"gemini-medium-v2": ca.SELF_AUDIT_NOTE}


def test_params_hash_covers_prompt_resolution_arms_seed(env):
    meta = json.loads((env.dir / "packets.json").read_text())
    h = cg.params_hash(env.cand, meta)
    assert cg.params_hash(dict(env.cand, prompt_text="other"), meta) != h
    assert cg.params_hash(dict(env.cand, media_resolution="low"), meta) != h
    assert cg.params_hash(env.cand, dict(meta, seed=6)) != h
    assert cg.params_hash(env.cand, dict(meta, arms=list(ca.ARMS))) != h


def test_invalid_reply_stored_with_raw(env):
    def bad(labels):
        out = reply(labels)
        del out["sets"][labels[-1]]
        return out

    env.serve(respond(bad))
    res = cg.run(env.cfg, env.dir, env.rows, env.items, env.cand, limit=1, log=lambda m: None)
    assert res.status == {"invalid": 1}
    recs = list(store.iter_records(env.cfg, "gemini/gemini-3.8-flash", res.params_hash, ca.KIND))
    assert recs[0]["status"] == "invalid" and recs[0]["sets"] is None and "SECRET NOTE" in recs[0]["raw"]
    assert ca.audit_records(env.cfg, "gemini/gemini-3.8-flash")[1] == []


def test_run_cap_stops_before_the_next_call(env):
    env.serve(respond(usage={"promptTokenCount": 1_000_000, "candidatesTokenCount": 0, "thoughtsTokenCount": 0}))
    res = cg.run(env.cfg, env.dir, env.rows, env.items, cg.candidate(BASE, max_usd=0.5, prompt="x"),
                 log=lambda m: None)
    assert res.calls == 1 and res.stopped and "--max-usd" in res.stopped and len(env.requests) == 1


def test_preflight_over_cap_refuses_without_http(env):
    env.serve(respond())
    with pytest.raises(privacy.PrivacyError, match="max_usd_per_run"):
        cg.run(env.cfg, env.dir, env.rows, env.items, cg.candidate(BASE, max_usd=1e-6, prompt="x"),
               log=lambda m: None)
    assert env.requests == []


def test_cli_dry_run_makes_no_http(env, monkeypatch, capsys):
    from shoebox.cli import main
    from shoebox.commands import eval as eval_cmd

    sets = env.tmp / "sets" / "s"
    sets.mkdir(parents=True)
    (sets / ca.SAMPLE_FILE).write_text("".join(json.dumps(r) + "\n" for r in env.rows))
    (sets / "items.jsonl").write_text("".join(json.dumps(r) + "\n" for r in env.items))
    monkeypatch.setattr(eval_cmd, "_cfg", lambda: env.cfg)
    env.serve(respond())
    assert main(["eval", "caption-audit-gemini", "--set", "s", "--packets", str(env.dir), "--dry-run",
                 "--sets-dir", str(env.tmp / "sets")]) == 0
    out = capsys.readouterr().out
    assert "dry run" in out and "4 photos" in out and "$" in out
    assert env.requests == [] and not privacy.SPEND_PATH.exists()


# ---------- two-auditor report ----------


def test_compare_side_by_side_agreement_and_self_audit():
    opus = hand_example()
    gem = hand_example()
    for r in gem:  # Gemini: e4b-bf16 useful one higher on even photos, and never errs on photo 1
        r["sets"]["e4b-bf16"] = dict(r["sets"]["e4b-bf16"])
        if int(r["key"][2:]) % 2 == 0:
            r["sets"]["e4b-bf16"]["useful"] = 3
    gem[0]["sets"]["e4b-bf16"]["wrong"] = 0
    for i, r in enumerate(opus):
        r["sets"][ca.OPUS_ARM] = s(useful=5 if i % 2 else 4)
    for i, r in enumerate(gem):
        r["sets"][ca.OPUS_ARM] = s(useful=3 if i % 2 else 2)
    ro = ca.build_report(opus, {"opus_model_id": "claude/opus-5.5"}, b=50, self_audit_arm=ca.OPUS_ARM)
    rg = ca.build_report(gem, {"auditor": "Gemini", "auditor_model_id": "gemini/x"}, b=50,
                         self_audit_arm=ca.REFERENCE_ARM)
    rep = cg.compare(ro, rg, opus, gem, {"set": "s"})
    ag = rep["agreement"]
    assert ag["e4b-bf16"]["n"] == 10
    assert ag["e4b-bf16"]["error_rate_mean_abs_diff"] == 0.1  # photo 1 only
    assert ag["e4b-bf16"]["useful_mean_abs_diff"] == 0.5
    assert ag[ca.OPUS_ARM]["useful_spearman"] == 1.0 and ag[ca.OPUS_ARM]["useful_mean_abs_diff"] == 2.0
    assert ag["gemini-medium-v2"]["useful_spearman"] is None  # constant on both sides
    side = rep["side_by_side"]
    assert side[ca.OPUS_ARM]["opus"]["self_audit"] and not side[ca.OPUS_ARM]["gemini"]["self_audit"]
    assert side["gemini-medium-v2"]["gemini"]["self_audit"] and not side["gemini-medium-v2"]["opus"]["self_audit"]
    assert ro["paired_vs_reference"][ca.OPUS_ARM]["self_audit"] is True
    md = cg.render_compare_md(rep)
    assert f"| {ca.OPUS_ARM} | opus ({ca.SELF_AUDIT_NOTE}) |" in md
    assert f"| gemini-medium-v2 | gemini ({ca.SELF_AUDIT_NOTE}) |" in md
    assert f"| {ca.OPUS_ARM} | gemini |" in md
    assert any(x.startswith(f"| {ca.OPUS_ARM} | 10 |") and x.endswith(f"| {ca.SELF_AUDIT_NOTE} |")
               for x in md.splitlines())  # the paired D11 cell
    assert "SECRET" not in md + json.dumps(rep)


def test_cli_caption_report_both(env, monkeypatch):
    from shoebox.cli import main
    from shoebox.commands import eval as eval_cmd

    env.serve(respond())
    cg.run(env.cfg, env.dir, env.rows, env.items, env.cand, log=lambda m: None)
    # Opus audit of the same packets via the existing ingest
    lines = []
    for k, m in env.mapping.items():
        lines.append({"key": k, **reply(sorted(m))})
    (env.dir / "audit-001.jsonl").write_text("".join(json.dumps(x) + "\n" for x in lines))
    ca.ingest(env.cfg, env.dir, env.rows, "instr")
    monkeypatch.setattr(eval_cmd, "_cfg", lambda: env.cfg)
    out = env.tmp / "runs"
    for who in ("both", "gemini", "opus"):
        assert main(["eval", "caption-report", "--set", "s", "--date", "2026-10-14", "--bootstrap", "20",
                     "--auditor", who, "--out-dir", str(out)]) == 0
    both = json.loads((out / "2026-10-14_s-caption-audit-both.json").read_text())
    assert set(both["auditors"]) == {"opus", "gemini"} and both["agreement"][ca.OPUS_ARM]["n"] == 4
    blob = "".join(p.read_text() for p in out.iterdir())
    assert len(list(out.iterdir())) == 6 and "SECRET" not in blob
    assert ca.SELF_AUDIT_NOTE in (out / "2026-10-14_s-caption-audit-gemini.md").read_text()
