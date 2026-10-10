"""Live EmbeddingGemma 2 checks on synthetic images through the local Ollama (skipped when unavailable)."""

import time

import httpx
import numpy as np
import pytest
from PIL import Image

from shoebox import embed, runners
from shoebox.runners import embed as runner

pytestmark = pytest.mark.live
CAND = runners.load_candidates()["eg2-bf16"]


def _available() -> bool:
    try:
        tags = httpx.get(f"{embed.OLLAMA_HOST}/api/tags", timeout=2).json()
    except (httpx.HTTPError, ValueError):
        return False
    return any(m["name"] == CAND["model"] for m in tags.get("models", []))


if not _available():
    pytest.skip(f"local Ollama with {CAND['model']} not available", allow_module_level=True)


@pytest.fixture(scope="module")
def images(tmp_path_factory):
    d = tmp_path_factory.mktemp("synthetic")
    rng = np.random.default_rng(0)
    pics = {
        "red": Image.new("RGB", (512, 512), (220, 20, 20)),
        "blue": Image.new("RGB", (512, 512), (20, 40, 220)),
        "noise": Image.fromarray(((rng.random((512, 512)) > 0.5) * 255).astype(np.uint8)).convert("RGB"),
    }
    out = {}
    for name, im in pics.items():
        out[name] = d / f"{name}.jpg"
        im.save(out[name], quality=92)
    return out


@pytest.fixture(scope="module")
def vectors(images):
    runner.fingerprint(CAND)  # pinned digest and BF16 weights, or refuse
    runner.preflight(CAND, [], None)
    out, timings = {}, {}
    for name, path in images.items():
        rec = runner.run_one(CAND, {"asset_id": name}, path, None)
        assert rec["status"] == "ok", rec
        out[name] = embed.decode_vector(rec["output"]["image"]["vector_b64"])
        timings[name] = rec["latency_ms"]
    print(f"\nEG2 {CAND['model']} per-image latency ms: {timings}")
    return out


def test_vectors_are_768_unit_and_finite(vectors):
    for v in vectors.values():
        embed.check_vector(v)


@pytest.mark.parametrize("dim", [768, 512, 256, 128])
@pytest.mark.parametrize("query,expect", [("a red square", "red"), ("a blue square", "blue"),
                                          ("black and white noise", "noise")])
def test_text_query_ranks_matching_synthetic_image_first(vectors, query, expect, dim):
    names = list(vectors)
    mat = np.array([embed.truncate(vectors[n], dim) for n in names])
    q = embed.embed_query(CAND, query, dim)
    hits = embed.rank(q, names, mat, np.zeros_like(mat), np.zeros(len(names), bool), "image", k=3)
    assert hits[0].asset_id == expect, [(h.asset_id, round(h.score, 4)) for h in hits]


def test_embedding_is_deterministic(images, vectors):
    again = runner.run_one(CAND, {"asset_id": "red"}, images["red"], None)
    assert np.allclose(embed.decode_vector(again["output"]["image"]["vector_b64"]), vectors["red"], atol=1e-6)


def test_image_and_caption_share_a_space(images):
    item = {"asset_id": "red", "caption": "A plain red square."}
    rec = runner.run_one(CAND, item, images["red"], None)
    img = embed.decode_vector(rec["output"]["image"]["vector_b64"])
    cap = embed.decode_vector(rec["output"]["caption"]["vector_b64"])
    blue = embed.embed_query(CAND, "a blue square")
    assert float(img @ cap) > float(img @ blue)


def test_batch_throughput_smoke(images):
    data = [p.read_bytes() for p in images.values()] * 4
    emb = embed.embedder_for(CAND)
    t = time.perf_counter()
    vecs, _ = emb.embed_images(data)
    per_image = (time.perf_counter() - t) / len(data) * 1000
    print(f"\nEG2 batch of {len(data)}: {per_image:.1f} ms/image")
    assert vecs.shape == (len(data), 768)
