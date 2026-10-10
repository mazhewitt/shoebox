import base64
import json
import os
import time
from pathlib import Path
from typing import ClassVar

import httpx
import pytest

from shoebox import runners
from shoebox.config import REPO_ROOT, Config
from shoebox.runners import ollama

SCHEMA_TEXT = (REPO_ROOT / "config" / "schemas" / "label-v1.json").read_text()
GOOD = {"caption": "A grey gradient.", "tags": ["gradient", "grey"], "medium": "digital", "digitiser": "software",
        "colour_class": "mono", "confidence": {"medium": 0.6, "digitiser": 0.5, "colour_class": 0.9}}


def cand(**kw):
    c = {"name": "t", "runtime": "ollama", "model": "gemma4:e4b-mlx-bf16", "quant": "bf16", "prompt_text": "Label it.",
         "schema_text": SCHEMA_TEXT, "think": False, "temperature": 0.0, "seed": 0, "num_ctx": 8192,
         "image_budget": "auto"}
    c.update(kw)
    return c


@pytest.fixture
def image(tmp_path):
    p = tmp_path / "x.jpg"
    p.write_bytes(b"\xff\xd8fakejpeg\xff\xd9")
    return p


@pytest.fixture
def server(monkeypatch):
    """Mock Ollama: `server.reply` is the /api/chat JSON (or an httpx.Response / exception)."""

    class S:
        requests: ClassVar[list] = []
        reply: object = None

    def handler(req: httpx.Request):
        S.requests.append(req)
        if req.url.path == "/api/version":
            return httpx.Response(200, json={"version": "0.40.2"})
        if req.url.path == "/api/tags":
            return httpx.Response(200, json={"models": [{"name": "gemma4:e4b-mlx-bf16", "digest": "9bf9" + "0" * 60}]})
        if req.url.path == "/api/show":
            return httpx.Response(200, json={"details": {"quantization_level": ""}, "capabilities": ["vision"]})
        if req.url.path == "/api/generate":
            return httpx.Response(200, json={"done": True})
        if isinstance(S.reply, Exception):
            raise S.reply
        if isinstance(S.reply, httpx.Response):
            return S.reply
        return httpx.Response(200, json=S.reply)

    S.requests = []
    monkeypatch.setattr(ollama, "_client", lambda timeout=None: httpx.Client(
        base_url=ollama.HOST, transport=httpx.MockTransport(handler)))
    return S


def chat_reply(content, **kw):
    r = {"model": "gemma4:e4b-mlx-bf16", "message": {"role": "assistant", "content": content}, "done": True,
         "done_reason": "stop", "prompt_eval_count": 300, "eval_count": 60}
    r.update(kw)
    return r


def test_host_is_loopback_only():
    assert ollama.HOST == "http://127.0.0.1:11434"


@pytest.mark.parametrize("var", ["HTTP_PROXY", "http_proxy", "ALL_PROXY", "all_proxy", "HTTPS_PROXY"])
def test_client_ignores_proxy_environment(monkeypatch, var):
    """A proxy variable must never route photo bytes away from the local Ollama."""
    monkeypatch.setenv(var, "http://127.0.0.1:9")
    monkeypatch.delenv("NO_PROXY", raising=False)
    monkeypatch.delenv("no_proxy", raising=False)
    with ollama._client() as c:
        transport = c._transport_for_url(httpx.URL(f"{ollama.HOST}/api/chat"))
        assert transport is c._transport
        assert type(transport._pool).__name__ == "ConnectionPool"


def test_runtime_version_and_digest(server):
    assert ollama.runtime_version(cand()) == "ollama 0.40.2"
    assert ollama.fingerprint(cand()) == {"model_digest": "9bf9" + "0" * 60}
    with pytest.raises(RuntimeError, match="not pulled"):
        ollama.fingerprint(cand(model="gemma4:nope"))


def test_request_shape(server, image):
    server.reply = chat_reply(json.dumps(GOOD))
    ollama.run_one(cand(), {"asset_id": "a"}, image, None)
    req = next(r for r in server.requests if r.url.path == "/api/chat")
    body = json.loads(req.content)
    assert body["model"] == "gemma4:e4b-mlx-bf16"
    assert body["stream"] is False and body["think"] is False
    assert body["options"] == {"temperature": 0.0, "seed": 0, "num_ctx": 8192}
    ollama.run_one(cand(num_predict=1024), {"asset_id": "a"}, image, None)
    last = json.loads([r for r in server.requests if r.url.path == "/api/chat"][-1].content)
    assert last["options"]["num_predict"] == 1024
    msg = body["messages"][0]
    assert msg["content"] == "Label it."
    assert base64.b64decode(msg["images"][0]) == image.read_bytes()
    assert body["format"]["properties"]["caption"]["type"] == "string"  # null caption is rules-only
    assert body["format"]["required"] == json.loads(SCHEMA_TEXT)["required"]
    assert "pattern" not in json.dumps(body["format"])  # breaks Ollama's GGUF grammar; validated after
    assert "keep_alive" in body


def test_ok_output(server, image):
    server.reply = chat_reply(json.dumps(GOOD))
    out = ollama.run_one(cand(), {"asset_id": "a"}, image, None)
    assert out["status"] == "ok" and out["output"] == GOOD
    assert out["usage"] == {"input_tokens": 300, "output_tokens": 60, "thinking_tokens": None}
    assert out["usd"] == 0.0 and out["latency_ms"] >= 0 and out["error"] is None


@pytest.mark.parametrize("content", [
    '{"caption": "A gradient", "tags": [',                      # truncated JSON
    json.dumps({**GOOD, "medium": "photo"}),                    # not in enum
    json.dumps({**GOOD, "extra": 1}),                           # additionalProperties false
    json.dumps({**GOOD, "caption": None}),                      # null caption is for rules only
    json.dumps({**GOOD, "tags": ["Beach"]}),                    # tags must be lowercase
    json.dumps({**GOOD, "confidence": {"medium": 2, "digitiser": 0.5, "colour_class": 0.5}}),
])
def test_malformed_output_is_invalid_with_raw_never_coerced(server, image, content):
    server.reply = chat_reply(content)
    out = ollama.run_one(cand(), {"asset_id": "a"}, image, None)
    assert out["status"] == "invalid"
    assert out["raw"] == content and out["output"] is None and out["error"]


def test_http_error_is_error(server, image):
    server.reply = httpx.Response(500, json={"error": "model runner crashed"})
    out = ollama.run_one(cand(), {"asset_id": "a"}, image, None)
    assert out["status"] == "error" and "500" in out["error"]


def test_transport_error_is_error(server, image):
    server.reply = httpx.ConnectError("refused")
    out = ollama.run_one(cand(), {"asset_id": "a"}, image, None)
    assert out["status"] == "error" and "refused" in out["error"]


def test_preflight_checks_quant_and_warms(server):
    its = [{"asset_id": "a"}]
    assert ollama.preflight(cand(), its, None) == its
    assert any(r.url.path == "/api/generate" for r in server.requests)


def test_preflight_refuses_quant_mismatch(server, monkeypatch):
    with pytest.raises(RuntimeError, match="quant"):
        ollama.check_model(cand(quant="Q4_K_M"), {"details": {"quantization_level": "Q8_0"}, "capabilities": ["vision"]})
    with pytest.raises(RuntimeError, match="vision"):
        ollama.check_model(cand(), {"details": {}, "capabilities": ["completion"]})


@pytest.mark.skipif(os.environ.get("SHOEBOX_LIVE") != "1", reason="live Ollama smoke test; set SHOEBOX_LIVE=1")
def test_live_smoke_synthetic_image(tmp_path):
    """Synthetic image only (grey gradient with coloured shapes) through the real local Ollama."""
    import pyvips

    grad = pyvips.Image.xyz(512, 384)[0].cast("uchar") * (255 / 511)
    rgb = grad.bandjoin([grad, grad]).cast("uchar")
    rgb = rgb.draw_rect([220, 40, 40], 60, 60, 120, 90, fill=True)
    rgb = rgb.draw_circle([30, 90, 220], 360, 220, 70, fill=True)
    img: Path = tmp_path / "synthetic.jpg"
    rgb.copy(interpretation="srgb").jpegsave(str(img), Q=90)

    res = runners.resolve("e4b-bf16")
    cfg = Config(shoebox_root=tmp_path / "shoebox", sources={})
    runners.get("ollama").preflight(res.cand, [{"asset_id": "x"}], cfg)
    t0 = time.monotonic()
    out = ollama.run_one(res.cand, {"asset_id": "x"}, img, cfg)
    wall = time.monotonic() - t0
    print(f"\nLIVE e4b-bf16: status={out['status']} latency_ms={out['latency_ms']} wall={wall:.1f}s "
          f"usage={out['usage']} params_hash={res.params_hash}\noutput={json.dumps(out['output'])}\n"
          f"raw={out['raw']!r} error={out['error']!r}")
    assert out["status"] == "ok", out
    assert out["usage"]["input_tokens"] > 0 and out["usage"]["output_tokens"] > 0
