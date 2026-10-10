"""Embed runtime: EmbeddingGemma 2 image (and caption-text) vectors in one 768-d space.

Output: {"image": {"dim", "dtype", "vector_b64"}, "caption": {..., "source"} | null}, vectors as base64
float32 little-endian, unit length. A caption is embedded when the item carries one (`shoebox embed run
--caption-from <candidate>` attaches it). Refuses float16 anywhere: it silently breaks EG2.
"""

import time
from pathlib import Path

import httpx
import numpy as np

from shoebox import embed

# A failure to reach or get an answer from the model: retryable `error`. A 4xx below is Ollama's verdict on
# the input (e.g. 400 "decode image: unexpected EOF" for a corrupt JPEG): final `invalid`, so it is not re-sent.
_TRANSPORT_ERRORS = (OSError, httpx.TransportError, httpx.DecodingError)
_REJECTED_INPUT = (400, 413, 415, 422)


def runtime_version(cand: dict) -> str:
    embed.check_precision(cand)
    if cand["backend"] == "sentence-transformers":
        try:
            return embed.SentenceTransformersEmbedder.version()
        except ImportError as e:
            raise RuntimeError("the sentence-transformers backend needs the optional extra: "
                               "uv sync --extra embed") from e
    return embed.embedder_for(cand).version()


def fingerprint(cand: dict) -> dict:
    """Verify the weights (pinned digest, no F16 tensors) and hash the actual digest and tensor types."""
    embed.check_precision(cand)
    if cand["backend"] != "ollama":
        return {}
    info = embed.embedder_for(cand).model_info()
    embed.check_tensor_types(cand, info.tensor_types)
    pinned = cand.get("model_digest") or ""
    if pinned and not info.digest.startswith(pinned):
        raise RuntimeError(f"{cand['model']}: Ollama has digest {info.digest[:12]}, candidate pins {pinned[:12]}")
    return {"model_digest": info.digest, "tensor_types": sorted(info.tensor_types)}


def preflight(cand: dict, items: list[dict], cfg) -> list[dict]:
    """Refuse fp16; load the model so the first item's latency excludes model load time."""
    embed.check_precision(cand)
    embed.embedder_for(cand).embed_texts([embed.query_text(cand, "warm-up")])
    return items


def _block(cand: dict, vecs: np.ndarray, **extra) -> tuple[dict | None, str | None]:
    """(stored block, None) for a good vector, else (None, a description of what was wrong)."""
    v = np.asarray(vecs, dtype=np.float32)
    try:
        if v.ndim != 2 or v.shape[0] != 1:
            raise ValueError(f"expected one vector, got shape {v.shape}")
        embed.check_vector(v[0], int(cand.get("dim", embed.NATIVE_DIM)))
        return embed.embedding_block(v[0], cand["dtype"], **extra), None
    except ValueError as e:
        return None, f"{e}; shape={v.shape}, finite={bool(np.all(np.isfinite(v)))}"


def _add_usage(total: dict, usage: dict) -> None:
    if usage.get("input_tokens") is not None:
        total["input_tokens"] = (total["input_tokens"] or 0) + usage["input_tokens"]


def run_one(cand: dict, item: dict, image_path: Path, cfg) -> dict:
    embed.check_precision(cand)
    usage = {"input_tokens": None, "output_tokens": 0, "thinking_tokens": None}
    t0 = time.perf_counter()
    try:
        emb = embed.embedder_for(cand)
        vecs, u = emb.embed_images([Path(image_path).read_bytes()])
        _add_usage(usage, u)
        image, problem = _block(cand, vecs)
        caption, raw = None, None
        if image and item.get("caption"):
            cvecs, u = emb.embed_texts([embed.document_text(cand, item["caption"])])
            _add_usage(usage, u)
            caption, cproblem = _block(cand, cvecs, source=cand.get("caption_from"))
            # A bad caption vector must not throw away a good image vector: keep the image, note why
            # the caption is missing.
            raw = f"caption vector invalid: {cproblem}" if cproblem else None
    except httpx.HTTPStatusError as e:
        status = e.response.status_code
        if status in _REJECTED_INPUT:
            return {"status": "invalid", "output": None, "raw": f"HTTP {status}: {e.response.text}", "error": None,
                    "usage": usage, "latency_ms": _ms(t0), "usd": 0.0}
        return {"status": "error", "output": None, "raw": None, "error": f"HTTP {status}: {e.response.text}",
                "usage": usage, "latency_ms": _ms(t0), "usd": 0.0}
    except _TRANSPORT_ERRORS as e:
        return {"status": "error", "output": None, "raw": None, "error": f"{type(e).__name__}: {e}",
                "usage": usage, "latency_ms": _ms(t0), "usd": 0.0}
    if problem:
        return {"status": "invalid", "output": None, "raw": problem, "error": None,
                "usage": usage, "latency_ms": _ms(t0), "usd": 0.0}
    return {"status": "ok", "output": {"image": image, "caption": caption}, "raw": raw, "error": None,
            "usage": usage, "latency_ms": _ms(t0), "usd": 0.0}


def _ms(t0: float) -> int:
    return round((time.perf_counter() - t0) * 1000)
