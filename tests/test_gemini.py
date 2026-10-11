"""Gemini runner against a mocked HTTP layer only: no request leaves the process."""

import base64
import io
import json
from datetime import date
from pathlib import Path

import httpx
import pytest
from PIL import Image

from shoebox import config, privacy
from shoebox.runners import gemini

FAKE_KEY = "FAKE-gemini-key-0123456789"
AID = "ab" + "c" * 62
SCHEMA = json.loads(
    (config.REPO_ROOT / "config" / "schemas" / "label-v1.json").read_text()
)
GOOD = {
    "caption": "Two adults on a beach at sunset.",
    "tags": ["beach", "sunset"],
    "medium": "print",
    "digitiser": "flatbed_scanner",
    "colour_class": "colour",
    "confidence": {"medium": 0.8, "digitiser": 0.7, "colour_class": 0.95},
}
CAND = {
    "name": "gemini-low",
    "runtime": "gemini",
    "model": "gemini-3.8-flash",
    "media_resolution": "low",
    "thinking_level": "low",
    "schema_field": "responseJsonSchema",
    "max_output_tokens": 4096,
    "prompt_text": "Label this image as JSON.",
    "schema_text": json.dumps(SCHEMA),
    "profile": "infer-v1",
    "max_usd_per_run": 2.0,
    "est_prompt_tokens": 1800,
    "est_output_tokens": 300,
    "est_thinking_tokens": 1000,
    "price_input_usd_per_mtok": 0.75,
    "price_output_usd_per_mtok": 3.75,
    "price_date": "2026-10-10",
    "price_valid_until": "2026-12-31",
}


def jpeg_bytes(exif: bool = False) -> bytes:
    img = Image.new("RGB", (64, 48), (200, 120, 40))
    buf = io.BytesIO()
    if exif:
        ex = Image.Exif()
        ex[0x010F] = "SomeCamera"
        img.save(buf, "JPEG", quality=90, exif=ex.tobytes())
    else:
        img.save(buf, "JPEG", quality=90)
    return buf.getvalue()


@pytest.fixture
def env(tmp_path, monkeypatch):
    """Filled checklist, empty never-send, temp spend log, fake key, derived image, mock transport."""
    checklist = tmp_path / "privacy-checklist.toml"
    checklist.write_text(
        'billing_project_id = "p"\npaid_tier_with_prepay_balance = true\nai_studio_logging_off = true\n'
        f'log_retention_days = 7\nno_datasets_or_feedback = true\nconfirmed_on = "{date.today()}"\n'
    )
    monkeypatch.setattr(privacy, "CHECKLIST_PATH", checklist)
    monkeypatch.setattr(privacy, "NEVER_SEND_PATH", tmp_path / "never-send.txt")
    monkeypatch.setattr(privacy, "SPEND_PATH", tmp_path / "spend.jsonl")
    monkeypatch.setattr(privacy, "SETS_DIR", tmp_path / "sets")
    monkeypatch.setattr(privacy, "limits", lambda path=None: privacy.Limits())
    monkeypatch.setattr(gemini, "ENV_PATH", tmp_path / ".env")
    monkeypatch.setenv("GEMINI_API_KEY", FAKE_KEY)
    sleeps: list[float] = []
    monkeypatch.setattr(gemini, "_sleep", sleeps.append)
    monkeypatch.setattr(gemini, "_runs", {})
    monkeypatch.setattr(gemini, "_allowed", {})

    src = tmp_path / "src"
    src.mkdir()
    cfg = config.Config(
        shoebox_root=tmp_path / "shoebox",
        sources={
            "importantfiles": config.Source("importantfiles", src, "owner", False, True)
        },
    )
    image = cfg.derived_dir / "infer-v1" / AID[:2] / f"{AID}.jpg"
    image.parent.mkdir(parents=True)
    image.write_bytes(jpeg_bytes())

    class Env:
        pass

    e = Env()
    e.cfg, e.image, e.sleeps, e.requests, e.tmp = cfg, image, sleeps, [], tmp_path
    e.item = {
        "asset_id": AID,
        "source": "importantfiles",
        "relpath": "Pictures/a.jpg",
        "group_id": None,
    }

    def serve(*responses):
        queue = list(responses)

        def handler(request: httpx.Request) -> httpx.Response:
            e.requests.append(request)
            return queue.pop(0) if len(queue) > 1 else queue[0]

        monkeypatch.setattr(gemini, "_TRANSPORT", httpx.MockTransport(handler))

    e.serve = serve
    yield e


def ok_response(output=GOOD, usage=None, finish="STOP") -> httpx.Response:
    body = {
        "candidates": [
            {
                "content": {"role": "model", "parts": [{"text": json.dumps(output)}]},
                "finishReason": finish,
            }
        ],
        "usageMetadata": usage
        or {
            "promptTokenCount": 600,
            "candidatesTokenCount": 120,
            "thoughtsTokenCount": 200,
        },
        "modelVersion": "gemini-3.8-flash",
    }
    return httpx.Response(200, json=body)


def run(env, cand=CAND, item=None):
    allowed = gemini.preflight(cand, [item or env.item], env.cfg)
    assert allowed
    return gemini.run_one(cand, item or env.item, env.image, env.cfg)


# ---------- request ----------


def test_request_body_has_no_tools_and_only_the_derived_image(env):
    env.serve(ok_response())
    rec = run(env)
    assert rec["status"] == "ok", rec
    (req,) = env.requests
    assert str(req.url) == (
        "https://generativelanguage.googleapis.com/v1beta/models/gemini-3.8-flash:generateContent"
    )
    assert req.method == "POST" and req.headers["x-goog-api-key"] == FAKE_KEY
    body = json.loads(req.content)
    assert set(body) == {
        "contents",
        "generationConfig",
    }  # no tools, toolConfig, cachedContent, ...
    parts = body["contents"][0]["parts"]
    images = [p for p in parts if "inlineData" in p]
    assert len(images) == 1 and len(parts) == 2
    assert images[0]["inlineData"]["mimeType"] == "image/jpeg"
    assert base64.b64decode(images[0]["inlineData"]["data"]) == env.image.read_bytes()
    assert images[0]["mediaResolution"] == {"level": "MEDIA_RESOLUTION_LOW"}
    gen = body["generationConfig"]
    assert gen["responseMimeType"] == "application/json"
    assert gen["thinkingConfig"] == {"thinkingLevel": "LOW"}
    assert gen["maxOutputTokens"] == 4096
    assert "responseSchema" not in gen and "tools" not in json.dumps(body)


def test_medium_resolution_and_response_format_variant(env):
    env.serve(ok_response())
    cand = {
        **CAND,
        "name": "gemini-medium",
        "media_resolution": "medium",
        "schema_field": "responseFormat",
    }
    run(env, cand)
    body = json.loads(env.requests[0].content)
    assert (
        body["contents"][0]["parts"][0]["mediaResolution"]["level"]
        == "MEDIA_RESOLUTION_MEDIUM"
    )
    fmt = body["generationConfig"]["responseFormat"]["text"]
    assert fmt["mimeType"] == "APPLICATION_JSON" and fmt["schema"]["type"] == "object"


def test_schema_adapted_in_code_not_on_disk():
    adapted = gemini.gemini_schema(SCHEMA)
    text = json.dumps(adapted)
    for kw in ("$schema", "minLength", "maxLength", "pattern", "$id"):
        assert kw not in text
    assert (
        adapted["properties"]["caption"]["type"] == "string"
    )  # null is for the rule baseline only
    assert adapted["properties"]["medium"]["type"] == "string"
    assert adapted["properties"]["medium"]["enum"][0] == "digital"
    assert adapted["properties"]["tags"]["maxItems"] == 25
    assert adapted["properties"]["confidence"]["properties"]["medium"] == {
        "type": "number",
        "minimum": 0,
        "maximum": 1,
    }
    assert SCHEMA["properties"]["caption"]["type"] == [
        "string",
        "null",
    ]  # original untouched


def test_bad_settings_refused():
    with pytest.raises(gemini.GeminiError, match="thinking_level"):
        gemini.build_request({**CAND, "thinking_level": "minimal"}, "p", SCHEMA, b"")


# ---------- responses ----------


def test_usage_to_usd_and_record_shape(env):
    env.serve(
        ok_response(
            usage={
                "promptTokenCount": 1000,
                "candidatesTokenCount": 200,
                "thoughtsTokenCount": 300,
            }
        )
    )
    rec = run(env)
    assert set(rec) == {
        "status",
        "output",
        "raw",
        "error",
        "usage",
        "latency_ms",
        "usd",
    }
    assert rec["usage"] == {
        "input_tokens": 1000,
        "output_tokens": 200,
        "thinking_tokens": 300,
    }
    assert rec["usd"] == pytest.approx(1000 * 0.75e-6 + 500 * 3.75e-6)
    assert rec["output"] == GOOD and rec["error"] is None


def test_usage_keeps_the_per_modality_prompt_breakdown(env):
    env.serve(
        ok_response(
            usage={
                "promptTokenCount": 1900,
                "candidatesTokenCount": 150,
                "thoughtsTokenCount": 400,
                "promptTokensDetails": [
                    {"modality": "TEXT", "tokenCount": 1620},
                    {"modality": "IMAGE", "tokenCount": 280},
                ],
            }
        )
    )
    rec = run(env)
    assert rec["usage"] == {
        "input_tokens": 1900,
        "output_tokens": 150,
        "thinking_tokens": 400,
        "input_by_modality": {"TEXT": 1620, "IMAGE": 280},
    }
    assert rec["usd"] == pytest.approx(1900 * 0.75e-6 + 550 * 3.75e-6)


def test_429_then_200_succeeds_with_backoff(env):
    env.serve(
        httpx.Response(
            429,
            headers={"Retry-After": "7"},
            json={"error": {"code": 429, "message": "slow down"}},
        ),
        httpx.Response(503, json={"error": {"code": 503, "message": "overloaded"}}),
        ok_response(),
    )
    rec = run(env)
    assert rec["status"] == "ok"
    assert len(env.requests) == 3
    assert env.sleeps == [
        7.0,
        4.0,
    ]  # Retry-After beats the 2 s first backoff; then 2 * 2**1


def test_retries_are_finite_then_error(env):
    env.serve(httpx.Response(500, json={"error": {"message": "boom"}}))
    rec = run(env, {**CAND, "max_attempts": 3})
    assert rec["status"] == "error" and "gave up after 3 attempts" in rec["error"]
    assert len(env.requests) == 3 and env.sleeps == [2.0, 4.0]
    assert rec["usd"] == 0.0 and rec["output"] is None


def test_non_retryable_status_is_error_without_retry(env):
    env.serve(
        httpx.Response(
            400, json={"error": {"status": "INVALID_ARGUMENT", "message": "bad schema"}}
        )
    )
    rec = run(env)
    assert rec["status"] == "error" and "INVALID_ARGUMENT" in rec["error"]
    assert len(env.requests) == 1


def test_transport_error_retried(env):
    calls = []

    def handler(request):
        calls.append(1)
        if len(calls) == 1:
            raise httpx.ConnectError("down", request=request)
        return ok_response()

    gemini._TRANSPORT = httpx.MockTransport(handler)
    assert run(env)["status"] == "ok" and len(calls) == 2


def test_safety_blocked_candidate_is_invalid_with_finish_reason(env):
    body = {"candidates": [{"finishReason": "SAFETY", "content": {"parts": []}}],
            "usageMetadata": {"promptTokenCount": 600, "candidatesTokenCount": 0}}  # fmt: skip
    env.serve(httpx.Response(200, json=body))
    rec = run(env)
    assert rec["status"] == "invalid" and rec["output"] is None
    assert json.loads(rec["raw"])["finishReason"] == "SAFETY"
    assert rec["usd"] == pytest.approx(600 * 0.75e-6)


def test_prompt_blocked_is_invalid(env):
    env.serve(
        httpx.Response(
            200, json={"promptFeedback": {"blockReason": "PROHIBITED_CONTENT"}}
        )
    )
    rec = run(env)
    assert (
        rec["status"] == "invalid"
        and json.loads(rec["raw"])["blockReason"] == "PROHIBITED_CONTENT"
    )


def test_schema_failure_is_invalid_not_coerced(env):
    bad = {**GOOD, "medium": "polaroid", "tags": ["Beach"]}
    env.serve(ok_response(bad))
    rec = run(env)
    assert rec["status"] == "invalid" and rec["output"] is None
    assert json.loads(rec["raw"]) == bad and "medium" in rec["error"]


def test_null_caption_invalid_for_gemini(env):
    env.serve(ok_response({**GOOD, "caption": None, "tags": []}))
    assert run(env)["status"] == "invalid"


def test_thought_parts_are_ignored(env):
    body = {"candidates": [{"finishReason": "STOP", "content": {"parts": [
        {"text": "thinking...", "thought": True}, {"text": json.dumps(GOOD)}]}}]}  # fmt: skip
    env.serve(httpx.Response(200, json=body))
    assert run(env)["output"] == GOOD


# ---------- key handling ----------


def test_key_never_in_records_or_exceptions(env, capsys):
    leak = f"API key {FAKE_KEY} not valid"
    env.serve(
        httpx.Response(
            400, json={"error": {"status": "INVALID_ARGUMENT", "message": leak}}
        )
    )
    rec = run(env)
    assert FAKE_KEY not in json.dumps(rec) and "[redacted]" in rec["error"]

    gemini._runs.clear()
    gemini._allowed.clear()
    env.serve(ok_response({"caption": FAKE_KEY}))  # even echoed back as invalid output
    rec = run(env)
    assert rec["status"] == "invalid" and FAKE_KEY not in json.dumps(rec)

    for exc_call in (
        lambda: gemini.run_one(CAND, env.item, env.tmp / "src" / "x.jpg", env.cfg),
        lambda: privacy.require_checklist(env.tmp / "missing.toml"),
    ):
        with pytest.raises(Exception) as info:
            exc_call()
        assert FAKE_KEY not in str(info.value) and FAKE_KEY not in repr(info.value)
    out = capsys.readouterr()
    assert FAKE_KEY not in out.out + out.err
    assert (
        FAKE_KEY not in (env.tmp / "spend.jsonl").read_text()
        if (env.tmp / "spend.jsonl").exists()
        else True
    )


def test_key_from_dotenv_when_env_unset(env, monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY")
    (env.tmp / ".env").write_text(
        '# secrets\nOTHER=1\nexport GEMINI_API_KEY="from-dotenv"\n'
    )
    assert gemini.api_key() == "from-dotenv"


def test_missing_key_refuses_in_preflight(env, monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY")
    with pytest.raises(gemini.GeminiError, match="GEMINI_API_KEY is not set"):
        gemini.preflight(CAND, [env.item], env.cfg)


# ---------- pre-flight gates ----------


def test_preflight_refuses_unfilled_checklist(env, monkeypatch):
    (env.tmp / "privacy-checklist.toml").write_text(
        'billing_project_id = ""\nai_studio_logging_off = false\n'
    )
    env.serve(ok_response())
    with pytest.raises(privacy.PrivacyError, match="ai_studio_logging_off"):
        gemini.preflight(CAND, [env.item], env.cfg)
    with pytest.raises(privacy.PrivacyError):  # run_one without preflight is gated too
        gemini.run_one(CAND, env.item, env.image, env.cfg)
    assert env.requests == []


def test_preflight_skips_never_send_and_non_consented(env, capsys):
    (env.tmp / "never-send.txt").write_text(f"{'1' * 64}\nPictures/Kyra/**\n")
    cfg = config.Config(
        env.cfg.shoebox_root,
        {
            **env.cfg.sources,
            "kyra": config.Source("kyra", env.tmp / "kyra", "kyra", False, False),
        },
    )
    items = [
        env.item,
        {"asset_id": "1" * 64, "source": "importantfiles", "relpath": "Pictures/b.jpg"},
        {
            "asset_id": "2" * 64,
            "source": "importantfiles",
            "relpath": "Pictures/Kyra/c.jpg",
        },
        {"asset_id": "3" * 64, "source": "kyra", "relpath": "d.jpg"},
    ]
    allowed = gemini.preflight(CAND, items, cfg)
    assert [i["asset_id"] for i in allowed] == [AID]
    assert gemini.last_eligibility.counts() == {"never_send": 2, "no_consent": 1}
    err = capsys.readouterr().err
    assert "1 allowed, 3 skipped (2 never_send, 1 no_consent)" in err
    assert f"skipped {'1' * 64} (importantfiles): never_send" in err
    assert f"skipped {'2' * 64} (importantfiles): never_send" in err
    assert f"skipped {'3' * 64} (kyra): no_consent" in err
    with pytest.raises(privacy.PrivacyError, match="never_send"):
        gemini.run_one(CAND, items[1], env.image, cfg)


def test_preflight_estimate_over_cap_refuses(env):
    items = [
        {**env.item, "asset_id": f"{i:064x}"} for i in range(1000)
    ]  # ~$6.4 at the estimates
    with pytest.raises(privacy.PrivacyError, match=r"over max_usd_per_run \$2.00"):
        gemini.preflight(CAND, items, env.cfg)


def test_preflight_refuses_over_project_budget(env):
    privacy.append_spend("earlier", 1, 149.999, "2026-10-10")
    with pytest.raises(privacy.PrivacyError, match="remaining project budget"):
        gemini.preflight(CAND, [env.item], env.cfg)


def test_run_stops_when_actual_spend_reaches_cap(env):
    env.serve(
        ok_response(usage={"promptTokenCount": 2_000_000, "candidatesTokenCount": 0})
    )  # $1.50 per call
    cand = {**CAND, "max_usd_per_run": 2.0}
    assert run(env, cand)["status"] == "ok"
    assert (
        gemini.run_one(cand, env.item, env.image, env.cfg)["status"] == "ok"
    )  # $1.50 < $2
    with pytest.raises(gemini.SpendCapReached):
        gemini.run_one(cand, env.item, env.image, env.cfg)  # $3.00 >= $2
    assert len(env.requests) == 2


def test_spend_written_durably_per_call(env):
    """Each billed call is in the spend log as soon as run_one returns: a killed run loses nothing."""
    env.serve(ok_response(usage={"promptTokenCount": 1000, "candidatesTokenCount": 0}))
    run(env)
    lines = [json.loads(x) for x in (env.tmp / "spend.jsonl").read_text().splitlines()]
    assert len(lines) == 1
    assert lines[0]["candidate"] == "gemini-low" and lines[0]["n"] == 1
    assert lines[0]["usd"] == pytest.approx(0.00075)
    assert lines[0]["price_date"] == "2026-10-10" and lines[0]["run"]
    gemini.run_one(CAND, env.item, env.image, env.cfg)
    lines = [json.loads(x) for x in (env.tmp / "spend.jsonl").read_text().splitlines()]
    assert len(lines) == 2 and lines[0]["run"] == lines[1]["run"]
    assert privacy.spent_total() == pytest.approx(0.0015)


def test_failed_call_writes_no_spend(env):
    env.serve(httpx.Response(400, json={"error": {"status": "INVALID_ARGUMENT"}}))
    assert run(env)["status"] == "error"
    assert not (env.tmp / "spend.jsonl").exists()


def test_project_budget_stops_mid_run(env):
    privacy.append_spend(
        "earlier", 1, 149.99, "2026-10-10"
    )  # preflight estimate ~$0.006 fits
    env.serve(
        ok_response(usage={"promptTokenCount": 2_000_000, "candidatesTokenCount": 0})
    )  # $1.50, under the $2 run cap
    assert run(env)["status"] == "ok"
    with pytest.raises(gemini.SpendCapReached, match="project spend"):
        gemini.run_one(CAND, env.item, env.image, env.cfg)
    assert len(env.requests) == 1


def test_project_budget_counts_spend_from_other_processes(env):
    env.serve(ok_response())
    run(env)
    privacy.append_spend("parallel-run", 1, 150.0, "2026-10-10")
    with pytest.raises(gemini.SpendCapReached, match="project spend"):
        gemini.run_one(CAND, env.item, env.image, env.cfg)


def test_expired_price_refuses_preflight_and_calls(env, monkeypatch):
    with pytest.raises(privacy.PrivacyError, match="price_valid_until"):
        gemini.preflight(
            {**CAND, "price_valid_until": "2026-01-01"}, [env.item], env.cfg
        )
    env.serve(ok_response())
    gemini.preflight(CAND, [env.item], env.cfg)
    monkeypatch.setattr(
        privacy, "_today", lambda: date(2027, 1, 1)
    )  # run crosses New Year
    with pytest.raises(privacy.PrivacyError, match="price_valid_until"):
        gemini.run_one(CAND, env.item, env.image, env.cfg)
    assert env.requests == []


# ---------- image guard ----------


def test_refuses_original_or_non_derived_path(env):
    original = env.tmp / "src" / "Pictures" / "a.jpg"
    original.parent.mkdir(parents=True)
    original.write_bytes(jpeg_bytes())
    gemini.preflight(CAND, [env.item], env.cfg)
    for path in (original, env.cfg.derived_dir / "other-profile" / "x.jpg"):
        with pytest.raises(gemini.GeminiError, match="refusing"):
            gemini.run_one(CAND, env.item, path, env.cfg)


def test_refuses_derived_dir_under_a_source_root(env):
    """A misconfigured shoebox_root inside a source share: derived files there are refused too."""
    root = env.tmp / "src"
    cfg = config.Config(shoebox_root=root / "shoebox", sources=env.cfg.sources)
    assert cfg.sources["importantfiles"].root == root
    image = cfg.derived_dir / "infer-v1" / AID[:2] / f"{AID}.jpg"
    image.parent.mkdir(parents=True)
    image.write_bytes(jpeg_bytes())
    env.serve(ok_response())
    gemini.preflight(CAND, [env.item], cfg)
    with pytest.raises(gemini.GeminiError, match="under a source root"):
        gemini.run_one(CAND, env.item, image, cfg)
    assert env.requests == []


def test_refuses_jpeg_with_exif(env):
    env.image.write_bytes(jpeg_bytes(exif=True))
    gemini.preflight(CAND, [env.item], env.cfg)
    with pytest.raises(gemini.GeminiError, match="EXIF"):
        gemini.run_one(CAND, env.item, env.image, env.cfg)


def test_check_derived_jpeg():
    gemini.check_derived_jpeg(jpeg_bytes())
    with pytest.raises(gemini.GeminiError, match="not a JPEG"):
        gemini.check_derived_jpeg(b"\x89PNG....")


def test_runtime_version_and_candidates_file():
    import tomllib

    data = tomllib.loads(
        Path(config.REPO_ROOT / "config" / "candidates.d" / "gemini.toml").read_text()
    )
    cands = data["candidates"]
    assert set(cands) == {"gemini-low", "gemini-medium", "gemini-medium-v2"}
    for c in cands.values():
        assert (
            c["runtime"] == "gemini"
            and c["model"] == "gemini-3.8-flash"
            and c["thinking_level"] == "low"
        )
        assert c["max_usd_per_run"] == (5.0 if c["prompt"] == "label-v2.md" else 2.0) and c["profile"] == "infer-v1"
        assert c["price_valid_until"] == "2026-12-31"  # introductory price ends
        assert privacy.estimate(c, 45).usd < 2.0
    assert privacy.estimate(cands["gemini-medium-v2"], 1000).usd < 5.0  # eval-1k fits its cap
    assert cands["gemini-low"]["media_resolution"] == "low"
    assert cands["gemini-medium"]["media_resolution"] == "medium"
    assert "gemini" in gemini.runtime_version(cands["gemini-low"])


def test_no_live_call_under_pytest(env, monkeypatch):
    monkeypatch.setattr(gemini, "_TRANSPORT", None)
    gemini.preflight(CAND, [env.item], env.cfg)
    with pytest.raises(gemini.GeminiError, match="live Gemini call refused"):
        gemini.run_one(CAND, env.item, env.image, env.cfg)


def test_direct_call_inside_run_keeps_spend_accumulator(env):
    env.serve(
        ok_response(usage={"promptTokenCount": 2_000_000, "candidatesTokenCount": 0})
    )
    run(env)  # $1.50 in this run
    other = {**env.item, "asset_id": "d" * 64}  # not in this run's preflight
    image = env.cfg.derived_dir / "infer-v1" / "dd" / f"{'d' * 64}.jpg"
    image.parent.mkdir(parents=True)
    image.write_bytes(jpeg_bytes())
    gemini.run_one(CAND, other, image, env.cfg)  # checked, same run: $3.00 now
    with pytest.raises(gemini.SpendCapReached):
        gemini.run_one(CAND, other, image, env.cfg)


def test_parse_response_lowercases_tags():
    schema = json.loads((gemini.SCHEMAS_DIR / "label-v1.json").read_text())
    label = {"caption": "A tower.", "tags": ["Eiffel Tower", "paris"], "medium": "digital", "digitiser": "phone",
             "colour_class": "colour", "confidence": {"medium": 0.9, "digitiser": 0.8, "colour_class": 0.9}}
    data = {"candidates": [{"finishReason": "STOP", "content": {"parts": [{"text": json.dumps(label)}]}}]}
    status, output, _, err = gemini.parse_response(data, schema)
    assert status == "ok" and err is None and output["tags"] == ["eiffel tower", "paris"]
    assert "gemini-runner-2" in gemini.runtime_version({})
