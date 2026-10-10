import base64
import json

import httpx
import numpy as np
import pytest

from shoebox import embed, runners

EG2 = {"runtime": "embed", "backend": "ollama", "model": "embeddinggemma-2:740m-bf16", "dtype": "bfloat16",
       "quant": "bf16", "dim": 768, "query_prompt": "task: search result | query: {text}",
       "document_prompt": "title: none | text: {text}"}


def unit(i: int, dim: int = 768) -> np.ndarray:
    v = np.zeros(dim, np.float32)
    v[i] = 1.0
    return v


# --- precision guard ---------------------------------------------------------------------------

@pytest.mark.parametrize("override", [
    {"dtype": "float16"}, {"dtype": "fp16"}, {"dtype": "F16"}, {"dtype": "half"},
    {"quant": "f16"}, {"quant": "FP16"}, {"model": "embeddinggemma-2:740m-fp16"},
    {"model": "embeddinggemma-2:740m-f16"},
])
def test_fp16_refused(override):
    with pytest.raises(embed.PrecisionError, match="float16"):
        embed.check_precision({**EG2, **override})


@pytest.mark.parametrize("dtype", ["bfloat16", "float32"])
def test_bf16_and_fp32_allowed(dtype):
    embed.check_precision({**EG2, "dtype": dtype})


@pytest.mark.parametrize("dtype", [None, "int8", "nvfp4", "BF16"])
def test_other_dtypes_refused(dtype):
    with pytest.raises(embed.PrecisionError, match="dtype"):
        embed.check_precision({**EG2, "dtype": dtype})


def test_tensor_types_guard():
    embed.check_tensor_types(EG2, {"BF16"})
    embed.check_tensor_types(EG2, {"BF16", "F32"})
    with pytest.raises(embed.PrecisionError, match="F16"):
        embed.check_tensor_types(EG2, {"BF16", "F16"})
    with pytest.raises(embed.PrecisionError, match="does not match"):
        embed.check_tensor_types({**EG2, "dtype": "float32"}, {"BF16"})
    with pytest.raises(embed.PrecisionError):
        embed.check_tensor_types(EG2, set())


def test_embedder_for_refuses_fp16():
    with pytest.raises(embed.PrecisionError):
        embed.embedder_for({**EG2, "dtype": "float16"})


# --- vectors ----------------------------------------------------------------------------------

def test_vector_codec_is_float32_little_endian():
    v = np.random.default_rng(0).standard_normal(768).astype(np.float32)
    b64 = embed.encode_vector(v)
    raw = base64.b64decode(b64)
    assert len(raw) == 768 * 4
    assert np.array_equal(np.frombuffer(raw, "<f4"), v)
    assert np.array_equal(embed.decode_vector(b64), v)


def test_embedding_block_normalises_and_records_dim_and_dtype():
    v = np.arange(1, 769, dtype=np.float32)
    b = embed.embedding_block(v, "bfloat16")
    assert b["dim"] == 768 and b["dtype"] == "bfloat16"
    out = embed.decode_vector(b["vector_b64"])
    assert out.shape == (768,)
    assert abs(float(np.linalg.norm(out)) - 1) < 1e-6


def test_check_vector_rejects_nan_wrong_dim_and_non_unit():
    v = embed.normalise(np.ones(768))
    embed.check_vector(v)
    bad = v.copy()
    bad[3] = np.nan
    with pytest.raises(ValueError, match="NaN"):
        embed.check_vector(bad)
    with pytest.raises(ValueError, match="768"):
        embed.check_vector(v[:512])
    with pytest.raises(ValueError, match="unit"):
        embed.check_vector(v * 2)
    with pytest.raises(ValueError):
        embed.normalise(np.zeros(768))


@pytest.mark.parametrize("dim", [768, 512, 256, 128])
def test_mrl_truncation_is_renormalised_prefix(dim):
    v = embed.normalise(np.random.default_rng(1).standard_normal(768))
    t = embed.truncate(v, dim)
    assert t.shape == (dim,)
    assert abs(float(np.linalg.norm(t)) - 1) < 1e-6
    assert np.allclose(t, v[:dim] / np.linalg.norm(v[:dim]), atol=1e-6)


def test_mrl_truncation_rejects_other_dims():
    with pytest.raises(ValueError, match="dim"):
        embed.truncate(np.ones(768), 300)


# --- prompts and config -------------------------------------------------------------------------

def test_query_and_document_prompts():
    assert embed.query_text(EG2, "a red {square}") == "task: search result | query: a red {square}"
    assert embed.document_text(EG2, "A dog.") == "title: none | text: A dog."


def test_shipped_candidates_are_safe():
    cands = runners.load_candidates()
    eg2 = {n: c for n, c in cands.items() if c.get("runtime") == "embed"}
    assert "eg2-bf16" in eg2
    for c in eg2.values():
        embed.check_precision(c)
        assert c["dim"] == 768 and c["profile"] == "infer-v1"
        assert c["query_prompt"].startswith("task: search result | query: ")
        assert c["document_prompt"].startswith("title: none | text: ")
    bf16 = eg2["eg2-bf16"]
    assert bf16["backend"] == "ollama" and bf16["dtype"] == "bfloat16"
    assert bf16["model_id"] == f"ollama/{bf16['model']}" and len(bf16["model_digest"]) == 64


# --- Ollama backend (mock transport, no network) --------------------------------------------------

def mock_ollama(seen: list, digest: str = "d" * 64, types=("BF16",)):
    def handler(req: httpx.Request) -> httpx.Response:
        assert req.url.host == "127.0.0.1" and req.url.port == 11434
        if req.url.path == "/api/version":
            return httpx.Response(200, json={"version": "0.40.2"})
        if req.url.path == "/api/tags":
            return httpx.Response(200, json={"models": [{"name": EG2["model"], "digest": digest}]})
        if req.url.path == "/api/show":
            return httpx.Response(200, json={"tensors": [{"type": t} for t in types]})
        body = json.loads(req.content)
        seen.append(body)
        return httpx.Response(200, json={"embeddings": [unit(i).tolist() for i in range(len(body["input"]))],
                                          "prompt_eval_count": 260})
    return httpx.MockTransport(handler)


def test_ollama_embedder_request_shape():
    seen = []
    e = embed.OllamaEmbedder(EG2["model"], transport=mock_ollama(seen))
    vecs, usage = e.embed_images([b"\xff\xd8jpeg"])
    assert vecs.shape == (1, 768) and usage["input_tokens"] == 260
    assert seen[-1] == {"model": EG2["model"], "truncate": False,
                        "input": [{"image": base64.b64encode(b"\xff\xd8jpeg").decode()}]}
    e.embed_texts(["task: search result | query: x"])
    assert seen[-1]["input"] == ["task: search result | query: x"]
    assert e.version() == "ollama 0.40.2"
    info = e.model_info()
    assert info.digest == "d" * 64 and info.tensor_types == {"BF16"}


@pytest.mark.parametrize("var", ["HTTP_PROXY", "http_proxy", "ALL_PROXY", "all_proxy", "HTTPS_PROXY"])
def test_ollama_embedder_ignores_proxy_environment(monkeypatch, var):
    """A proxy variable must never route photo bytes away from the local Ollama."""
    monkeypatch.setenv(var, "http://127.0.0.1:9")
    monkeypatch.delenv("NO_PROXY", raising=False)
    monkeypatch.delenv("no_proxy", raising=False)
    e = embed.OllamaEmbedder(EG2["model"])
    transport = e.client._transport_for_url(httpx.URL(f"{embed.OLLAMA_HOST}/api/embed"))
    assert transport is e.client._transport
    assert type(transport._pool).__name__ == "ConnectionPool"


@pytest.mark.parametrize("host", ["http://10.0.0.5:11434", "http://localhost:11434",
                                  "https://generativelanguage.googleapis.com"])
def test_ollama_embedder_is_localhost_only(host):
    with pytest.raises(ValueError, match="local"):
        embed.OllamaEmbedder(EG2["model"], host=host)


# --- search -----------------------------------------------------------------------------------

def record(aid, img, cap=None, status="ok"):
    out = {"image": embed.embedding_block(img, "bfloat16"),
           "caption": embed.embedding_block(cap, "bfloat16") if cap is not None else None}
    return {"asset_id": aid, "status": status, "output": out if status == "ok" else None}


def test_search_matrix_and_rank_modes():
    recs = {
        "a": record("a", unit(0), cap=unit(5)),
        "b": record("b", unit(1)),
        "c": record("c", embed.normalise(unit(0) + unit(1))),
        "d": record("d", unit(0), status="invalid"),
    }
    ids, img, cap, has = embed.search_matrix(recs, 768)
    assert ids == ["a", "b", "c"] and img.shape == (3, 768) and has.tolist() == [True, False, False]

    hits = embed.rank(unit(0), ids, img, cap, has, "image", k=2)
    assert [h.asset_id for h in hits] == ["a", "c"]
    assert hits[0].score == pytest.approx(1.0)

    hits = embed.rank(unit(5), ids, img, cap, has, "caption", k=5)
    assert [h.asset_id for h in hits] == ["a"]

    q = embed.normalise(unit(0) + unit(5))
    hits = embed.rank(q, ids, img, cap, has, "blend", k=3)
    assert hits[0].asset_id == "a"
    assert hits[0].score == pytest.approx((hits[0].image_score + hits[0].caption_score) / 2)
    assert hits[-1].caption_score is None


def test_search_matrix_truncates_to_query_dim():
    _, img, _, _ = embed.search_matrix({"a": record("a", embed.normalise(np.ones(768)))}, 256)
    assert img.shape == (1, 256)
    assert abs(float(np.linalg.norm(img[0])) - 1) < 1e-6


def test_rank_rejects_unknown_mode():
    with pytest.raises(ValueError):
        embed.rank(unit(0), ["a"], unit(0)[None], unit(0)[None], np.array([True]), "fuzzy")
