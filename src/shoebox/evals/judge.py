"""Pooled, graded relevance judging with a Gemini grid judge (design D10; tasks 6.1-6.2).

Pool: per query, the union of every route's top-k (the rankings `shoebox eval search-run` wrote, found via
`results/search/<set>/index.json`) plus the query's `relevant` items -> `evals/sets/<set>/pool-v1.jsonl`.

Judge: each query's pool is shuffled with a seed derived from (shuffle seed, query_id), so position bias
isn't systematic, and cut into grids of at most `GRID_SIZE`. One generateContent call per grid: the prompt
and query as text, then per photo a "Photo N" text part and the derived infer-v1 JPEG at LOW resolution. The
reply must grade every photo exactly once with 0/1/2, else the whole grid is `invalid` (raw kept).

Guards are the Gemini runner's: privacy checklist, `privacy.filter_items(provider="gemini")` per photo
(refused photos are skipped and reported), the derived-image path and metadata checks, current prices, the
project budget, a per-run USD cap, and one fsynced `evals/spend.jsonl` line per billed call.

Store: kind "judgments", one store dir per (model_id, params_hash). A record's `asset_id` is the photo's
asset_id and it also carries `query_id`; the store is indexed by `(query_id, asset_id)` through
`store.index_records(key=...)`, so a key is (query_id, asset_id, model_id, params_hash) as D10 requires.
"""

import base64
import hashlib
import json
import random
import sys
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from shoebox import derive, privacy, store
from shoebox.config import Config
from shoebox.evals import search
from shoebox.runners import gemini

KIND = "judgments"
PROMPT_FILE = "judge-v2.md"
POOL_FILE = "pool-v1.jsonl"
JUDGMENTS_FILE = "judgments-v1.jsonl"
BASE_CANDIDATE = "gemini-low"  # model, resolution, thinking level and prices come from this candidate
JUDGE_NAME = "gemini-judge-v2"
JUDGE_VERSION = "judge-runner-2"  # 2: a per-photo date/folder note  # bump when request building, gridding or parsing changes
GRID_SIZE = 10
SHUFFLE_SEED = 20261011
TEMPERATURE = None  # Gemini 3 default (1.0), as the label runner: Google advises against lowering it (looping)
DEFAULT_MAX_USD = 25.0
GRADES = (0, 1, 2)
# Pre-flight estimate only (measure on the first live run): text per "Photo N" part, output per grade,
# thinking per call (the label runs measured 0 at "low"; kept above 0 to be safe).
EST_TEXT_TOKENS_PER_PHOTO = 5
EST_OUTPUT_TOKENS_PER_PHOTO = 12
EST_OUTPUT_TOKENS_PER_GRID = 20
EST_THINKING_TOKENS_PER_GRID = 300


# ---------- pool ----------


def load_rankings(cfg: Config, set_name: str, queries_file: str) -> dict[str, dict]:
    """`{route: {"params_hash", "rankings": {query_id: [asset_id, ...]}}}` for every route in the set's
    search index whose rankings were made from `queries_file`."""
    path = search.search_dir(cfg, set_name) / "index.json"
    if not path.exists():
        raise FileNotFoundError(f"{path} not found: run `shoebox eval search-run --set {set_name}` first")
    out = {}
    for route, entry in sorted(json.loads(path.read_text())["routes"].items()):
        if entry.get("queries_file", search.QUERIES_FILE) != queries_file:
            continue
        rows = search.read_jsonl(Path(entry["path"]))
        out[route] = {"params_hash": entry["params_hash"], "rankings": {r["query_id"]: r["ranking"] for r in rows}}
    if not out:
        raise ValueError(f"no route in {path} was run on {queries_file}")
    return out


def build_pool(queries: list[dict], rankings: dict[str, dict], k: int = 10, split: str | None = None) -> list[dict]:
    """Per query (in query_id order): the union of every route's top-k plus its relevant items, ordered by best
    rank over the routes then asset_id (relevant items not retrieved by any route come last, by asset_id)."""
    rows = []
    for q in sorted(queries, key=lambda q: q["query_id"]):
        if split and q.get("split") != split:
            continue
        best: dict[str, int] = {}
        routes: dict[str, list[str]] = {}
        missing = []
        for route, r in sorted(rankings.items()):
            ranking = r["rankings"].get(q["query_id"])
            if ranking is None:
                missing.append(route)
                continue
            for i, a in enumerate(ranking[:k]):
                best[a] = min(best.get(a, i), i)
                routes.setdefault(a, []).append(route)
        for a in q.get("relevant") or []:
            best.setdefault(a, k)
        ids = sorted(best, key=lambda a: (best[a], a))
        rows.append({"query_id": q["query_id"], "text": q["text"], "kind": q.get("kind"), "split": q.get("split"),
                     "relevant": list(q.get("relevant") or []), "asset_ids": ids,
                     "routes": {a: routes.get(a, []) for a in ids}, "routes_missing": missing,
                     "route_params": {route: r["params_hash"] for route, r in sorted(rankings.items())}, "k": k})
    return rows


def pool_summary(rows: list[dict]) -> dict:
    sizes = sorted(len(r["asset_ids"]) for r in rows)
    n = len(sizes)
    return {"queries": n, "pairs": sum(sizes), "min": sizes[0] if n else 0, "median": sizes[n // 2] if n else 0,
            "max": sizes[-1] if n else 0, "mean": round(sum(sizes) / n, 1) if n else 0.0,
            "queries_with_missing_routes": sum(bool(r["routes_missing"]) for r in rows)}


def write_jsonl(path: Path, rows: list[dict]) -> None:
    search._atomic_write(path, "".join(json.dumps(r, sort_keys=True) + "\n" for r in rows))


# ---------- candidate and params ----------


def judge_candidate(base: dict, max_usd: float = DEFAULT_MAX_USD, prompt: str | None = None) -> dict:
    """The judge's settings: model, resolution, thinking level and prices from `base` (gemini-low)."""
    text = prompt if prompt is not None else (gemini.PROMPTS_DIR / PROMPT_FILE).read_text()
    cand = {k: base[k] for k in ("model", "media_resolution", "thinking_level", "price_input_usd_per_mtok",
                                 "price_output_usd_per_mtok", "price_date", "price_valid_until") if k in base}
    cand.update(name=JUDGE_NAME, runtime="gemini", prompt=PROMPT_FILE, prompt_text=text, profile="infer-v1",
                max_output_tokens=base.get("max_output_tokens", 4096), temperature=TEMPERATURE,
                max_usd_per_run=float(max_usd), grid_size=GRID_SIZE, shuffle_seed=SHUFFLE_SEED,
                model_id=f"gemini/{base['model']}")
    return cand


def judge_params(cand: dict) -> dict:
    return {"prompt_sha256": hashlib.sha256(cand["prompt_text"].encode()).hexdigest(), "prompt": cand["prompt"],
            "model": cand["model"], "media_resolution": cand["media_resolution"],
            "thinking_level": cand["thinking_level"], "temperature": cand.get("temperature"),
            "max_output_tokens": cand.get("max_output_tokens"), "grid_size": cand["grid_size"],
            "shuffle_seed": cand["shuffle_seed"], "profile": cand["profile"], "runner_version": JUDGE_VERSION,
            "gemini_runner_version": gemini.RUNNER_VERSION}


def params_hash(cand: dict) -> str:
    return store.params_hash(judge_params(cand))


def _pair(r: dict) -> str:
    return f"{r.get('query_id', '')}:{r['asset_id']}"


def stored(cfg: Config, cand: dict) -> dict[str, dict]:
    """`{"query_id:asset_id": record}` for this judge's params (first final record, else latest error)."""
    return store.index_records(store.iter_records(cfg, cand["model_id"], params_hash(cand), KIND), key=_pair)


# ---------- grids ----------


def shuffled(query_id: str, asset_ids: list[str], seed: int) -> list[str]:
    ids = sorted(asset_ids)
    random.Random(f"{seed}:{query_id}").shuffle(ids)
    return ids


def split_grids(ids: list[str], size: int = GRID_SIZE) -> list[list[str]]:
    """Near-equal chunks of at most `size` (23 -> 8, 8, 7), so no grid is a lone leftover photo."""
    if not ids:
        return []
    n = -(-len(ids) // size)
    base, extra = divmod(len(ids), n)
    out, i = [], 0
    for g in range(n):
        m = base + (g < extra)
        out.append(ids[i:i + m])
        i += m
    return out


@dataclass
class Grid:
    query_id: str
    text: str
    asset_ids: list[str]  # in the order shown: Photo 1 is asset_ids[0]

    @property
    def grid_id(self) -> str:
        return hashlib.sha256(f"{self.query_id}|{','.join(self.asset_ids)}".encode()).hexdigest()[:16]


@dataclass
class Plan:
    grids: list[Grid]
    skipped: list[dict]  # {query_id, asset_id, reason}: refused by the privacy filter or not in the set
    cached: int  # pairs already final in the store

    @property
    def images(self) -> int:
        return sum(len(g.asset_ids) for g in self.grids)


def plan(cfg: Config, pool: list[dict], cand: dict, items: list[dict], others: list[dict] = ()) -> Plan:
    """Grids for every (query, photo) pair that is allowed and not yet final. No network, no spend."""
    by_id = {it["asset_id"]: it for it in items}
    wanted = sorted({a for row in pool for a in row["asset_ids"]})
    elig = privacy.filter_items([by_id[a] for a in wanted if a in by_id], cfg, others=list(others),
                                provider="gemini")
    allowed = {it["asset_id"] for it in elig.allowed}
    reasons = {s.asset_id: s.reason for s in elig.skipped}
    done = {k for k, r in stored(cfg, cand).items() if store.is_final(r)}
    grids, skipped, cached = [], [], 0
    for row in pool:
        todo = []
        for a in row["asset_ids"]:
            if f"{row['query_id']}:{a}" in done:
                cached += 1
            elif a not in allowed:
                skipped.append({"query_id": row["query_id"], "asset_id": a,
                                "reason": reasons.get(a, "not_in_set")})
            else:
                todo.append(a)
        for ids in split_grids(shuffled(row["query_id"], todo, cand["shuffle_seed"]), cand["grid_size"]):
            grids.append(Grid(row["query_id"], row["text"], ids))
    return Plan(grids, skipped, cached)


def query_text(cand: dict, text: str) -> str:
    return f"{cand['prompt_text'].rstrip()}\n\nSearch query: {json.dumps(text, ensure_ascii=False)}"


def estimate(cand: dict, p: Plan) -> privacy.Estimate:
    img = privacy.IMAGE_TOKENS[cand["media_resolution"]] + EST_TEXT_TOKENS_PER_PHOTO
    tin = sum(len(g.asset_ids) * img + len(query_text(cand, g.text)) // 3 for g in p.grids)
    tout = sum(len(g.asset_ids) * EST_OUTPUT_TOKENS_PER_PHOTO + EST_OUTPUT_TOKENS_PER_GRID
               + EST_THINKING_TOKENS_PER_GRID for g in p.grids)
    pin, pout = privacy.price(cand)
    return privacy.Estimate(p.images, tin, tout, tin * pin + tout * pout)


# ---------- request and response ----------


def grade_schema(n: int) -> dict:
    """responseJsonSchema (only keywords Gemini accepts); `validate_grades` enforces the rest."""
    return {"type": "object", "required": ["grades"], "properties": {"grades": {
        "type": "array", "minItems": n, "maxItems": n, "items": {
            "type": "object", "required": ["photo", "grade"], "properties": {
                "photo": {"type": "integer", "minimum": 1, "maximum": n},
                "grade": {"type": "integer", "minimum": 0, "maximum": 2}}}}}}


def photo_note(item: dict, frame_row: dict | None) -> str:
    """What the library knows about a photo's time and place, for queries about them: the capture date
    (EXIF or file-name dates only; a scan or file date is not a capture date) and the folder it is filed in.
    No file name (camera codes carry nothing; names in file names would be identity hints)."""
    from shoebox.evals.queries import DATED_KINDS

    row = frame_row or {}
    d = str(row.get("date") or "")
    when = f"taken {d[:10]}" if row.get("date_kind") in DATED_KINDS and d[:4].isdigit() else "date unknown"
    folder = str(item.get("relpath") or "").rsplit("/", 1)[0] if "/" in str(item.get("relpath") or "") else ""
    return f"{when}; folder: {folder}" if folder else when


def build_request(cand: dict, text: str, jpegs: list[bytes], notes: list[str] | None = None) -> dict:
    level = gemini._setting(cand, "media_resolution", gemini.MEDIA_RESOLUTION)
    parts: list[dict] = [{"text": query_text(cand, text)}]
    for i, jpeg in enumerate(jpegs, 1):
        parts.append({"text": f"Photo {i} ({notes[i - 1]})" if notes else f"Photo {i}"})
        parts.append({"inlineData": {"mimeType": "image/jpeg", "data": base64.b64encode(jpeg).decode("ascii")},
                      "mediaResolution": {"level": level}})
    gen = {"responseMimeType": "application/json", "responseJsonSchema": grade_schema(len(jpegs)),
           "thinkingConfig": {"thinkingLevel": gemini._setting(cand, "thinking_level", gemini.THINKING_LEVEL)}}
    if cand.get("max_output_tokens"):
        gen["maxOutputTokens"] = int(cand["max_output_tokens"])
    if cand.get("temperature") is not None:
        gen["temperature"] = cand["temperature"]
    return {"contents": [{"role": "user", "parts": parts}], "generationConfig": gen}


def validate_grades(output, n: int) -> tuple[dict[int, int] | None, str | None]:
    """`{photo: grade}` if every photo 1..n is graded exactly once with 0/1/2, else (None, why)."""
    if not isinstance(output, dict) or not isinstance(output.get("grades"), list):
        return None, "no grades list"
    grades: dict[int, int] = {}
    for e in output["grades"]:
        photo = e.get("photo") if isinstance(e, dict) else None
        grade = e.get("grade") if isinstance(e, dict) else None
        if type(photo) is not int or not 1 <= photo <= n:
            return None, f"bad photo number {photo!r}"
        if type(grade) is not int or grade not in GRADES:
            return None, f"bad grade {grade!r} for photo {photo}"
        if photo in grades:
            return None, f"photo {photo} graded twice"
        grades[photo] = grade
    missing = [i for i in range(1, n + 1) if i not in grades]
    if missing:
        return None, f"photos not graded: {missing}"
    return grades, None


def parse_response(data: dict, n: int) -> tuple[str, dict[int, int] | None, str | None, str | None]:
    """(status, grades, raw, error), as `gemini.parse_response` but for grade lists."""
    block = (data.get("promptFeedback") or {}).get("blockReason")
    cands = data.get("candidates") or []
    first = cands[0] if cands else {}
    finish = first.get("finishReason")
    text = "".join(p.get("text", "") for p in (first.get("content") or {}).get("parts") or [] if not p.get("thought"))
    if block or not text.strip():
        raw = json.dumps({"finishReason": finish, "blockReason": block, "text": text or None})
        return "invalid", None, raw, f"no output (finishReason={finish}, blockReason={block})"
    try:
        output = json.loads(text)
    except ValueError as e:
        return "invalid", None, text, f"not JSON (finishReason={finish}): {e}"
    grades, err = validate_grades(output, n)
    if err:
        return "invalid", None, text, err
    if finish not in (None, "STOP"):
        return "invalid", None, text, f"finishReason={finish}"
    return "ok", grades, None, None


# ---------- run ----------


@dataclass
class JudgeResult:
    plan: Plan
    params_hash: str
    calls: int = 0
    usd: float = 0.0
    status: dict = field(default_factory=dict)  # record status -> pair count
    stopped: str | None = None  # why the run stopped early (a spend cap)


def run(cfg: Config, pool: list[dict], cand: dict, items: list[dict], others: list[dict] = (),
        log=lambda msg: print(msg, file=sys.stderr), notes: dict[str, str] | None = None) -> JudgeResult:
    """Judge every planned grid. Stops before a grid (no records for it) once the run cap or the project
    budget is reached; `result.stopped` says why."""
    privacy.require_checklist()
    privacy.require_current_price(cand)
    p = plan(cfg, pool, cand, items, others)
    for s in p.skipped:
        log(f"judge: skipped {s['asset_id']} for {s['query_id']}: {s['reason']}")
    est = estimate(cand, p)
    privacy.check_budget(cand, est, already_spent=privacy.spent_total())
    ph = params_hash(cand)
    res = JudgeResult(p, ph)
    log(f"judge [{JUDGE_NAME} {ph}]: {len(p.grids)} grids, {p.images} images, {p.cached} cached, "
        f"{len(p.skipped)} skipped; estimate ${est.usd:.4f} (cap ${cand['max_usd_per_run']:.2f})")
    if not p.grids:
        return res
    key = gemini.api_key()
    store.write_params(cfg, cand["model_id"], ph, {"candidate": JUDGE_NAME, "model_id": cand["model_id"],
                                                   "params_hash": ph, "params": judge_params(cand)}, KIND)
    run_id = uuid.uuid4().hex[:12]
    for g in p.grids:
        privacy.require_current_price(cand)
        if res.usd >= float(cand["max_usd_per_run"]):
            res.stopped = f"run spend ${res.usd:.4f} reached --max-usd ${float(cand['max_usd_per_run']):.2f}"
            break
        spent = privacy.spent_total()
        if spent >= privacy.limits().max_total_usd:
            res.stopped = f"project spend ${spent:.4f} reached the budget ${privacy.limits().max_total_usd:.2f}"
            break
        jpegs = []
        for a in g.asset_ids:
            path = gemini._check_image_path(cand, derive.derived_path(cfg, cand["profile"], a), cfg)
            data = path.read_bytes()
            gemini.check_derived_jpeg(data)
            jpegs.append(data)
        body = build_request(cand, g.text, jpegs, [notes[a] for a in g.asset_ids] if notes is not None else None)
        t0 = time.monotonic()
        data, error = gemini._post(cand, body, key)
        latency_ms = int((time.monotonic() - t0) * 1000)
        base = {"query_id": g.query_id, "model_id": cand["model_id"], "params_hash": ph, "run": run_id,
                "latency_ms": latency_ms}
        if data is None:
            status, grades, raw, err, usage, usd = "error", None, None, gemini._scrub(error, key), None, 0.0
        else:
            usage = gemini.usage_of(data)
            usd = gemini.usd_for(usage, cand)
            res.calls += 1
            res.usd += usd
            privacy.append_spend(JUDGE_NAME, 1, usd, str(cand.get("price_date", "")), run=run_id)
            status, grades, raw, err = parse_response(data, len(g.asset_ids))
            raw, err = gemini._scrub(raw, key), gemini._scrub(err, key)
        for i, a in enumerate(g.asset_ids, 1):
            store.append(cfg, {"asset_id": a, **base, "status": status,
                               "grade": grades[i] if grades else None, "raw": raw, "error": err,
                               "grid": {"id": g.grid_id, "photo": i, "size": len(g.asset_ids)},
                               "usage": usage, "usd": round(usd / len(g.asset_ids), 8)}, KIND)
            res.status[status] = res.status.get(status, 0) + 1
        if status != "ok":
            log(f"judge: grid {g.grid_id} ({g.query_id}) {status}: {err}")
    return res


# ---------- qrels ----------


def qrels(cfg: Config, cand: dict) -> list[dict]:
    """`{query_id, asset_id, grade, model_id, params_hash}` for every `ok` judgment, sorted."""
    ph = params_hash(cand)
    rows = [{"query_id": r["query_id"], "asset_id": r["asset_id"], "grade": r["grade"],
             "model_id": r["model_id"], "params_hash": ph}
            for r in stored(cfg, cand).values() if r.get("status") == "ok"]
    return sorted(rows, key=lambda r: (r["query_id"], r["asset_id"]))
