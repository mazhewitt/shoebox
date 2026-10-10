import io

import httpx
import numpy as np
import pytest
from PIL import Image

from shoebox import embed
from shoebox.runners import embed as runner

CAND = {"name": "eg2-test", "runtime": "embed", "backend": "ollama", "model": "embeddinggemma-2:740m-bf16",
        "model_digest": "d" * 64, "dtype": "bfloat16", "quant": "bf16", "dim": 768, "image_budget": 280,
        "query_prompt": "task: search result | query: {text}", "document_prompt": "title: none | text: {text}",
        "profile": "infer-v1"}


class FakeEmbedder:
    """Maps an image's mean colour, or colour words in text, onto the first three axes."""

    def __init__(self, digest="d" * 64, types=("BF16",), nan=False, fail=None, text_nan=False):
        self.digest, self.types, self.nan, self.fail, self.text_nan = digest, set(types), nan, fail, text_nan
        self.texts: list[str] = []

    def version(self):
        return "ollama 0.40.2"

    def model_info(self):
        return embed.ModelInfo(self.digest, self.types)

    @staticmethod
    def _vec(rgb) -> np.ndarray:
        v = np.full(768, 0.01, np.float32)
        v[:3] = rgb
        return v / np.linalg.norm(v)

    def embed_images(self, images):
        if self.fail:
            raise self.fail
        out = []
        for b in images:
            px = np.asarray(Image.open(io.BytesIO(b)).convert("RGB"), np.float32).mean(axis=(0, 1)) / 255
            out.append(self._vec(px))
        vecs = np.array(out)
        if self.nan:
            vecs[:, 7] = np.nan
        return vecs, {"input_tokens": 260, "output_tokens": 0, "thinking_tokens": None}

    def embed_texts(self, texts):
        self.texts.extend(texts)
        words = {"red": (1, 0, 0), "blue": (0, 0, 1), "green": (0, 1, 0)}
        out = np.array([self._vec(next((c for w, c in words.items() if w in t), (0.3, 0.3, 0.3))) for t in texts])
        if self.text_nan:
            out[:, 3] = np.nan
        return out, {"input_tokens": 8, "output_tokens": 0, "thinking_tokens": None}


@pytest.fixture
def fake(monkeypatch):
    f = FakeEmbedder()
    monkeypatch.setattr(embed, "embedder_for", lambda cand: (embed.check_precision(cand), f)[1])
    return f


def jpeg(tmp_path, rgb, name="x.jpg"):
    p = tmp_path / name
    Image.new("RGB", (64, 64), rgb).save(p, quality=95)
    return p


def test_run_one_image_only(fake, tmp_path):
    out = runner.run_one(CAND, {"asset_id": "a" * 64}, jpeg(tmp_path, (230, 10, 10)), None)
    assert out["status"] == "ok" and out["error"] is None and out["usd"] == 0.0
    assert out["output"]["caption"] is None
    img = out["output"]["image"]
    assert img["dim"] == 768 and img["dtype"] == "bfloat16"
    v = embed.decode_vector(img["vector_b64"])
    assert v.shape == (768,) and abs(float(np.linalg.norm(v)) - 1) < 1e-5
    assert int(np.argmax(v)) == 0
    assert out["usage"]["input_tokens"] == 260 and out["latency_ms"] >= 0


def test_run_one_with_caption(fake, tmp_path):
    cand = {**CAND, "caption_from": {"candidate": "e4b", "model_id": "ollama/x", "params_hash": "p" * 16}}
    item = {"asset_id": "a" * 64, "caption": "A blue door."}
    out = runner.run_one(cand, item, jpeg(tmp_path, (10, 10, 230)), None)
    assert out["status"] == "ok"
    cap = out["output"]["caption"]
    assert cap["dim"] == 768 and cap["dtype"] == "bfloat16" and cap["source"]["params_hash"] == "p" * 16
    assert fake.texts == ["title: none | text: A blue door."]
    assert out["usage"]["input_tokens"] == 268


def test_run_one_nan_vector_is_invalid(monkeypatch, tmp_path):
    monkeypatch.setattr(embed, "embedder_for", lambda cand: FakeEmbedder(nan=True))
    out = runner.run_one(CAND, {"asset_id": "a" * 64}, jpeg(tmp_path, (1, 2, 3)), None)
    assert out["status"] == "invalid" and out["output"] is None and "NaN" in out["raw"]


def test_run_one_transport_failure_is_retryable_error(monkeypatch, tmp_path):
    monkeypatch.setattr(embed, "embedder_for", lambda cand: FakeEmbedder(fail=httpx.ConnectError("refused")))
    out = runner.run_one(CAND, {"asset_id": "a" * 64}, jpeg(tmp_path, (1, 2, 3)), None)
    assert out["status"] == "error" and "ConnectError" in out["error"]


@pytest.mark.parametrize("fn", ["run_one", "runtime_version", "fingerprint", "preflight"])
def test_every_entry_point_refuses_fp16(fake, tmp_path, fn):
    cand = {**CAND, "dtype": "float16"}
    args = {"run_one": (cand, {"asset_id": "a" * 64}, jpeg(tmp_path, (1, 2, 3)), None),
            "runtime_version": (cand,), "fingerprint": (cand,), "preflight": (cand, [], None)}[fn]
    with pytest.raises(embed.PrecisionError):
        getattr(runner, fn)(*args)


def test_fingerprint_verifies_digest_and_tensor_types(monkeypatch):
    monkeypatch.setattr(embed, "embedder_for", lambda cand: FakeEmbedder())
    assert runner.fingerprint(CAND) == {"model_digest": "d" * 64, "tensor_types": ["BF16"]}
    monkeypatch.setattr(embed, "embedder_for", lambda cand: FakeEmbedder(digest="e" * 64))
    with pytest.raises(RuntimeError, match="pins"):
        runner.fingerprint(CAND)
    monkeypatch.setattr(embed, "embedder_for", lambda cand: FakeEmbedder(types=("BF16", "F16")))
    with pytest.raises(embed.PrecisionError, match="F16"):
        runner.fingerprint(CAND)


def test_preflight_warms_up_with_a_query(fake):
    items = [{"asset_id": "a" * 64}]
    assert runner.preflight(CAND, items, None) == items
    assert fake.texts == ["task: search result | query: warm-up"]


def test_run_one_bad_caption_vector_keeps_the_image(monkeypatch, tmp_path):
    monkeypatch.setattr(embed, "embedder_for", lambda cand: FakeEmbedder(text_nan=True))
    item = {"asset_id": "a" * 64, "caption": "A blue door."}
    out = runner.run_one(CAND, item, jpeg(tmp_path, (10, 10, 230)), None)
    assert out["status"] == "ok" and out["output"]["caption"] is None
    assert embed.decode_vector(out["output"]["image"]["vector_b64"]).shape == (768,)
    assert out["raw"].startswith("caption vector invalid") and "NaN" in out["raw"]


def ollama_replying(status: int, body: dict):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, json=body)
    return embed.OllamaEmbedder(CAND["model"], transport=httpx.MockTransport(handler))


def test_run_one_ollama_rejecting_the_image_is_invalid(monkeypatch, tmp_path):
    emb = ollama_replying(400, {"error": "decode image: unexpected EOF"})  # what Ollama 0.40.2 says
    monkeypatch.setattr(embed, "embedder_for", lambda cand: emb)
    out = runner.run_one(CAND, {"asset_id": "a" * 64}, jpeg(tmp_path, (1, 2, 3)), None)
    assert out["status"] == "invalid" and out["output"] is None and out["error"] is None
    assert "400" in out["raw"] and "decode image: unexpected EOF" in out["raw"]


@pytest.mark.parametrize("status", [404, 429, 500, 503])
def test_run_one_ollama_server_failure_is_retryable_error(monkeypatch, tmp_path, status):
    emb = ollama_replying(status, {"error": "model runner has unexpectedly stopped"})
    monkeypatch.setattr(embed, "embedder_for", lambda cand: emb)
    out = runner.run_one(CAND, {"asset_id": "a" * 64}, jpeg(tmp_path, (1, 2, 3)), None)
    assert out["status"] == "error" and str(status) in out["error"]
