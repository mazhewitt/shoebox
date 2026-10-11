"""EmbeddingGemma 2 (EG2): precision guard, vector codec, MRL truncation, prompts, backends and search.

Backends: Ollama (`/api/embed` on localhost only) and sentence-transformers (optional `embed` extra,
imported lazily). EG2 must run in bfloat16 or float32: float16 silently returns NaN or degraded vectors.
"""

import base64
import io
import re
from dataclasses import dataclass

import numpy as np

from shoebox.runners.ollama import (
    HOST as OLLAMA_HOST,  # the one allowed endpoint (loopback, no proxies)
)

ALLOWED_DTYPES = ("bfloat16", "float32")
MRL_DIMS = (768, 512, 256, 128)
NATIVE_DIM = 768
MODES = ("image", "caption", "blend")

_FP16 = re.compile(r"(?<![b])(float16|fp16|f16)|\bhalf\b", re.IGNORECASE)


class PrecisionError(ValueError):
    """The candidate would run EG2 in a precision other than bfloat16/float32."""


def check_precision(cand: dict) -> None:
    """Refuse fp16 anywhere in the candidate (dtype, quant, model tag), and any dtype not bf16/fp32."""
    for key in ("dtype", "quant", "model"):
        value = str(cand.get(key) or "")
        if _FP16.search(value):
            raise PrecisionError(
                f"refusing {key}={value!r}: EmbeddingGemma 2 must not run in float16 "
                "(it returns NaN or silently degraded embeddings); use bfloat16 or float32"
            )
    if cand.get("dtype") not in ALLOWED_DTYPES:
        raise PrecisionError(f"dtype must be one of {ALLOWED_DTYPES}, got {cand.get('dtype')!r}")


def check_tensor_types(cand: dict, types: set[str]) -> None:
    """Refuse weights stored as F16, or weights that don't match the declared dtype."""
    types = {t.upper() for t in types}
    if not types:
        raise PrecisionError("could not read the model's tensor types")
    if "F16" in types:
        raise PrecisionError("refusing: the model's weights include F16 tensors")
    expect = {"bfloat16": {"BF16", "F32"}, "float32": {"F32"}}[cand["dtype"]]
    if not types <= expect:
        raise PrecisionError(f"weights are {sorted(types)}, which does not match dtype={cand['dtype']}")


# --- vectors ---------------------------------------------------------------------------------

def normalise(v: np.ndarray) -> np.ndarray:
    v = np.asarray(v, dtype=np.float32)
    n = float(np.linalg.norm(v))
    if not np.isfinite(n) or n == 0.0:
        raise ValueError("vector is zero or not finite")
    return v / n


def truncate(v: np.ndarray, dim: int) -> np.ndarray:
    """Matryoshka truncation: keep the first `dim` values and L2-normalise again."""
    if dim not in MRL_DIMS:
        raise ValueError(f"dim must be one of {MRL_DIMS}, got {dim}")
    v = np.asarray(v, dtype=np.float32)
    if v.shape[-1] < dim:
        raise ValueError(f"vector has {v.shape[-1]} dims, cannot truncate to {dim}")
    return normalise(v[..., :dim])


def encode_vector(v: np.ndarray) -> str:
    return base64.b64encode(np.asarray(v, dtype="<f4").tobytes()).decode("ascii")


def decode_vector(b64: str) -> np.ndarray:
    return np.frombuffer(base64.b64decode(b64), dtype="<f4").astype(np.float32)


def check_vector(v: np.ndarray, dim: int = NATIVE_DIM) -> None:
    """A usable EG2 vector: right size, finite and unit length. NaNs are the float16 failure mode."""
    v = np.asarray(v, dtype=np.float64)
    if v.shape != (dim,):
        raise ValueError(f"expected a {dim}-d vector, got shape {v.shape}")
    if not np.all(np.isfinite(v)):
        raise ValueError("vector contains NaN or inf")
    n = float(np.linalg.norm(v))
    if abs(n - 1.0) > 1e-2:
        raise ValueError(f"vector is not unit length (norm {n:.4f})")


def embedding_block(v: np.ndarray, dtype: str, **extra) -> dict:
    """The stored form: {"dim", "dtype", "vector_b64"} (float32 little-endian), plus any extra keys."""
    v = normalise(v)
    check_vector(v, len(v))
    return {"dim": len(v), "dtype": dtype, "vector_b64": encode_vector(v), **extra}


# --- prompts ---------------------------------------------------------------------------------

def query_text(cand: dict, query: str) -> str:
    return cand["query_prompt"].format(text=query)


def document_text(cand: dict, text: str) -> str:
    return cand["document_prompt"].format(text=text)


# --- captions from a label candidate ---------------------------------------------------------

def caption_source(name: str) -> dict:
    """{"candidate", "model_id", "params_hash"[, "retry_params_hash"]} of the label candidate whose captions
    get embedded (`shoebox.runners.label_source`)."""
    from shoebox.runners import label_source, resolve

    return label_source(resolve(name))


def load_captions(cfg, source: dict) -> dict[str, str]:
    """asset_id -> caption, from the source's `ok` records with a non-empty caption."""
    from shoebox.runners import source_index

    out = {}
    for aid, r in source_index(cfg, source).items():
        cap = (r.get("output") or {}).get("caption") if r.get("status") == "ok" else None
        if isinstance(cap, str) and cap.strip():
            out[aid] = cap.strip()
    return out


# --- backends --------------------------------------------------------------------------------

@dataclass
class ModelInfo:
    digest: str
    tensor_types: set[str]


class OllamaEmbedder:
    """EG2 through Ollama's `/api/embed`. Only the local server is allowed: photos never leave the machine."""

    def __init__(self, model: str, host: str = OLLAMA_HOST, transport=None, timeout: float = 300.0):
        if host.rstrip("/") != OLLAMA_HOST:
            raise ValueError(f"only the local Ollama at {OLLAMA_HOST} is allowed, not {host}")
        import httpx

        self.model = model
        self.client = httpx.Client(base_url=host, timeout=timeout, transport=transport, trust_env=False)

    def version(self) -> str:
        r = self.client.get("/api/version")
        r.raise_for_status()
        return f"ollama {r.json()['version']}"

    def model_info(self) -> ModelInfo:
        r = self.client.get("/api/tags")
        r.raise_for_status()
        digest = next((m["digest"] for m in r.json()["models"] if m["name"] == self.model), "")
        if not digest:
            raise RuntimeError(f"{self.model} is not pulled in Ollama (run: ollama pull {self.model})")
        r = self.client.post("/api/show", json={"model": self.model, "verbose": True})
        r.raise_for_status()
        types = {t["type"] for t in r.json().get("tensors") or []}
        return ModelInfo(digest=digest, tensor_types=types)

    def _embed(self, inputs: list) -> tuple[np.ndarray, dict]:
        r = self.client.post("/api/embed", json={"model": self.model, "input": inputs, "truncate": False})
        r.raise_for_status()
        body = r.json()
        return np.asarray(body["embeddings"], dtype=np.float32), {
            "input_tokens": body.get("prompt_eval_count"), "output_tokens": 0, "thinking_tokens": None}

    def embed_images(self, images: list[bytes]) -> tuple[np.ndarray, dict]:
        return self._embed([{"image": base64.b64encode(b).decode("ascii")} for b in images])

    def embed_texts(self, texts: list[str]) -> tuple[np.ndarray, dict]:
        return self._embed(list(texts))


class SentenceTransformersEmbedder:
    """EG2 through sentence-transformers on MPS (or CPU) in bf16/fp32. Needs the `embed` extra."""

    def __init__(self, model: str, revision: str, dtype: str, image_budget: int, device: str | None = None):
        import torch
        from sentence_transformers import SentenceTransformer

        device = device or ("mps" if torch.backends.mps.is_available() else "cpu")
        self.image_budget = image_budget
        self.st = SentenceTransformer(
            model, revision=revision, device=device,
            model_kwargs={"torch_dtype": getattr(torch, dtype)}, config_kwargs={"audio_config": None})

    @staticmethod
    def version() -> str:
        import sentence_transformers
        import torch
        import transformers

        return (f"sentence-transformers {sentence_transformers.__version__}, "
                f"transformers {transformers.__version__}, torch {torch.__version__}")

    def model_info(self) -> ModelInfo:
        return ModelInfo(digest="", tensor_types=set())

    def _encode(self, inputs: list, **kw) -> np.ndarray:
        return np.asarray(self.st.encode(inputs, normalize_embeddings=True, convert_to_numpy=True, **kw),
                          dtype=np.float32)

    def embed_images(self, images: list[bytes]) -> tuple[np.ndarray, dict]:
        from PIL import Image

        pics = [Image.open(io.BytesIO(b)).convert("RGB") for b in images]
        kw = {"processing_kwargs": {"image": {"max_soft_tokens": self.image_budget}}}
        return self._encode([{"image": p} for p in pics], **kw), _no_usage()

    def embed_texts(self, texts: list[str]) -> tuple[np.ndarray, dict]:
        return self._encode(list(texts)), _no_usage()


def _no_usage() -> dict:
    return {"input_tokens": None, "output_tokens": 0, "thinking_tokens": None}


_EMBEDDERS: dict[tuple, object] = {}


def embedder_for(cand: dict):
    """One cached backend instance per (backend, model, revision, dtype, budget)."""
    check_precision(cand)
    key = tuple(cand.get(k) for k in ("backend", "model", "revision", "dtype", "image_budget"))
    if key not in _EMBEDDERS:
        if cand["backend"] == "ollama":
            _EMBEDDERS[key] = OllamaEmbedder(cand["model"])
        elif cand["backend"] == "sentence-transformers":
            _EMBEDDERS[key] = SentenceTransformersEmbedder(
                cand["model"], cand["revision"], cand["dtype"], int(cand["image_budget"]))
        else:
            raise ValueError(f"unknown embed backend {cand['backend']!r}")
    return _EMBEDDERS[key]


def embed_query(cand: dict, query: str, dim: int = NATIVE_DIM) -> np.ndarray:
    vecs, _ = embedder_for(cand).embed_texts([query_text(cand, query)])
    check_vector(normalise(vecs[0]))
    return truncate(vecs[0], dim)


# --- search ----------------------------------------------------------------------------------

@dataclass
class Hit:
    asset_id: str
    score: float
    image_score: float | None
    caption_score: float | None


def search_matrix(records: dict[str, dict], dim: int) -> tuple[list[str], np.ndarray, np.ndarray, np.ndarray]:
    """From ok records: ids, image matrix, caption matrix (zeros where missing) and a has-caption mask."""
    ids, img, cap, has = [], [], [], []
    for aid, r in sorted(records.items()):
        out = r.get("output") or {}
        if r["status"] != "ok" or not out.get("image"):
            continue
        ids.append(aid)
        img.append(truncate(decode_vector(out["image"]["vector_b64"]), dim))
        c = out.get("caption")
        cap.append(truncate(decode_vector(c["vector_b64"]), dim) if c else np.zeros(dim, np.float32))
        has.append(bool(c))
    return (ids, np.array(img, dtype=np.float32).reshape(-1, dim),
            np.array(cap, dtype=np.float32).reshape(-1, dim), np.array(has, dtype=bool))


def rank(q: np.ndarray, ids: list[str], img: np.ndarray, cap: np.ndarray, has_cap: np.ndarray,
         mode: str = "image", k: int = 5) -> list[Hit]:
    """Cosine ranking. `blend` averages image and caption scores; assets with no caption use the image score."""
    if mode not in MODES:
        raise ValueError(f"mode must be one of {MODES}")
    if not ids:
        return []
    s_img = img @ q
    s_cap = cap @ q
    if mode == "image":
        score, keep = s_img, np.ones(len(ids), dtype=bool)
    elif mode == "caption":
        score, keep = s_cap, has_cap
    else:
        score, keep = np.where(has_cap, (s_img + s_cap) / 2, s_img), np.ones(len(ids), dtype=bool)
    order = [i for i in np.argsort(-score, kind="stable") if keep[i]][:k]
    return [Hit(ids[i], float(score[i]), float(s_img[i]),
                float(s_cap[i]) if has_cap[i] else None) for i in order]

