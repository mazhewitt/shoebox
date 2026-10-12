"""Gemini 3.8 Flash runner (runtime `gemini`, docs/pilot.md "Gemini arm"; design D9).

Calls `models/<model>:generateContent` (v1beta REST, httpx) with one derived infer-v1 JPEG inline,
per-part `mediaResolution`, `thinkingConfig.thinkingLevel`, and a JSON-schema response. No tools,
no grounding, no cached content, no Files API, no Interactions. Field names follow the v1beta
reference (https://ai.google.dev/api/generate-content, read 2026-10-10).

Spend: every billed call appends (and fsyncs) one `evals/spend.jsonl` line before `run_one`
returns, so a killed run loses no spend. Before every call the run's spend is checked against
`max_usd_per_run`, the whole log (all processes) against the project budget, and the candidate's
`price_valid_until` against today.

The API key comes from `GEMINI_API_KEY`, else the repo-root `.env`. It is only ever put in the
request header and is scrubbed from every error and raw text this module returns or raises.
"""

import base64
import copy
import email.utils
import json
import os
import sys
import time
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from functools import lru_cache
from pathlib import Path

import httpx
import jsonschema

from shoebox import config, privacy
from shoebox.runners import StopRun, normalise_label

API_ROOT = "https://generativelanguage.googleapis.com/v1beta"
RUNNER_VERSION = (
    "gemini-runner-2"  # bump when request building, schema adaptation or parsing changes; 2: tags lowercased
)
ENV_PATH = config.REPO_ROOT / ".env"
PROMPTS_DIR = config.REPO_ROOT / "config" / "prompts"
SCHEMAS_DIR = config.REPO_ROOT / "config" / "schemas"

MEDIA_RESOLUTION = {
    "low": "MEDIA_RESOLUTION_LOW",
    "medium": "MEDIA_RESOLUTION_MEDIUM",
    "high": "MEDIA_RESOLUTION_HIGH",
}
THINKING_LEVEL = {
    "low": "LOW",
    "medium": "MEDIUM",
    "high": "HIGH",
}  # 3.8 Flash rejects "minimal"
SCHEMA_FIELDS = ("responseJsonSchema", "responseFormat", "responseSchema")
RETRY_STATUS = {429, 500, 503}
MAX_INLINE_BYTES = 20 * 1024 * 1024  # the stricter of the documented inline limits

# JSON Schema keywords Gemini's responseJsonSchema accepts (API reference, GenerationConfig).
_SCHEMA_KEYWORDS = {
    "$id", "$defs", "$ref", "$anchor", "type", "format", "title", "description", "enum", "items",
    "prefixItems", "minItems", "maxItems", "minimum", "maximum", "anyOf", "oneOf", "properties",
    "additionalProperties", "required", "propertyOrdering",
}  # fmt: skip

# Tests swap these for an httpx.MockTransport and a no-op sleep.
_TRANSPORT: httpx.BaseTransport | None = None
_sleep = time.sleep


class GeminiError(RuntimeError):
    """A Gemini run cannot proceed (missing key, cap reached, refused input)."""


class SpendCapReached(GeminiError, StopRun):
    """A spend cap is reached: the framework stops the run rather than recording an error per item."""


# ---------- key ----------


def _read_env_file(path: Path) -> dict[str, str]:
    out = {}
    try:
        lines = path.read_text().splitlines()
    except FileNotFoundError:
        return out
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, _, value = line.removeprefix("export ").partition("=")
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
            value = value[1:-1]
        out[name.strip()] = value
    return out


def api_key() -> str:
    key = os.environ.get("GEMINI_API_KEY") or _read_env_file(ENV_PATH).get(
        "GEMINI_API_KEY", ""
    )
    if not key:
        raise GeminiError("GEMINI_API_KEY is not set (environment or repo-root .env)")
    return key


def _scrub(text, key: str | None):
    if isinstance(text, str) and key:
        return text.replace(key, "[redacted]")
    return text


# ---------- candidate settings ----------


def _setting(cand: dict, name: str, table: dict) -> str:
    value = cand.get(name)
    if value not in table:
        raise GeminiError(f"{name} must be one of {sorted(table)}, got {value!r}")
    return table[value]


def _cand_name(cand: dict) -> str:
    if cand.get("name"):
        return str(cand["name"])
    return f"{cand.get('model', 'gemini')}/{cand.get('media_resolution')}/{cand.get('thinking_level')}"


@lru_cache(maxsize=8)
def _load_text(path: str) -> str:
    return Path(path).read_text()


def prompt_text(cand: dict) -> str:
    return cand.get("prompt_text") or _load_text(str(PROMPTS_DIR / cand["prompt"]))


def schema_doc(cand: dict) -> dict:
    return json.loads(
        cand.get("schema_text") or _load_text(str(SCHEMAS_DIR / cand["schema"]))
    )


def runtime_version(cand: dict) -> str:
    return f"gemini-api v1beta generateContent ({RUNNER_VERSION})"


# ---------- schema and request ----------


def gemini_schema(schema: dict, non_null: tuple[str, ...] = ("caption",)) -> dict:
    """Adapt a draft 2020-12 schema to the keywords Gemini accepts. The original still validates
    the output afterwards, so dropped constraints (minLength, pattern, ...) are enforced locally.
    Top-level properties in `non_null` lose their "null" type: a null caption is valid for the
    rule baseline only (docs/pilot.md, section 3)."""

    def walk(node):
        if isinstance(node, list):
            return [walk(n) for n in node]
        if not isinstance(node, dict):
            return node
        out = {}
        for k, v in node.items():
            if k not in _SCHEMA_KEYWORDS:
                continue
            if k in ("properties", "$defs"):
                out[k] = {name: walk(sub) for name, sub in v.items()}
            elif k in (
                "items",
                "additionalProperties",
                "anyOf",
                "oneOf",
                "prefixItems",
            ):
                out[k] = walk(v)
            else:
                out[k] = copy.deepcopy(v)
        if (
            "enum" in out
            and "type" not in out
            and all(isinstance(e, str) for e in out["enum"])
        ):
            out["type"] = "string"
        return out

    adapted = walk(schema)
    adapted.pop("$id", None)
    for name in non_null:
        prop = adapted.get("properties", {}).get(name)
        if prop and isinstance(prop.get("type"), list):
            types = [t for t in prop["type"] if t != "null"]
            prop["type"] = types[0] if len(types) == 1 else types
    return adapted


# OpenAPI Schema keywords `responseSchema` takes (API reference, Schema); `type` is upper-cased.
_OPENAPI_KEYWORDS = {"type", "format", "description", "nullable", "enum", "items", "minItems", "maxItems",
                     "minimum", "maximum", "properties", "required", "propertyOrdering", "anyOf"}


def openapi_schema(schema: dict, non_null: tuple[str, ...] = ("caption",)) -> dict:
    """`gemini_schema(schema)` in the OpenAPI subset of `responseSchema` (the Batch API's structured-output
    field): a ["x", "null"] type becomes type X with `nullable`, an untyped enum is a STRING, objects keep their
    field order in `propertyOrdering`, and keywords outside the subset (additionalProperties, $id), and enums of a
non-string type, are dropped.
    The original schema still validates every output."""

    def walk(node):
        if not isinstance(node, dict):
            return node
        out = {}
        t = node.get("type")
        if isinstance(t, list):
            kinds = [k for k in t if k != "null"]
            t = kinds[0] if len(kinds) == 1 else None
            if "null" in node["type"]:
                out["nullable"] = True
        if t is None and "enum" in node:
            t = "string"
        if t is not None:
            out["type"] = t.upper()
        for k, v in node.items():
            if k == "type" or k not in _OPENAPI_KEYWORDS:
                continue
            if k == "properties":
                out[k] = {name: walk(sub) for name, sub in v.items()}
                out.setdefault("propertyOrdering", list(v))
            elif k in ("items",):
                out[k] = walk(v)
            elif k == "anyOf":
                out[k] = [walk(n) for n in v]
            elif k == "enum" and out.get("type") != "STRING":
                continue  # enum takes strings only (live 400 on rotate_cw); local validation keeps it exact
            else:
                out[k] = v
        return {k: out[k] for k in sorted(out, key=lambda k: (k != "type", k != "nullable"))}

    return walk(gemini_schema(schema, non_null))


def build_request(cand: dict, prompt: str, schema: dict, jpeg: bytes) -> dict:
    """The full generateContent body. Image first, then the instructions (Google's advice for one image)."""
    gen: dict = {
        "responseMimeType": "application/json",
        "thinkingConfig": {
            "thinkingLevel": _setting(cand, "thinking_level", THINKING_LEVEL)
        },
    }
    field = cand.get("schema_field", "responseJsonSchema")
    if field == "responseJsonSchema":
        gen["responseJsonSchema"] = gemini_schema(schema)
    elif field == "responseSchema":
        gen["responseSchema"] = openapi_schema(schema)
    elif field == "responseFormat":
        gen["responseFormat"] = {
            "text": {"mimeType": "APPLICATION_JSON", "schema": gemini_schema(schema)}
        }
        del gen["responseMimeType"]
    else:
        raise GeminiError(f"schema_field must be one of {SCHEMA_FIELDS}, got {field!r}")
    if cand.get("max_output_tokens"):
        gen["maxOutputTokens"] = int(cand["max_output_tokens"])
    for src, dst in (("temperature", "temperature"), ("seed", "seed")):
        if cand.get(src) is not None:
            gen[dst] = cand[src]
    image_part = {
        "inlineData": {
            "mimeType": "image/jpeg",
            "data": base64.b64encode(jpeg).decode("ascii"),
        },
        "mediaResolution": {
            "level": _setting(cand, "media_resolution", MEDIA_RESOLUTION)
        },
    }
    return {
        "contents": [{"role": "user", "parts": [image_part, {"text": prompt}]}],
        "generationConfig": gen,
    }


# ---------- image guard ----------

_METADATA_APPS = {0xE1: "EXIF/XMP", 0xED: "IPTC"}


def check_derived_jpeg(data: bytes) -> None:
    """Refuse anything that isn't a baseline/progressive JPEG free of EXIF, XMP, IPTC and comments.
    infer-v1 strips metadata, so a hit here means a derive bug or a wrong file."""
    if len(data) > MAX_INLINE_BYTES:
        raise GeminiError(
            f"derived image is {len(data)} bytes, over the {MAX_INLINE_BYTES} inline limit"
        )
    if data[:2] != b"\xff\xd8":
        raise GeminiError("derived image is not a JPEG")
    i = 2
    while i + 4 <= len(data):
        if data[i] != 0xFF:
            raise GeminiError("derived image has a malformed JPEG marker stream")
        marker = data[i + 1]
        if marker == 0xFF:
            i += 1
            continue
        if (
            marker == 0xDA
        ):  # start of scan: no more metadata segments before the image data
            return
        if marker in (0x01,) or 0xD0 <= marker <= 0xD7:
            i += 2
            continue
        length = int.from_bytes(data[i + 2 : i + 4], "big")
        if marker in _METADATA_APPS or marker == 0xFE:
            raise GeminiError(
                f"derived image carries {_METADATA_APPS.get(marker, 'comment')} metadata; refusing"
            )
        i += 2 + length
    raise GeminiError("derived image has no image data")


def _check_image_path(cand: dict, image_path: Path, cfg: config.Config) -> Path:
    """Only the derived infer-v1 file for this profile, never an original under a source root."""
    path = Path(image_path).resolve()
    base = (cfg.derived_dir / cand.get("profile", "infer-v1")).resolve()
    if not path.is_relative_to(base) or path.suffix.lower() != ".jpg":
        raise GeminiError(
            f"refusing to send {path.name}: not a derived {cand.get('profile', 'infer-v1')} JPEG"
        )
    for src in cfg.sources.values():
        if path.is_relative_to(src.root.resolve()):
            raise GeminiError("refusing to send a file under a source root")
    return path


# ---------- response ----------


def usage_of(data: dict) -> dict:
    """Token counts; plus `input_by_modality` ({"IMAGE": n, "TEXT": m}) when the response carries
    `promptTokensDetails`, so image tokens can be measured against the media_resolution estimate."""
    meta = data.get("usageMetadata") or {}
    usage = {
        "input_tokens": meta.get("promptTokenCount"),
        "output_tokens": meta.get("candidatesTokenCount"),
        "thinking_tokens": meta.get("thoughtsTokenCount"),
    }
    details = {
        d["modality"]: d.get("tokenCount")
        for d in meta.get("promptTokensDetails") or []
        if isinstance(d, dict) and d.get("modality")
    }
    if details:
        usage["input_by_modality"] = details
    return usage


def usd_for(usage: dict, cand: dict) -> float:
    pin, pout = privacy.price(cand)
    out = (usage.get("output_tokens") or 0) + (usage.get("thinking_tokens") or 0)
    return (usage.get("input_tokens") or 0) * pin + out * pout


def parse_response(
    data: dict, schema: dict
) -> tuple[str, dict | None, str | None, str | None]:
    """(status, output, raw, error). Blocked or empty -> invalid with finishReason in raw;
    bad JSON or schema failure -> invalid with the text in raw. Never coerced beyond lowercasing tags."""
    block = (data.get("promptFeedback") or {}).get("blockReason")
    cands = data.get("candidates") or []
    first = cands[0] if cands else {}
    finish = first.get("finishReason")
    parts = (first.get("content") or {}).get("parts") or []
    text = "".join(p.get("text", "") for p in parts if not p.get("thought"))
    if block or not text.strip():
        raw = json.dumps(
            {"finishReason": finish, "blockReason": block, "text": text or None}
        )
        return (
            "invalid",
            None,
            raw,
            f"no output (finishReason={finish}, blockReason={block})",
        )
    try:
        output = json.loads(text)
    except ValueError as e:
        return "invalid", None, text, f"not JSON (finishReason={finish}): {e}"
    output = normalise_label(output)
    errors = sorted(
        jsonschema.Draft202012Validator(schema).iter_errors(output),
        key=lambda e: list(e.path),
    )
    if errors:
        msg = "; ".join(
            f"{'/'.join(map(str, e.path)) or '<root>'}: {e.message}" for e in errors[:5]
        )
        return "invalid", None, text, f"schema: {msg}"
    if isinstance(output, dict) and output.get("caption") is None:
        return (
            "invalid",
            None,
            text,
            "schema: null caption is valid for the rule baseline only",
        )
    if finish not in (None, "STOP"):
        return "invalid", None, text, f"finishReason={finish}"
    return "ok", output, None, None


# ---------- HTTP with retries ----------


def _retry_after(resp: httpx.Response) -> float | None:
    value = resp.headers.get("retry-after")
    if not value:
        return None
    try:
        return max(0.0, float(value))
    except ValueError:
        pass
    try:
        when = email.utils.parsedate_to_datetime(value)
        return max(0.0, (when - datetime.now(UTC)).total_seconds())
    except (TypeError, ValueError):
        return None


def _error_message(resp: httpx.Response) -> str:
    try:
        err = resp.json().get("error", {})
        return f"HTTP {resp.status_code} {err.get('status', '')}: {err.get('message', '')}".strip()
    except (ValueError, AttributeError):
        return f"HTTP {resp.status_code}: {resp.text[:300]}"


def _post(cand: dict, body: dict, key: str) -> tuple[dict | None, str | None]:
    """POST with exponential backoff on 429/500/503 and transport errors (Retry-After respected).
    Returns (json, None) or (None, error)."""
    if _TRANSPORT is None and "PYTEST_CURRENT_TEST" in os.environ:
        raise GeminiError(
            "live Gemini call refused under pytest: inject gemini._TRANSPORT"
        )
    url = f"{API_ROOT}/models/{cand['model']}:generateContent"
    attempts = int(cand.get("max_attempts", 5))
    base, cap = (
        float(cand.get("backoff_base_s", 2.0)),
        float(cand.get("backoff_max_s", 120.0)),
    )
    timeout = float(cand.get("timeout_s", 120.0))
    error = "no attempt made"
    with httpx.Client(
        transport=_TRANSPORT, timeout=timeout, follow_redirects=False
    ) as client:
        for attempt in range(attempts):
            wait = min(cap, base * 2**attempt)
            try:
                resp = client.post(url, json=body, headers={"x-goog-api-key": key})
            except httpx.TransportError as e:
                error = f"transport: {type(e).__name__}"
            else:
                if resp.status_code == 200:
                    try:
                        return resp.json(), None
                    except ValueError:
                        return None, "HTTP 200 with a non-JSON body"
                error = _error_message(resp)
                if resp.status_code not in RETRY_STATUS:
                    return None, error
                after = _retry_after(resp)
                if after is not None:
                    wait = min(cap, max(wait, after))
            if attempt + 1 < attempts:
                _sleep(wait)
    return None, f"gave up after {attempts} attempts: {error}"


# ---------- per-run state ----------


@dataclass
class _Run:
    candidate: str
    price_date: str
    max_usd: float
    run_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    n: int = 0
    usd: float = 0.0


_runs: dict[str, _Run] = {}
_allowed: dict[str, set[str]] = {}


def _record_spend(run: _Run, usd: float) -> None:
    """Durable before anything else happens to the response: the call has been billed."""
    run.n += 1
    run.usd += usd
    privacy.append_spend(run.candidate, 1, usd, run.price_date, run=run.run_id)


# ---------- runner interface ----------


def _check(cand: dict, items: list[dict], cfg: config.Config) -> privacy.Eligibility:
    """Settings, checklist, never-send/consent, spend estimate and key. Raises to refuse."""
    global last_eligibility
    _setting(cand, "thinking_level", THINKING_LEVEL)
    _setting(cand, "media_resolution", MEDIA_RESOLUTION)
    privacy.require_checklist()
    privacy.require_current_price(cand)
    elig = privacy.filter_items(items, cfg, others=privacy.known_items(), provider="gemini")
    last_eligibility = elig
    name = _cand_name(cand)
    print(f"gemini preflight [{name}]: {elig.summary()}", file=sys.stderr)
    for (
        s
    ) in elig.skipped:  # per asset, so the run report says why (consent vs never-send)
        print(
            f"gemini preflight [{name}]: skipped {s.asset_id} ({s.source}): {s.reason}",
            file=sys.stderr,
        )
    est = privacy.estimate(cand, len(elig.allowed))
    privacy.check_budget(cand, est, already_spent=privacy.spent_total())
    print(
        f"gemini preflight [{name}]: estimate ${est.usd:.4f} for {est.n} images "
        f"(cap ${float(cand['max_usd_per_run']):.2f})",
        file=sys.stderr,
    )
    api_key()  # fail now, not on the first item
    return elig


def preflight(cand: dict, items: list[dict], cfg: config.Config) -> list[dict]:
    """Start a run: check everything, return the allowed items. Each skipped item is reported on
    stderr with its reason; `last_eligibility` keeps the details."""
    elig = _check(cand, items, cfg)
    name = _cand_name(cand)
    _allowed[name] = {it["asset_id"] for it in elig.allowed if it.get("asset_id")}
    _runs[name] = _Run(
        name, str(cand.get("price_date", "")), float(cand["max_usd_per_run"])
    )
    return elig.allowed


last_eligibility: privacy.Eligibility | None = None


def _ensure_cleared(cand: dict, item: dict, cfg: config.Config) -> _Run:
    """run_one without a preflight for this item (e.g. a direct call) checks it first. Inside a
    run, the run and its spend accumulator are kept, so the run cap can't be reset this way."""
    name = _cand_name(cand)
    if name in _runs and item.get("asset_id") in _allowed.get(name, set()):
        return _runs[name]
    if name in _runs:
        allowed = _check(cand, [item], cfg).allowed
        _allowed[name].update(it["asset_id"] for it in allowed)
    else:
        allowed = preflight(cand, [item], cfg)
    if not allowed:
        skipped = last_eligibility.skipped if last_eligibility else []
        reason = skipped[0].reason if skipped else "?"
        raise privacy.PrivacyError(
            f"asset {item.get('asset_id')} is not eligible for hosted calls: {reason}"
        )
    return _runs[name]


def run_one(cand: dict, item: dict, image_path: Path, cfg: config.Config) -> dict:
    """One image -> {status, output, raw, error, usage, latency_ms, usd}."""
    run = _ensure_cleared(cand, item, cfg)
    privacy.require_current_price(cand)  # a long run can cross the price boundary
    lim = privacy.limits()
    if run.usd >= run.max_usd:
        raise SpendCapReached(
            f"run spend ${run.usd:.4f} reached max_usd_per_run ${run.max_usd:.2f}"
        )
    spent = (
        privacy.spent_total()
    )  # the whole log: earlier, killed and parallel runs too
    if spent >= lim.max_total_usd:
        raise SpendCapReached(
            f"project spend ${spent:.4f} reached the budget ${lim.max_total_usd:.2f}"
        )

    path = _check_image_path(cand, image_path, cfg)
    jpeg = path.read_bytes()
    check_derived_jpeg(jpeg)
    schema = schema_doc(cand)
    body = build_request(cand, prompt_text(cand), schema, jpeg)
    key = api_key()

    t0 = time.monotonic()
    data, error = _post(cand, body, key)
    latency_ms = int((time.monotonic() - t0) * 1000)
    if data is None:
        empty = {"input_tokens": None, "output_tokens": None, "thinking_tokens": None}
        return {"status": "error", "output": None, "raw": None, "error": _scrub(error, key),
                "usage": empty, "latency_ms": latency_ms, "usd": 0.0}  # fmt: skip

    usage = usage_of(data)
    usd = usd_for(usage, cand)
    _record_spend(run, usd)
    status, output, raw, err = parse_response(data, schema)
    return {
        "status": status,
        "output": output,
        "raw": _scrub(raw, key),
        "error": _scrub(err, key),
        "usage": usage,
        "latency_ms": latency_ms,
        "usd": round(usd, 8),
    }
