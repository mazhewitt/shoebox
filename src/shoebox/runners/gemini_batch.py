"""Gemini Batch API runner (runtime `gemini-batch`; design D9, .scratch/bulk-captioning/01).

The same request, parsing, normalisation and pricing as the synchronous runner (`runners.gemini`: imported,
not copied), sent as batch jobs. Entry point: `run_batch(res, items, cfg, transport)`; the framework's
per-item loop (`run_items`) does not apply, so `run_one` stops it.

Guards, all before anything is submitted (the sync runner's, plus the batch's own):
- the privacy checklist and the candidate's current price (`price_valid_until`), checked again per job;
- `privacy.filter_items(provider="gemini")`: never-send, consent, held_back, copy restrictions and any later
  reason (e.g. quarantine); a skipped item is never put in a request;
- the derived-image checks (`gemini._check_image_path`, `gemini.check_derived_jpeg`);
- per job: the batch-price estimate (`price_batch_discount`) plus this run's spend must stay within
  `max_usd_per_run`, and plus the whole spend log and open jobs' estimates within the project budget. A job
  that would not fit is not submitted (`BatchTally.refused` says why).

Jobs are chunked so the enqueued input tokens of all open jobs stay within `queue_tokens` (Tier 1 batch
queue for 3.8 Flash: 3M tokens, research/mvp-models.md) and each job's inline requests within
`max_job_bytes`.

Job ledger: `results/batch-jobs/<candidate>/<job_id>.json` (`/` in the id becomes `_`), written (fsynced,
atomically) on every state change:
  {job_id, display_name, candidate, model_id, params_hash, items: [asset_id], state, submitted_ts,
   updated_ts, est_input_tokens, est_usd, spend_logged, actual_usd, n_results}
Before `transport.submit`, an intent record (`job_id: null`, a unique `display_name`, state `submitting`) is
written as `intent_<display_name>.json`; after submit it becomes the `<job_id>.json` record and the intent file
is removed. A crash or client timeout after the server accepted the job therefore leaves the intent behind.
States: `submitting` (intent; may or may not exist at the provider), `submitted`/`pending`/`running` (open),
`succeeded` (downloaded, labels not all stored yet), `stored` (done), `expired`/`failed`/`cancelled` (its
items are pending again).
A restarted run first reconciles every `submitting` intent through `transport.find(display_name)`: a job
found is adopted (open, never resubmitted); a lookup that succeeds and finds none marks the intent `failed`
(its items are pending again); a lookup that fails raises `LedgerError` before anything is submitted.
It then polls every open job and finishes every `succeeded` one; an item in such a job is never resubmitted.
Items of a stored job are cached by the store (final records) like any run.
A `submitting`, open or `succeeded` job recorded under another params_hash than the candidate's current one
is refused (`LedgerError`, naming the job and both hashes): its results belong to the old store key, and
collecting them under the new one would label them with params they were not made with.

On download: the job's actual cost (from each response's usageMetadata at the batch price) and result count
are appended to `evals/spend.jsonl` before any label is stored, and only once per job (`spend_logged`).
Each result is parsed exactly as sync (`gemini.parse_response`); an invalid result gets `reason: refused`
(blockReason, or finishReason SAFETY/PROHIBITED_CONTENT/...) or `reason: truncated` (MAX_TOKENS).
"""

import json
import os
import sys
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

import httpx

from shoebox import config, privacy, store
from shoebox.runners import gemini

BATCH_RUNNER_VERSION = "gemini-batch-runner-1"  # bump when job building, result handling or reasons change
QUEUE_TOKENS = 3_000_000  # Tier 1 enqueued-token limit for 3.8 Flash batch (research/mvp-models.md)
MAX_JOB_BYTES = 19 * 1024 * 1024  # inline batch requests: under the 20 MB request limit
POLL_INTERVAL_S = 60.0

OPEN = ("submitted", "pending", "running")
RETURNED = ("expired", "failed", "cancelled")
REFUSAL = {"SAFETY", "PROHIBITED_CONTENT", "RECITATION", "BLOCKLIST", "SPII", "IMAGE_SAFETY"}


class BatchOnly(gemini.GeminiError, gemini.StopRun):
    """`gemini-batch` candidates run through `run_batch`, not the per-item loop."""


class LedgerError(gemini.GeminiError):
    """The job ledger cannot be reconciled safely; nothing is submitted (fail closed)."""


# ---------- runner interface (framework) ----------


def runtime_version(cand: dict) -> str:
    return f"gemini-api v1beta batchGenerateContent ({BATCH_RUNNER_VERSION}; {gemini.RUNNER_VERSION})"


def run_one(cand, item, image_path, cfg) -> dict:
    raise BatchOnly("gemini-batch candidates are run with gemini_batch.run_batch, not item by item")


# ---------- prices and reasons ----------


def batch_priced(cand: dict) -> dict:
    """The candidate with its prices at the batch rate, so `privacy.estimate` and `gemini.usd_for` price
    batch work without copies of either."""
    disc = cand.get("price_batch_discount")
    if disc is None or not 0 < float(disc) <= 1:
        raise privacy.PrivacyError(f"candidate needs price_batch_discount in (0, 1], got {disc!r}")
    pin, pout = privacy.price(cand)
    return {**cand, "price_input_usd_per_mtok": pin * 1e6 * float(disc),
            "price_output_usd_per_mtok": pout * 1e6 * float(disc)}


def failure_reason(data: dict) -> str | None:
    """`refused` for a safety block, `truncated` for an output cut at max_output_tokens, else None."""
    block = (data.get("promptFeedback") or {}).get("blockReason")
    finish = ((data.get("candidates") or [{}])[0] or {}).get("finishReason")
    if block or finish in REFUSAL:
        return "refused"
    if finish == "MAX_TOKENS":
        return "truncated"
    return None


# ---------- transport ----------


class Transport(Protocol):
    def submit(self, model: str, requests: list[tuple[str, dict]], display_name: str) -> str:
        """Submit `(key, generateContent body)` pairs as one job; return the job id."""

    def poll(self, job_id: str) -> str:
        """One of pending, running, succeeded, failed, expired, cancelled."""

    def download(self, job_id: str) -> list[dict]:
        """`[{key, response}]` or `[{key, error}]` per request of a succeeded job."""

    def delete(self, job_id: str) -> None:
        """Remove the job and its results from the provider (research/mvp-models.md: delete after download)."""

    def find(self, display_name: str) -> str | None:
        """The id of the job submitted with `display_name`, or None if the provider has none. Raises if the
        lookup itself fails (never None for "could not tell")."""


class HttpTransport:
    """The Gemini Batch API over v1beta REST (httpx, as the sync runner; the google-genai SDK is not a
    dependency). Inline requests: `models/<model>:batchGenerateContent`, then `GET batches/<id>`.
    Field names follow https://ai.google.dev/gemini-api/docs/batch-api; not yet exercised live (task 8.6)."""

    def __init__(self, key: str | None = None, timeout_s: float = 300.0):
        self._key, self._timeout = key, timeout_s

    def _client(self) -> httpx.Client:
        if "PYTEST_CURRENT_TEST" in os.environ:
            raise gemini.GeminiError("live Gemini batch call refused under pytest: use a fake transport")
        return httpx.Client(timeout=self._timeout, follow_redirects=False,
                            headers={"x-goog-api-key": self._key or gemini.api_key()})

    def _call(self, method: str, url: str, **kw) -> dict:
        with self._client() as client:
            resp = client.request(method, url, **kw)
        if resp.status_code != 200:
            raise gemini.GeminiError(gemini._scrub(gemini._error_message(resp), self._key))
        return resp.json() if resp.content else {}

    def submit(self, model, requests, display_name):
        body = {"batch": {"displayName": display_name, "inputConfig": {"requests": {"requests": [
            {"request": req, "metadata": {"key": key}} for key, req in requests]}}}}
        return self._call("POST", f"{gemini.API_ROOT}/models/{model}:batchGenerateContent", json=body)["name"]

    def _get(self, job_id: str) -> dict:
        return self._call("GET", f"{gemini.API_ROOT}/{job_id}")

    def poll(self, job_id):
        op = self._get(job_id)
        state = str((op.get("metadata") or {}).get("state") or op.get("state") or "")
        for name in ("SUCCEEDED", "FAILED", "EXPIRED", "CANCELLED", "RUNNING", "PENDING"):
            if state.endswith(name):
                return name.lower()
        return "running" if not op.get("done") else "failed"

    def download(self, job_id):
        op = self._get(job_id)
        out = op.get("response") or (op.get("metadata") or {}).get("output") or {}
        inlined = out.get("inlinedResponses") or {}
        rows = inlined.get("inlinedResponses", inlined) if isinstance(inlined, dict) else inlined
        return [{"key": (r.get("metadata") or {}).get("key"),
                 **({"response": r["response"]} if "response" in r else {"error": r.get("error")})}
                for r in rows or []]

    def delete(self, job_id):
        self._call("DELETE", f"{gemini.API_ROOT}/{job_id}")

    def find(self, display_name):
        """List batches (`GET batches`, every page) and match the displayName. Not yet exercised live."""
        token = None
        while True:
            params = {"pageSize": 100, **({"pageToken": token} if token else {})}
            page = self._call("GET", f"{gemini.API_ROOT}/batches", params=params)
            for op in page.get("operations") or page.get("batches") or []:
                meta = op.get("metadata") or {}
                if display_name in (op.get("displayName"), meta.get("displayName")):
                    return op.get("name") or meta.get("name")
            token = page.get("nextPageToken")
            if not token:
                return None


# ---------- ledger ----------


def ledger_dir(cfg: config.Config, candidate: str) -> Path:
    return cfg.results_dir / "batch-jobs" / candidate


def _ledger_path(cfg: config.Config, job: dict) -> Path:
    name = job["job_id"] if job.get("job_id") else f"intent_{job['display_name']}"
    return ledger_dir(cfg, job["candidate"]) / f"{name.replace('/', '_')}.json"


def _record_job_id(cfg: config.Config, job: dict, job_id: str) -> None:
    """The intent becomes the `<job_id>.json` record (saved first), then the intent file goes."""
    intent = _ledger_path(cfg, job)
    job.update(job_id=job_id, state="submitted")
    save_job(cfg, job)
    intent.unlink(missing_ok=True)


def save_job(cfg: config.Config, job: dict) -> None:
    job["updated_ts"] = gemini_now()
    path = _ledger_path(cfg, job)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    with tmp.open("w") as f:
        f.write(json.dumps(job, indent=1, sort_keys=True) + "\n")
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def load_jobs(cfg: config.Config, candidate: str) -> list[dict]:
    d = ledger_dir(cfg, candidate)
    return [json.loads(p.read_text()) for p in sorted(d.glob("*.json"))] if d.exists() else []


def gemini_now() -> str:
    from shoebox.runners import _now

    return _now()


# ---------- run ----------


@dataclass
class BatchTally:
    total: int = 0
    cached: int = 0
    skipped: int = 0
    submitted: int = 0        # items put in new jobs this run
    jobs_submitted: int = 0
    jobs_resumed: int = 0     # open or downloaded jobs from an earlier run, finished by this one
    open_jobs: int = 0        # still open when the run returned (the next run polls them)
    returned: int = 0         # items of expired/failed/cancelled jobs, pending again
    ok: int = 0
    invalid: int = 0
    error: int = 0
    usd: float = 0.0          # actual spend logged this run
    refused: str | None = None  # why a job was not submitted (cap or budget), if one wasn't
    skipped_items: list[tuple[str, str]] = field(default_factory=list)

    def skip(self, asset_id: str, reason: str) -> None:
        self.skipped += 1
        self.skipped_items.append((asset_id, reason))


class _Run:
    def __init__(self, res, cfg, transport, tally, sleep, poll_interval_s):
        self.res, self.cfg, self.t, self.tally = res, cfg, transport, tally
        self.sleep, self.poll_interval_s = sleep, poll_interval_s
        self.bcand = batch_priced(res.cand)
        self.schema = gemini.schema_doc(res.cand)
        self.open: list[dict] = []
        self.mine: set[str] = set()  # job ids submitted by this run (they count toward its cap)

    def record(self, job: dict, row: dict) -> dict:
        aid = row["key"]
        base = {"asset_id": aid, "model_id": self.res.model_id, "params_hash": self.res.params_hash,
                "latency_ms": None, "machine": _machine(), "runtime": self.res.runtime, "ts": gemini_now(),
                "batch_job": job["job_id"]}
        data = row.get("response")
        if not isinstance(data, dict):
            err = row.get("error") or "no response"
            empty = {"input_tokens": None, "output_tokens": None, "thinking_tokens": None}
            return {**base, "status": "error", "output": None, "raw": None,
                    "error": f"batch item error: {json.dumps(err) if isinstance(err, dict) else err}",
                    "usage": empty, "usd": 0.0}
        usage = gemini.usage_of(data)
        status, output, raw, err = gemini.parse_response(data, self.schema)
        rec = {**base, "status": status, "output": output, "raw": raw, "error": err, "usage": usage,
               "usd": round(gemini.usd_for(usage, self.bcand), 8)}
        reason = failure_reason(data) if status == "invalid" else None
        if reason:
            rec["reason"] = reason
        return rec

    def collect(self, job: dict) -> None:
        """Download a succeeded job: spend first (once per job), then the labels, then `stored`."""
        rows = [r for r in self.t.download(job["job_id"]) if r.get("key") in set(job["items"])]
        if not job.get("spend_logged"):
            billed = [r["response"] for r in rows if isinstance(r.get("response"), dict)]
            usd = sum(gemini.usd_for(gemini.usage_of(d), self.bcand) for d in billed)
            privacy.append_spend(self.res.name, len(billed), usd, str(self.res.cand.get("price_date", "")),
                                 run=job["job_id"])
            job.update(state="succeeded", spend_logged=True, actual_usd=round(usd, 8), n_results=len(billed))
            save_job(self.cfg, job)
            self.tally.usd += usd
        done = store.index(self.cfg, self.res.model_id, self.res.params_hash)
        for row in rows:
            if store.is_final(done.get(row["key"])):
                continue
            rec = self.record(job, row)
            store.append(self.cfg, rec)
            setattr(self.tally, rec["status"], getattr(self.tally, rec["status"]) + 1)
        job["state"] = "stored"
        save_job(self.cfg, job)
        try:
            self.t.delete(job["job_id"])
        except Exception as e:  # noqa: BLE001 - results are stored; a failed cleanup is only reported
            print(f"gemini-batch: could not delete {job['job_id']}: {type(e).__name__}", file=sys.stderr)

    def advance(self, job: dict) -> None:
        """Poll one open job once and act on a terminal state."""
        state = self.t.poll(job["job_id"])
        if state in ("pending", "running"):
            if state != job["state"]:
                job["state"] = state
                save_job(self.cfg, job)
            return
        self.open.remove(job)
        if state == "succeeded":
            job["state"] = "succeeded"
            save_job(self.cfg, job)
            self.collect(job)
        else:
            job["state"] = state if state in RETURNED else "failed"
            save_job(self.cfg, job)
            self.tally.returned += len(job["items"])

    def wait(self, until: Callable[[], bool], max_rounds: int | None) -> bool:
        """Poll open jobs (sleeping between rounds) until `until()`; False if max_rounds ran out first."""
        rounds = 0
        while not until():
            if max_rounds is not None and rounds >= max_rounds:
                return False
            if rounds:
                self.sleep(self.poll_interval_s)
            for job in list(self.open):
                self.advance(job)
            rounds += 1
        return True

    def enqueued(self) -> int:
        return sum(int(j.get("est_input_tokens") or 0) for j in self.open)

    def committed_usd(self) -> float:
        """Estimates of open jobs (not yet in the spend log)."""
        return sum(float(j.get("est_usd") or 0.0) for j in self.open if not j.get("spend_logged"))


def _machine() -> str:
    from shoebox.runners import machine

    return machine()


def _prepare(res, items: list[dict], cfg, busy: set[str], tally: BatchTally,
             others: list[dict] = ()) -> list[tuple[dict, Path, int]]:
    """Items to send: not final in the store, not in an open job, with an ok derived image, allowed by
    filter_items, and passing the derived-image checks. `(item, image, size)` each."""
    from shoebox import runners

    rt = runners.Tally()
    pending = runners._pending(res, items, cfg, rt)  # store cache + derive index checks, as sync runs
    tally.total, tally.cached = rt.total, rt.cached
    for aid, why in rt.skipped_items:
        tally.skip(aid, why)
    pending = [(it, im) for it, im in pending if it["asset_id"] not in busy]
    elig = privacy.filter_items([it for it, _ in pending], cfg, others=[*privacy.known_items(), *others],
                                provider="gemini")
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
        except (gemini.GeminiError, OSError) as e:
            tally.skip(it["asset_id"], str(e))
            continue
        out.append((it, path, path.stat().st_size))
    return out


def _chunks(work, per_item_tokens: int, prompt_bytes: int, queue_tokens: int, max_job_bytes: int):
    chunk, tokens, size = [], 0, 0
    for it, path, nbytes in work:
        item_bytes = (nbytes * 4) // 3 + prompt_bytes + 4096  # base64 image + prompt + schema/config
        if chunk and (tokens + per_item_tokens > queue_tokens or size + item_bytes > max_job_bytes):
            yield chunk
            chunk, tokens, size = [], 0, 0
        chunk.append((it, path))
        tokens += per_item_tokens
        size += item_bytes
    if chunk:
        yield chunk


def run_batch(res, items: list[dict], cfg: config.Config, transport: Transport, *,
              queue_tokens: int = QUEUE_TOKENS, max_job_bytes: int = MAX_JOB_BYTES,
              poll_interval_s: float = POLL_INTERVAL_S, sleep: Callable[[float], None] = time.sleep,
              max_poll_rounds: int | None = None, others: list[dict] = ()) -> BatchTally:
    """Label `items` with a `gemini-batch` candidate through `transport`; resumable (see the module doc).
    `others` are further siblings for `privacy.filter_items` (e.g. a capture's members, same `group_id`).

    Raises PrivacyError before anything is polled or submitted when the checklist is not confirmed, the price
    has expired or the candidate has no batch discount. Returns with `refused` set when a job would breach
    the run cap or the project budget, and with `open_jobs` > 0 when `max_poll_rounds` ran out (rerun to
    resume). An exception from the transport propagates; the ledger already holds every submitted job.
    """
    cand = res.cand
    gemini._setting(cand, "thinking_level", gemini.THINKING_LEVEL)
    gemini._setting(cand, "media_resolution", gemini.MEDIA_RESOLUTION)
    privacy.require_checklist()
    privacy.require_current_price(cand)
    tally = BatchTally()
    run = _Run(res, cfg, transport, tally, sleep, poll_interval_s)
    store.write_params(cfg, res.model_id, res.params_hash, _params_doc(res), res.kind)

    # 1. resume: reconcile intents, finish downloaded jobs, poll open ones (their items are never resubmitted)
    jobs = load_jobs(cfg, res.name)
    for job in jobs:
        if job["state"] in ("submitting", "succeeded", *OPEN) and job.get("params_hash") != res.params_hash:
            raise LedgerError(
                f"batch job {job.get('job_id') or job.get('display_name')} ({job['state']}) was made under "
                f"params_hash {job.get('params_hash')}, not the current {res.params_hash}: its results are not "
                f"stored under the new key. Finish it with the old candidate config, or move its ledger record "
                f"out of {ledger_dir(cfg, res.name)}")
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

    # 2. what is left to send
    work = _prepare(res, items, cfg, busy, tally, others)
    lim = privacy.limits()
    per_item = privacy.estimate(run.bcand, 1).input_tokens
    prompt_bytes = len(gemini.prompt_text(cand).encode()) + len(json.dumps(run.schema))
    for chunk in _chunks(work, per_item, prompt_bytes, queue_tokens, max_job_bytes):
        est = privacy.estimate(run.bcand, len(chunk))
        need = est.input_tokens
        if run.open and run.enqueued() + need > queue_tokens and not run.wait(
                lambda need=need: not run.open or run.enqueued() + need <= queue_tokens, max_poll_rounds):
            break  # the queue is still full: the rest waits for the next run
        privacy.require_current_price(cand)
        privacy.require_checklist()
        # run cap: this run's logged spend + its open jobs' estimates + this job <= max_usd_per_run;
        # budget: the whole spend log + every open job's estimate + this job <= the project budget.
        run_usd = tally.usd + sum(float(j["est_usd"]) for j in run.open if j["job_id"] in run.mine)
        try:
            privacy.check_budget(
                run.bcand, privacy.Estimate(est.n, est.input_tokens, est.output_tokens, run_usd + est.usd),
                already_spent=privacy.spent_total() + run.committed_usd() - run_usd, lim=lim)
        except privacy.PrivacyError as e:
            tally.refused = str(e)
            print(f"gemini-batch [{res.name}]: job of {len(chunk)} not submitted: {e}", file=sys.stderr)
            break
        requests = [(it["asset_id"], gemini.build_request(cand, gemini.prompt_text(cand), run.schema,
                                                          path.read_bytes())) for it, path in chunk]
        job = {"job_id": None, "display_name": f"{res.name}-{uuid.uuid4().hex}", "candidate": res.name,
               "model_id": res.model_id, "params_hash": res.params_hash,
               "items": [it["asset_id"] for it, _ in chunk], "state": "submitting", "submitted_ts": gemini_now(),
               "est_input_tokens": est.input_tokens, "est_usd": round(est.usd, 8), "spend_logged": False,
               "actual_usd": None, "n_results": None}
        save_job(cfg, job)  # the intent, before anything reaches the provider
        job_id = transport.submit(cand["model"], requests, job["display_name"])
        _record_job_id(cfg, job, job_id)
        run.mine.add(job_id)
        run.open.append(job)
        tally.jobs_submitted += 1
        tally.submitted += len(chunk)

    # 3. wait for everything open
    run.wait(lambda: not run.open, max_poll_rounds)
    tally.open_jobs = len(run.open)
    return tally


def _reconcile_intents(cfg, transport: Transport, jobs: list[dict], tally: BatchTally) -> list[dict]:
    """Settle every `submitting` intent before anything is submitted (see the module doc). Returns the ledger
    with each intent replaced by what it became."""
    recorded = {j.get("display_name") for j in jobs if j.get("job_id")}
    out = []
    for job in jobs:
        if job["state"] != "submitting":
            out.append(job)
            continue
        if job["display_name"] in recorded:  # crashed after the job record was saved, before the unlink
            _ledger_path(cfg, job).unlink(missing_ok=True)
            continue
        try:
            found = transport.find(job["display_name"])
        except Exception as e:  # any lookup failure: we cannot tell, so we must not resubmit
            raise LedgerError(
                f"batch job intent {job['display_name']} is `submitting` and the provider lookup failed "
                f"({type(e).__name__}); not submitting anything until it can be checked (rerun)") from e
        if found:
            _record_job_id(cfg, job, found)
        else:
            job["state"] = "failed"
            save_job(cfg, job)
            tally.returned += len(job["items"])
        out.append(job)
    return out


def _params_doc(res) -> dict:
    from shoebox.runners import _params_doc as doc

    return doc(res)
