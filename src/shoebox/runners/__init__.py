"""Runner framework: candidates, params hashing, and the cache-aware labelling loop (design D2/D7).

A runtime is a module `shoebox.runners.<runtime>` with `runtime_version(cand)`, `run_one(cand, item,
image_path, cfg)`, and optionally `preflight(cand, items, cfg)` (raise to refuse; returns the items
allowed) and `fingerprint(cand)` (extra hashed params, e.g. the model digest). See docs/pilot.md.

The `cand` passed to a runner is the TOML table plus `name`, `model_id`, `params_hash` and, when the
candidate names them, `prompt_text` and `schema_text` (the exact texts that were hashed).
"""

import importlib
import json
import re
import socket
import subprocess
import time
import tomllib
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from functools import cache
from pathlib import Path
from types import ModuleType

from shoebox import store
from shoebox.config import REPO_ROOT, Config

CONFIG_DIR = REPO_ROOT / "config"
# Candidate keys that are file references (hashed by their text instead), notes, or run-budget
# settings that never change a model's output (spend caps, cost estimates, prices).
# `retry` is the truncation-retry override table: it never changes the first attempt's output, and the retry
# gets its own params_hash (see `resolve`).
# `label_sources` is the ordered label-source chain a bulk run reuses labels from (design D8): where to look,
# not how to label.
_NOT_HASHED = {"name", "description", "notes", "prompt", "schema", "profile", "retry", "label_sources"}
_NOT_HASHED_PREFIXES = ("est_", "price_", "max_usd")
# Store kind (`results/<kind>/...`) per runtime: label-v1 records go under `labels`, which the sheet and the
# Vally executor read; embedding vectors are not label-v1 records and go under `embeddings`.
STORE_KIND = {"embed": "embeddings", "screen": "screen"}


class StopRun(Exception):
    """Raised by a runner when the whole run must stop now (e.g. a spend cap). No record is written for the
    item; `run_items` re-raises it, so `shoebox label run` exits with the message instead of filling the
    store with `error` records."""


class Skipped(Exception):
    """An item that cannot be labelled now (no derived image, or refused by preflight). Not a model failure."""


def normalise_label(parsed):
    """The one documented normalisation of a label (spec label-runners): string tags are lowercased, so a
    proper noun ("Eiffel Tower") is kept, not rejected. Anything else is left for the schema to judge."""
    if isinstance(parsed, dict) and isinstance(parsed.get("tags"), list):
        return {**parsed, "tags": [t.lower() if isinstance(t, str) else t for t in parsed["tags"]]}
    return parsed


def get(runtime: str) -> ModuleType:
    return importlib.import_module(f"shoebox.runners.{runtime.replace('-', '_')}")  # gemini-batch -> gemini_batch


def load_candidates(config_dir: Path = CONFIG_DIR) -> dict[str, dict]:
    """`candidates.toml` merged with `candidates.d/*.toml` (sorted). A repeated name is an error."""
    files = [config_dir / "candidates.toml", *sorted((config_dir / "candidates.d").glob("*.toml"))]
    out: dict[str, dict] = {}
    for f in files:
        if not f.exists():
            continue
        for name, table in tomllib.loads(f.read_text()).get("candidates", {}).items():
            if name in out:
                raise ValueError(f"candidate {name!r} defined twice (again in {f})")
            out[name] = {**table, "name": name}
    return out


def profile_settings(profile: str, config_dir: Path = CONFIG_DIR) -> dict | None:
    """The profile's byte-affecting settings (`shoebox.derive.byte_settings`), or None if it is absent."""
    from shoebox import derive

    f = config_dir / "profiles.toml"
    if not f.exists():
        return None
    try:
        return derive.byte_settings(derive.load_profile(profile, f))
    except KeyError:
        return None


def profile_params(profile: str, config_dir: Path = CONFIG_DIR) -> dict:
    """What the derived image's bytes depend on: profile name, settings (and their derive settings_hash), and
    the derive tool versions, since a libvips/LibRaw upgrade re-derives the image under the same profile."""
    from shoebox import derive

    settings = profile_settings(profile, config_dir)
    return {"name": profile, "settings": settings,
            "settings_hash": None if settings is None else derive.settings_hash(settings),
            "tools": derive.tool_versions()}


def model_id(cand: dict) -> str:
    if cand.get("model_id"):
        return cand["model_id"]
    if cand["runtime"] == "rules":
        return cand.get("model", cand["name"])
    return f"{cand['runtime']}/{cand['model']}"


@dataclass(frozen=True)
class Resolved:
    name: str
    cand: dict
    model_id: str
    params: dict
    params_hash: str
    runner: ModuleType
    retry: "Resolved | None" = None

    @property
    def runtime(self) -> str:
        return self.params["runtime_version"]

    @property
    def kind(self) -> str:
        return STORE_KIND.get(self.cand["runtime"], "labels")

    def store_dir(self, cfg: Config) -> Path:
        return store.store_dir(cfg, self.model_id, self.params_hash, self.kind)


def resolve(name: str, config_dir: Path = CONFIG_DIR) -> Resolved:
    """Load a candidate and compute its params and params_hash (may ask the runtime for its version).

    A candidate with a `retry` table (e.g. `num_predict = 300`, `repeat_penalty = 1.1`) also resolves its
    truncation retry: the candidate with those overrides, named `<name>~retry`, under its own params_hash.
    """
    cands = load_candidates(config_dir)
    if name not in cands:
        raise KeyError(f"unknown candidate {name!r}; known: {', '.join(sorted(cands))}")
    cand = dict(cands[name])
    res = _resolve_cand(name, cand, config_dir)
    if isinstance(cand.get("retry"), dict):
        retry = {k: v for k, v in cand.items() if k != "retry"} | cand["retry"]
        res = replace(res, retry=_resolve_cand(f"{name}~retry", retry, config_dir))
    return res


def _resolve_cand(name: str, cand: dict, config_dir: Path) -> Resolved:
    cand = {**cand, "name": name}
    runner = get(cand["runtime"])
    params = {k: v for k, v in cand.items() if k not in _NOT_HASHED and not k.startswith(_NOT_HASHED_PREFIXES)}
    if cand.get("prompt"):
        cand["prompt_text"] = params["prompt_text"] = (config_dir / "prompts" / cand["prompt"]).read_text()
    if cand.get("schema"):
        cand["schema_text"] = params["schema_text"] = (config_dir / "schemas" / cand["schema"]).read_text()
    if cand.get("profile"):
        params["profile"] = profile_params(cand["profile"], config_dir)
    params["runtime_version"] = runner.runtime_version(cand)
    if hasattr(runner, "fingerprint"):
        params.update(runner.fingerprint(cand))
    mid = model_id(cand)
    ph = store.params_hash(params)
    cand.update(model_id=mid, params_hash=ph)
    return Resolved(name=name, cand=cand, model_id=mid, params=params, params_hash=ph, runner=runner)


def derived_image(cfg: Config, profile: str, asset_id: str) -> Path:
    """The derived JPEG for an asset (`shoebox.derive.derived_path`; imported lazily, it loads libvips)."""
    from shoebox.derive import derived_path

    return derived_path(cfg, profile, asset_id)


@cache
def machine() -> str:
    try:
        chip = subprocess.run(["sysctl", "-n", "machdep.cpu.brand_string"], check=False,
                              capture_output=True, text=True, timeout=5).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        chip = ""
    return f"{socket.gethostname()}, {chip or 'unknown chip'}"


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def run_and_store(res: Resolved, item: dict, image: Path, cfg: Config) -> dict:
    """Call the runner for one item and append its record. Runner exceptions become `error` records."""
    t0 = time.monotonic()
    try:
        out = res.runner.run_one(res.cand, item, image, cfg)
    except StopRun:
        raise
    except Exception as e:  # noqa: BLE001 - a runner bug or I/O failure: retryable, never a model verdict
        out = {"status": "error", "error": f"{type(e).__name__}: {e}",
               "latency_ms": round((time.monotonic() - t0) * 1000)}
    status = out.get("status")
    if status not in ("ok", "invalid", "error"):
        out = {**out, "status": "error", "error": f"runner returned status {status!r}"}
    rec = {
        "asset_id": item["asset_id"], "model_id": res.model_id, "params_hash": res.params_hash,
        "status": out["status"], "output": out.get("output"), "raw": out.get("raw"), "error": out.get("error"),
        "usage": out.get("usage") or {"input_tokens": None, "output_tokens": None, "thinking_tokens": None},
        "latency_ms": out.get("latency_ms"), "usd": out.get("usd", 0.0),
        "machine": machine(), "runtime": res.runtime, "ts": _now(),
    }
    if out.get("reason"):
        rec["reason"] = out["reason"]
    store.append(cfg, rec, res.kind)
    return rec


@dataclass
class Tally:
    ok: int = 0
    invalid: int = 0
    error: int = 0
    cached: int = 0
    skipped: int = 0
    total: int = 0
    retried: int = 0      # truncated first attempts sent to the retry candidate this run
    recovered: int = 0    # of those, retries that came back ok
    skipped_items: list[tuple[str, str]] = field(default_factory=list)
    started: float = field(default_factory=time.monotonic)

    @property
    def ran(self) -> int:
        return self.ok + self.invalid + self.error

    def per_hour(self) -> float:
        elapsed = time.monotonic() - self.started
        return self.ran / elapsed * 3600 if elapsed > 0 and self.ran else 0.0

    def skip(self, asset_id: str, reason: str) -> None:
        self.skipped += 1
        self.skipped_items.append((asset_id, reason))


def _params_doc(res: Resolved) -> dict:
    public = {k: v for k, v in res.cand.items() if k not in ("prompt_text", "schema_text")}
    return {"candidate": res.name, "model_id": res.model_id, "params_hash": res.params_hash,
            "candidate_config": public, "params": res.params}


def _derived_problem(res: Resolved, drec: dict | None, image: Path) -> str | None:
    """Why the derived image can't be labelled under this params_hash, or None. The derive index decides: the
    latest record must be ok, and made with the profile settings and tools that this params_hash covers."""
    if not drec or drec.get("status") != "ok" or not image.exists():
        return "no ok derived image (run `shoebox derive`)"
    want = res.params.get("profile") or {}
    if want.get("settings_hash") is not None and (
            drec.get("settings_hash") != want["settings_hash"] or drec.get("tools") != want.get("tools")):
        return "derived image was made with other profile settings or tools (re-run `shoebox derive`)"
    return None


def _pending(res: Resolved, items: list[dict], cfg: Config, tally: Tally) -> list[tuple[dict, Path]]:
    from shoebox import derive

    done = store.index(cfg, res.model_id, res.params_hash, res.kind)
    profile = res.cand.get("profile", "infer-v1")
    derived = derive.load_index(cfg, profile)
    seen: set[str] = set()
    pending = []
    for it in items:
        aid = it["asset_id"]
        if aid in seen:
            continue
        seen.add(aid)
        if store.is_final(done.get(aid)):
            tally.cached += 1
            continue
        image = derived_image(cfg, profile, aid)
        why = _derived_problem(res, derived.get(aid), image)
        if why:
            tally.skip(aid, why)
            continue
        pending.append((it, image))
    tally.total = len(seen)
    if pending and hasattr(res.runner, "preflight"):
        allowed = {a["asset_id"] for a in res.runner.preflight(res.cand, [it for it, _ in pending], cfg)}
        for it, _ in pending:
            if it["asset_id"] not in allowed:
                tally.skip(it["asset_id"], "refused by preflight")
        pending = [(it, im) for it, im in pending if it["asset_id"] in allowed]
    return pending


def run_items(res: Resolved, items: list[dict], cfg: Config,
              progress: Callable[[Tally, dict, dict], None] | None = None) -> Tally:
    """Label every item not already final in the store. Re-running resumes from the first missing key.

    Then, if the candidate has a retry, every item whose stored record is `invalid` with reason `truncated`
    (from this run or an earlier one) is run once under the retry candidate, cache-aware like any run.
    """
    tally = Tally()
    pending = _pending(res, items, cfg, tally)
    if pending:
        store.write_params(cfg, res.model_id, res.params_hash, _params_doc(res), res.kind)
    tally.started = time.monotonic()
    for it, image in pending:
        rec = run_and_store(res, it, image, cfg)
        setattr(tally, rec["status"], getattr(tally, rec["status"]) + 1)
        if progress:
            progress(tally, it, rec)
    if res.retry:
        done = store.index(cfg, res.model_id, res.params_hash, res.kind)
        truncated = [it for it in items if (done.get(it["asset_id"]) or {}).get("reason") == "truncated"]
        if truncated:
            rt = run_items(res.retry, truncated, cfg, progress)
            tally.retried, tally.recovered = rt.ran, rt.ok
    return tally


def label_source(res: "Resolved | list[Resolved]") -> dict | list[dict]:
    """What a reader needs to find a candidate's labels: `{candidate, model_id, params_hash}`, plus
    `retry_params_hash` when it has a truncation retry. A list of candidates gives the ordered list of their
    sources (design D10: primary first, then fallbacks), which `source_index` resolves to one record per asset."""
    if isinstance(res, list):
        return [label_source(r) for r in res]
    src = {"candidate": res.name, "model_id": res.model_id, "params_hash": res.params_hash}
    if res.retry:
        src["retry_params_hash"] = res.retry.params_hash
    return src


_FINISH = re.compile(r"\b(finishReason|blockReason)=([A-Z_]+)\b")


def refusal_reason(rec: dict) -> str | None:
    """`refused`, `truncated` or None for an `invalid` record. The record's `reason` when it has one (batch and
    Ollama runners set it). Synchronous `gemini` records carry none (changing that runner would change its
    params_hash), so for a `gemini...` model_id it is read from what the record kept: the raw
    `{finishReason, blockReason, text}` of an empty or blocked response, else `finishReason=`/`blockReason=` in
    the error text. A blockReason or a safety finishReason (`gemini_batch.REFUSAL`) is `refused`; MAX_TOKENS
    is `truncated`. Anything else (schema or JSON failures, errors) is None."""
    if rec.get("status") != "invalid":
        return None
    if rec.get("reason"):
        return rec["reason"]
    if not str(rec.get("model_id") or "").startswith("gemini"):
        return None
    from shoebox.runners.gemini_batch import REFUSAL

    found: dict[str, str] = {}
    try:
        raw = json.loads(rec.get("raw") or "")
    except (TypeError, ValueError):
        raw = None
    if isinstance(raw, dict) and {"finishReason", "blockReason"} & set(raw):
        found = {k: raw[k] for k in ("finishReason", "blockReason") if isinstance(raw.get(k), str)}
    else:
        found = {k: v for k, v in _FINISH.findall(str(rec.get("error") or "")) if v != "None"}
    if found.get("blockReason") or found.get("finishReason") in REFUSAL:
        return "refused"
    if found.get("finishReason") == "MAX_TOKENS":
        return "truncated"
    return None


def _falls_through(rec: dict) -> bool:
    """A final record the next source in an ordered list should replace (design D10): the model refused the
    image, or it was truncated and no retry recovered it (the retry failed, or the source has no retry).
    Other failures are not routed; a truncated record still awaiting its retry is not final yet."""
    if rec.get("status") != "invalid" or rec.get("awaiting_retry"):
        return False
    return refusal_reason(rec) in ("refused", "truncated") or "retry_of" in rec


def _usable(rec: dict) -> bool:
    return store.is_final(rec) and not _falls_through(rec)


def source_index(cfg: Config, source: dict | list[dict], kind: str = "labels") -> dict[str, dict]:
    """`{asset_id: record}` as evals should read a label source: its own records, except that a `truncated`
    record is replaced by its retry's final record (marked `retry_of`), so the retry counts if it is valid and
    the item counts as a failure if it is not. A truncated record whose retry has not run yet is returned
    with `awaiting_retry: true`, so a consumer that needs final captions can wait for it.

    `source` may be an ordered list of sources: each asset gets the first source's usable final record (each
    record keeps its own model_id/params_hash; `fell_through` names the sources passed over). A refused or
    unrecovered truncated record falls through to the next source; if no later source has a usable record,
    the first source's record with one is returned, so coverage reports still see why."""
    if isinstance(source, list):
        out: dict[str, dict] = {}
        passed: dict[str, list[str]] = {}
        for src in source:
            for aid, rec in source_index(cfg, src, kind).items():
                have = out.get(aid)
                if have is not None and (_usable(have) or not _usable(rec)):
                    continue
                if have is not None:
                    passed.setdefault(aid, []).append(have.get("_source"))
                out[aid] = {**rec, "_source": src.get("candidate")}
        for aid, rec in out.items():
            del rec["_source"]
            if aid in passed:
                rec["fell_through"] = passed[aid]
        return out
    out = store.index(cfg, source["model_id"], source["params_hash"], kind)
    if source.get("retry_params_hash"):
        retried = store.index(cfg, source["model_id"], source["retry_params_hash"], kind)
        for aid, rec in out.items():
            if rec.get("reason") != "truncated":
                continue
            r = retried.get(aid)
            out[aid] = {**r, "retry_of": source["params_hash"]} if store.is_final(r) else {**rec, "awaiting_retry": True}
    return out


def label_index(cfg: Config, res: Resolved) -> dict[str, dict]:
    """`source_index` for a resolved label candidate."""
    return source_index(cfg, label_source(res), res.kind)


def replay(res: Resolved, old_dir: Path, cfg: Config) -> dict[str, int]:
    """Rebuild this candidate's store from an older store of the same candidate without inference, by
    re-applying the runner's current parsing (`runner.reparse`) to each stored final record.

    Refuses unless the old params equal the new ones apart from the runner's parsing version (`runner`), so a
    replay never stands in for a different model, prompt, schema, image or option. Records already final in
    the new store are left alone; `error` records are not replayed (re-run them). Each replayed record keeps
    the original usage, latency, machine and ts, and adds `replay_of` (the old params_hash) and `replayed_ts`.
    """
    if not hasattr(res.runner, "reparse"):
        raise ValueError(f"runtime {res.cand['runtime']} has no reparse; replay is not supported")
    old = json.loads((old_dir / "params.json").read_text())
    old_hash = old["params_hash"]
    if old_hash == res.params_hash:
        raise ValueError("the old store has the current params_hash: nothing to replay")
    diff = sorted(k for k in set(old["params"]) | set(res.params) if k != "runner"
                  and old["params"].get(k) != res.params.get(k))
    if diff or old["model_id"] != res.model_id:
        raise ValueError(f"params differ beyond the runner version ({', '.join(diff) or 'model_id'}); "
                         "re-run instead of replaying")
    done = store.index(cfg, res.model_id, res.params_hash, res.kind)
    counts = {"ok": 0, "invalid": 0, "already": 0, "changed": 0}
    store.write_params(cfg, res.model_id, res.params_hash, _params_doc(res), res.kind)
    for aid, rec in store.index_records(store.iter_dir(old_dir)).items():
        if not store.is_final(rec):
            continue
        if store.is_final(done.get(aid)):
            counts["already"] += 1
            continue
        out = res.runner.reparse(res.cand, rec)
        new = {**{k: v for k, v in rec.items() if k != "reason"}, **out,
               "params_hash": res.params_hash, "replay_of": old_hash, "replayed_ts": _now()}
        store.append(cfg, new, res.kind)
        counts[out["status"]] += 1
        counts["changed"] += out["status"] != rec["status"] or out.get("output") != rec.get("output")
    return counts


def label_one(res: Resolved, asset_id: str, items: list[dict], cfg: Config) -> dict:
    """The stored final record for one asset, running it first if needed.

    Raises LookupError if the asset is not in `items`, Skipped if it cannot be run now.
    """
    rec = store.lookup(cfg, res.model_id, res.params_hash, asset_id, res.kind)
    if store.is_final(rec):
        return rec
    item = next((it for it in items if it["asset_id"] == asset_id), None)
    if item is None:
        raise LookupError(f"asset {asset_id} is not in the items list")
    tally = Tally()
    pending = _pending(res, [item], cfg, tally)
    if not pending:
        raise Skipped(f"asset {asset_id}: {tally.skipped_items[0][1]}")
    store.write_params(cfg, res.model_id, res.params_hash, _params_doc(res), res.kind)
    return run_and_store(res, *pending[0], cfg)
