"""Anthropic Message Batches runner (runtime `anthropic-batch`), modelled on `runners.gemini_batch`.

Sends each derived infer-v1 image as the Claude packets' 1024 px thumbnail (`external.thumbnail_jpeg`, the
image the `opus-v2` subagents saw) with the candidate's prompt and a structured-output schema
(`output_config.format` json_schema; `anthropic_schema` drops the keywords the API does not take, and the
full schema validates every output locally). API: https://api.anthropic.com/v1/messages/batches
(anthropic-version 2023-06-01; facts checked 2026-10-13). Entry point: `run_batch(res, items, cfg, transport)`.

Guards, all before anything is submitted: the privacy checklist and the candidate's current price (checked
again per batch); `privacy.filter_items(provider="claude")`; the derived-image checks
(`gemini._check_image_path`, `gemini.check_derived_jpeg`, on the derived file and on the thumbnail); per batch,
the batch-price estimate plus this run's spend within `max_usd_per_run`, and plus the provider's spend log rows
and open batches' estimates within the candidate's `budget_provider` budget (anthropic: $100, separate from
the Gemini project budget; `privacy.Limits.budget`). A batch that would breach either is cut to the images that
fit and the run stops there (`refused` says why; rerun to continue). The candidate must have
`budget_provider = "anthropic"`, or nothing runs.

Ledger: `results/batch-jobs/<candidate>/` with gemini_batch's record format and helpers (`save_job`,
`load_jobs`), plus `n_requests`. A batch has no display name at the provider, so an intent
(`intent_<local name>.json`, state `submitting`) records the request count and `submitted_ts` (taken before
the POST). A restarted run settles each intent with `transport.find(created_after, n)`: batches created at or
after `submitted_ts - CLOCK_SLACK_S` with exactly n requests, minus every batch id already in the ledger.
Exactly one is adopted (never resubmitted); none marks the intent `failed` (its items are pending again);
more than one raises `LedgerError` and nothing is submitted: the owner must check the console
(console.anthropic.com, Batches) and either rename the intent to `<batch id>.json` with that `job_id` and
state `submitted`, or set its state to `failed`. A lookup that fails raises `LedgerError` too.

On `ended`: the results are downloaded; a result whose custom_id is not one of the job's items (e.g. an adopted
intent that was another client's batch) raises `LedgerError` before anything is logged or stored. The batch's actual cost (usage of every `succeeded` result at the
batch price: errored, canceled and expired requests are not billed) is appended to `evals/spend.jsonl` with
`provider: anthropic` before any label is stored, once per batch (`spend_logged`). Per result: `succeeded` is
parsed (stop_reason `refusal` -> invalid, reason `refused`; `max_tokens` -> invalid, reason `truncated`; else
JSON + the full schema); `errored` is stored as an `error` record (retried by the next run); `canceled` and
`expired` store nothing (pending again, counted in `returned`).
"""

import base64
import copy
import json
import os
import sys
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Protocol

import httpx

from shoebox import config, privacy, store
from shoebox.runners import StopRun, external, gemini
from shoebox.runners.gemini_batch import (
    _ledger_path,
    _record_job_id,
    ledger_dir,
    load_jobs,
    save_job,
)

API_ROOT = "https://api.anthropic.com/v1"
API_VERSION = "2023-06-01"
BATCH_RUNNER_VERSION = "anthropic-batch-runner-1"  # bump when request building, schema adaptation or parsing change
PROVIDER = "claude"            # privacy.filter_items / hosted_models
MAX_REQUESTS = 100_000         # per batch (API limit)
MAX_BATCH_BYTES = 250 * 1024 * 1024  # under the 256 MB limit
POLL_INTERVAL_S = 60.0
CLOCK_SLACK_S = 300            # intent lookup window before submitted_ts (local vs server clock)

OPEN = ("submitted", "in_progress", "canceling")
_UNSUPPORTED = {"minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum", "multipleOf", "minLength",
                "maxLength", "pattern", "maxItems", "uniqueItems"}


class AnthropicError(RuntimeError):
    """An Anthropic batch run cannot proceed."""


class BatchOnly(AnthropicError, StopRun):
    """`anthropic-batch` candidates run through `run_batch`, not the per-item loop."""


class LedgerError(AnthropicError):
    """The batch ledger cannot be reconciled safely; nothing is submitted (fail closed)."""


# ---------- runner interface (framework) ----------


def runtime_version(cand: dict) -> str:
    return f"anthropic-api {API_VERSION} messages/batches ({BATCH_RUNNER_VERSION}; thumb {external.THUMB_PX}px q85)"


def run_one(cand, item, image_path, cfg) -> dict:
    raise BatchOnly("anthropic-batch candidates are run with anthropic_batch.run_batch, not item by item")


def api_key() -> str:
    key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not key:
        raise AnthropicError(
            "ANTHROPIC_API_KEY is not set: run via `sops exec-env secrets/shoebox.sops.yaml '<cmd>'`")
    return key


def now_iso() -> str:
    from shoebox.runners import _now

    return _now()


# ---------- schema, request, prices ----------


def anthropic_schema(schema: dict, non_null: tuple[str, ...] = ("caption",)) -> dict:
    """The schema in the subset structured outputs accept: unsupported keywords dropped (minItems kept only at
    0 or 1), `additionalProperties: false` on every object. Top-level `non_null` properties lose "null" (a
    null caption is valid for the rule baseline only). The original still validates every output."""

    def walk(node):
        if isinstance(node, list):
            return [walk(n) for n in node]
        if not isinstance(node, dict):
            return node
        out = {}
        for k, v in node.items():
            if k in _UNSUPPORTED or (k == "minItems" and v > 1):
                continue
            if k in ("properties", "$defs", "definitions"):
                out[k] = {name: walk(sub) for name, sub in v.items()}
            elif k in ("items", "anyOf", "allOf", "oneOf", "prefixItems"):
                out[k] = walk(v)
            else:
                out[k] = copy.deepcopy(v)
        if out.get("type") == "object" or "properties" in out:
            out["additionalProperties"] = False
        return out

    adapted = walk(schema)
    for name in non_null:
        prop = adapted.get("properties", {}).get(name)
        if prop and isinstance(prop.get("type"), list):
            types = [t for t in prop["type"] if t != "null"]
            prop["type"] = types[0] if len(types) == 1 else types
    return adapted


# The user turn's text when the prompt is in the cached system block (`prompt_placement = "system-cached"`).
USER_TEXT = "Label this photo."
CACHE_CONTROL = {"type": "ephemeral", "ttl": "1h"}  # the 1-hour TTL the batch docs recommend


def build_params(cand: dict, prompt: str, schema: dict, jpeg: bytes) -> dict:
    """One request's `params`: the image first, then the instructions. With `prompt_placement =
    "system-cached"` the prompt is a system block marked for the 1-hour prompt cache (identical in every
    request, so later requests read it at the cache price) and the user turn is the image then `USER_TEXT`;
    without it, the prompt follows the image in the user turn (the layout of the first smoke run)."""
    image = {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg",
                                         "data": base64.b64encode(jpeg).decode("ascii")}}
    params = {"model": cand["model"], "max_tokens": int(cand["max_tokens"])}
    placement = cand.get("prompt_placement")
    if placement == "system-cached":
        params["system"] = [{"type": "text", "text": prompt, "cache_control": dict(CACHE_CONTROL)}]
        params["messages"] = [{"role": "user", "content": [image, {"type": "text", "text": USER_TEXT}]}]
    elif placement is None:
        params["messages"] = [{"role": "user", "content": [image, {"type": "text", "text": prompt}]}]
    else:
        raise AnthropicError(f"unknown prompt_placement {placement!r}; expected 'system-cached' or none")
    params["output_config"] = {"format": {"type": "json_schema", "schema": anthropic_schema(schema)}}
    if cand.get("temperature") is not None:
        params["temperature"] = cand["temperature"]
    return params


def batch_priced(cand: dict) -> dict:
    disc = cand.get("price_batch_discount")
    if disc is None or not 0 < float(disc) <= 1:
        raise privacy.PrivacyError(f"candidate needs price_batch_discount in (0, 1], got {disc!r}")
    pin, pout = privacy.price(cand)
    return {**cand, "price_input_usd_per_mtok": pin * 1e6 * float(disc),
            "price_output_usd_per_mtok": pout * 1e6 * float(disc)}


def _mult(cand: dict, key: str) -> float:
    if cand.get(key) is None:
        raise privacy.PrivacyError(f"candidate has no {key}: hosted runs need explicit prices")
    return float(cand[key])


def usage_of(message: dict) -> dict:
    """Token counts. `input_tokens` is the total input (uncached + cache writes + cache reads, as for every
    other runner); the API's own split is kept beside it: `uncached_input_tokens` (the API's `input_tokens`,
    after the last cache breakpoint), `cache_creation_input_tokens`, `cache_read_input_tokens` and the
    `cache_creation` TTL breakdown when the API sends one."""
    u = message.get("usage") or {}
    unc, cw, cr = (int(u.get(k) or 0) for k in ("input_tokens", "cache_creation_input_tokens",
                                                 "cache_read_input_tokens"))
    out = {"input_tokens": unc + cw + cr, "output_tokens": u.get("output_tokens"), "thinking_tokens": None,
           "uncached_input_tokens": unc, "cache_creation_input_tokens": cw, "cache_read_input_tokens": cr}
    if isinstance(u.get("cache_creation"), dict):
        out["cache_creation"] = {k: int(v or 0) for k, v in u["cache_creation"].items()}
    return out


def usd_for(usage: dict, bcand: dict) -> float:
    """Exact cost from the token split at the batch-priced base rates and the candidate's cache multipliers.
    Cache writes without a TTL breakdown (or beyond it) are billed as 1h writes, the dearer kind."""
    pin, pout = privacy.price(bcand)
    w5, w1h, rd = (_mult(bcand, k) for k in ("price_cache_write_5m_multiplier", "price_cache_write_1h_multiplier",
                                             "price_cache_read_multiplier"))
    if "uncached_input_tokens" not in usage:  # a usage without the split (error records): all uncached
        return (usage.get("input_tokens") or 0) * pin + (usage.get("output_tokens") or 0) * pout
    cw = int(usage.get("cache_creation_input_tokens") or 0)
    tt = usage.get("cache_creation") or {}
    t5 = min(cw, int(tt.get("ephemeral_5m_input_tokens") or 0))
    inp = (usage["uncached_input_tokens"] + t5 * w5 + (cw - t5) * w1h
           + int(usage.get("cache_read_input_tokens") or 0) * rd)
    return inp * pin + (usage.get("output_tokens") or 0) * pout


def estimate(bcand: dict, n: int, cache_hits: bool = False) -> privacy.Estimate:
    """Pre-flight cost for n requests at the batch-priced `bcand`. Default: the worst case, every request
    writing the prompt (`est_prompt_tokens`) to the 1h cache, used by the run-cap and budget checks. With
    `cache_hits`: the expected cost, one write then reads (a quote only; batch caching is best effort)."""
    base = privacy.estimate(bcand, n)
    if bcand.get("prompt_placement") != "system-cached" or n <= 0:
        return base
    pin, _ = privacy.price(bcand)
    w1h = _mult(bcand, "price_cache_write_1h_multiplier")
    prompt = int(bcand.get("est_prompt_tokens", 1000))
    if cache_hits:
        extra = prompt * pin * ((w1h - 1) + (n - 1) * (_mult(bcand, "price_cache_read_multiplier") - 1))
    else:
        extra = n * prompt * pin * (w1h - 1)
    return privacy.Estimate(base.n, base.input_tokens, base.output_tokens, base.usd + extra)


def parse_message(message: dict, schema: dict) -> tuple[str, dict | None, str | None, str | None, str | None]:
    """(status, output, raw, error, reason) for a succeeded result's message."""
    stop = message.get("stop_reason")
    text = "".join(b.get("text", "") for b in message.get("content") or [] if b.get("type") == "text")
    if stop == "refusal":
        return "invalid", None, json.dumps({"stop_reason": stop, "text": text or None}), "refused", "refused"
    if stop == "max_tokens":
        return "invalid", None, text, "stop_reason=max_tokens", "truncated"
    try:
        parsed = json.loads(text)
    except ValueError as e:
        return "invalid", None, text, f"not JSON (stop_reason={stop}): {e}", None
    output, err = external._validate(parsed, schema)
    if err:
        return "invalid", None, text, err, None
    return "ok", output, None, None, None


# ---------- transport ----------


class Transport(Protocol):
    def submit(self, requests: list[tuple[str, dict]]) -> str:
        """Submit `(custom_id, params)` pairs as one batch; return its id."""

    def poll(self, batch_id: str) -> str:
        """processing_status: in_progress, canceling or ended."""

    def download(self, batch_id: str) -> list[dict]:
        """The result lines `{custom_id, result}` of an ended batch."""

    def delete(self, batch_id: str) -> None:
        """Remove an ended batch and its results from the provider."""

    def find(self, created_after: str, n: int) -> list[str]:
        """Ids of batches created at or after `created_after` (ISO 8601) with `n` requests. Raises if the
        listing itself fails."""


class HttpTransport:
    """The Message Batches API over REST (httpx; no SDK dependency). Not yet exercised live."""

    def __init__(self, key: str | None = None, timeout_s: float = 300.0):
        self._key, self._timeout = key, timeout_s

    def _client(self) -> httpx.Client:
        if "PYTEST_CURRENT_TEST" in os.environ:
            raise AnthropicError("live Anthropic batch call refused under pytest: use a fake transport")
        return httpx.Client(timeout=self._timeout, follow_redirects=False, headers={
            "x-api-key": self._key or api_key(), "anthropic-version": API_VERSION,
            "content-type": "application/json"})

    def _call(self, method: str, url: str, **kw) -> httpx.Response:
        with self._client() as client:
            resp = client.request(method, url, **kw)
        if resp.status_code != 200:
            try:
                err = resp.json().get("error", {})
                msg = f"HTTP {resp.status_code} {err.get('type', '')}: {err.get('message', '')}"
            except (ValueError, AttributeError):
                msg = f"HTTP {resp.status_code}: {resp.text[:300]}"
            raise AnthropicError(gemini._scrub(msg, self._key or os.environ.get("ANTHROPIC_API_KEY")))
        return resp

    def submit(self, requests):
        body = {"requests": [{"custom_id": cid, "params": params} for cid, params in requests]}
        return self._call("POST", f"{API_ROOT}/messages/batches", json=body).json()["id"]

    def _get(self, batch_id: str) -> dict:
        return self._call("GET", f"{API_ROOT}/messages/batches/{batch_id}").json()

    def poll(self, batch_id):
        return str(self._get(batch_id).get("processing_status") or "")

    def download(self, batch_id):
        url = self._get(batch_id).get("results_url")
        if not url:
            raise AnthropicError(f"batch {batch_id} has no results_url (not ended?)")
        text = self._call("GET", url).text
        return [json.loads(line) for line in text.splitlines() if line.strip()]

    def delete(self, batch_id):
        self._call("DELETE", f"{API_ROOT}/messages/batches/{batch_id}")

    def find(self, created_after, n):
        """Every page of `GET /messages/batches` (newest first) until one older than `created_after`."""
        cutoff, out, after = _parse_ts(created_after), [], None
        while True:
            params = {"limit": 100, **({"after_id": after} if after else {})}
            page = self._call("GET", f"{API_ROOT}/messages/batches", params=params).json()
            older = False
            for b in page.get("data") or []:
                if _parse_ts(b["created_at"]) < cutoff:
                    older = True
                    continue
                if sum(int(v or 0) for v in (b.get("request_counts") or {}).values()) == n:
                    out.append(b["id"])
            if older or not page.get("has_more"):
                return out
            after = page.get("last_id")


def _parse_ts(value: str) -> datetime:
    return datetime.fromisoformat(value)


# ---------- run ----------


@dataclass
class BatchTally:
    total: int = 0
    cached: int = 0
    skipped: int = 0
    submitted: int = 0
    jobs_submitted: int = 0
    jobs_resumed: int = 0
    open_jobs: int = 0
    returned: int = 0         # items of failed intents, and canceled/expired requests: pending again
    ok: int = 0
    invalid: int = 0
    error: int = 0
    usd: float = 0.0
    refused: str | None = None
    skipped_items: list[tuple[str, str]] = field(default_factory=list)

    def skip(self, asset_id: str, reason: str) -> None:
        self.skipped += 1
        self.skipped_items.append((asset_id, reason))


class _Run:
    def __init__(self, res, cfg, transport, tally, sleep, poll_interval_s):
        self.res, self.cfg, self.t, self.tally = res, cfg, transport, tally
        self.sleep, self.poll_interval_s = sleep, poll_interval_s
        self.bcand = batch_priced(res.cand)
        self.schema = json.loads(res.cand["schema_text"])
        self.open: list[dict] = []
        self.mine: set[str] = set()

    def record(self, job: dict, row: dict) -> dict | None:
        from shoebox.runners import machine

        result = row.get("result") or {}
        kind = result.get("type")
        if kind in ("canceled", "expired"):
            return None
        base = {"asset_id": row["custom_id"], "model_id": self.res.model_id, "params_hash": self.res.params_hash,
                "latency_ms": None, "machine": machine(), "runtime": self.res.runtime, "ts": now_iso(),
                "batch_job": job["job_id"]}
        if kind != "succeeded":
            err = result.get("error") or f"result type {kind!r}"
            return {**base, "status": "error", "output": None, "raw": None,
                    "error": f"batch item error: {json.dumps(err) if isinstance(err, dict) else err}",
                    "usage": dict(external.NO_USAGE), "usd": 0.0}
        msg = result.get("message") or {}
        usage = usage_of(msg)
        status, output, raw, err, reason = parse_message(msg, self.schema)
        rec = {**base, "status": status, "output": output, "raw": raw, "error": err, "usage": usage,
               "usd": round(usd_for(usage, self.bcand), 8)}
        if reason:
            rec["reason"] = reason
        return rec

    def collect(self, job: dict) -> None:
        """Download an ended batch: spend first (once per batch), then the labels, then `stored`."""
        rows = self.t.download(job["job_id"])
        foreign = [r.get("custom_id") for r in rows if r.get("custom_id") not in set(job["items"])]
        if foreign:  # e.g. an intent adopted by count and time that was another client's batch
            raise LedgerError(
                f"batch {job['job_id']} returned {len(foreign)} results whose custom_id is not in its ledger "
                f"items, so it may not be ours: nothing logged or stored. Check it in the console, then fix "
                f"{_ledger_path(self.cfg, job).name} (job_id, or state `failed`)")
        if not job.get("spend_logged"):
            billed = [r["result"]["message"] for r in rows
                      if (r.get("result") or {}).get("type") == "succeeded"]
            usd = sum(usd_for(usage_of(m), self.bcand) for m in billed)
            privacy.append_spend(self.res.name, len(billed), usd, str(self.res.cand.get("price_date", "")),
                                 run=job["job_id"], provider=self.res.cand.get("budget_provider"))
            job.update(state="succeeded", spend_logged=True, actual_usd=round(usd, 8), n_results=len(billed))
            save_job(self.cfg, job)
            self.tally.usd += usd
        done = store.index(self.cfg, self.res.model_id, self.res.params_hash)
        for row in rows:
            if store.is_final(done.get(row["custom_id"])):
                continue
            rec = self.record(job, row)
            if rec is None:
                self.tally.returned += 1
                continue
            store.append(self.cfg, rec)
            setattr(self.tally, rec["status"], getattr(self.tally, rec["status"]) + 1)
        job["state"] = "stored"
        save_job(self.cfg, job)
        try:
            self.t.delete(job["job_id"])
        except Exception as e:  # noqa: BLE001 - results are stored; a failed cleanup is only reported
            print(f"anthropic-batch: could not delete {job['job_id']}: {type(e).__name__}", file=sys.stderr)

    def advance(self, job: dict) -> None:
        state = self.t.poll(job["job_id"])
        if state != "ended":
            if state != job["state"]:
                job["state"] = state if state in OPEN else job["state"]
                save_job(self.cfg, job)
            return
        self.open.remove(job)
        job["state"] = "succeeded"
        save_job(self.cfg, job)
        self.collect(job)

    def committed_usd(self) -> float:
        """Estimates of open batches (not yet in the spend log)."""
        return sum(float(j.get("est_usd") or 0.0) for j in self.open if not j.get("spend_logged"))

    def wait(self, max_rounds: int | None) -> None:
        rounds = 0
        while self.open:
            if max_rounds is not None and rounds >= max_rounds:
                return
            if rounds:
                self.sleep(self.poll_interval_s)
            for job in list(self.open):
                self.advance(job)
            rounds += 1


def _prepare(res, items, cfg, busy: set[str], tally: BatchTally, others=()) -> list[tuple[dict, bytes]]:
    """Items to send with their thumbnail: not final, not in an open batch, ok derived image, allowed by
    filter_items (provider claude), and passing the derived-image checks on the file and the thumbnail."""
    from shoebox import runners

    rt = runners.Tally()
    pending = runners._pending(res, items, cfg, rt)
    tally.total, tally.cached = rt.total, rt.cached
    for aid, why in rt.skipped_items:
        tally.skip(aid, why)
    pending = [(it, im) for it, im in pending if it["asset_id"] not in busy]
    elig = privacy.filter_items([it for it, _ in pending], cfg, others=[*privacy.known_items(), *others],
                                provider=PROVIDER)
    for s in elig.skipped:
        tally.skip(s.asset_id, s.reason)
    allowed = {it["asset_id"] for it in elig.allowed}
    out = []
    for it, image in pending:
        if it["asset_id"] not in allowed:
            continue
        try:
            path = gemini._check_image_path(res.cand, image, cfg)
            gemini.check_derived_jpeg(path.read_bytes())
            thumb = external.thumbnail_jpeg(path)
            gemini.check_derived_jpeg(thumb)
        except (gemini.GeminiError, OSError) as e:
            tally.skip(it["asset_id"], str(e))
            continue
        out.append((it, thumb))
    return out


def _chunks(work, max_requests: int, max_bytes: int, prompt_bytes: int):
    chunk, size = [], 0
    for it, thumb in work:
        item_bytes = (len(thumb) * 4) // 3 + prompt_bytes + 4096
        if chunk and (len(chunk) >= max_requests or size + item_bytes > max_bytes):
            yield chunk
            chunk, size = [], 0
        chunk.append((it, thumb))
        size += item_bytes
    if chunk:
        yield chunk


def run_batch(res, items: list[dict], cfg: config.Config, transport: Transport, *,
              max_requests: int = MAX_REQUESTS, max_batch_bytes: int = MAX_BATCH_BYTES,
              poll_interval_s: float = POLL_INTERVAL_S, sleep: Callable[[float], None] = time.sleep,
              max_poll_rounds: int | None = None, others: list[dict] = ()) -> BatchTally:
    """Label `items` with an `anthropic-batch` candidate through `transport`; resumable (see the module doc).
    Raises PrivacyError before anything is polled or submitted when the checklist is not confirmed or the price
    has expired; returns with `refused` set when a batch would breach the run cap or the provider budget."""
    cand = res.cand
    if cand.get("budget_provider") != "anthropic":
        raise AnthropicError(f"anthropic-batch candidate {res.name} needs budget_provider = \"anthropic\" "
                             f"(got {cand.get('budget_provider')!r}): its spend must not count against another budget")
    privacy.require_checklist()
    privacy.require_current_price(cand)
    tally = BatchTally()
    run = _Run(res, cfg, transport, tally, sleep, poll_interval_s)
    store.write_params(cfg, res.model_id, res.params_hash, _params_doc(res), res.kind)

    jobs = load_jobs(cfg, res.name)
    for job in jobs:
        if job["state"] in ("submitting", "succeeded", *OPEN) and job.get("params_hash") != res.params_hash:
            raise LedgerError(
                f"batch {job.get('job_id') or job.get('display_name')} ({job['state']}) was made under "
                f"params_hash {job.get('params_hash')}, not the current {res.params_hash}: finish it with the old "
                f"candidate config, or move its ledger record out of {ledger_dir(cfg, res.name)}")
    jobs = _reconcile_intents(cfg, transport, jobs, tally)
    for job in jobs:
        if job["state"] == "succeeded":
            tally.jobs_resumed += 1
            run.collect(job)
        elif job["state"] in OPEN:
            tally.jobs_resumed += 1
            run.open.append(job)
    for job in list(run.open):
        run.advance(job)
    busy = {aid for j in run.open for aid in j["items"]}

    work = _prepare(res, items, cfg, busy, tally, others)
    lim = privacy.limits()
    provider = cand["budget_provider"]
    prompt = cand["prompt_text"]
    prompt_bytes = len(prompt.encode()) + len(json.dumps(run.schema))
    for chunk in _chunks(work, max_requests, max_batch_bytes, prompt_bytes):
        privacy.require_current_price(cand)
        privacy.require_checklist()
        run_usd = tally.usd + sum(float(j["est_usd"]) for j in run.open if j["job_id"] in run.mine)
        already = privacy.spent_total(provider=provider) + run.committed_usd() - run_usd
        room = min(float(cand.get("max_usd_per_run") or 0.0), lim.budget(provider) - already)
        per = estimate(run.bcand, 1).usd
        fit = max(1, min(len(chunk), int((room - run_usd) / per) + 1 if per > 0 else len(chunk)))
        while fit > 1 and run_usd + estimate(run.bcand, fit).usd > room:
            fit -= 1
        short = fit < len(chunk)  # the rest waits for the next run (or the next budget)
        chunk = chunk[:fit]
        est = estimate(run.bcand, fit)
        try:
            privacy.check_budget(
                run.bcand, privacy.Estimate(est.n, est.input_tokens, est.output_tokens, run_usd + est.usd),
                already_spent=already, lim=lim)
        except privacy.PrivacyError as e:
            tally.refused = str(e)
            print(f"anthropic-batch [{res.name}]: batch of {len(chunk)} not submitted: {e}", file=sys.stderr)
            break
        requests = [(it["asset_id"], build_params(cand, prompt, run.schema, thumb)) for it, thumb in chunk]
        job = {"job_id": None, "display_name": f"{res.name}-{uuid.uuid4().hex}", "candidate": res.name,
               "model_id": res.model_id, "params_hash": res.params_hash,
               "items": [it["asset_id"] for it, _ in chunk], "n_requests": len(chunk), "state": "submitting",
               "submitted_ts": now_iso(), "est_input_tokens": est.input_tokens, "est_usd": round(est.usd, 8),
               "spend_logged": False, "actual_usd": None, "n_results": None}
        save_job(cfg, job)  # the intent, before anything reaches the provider
        batch_id = transport.submit(requests)
        _record_job_id(cfg, job, batch_id)
        run.mine.add(batch_id)
        run.open.append(job)
        tally.jobs_submitted += 1
        tally.submitted += len(chunk)
        if short:
            tally.refused = (f"batch cut to {fit} images to stay within max_usd_per_run "
                             f"${float(cand['max_usd_per_run']):.2f} and the remaining {provider} budget; "
                             "rerun to continue")
            print(f"anthropic-batch [{res.name}]: {tally.refused}", file=sys.stderr)
            break

    run.wait(max_poll_rounds)
    tally.open_jobs = len(run.open)
    return tally


def _reconcile_intents(cfg, transport: Transport, jobs: list[dict], tally: BatchTally) -> list[dict]:
    """Settle every `submitting` intent by request count and submit time (see the module doc)."""
    recorded = {j.get("display_name") for j in jobs if j.get("job_id")}
    known = {j["job_id"] for j in jobs if j.get("job_id")}
    out = []
    for job in jobs:
        if job["state"] != "submitting":
            out.append(job)
            continue
        if job["display_name"] in recorded:  # crashed after the batch record was saved, before the unlink
            _ledger_path(cfg, job).unlink(missing_ok=True)
            continue
        since = (_parse_ts(job["submitted_ts"]) - timedelta(seconds=CLOCK_SLACK_S)).astimezone(UTC)
        since_iso = since.isoformat(timespec="seconds").replace("+00:00", "Z")
        try:
            found = [b for b in transport.find(since_iso, int(job["n_requests"])) if b not in known]
        except Exception as e:  # any lookup failure: we cannot tell, so we must not resubmit
            raise LedgerError(
                f"batch intent {job['display_name']} is `submitting` and the provider lookup failed "
                f"({type(e).__name__}); not submitting anything until it can be checked (rerun)") from e
        if len(found) > 1:
            raise LedgerError(
                f"batch intent {job['display_name']} ({job['n_requests']} requests, submitted {job['submitted_ts']}) "
                f"matches {len(found)} batches at the provider ({', '.join(found)}), so it cannot be told which "
                f"is ours. Check them in the console, then rename {_ledger_path(cfg, job).name} to "
                "<batch id>.json with that job_id and state `submitted`, or set its state to `failed`.")
        if found:
            _record_job_id(cfg, job, found[0])
            known.add(found[0])
        else:
            job["state"] = "failed"
            save_job(cfg, job)
            tally.returned += len(job["items"])
        out.append(job)
    return out


def _params_doc(res) -> dict:
    from shoebox.runners import _params_doc as doc

    return doc(res)
