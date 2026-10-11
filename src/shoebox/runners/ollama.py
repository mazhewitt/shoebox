"""Ollama runtime: schema-constrained labels from a local Gemma via `POST /api/chat` (localhost only)."""

import base64
import json
import time
from functools import cache
from pathlib import Path

import httpx
from jsonschema import Draft202012Validator

from shoebox.runners import normalise_label

# The only model endpoint allowed: no photo leaves this machine. The loopback address (not "localhost")
# so a hosts-file entry cannot redirect it, and trust_env=False so HTTP(S)_PROXY / ALL_PROXY cannot either.
HOST = "http://127.0.0.1:11434"
KEEP_ALIVE = "15m"
# Hashed into every Ollama candidate's params (via `fingerprint`): bump when parsing or normalisation changes,
# so outputs made under different rules never share a key. 2: tags lowercased; cap hits marked `truncated`.
RUNNER_VERSION = "ollama-runner-2"
OPTIONS = ("temperature", "seed", "num_ctx", "num_predict", "repeat_penalty")
TIMEOUT = httpx.Timeout(900.0, connect=5.0)


def _client(timeout: httpx.Timeout | None = None) -> httpx.Client:
    return httpx.Client(base_url=HOST, timeout=timeout or TIMEOUT, trust_env=False)


def _get(path: str) -> dict:
    with _client() as c:
        r = c.get(path)
        r.raise_for_status()
        return r.json()


def _post(path: str, body: dict) -> dict:
    with _client() as c:
        r = c.post(path, json=body)
        r.raise_for_status()
        return r.json()


def runtime_version(cand: dict) -> str:
    return f"ollama {_get('/api/version')['version']}"


def fingerprint(cand: dict) -> dict:
    """The model's digest, so a re-pulled tag gets a new params_hash, and the runner's parsing version."""
    names = {cand["model"], f"{cand['model']}:latest"}
    for m in _get("/api/tags").get("models", []):
        if m.get("name") in names or m.get("model") in names:
            return {"model_digest": m["digest"], "runner": RUNNER_VERSION}
    raise RuntimeError(f"model {cand['model']} is not pulled in the local Ollama")


def check_model(cand: dict, show: dict) -> None:
    """Refuse a model without vision, or whose reported quantisation differs from the candidate's."""
    if "vision" not in show.get("capabilities", []):
        raise RuntimeError(f"{cand['model']} has no vision capability")
    reported = (show.get("details") or {}).get("quantization_level") or ""
    if reported and cand.get("quant") and reported.lower() != str(cand["quant"]).lower():
        raise RuntimeError(f"{cand['model']}: candidate quant {cand['quant']!r} but Ollama reports {reported!r}")


def preflight(cand: dict, items: list[dict], cfg) -> list[dict]:
    """Check the model, then load it so the first item's latency excludes model load time."""
    check_model(cand, _post("/api/show", {"model": cand["model"]}))
    _post("/api/generate", {"model": cand["model"], "keep_alive": KEEP_ALIVE})
    return items


@cache
def _schemas(schema_text: str) -> tuple[dict, Draft202012Validator, Draft202012Validator]:
    """(format sent to the model, validator for the stored schema, validator for the model-only rules).

    A null caption is valid in label-v1 for the rule baseline only, so the model must give a string.
    The format omits `pattern`: with it, Ollama 0.40.2's GGUF runner silently drops the whole grammar
    (tested 2026-10-10). Outputs are still validated against the full schema, pattern included.
    """
    schema = json.loads(schema_text)
    strict = json.loads(schema_text)
    strict["properties"]["caption"] = {**strict["properties"]["caption"], "type": "string"}
    return _without(strict, "pattern"), Draft202012Validator(schema), Draft202012Validator(strict)


def _without(node, key: str):
    if isinstance(node, dict):
        return {k: _without(v, key) for k, v in node.items() if k != key}
    if isinstance(node, list):
        return [_without(v, key) for v in node]
    return node


def _usage(resp: dict) -> dict:
    return {"input_tokens": resp.get("prompt_eval_count"), "output_tokens": resp.get("eval_count"),
            "thinking_tokens": None}


def run_one(cand: dict, item: dict, image_path: Path, cfg) -> dict:
    fmt = _schemas(cand["schema_text"])[0]
    body = {
        "model": cand["model"],
        "messages": [{"role": "user", "content": cand["prompt_text"],
                      "images": [base64.b64encode(Path(image_path).read_bytes()).decode()]}],
        "format": fmt,
        "think": bool(cand.get("think", False)),
        "stream": False,
        "keep_alive": KEEP_ALIVE,
        "options": {k: cand[k] for k in OPTIONS if k in cand},
    }
    result = {"status": "error", "output": None, "raw": None, "error": None,
              "usage": {"input_tokens": None, "output_tokens": None, "thinking_tokens": None}, "usd": 0.0}
    t0 = time.monotonic()
    try:
        with _client() as c:
            r = c.post("/api/chat", json=body)
        result["latency_ms"] = round((time.monotonic() - t0) * 1000)
        if r.status_code != 200:
            result["error"] = f"HTTP {r.status_code}: {r.text[:300]}"
            return result
        resp = r.json()
    except (httpx.HTTPError, ValueError) as e:
        result["latency_ms"] = round((time.monotonic() - t0) * 1000)
        result["error"] = f"{type(e).__name__}: {e}"
        return result

    raw = (resp.get("message") or {}).get("content", "")
    result.update(usage=_usage(resp), raw=raw)
    return {**result, **parse_output(cand, raw, capped=resp.get("done_reason") == "length")}


def parse_output(cand: dict, raw: str, capped: bool = False) -> dict:
    """{status, output, raw, error[, reason]} for the model's text. Tags are lowercased before validation (the
    one documented normalisation); nothing else is coerced. Output that hit `num_predict` and fails is
    `invalid` with reason `truncated`, which the framework retries once under the candidate's `retry` table."""
    _, base_v, strict_v = _schemas(cand["schema_text"])
    cap = f"truncated at num_predict={cand.get('num_predict')}: " if capped else ""
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as e:
        return _invalid(raw, f"{cap}not JSON: {e}", capped)
    parsed = normalise_label(parsed)
    errors = sorted(base_v.iter_errors(parsed), key=str) or sorted(strict_v.iter_errors(parsed), key=str)
    if errors:
        e = errors[0]
        where = "/".join(str(p) for p in e.absolute_path) or "(root)"
        return _invalid(raw, f"{cap}schema: {where}: {e.message}"[:500], capped)
    return {"status": "ok", "output": parsed, "raw": raw if parsed != json.loads(raw) else None, "error": None}


def _invalid(raw: str, error: str, capped: bool) -> dict:
    out = {"status": "invalid", "output": None, "raw": raw, "error": error}
    return {**out, "reason": "truncated"} if capped else out


def reparse(cand: dict, rec: dict) -> dict:
    """Re-apply this runner's parsing to a stored final record (a replay: no inference). An `ok` record's
    output is re-validated; an `invalid` record's raw text is parsed again. Records from ollama-runner-1 mark a
    cap hit only in the error text."""
    if rec["status"] == "ok":
        return parse_output(cand, rec["raw"] or json.dumps(rec["output"], ensure_ascii=False))
    capped = rec.get("reason") == "truncated" or "done_reason=length" in (rec.get("error") or "")
    return parse_output(cand, rec.get("raw") or "", capped=capped)
