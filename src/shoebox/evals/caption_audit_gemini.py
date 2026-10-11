"""Gemini caption auditor: a second, independent auditor for the caption audit (task 6.5), so the Claude Opus
caption arm (`opus-v2`) is not audited only by Opus itself.

Reads a `caption-packets` dir (blind labels in `batch-*.json`, `map.json`, `packets.json`). Per photo one
generateContent call: the instructions (`caption-audit-gemini-v1.md`: the counting rules of caption-audit-v1,
adapted to one call and JSON output), the derived infer-v1 JPEG at MEDIUM resolution, then the blind caption
sets as JSON text. The reply must match `audit_schema` and pass `caption_audit.validate_line`, else it is
stored `invalid` with its raw text. Ok replies are un-blinded via `map.json` into the record shape
`caption_audit.ingest` writes (kind `caption_audits`), so `caption-report` reads either auditor.

Guards are the Gemini runner's: privacy checklist, `privacy.filter_items(provider="gemini")`, the derived-image
path and metadata checks, current prices, a pre-flight estimate against the run cap and project budget, the
per-run cap and project budget before every call, and one fsynced `evals/spend.jsonl` line per billed call.
Photos already final in the store (same params_hash) are cache hits.

`compare` builds the two-auditor comparison: per-arm numbers side by side, Opus-Gemini agreement per arm, and
the self-audit cells (an auditor scoring its own model's arm) marked as not used for the rule. Counts only.
"""

import hashlib
import json
import sys
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from shoebox import derive, privacy, store
from shoebox.config import Config
from shoebox.evals import caption_audit as ca
from shoebox.evals import review
from shoebox.runners import gemini

NAME = "gemini-caption-audit"
RUNNER_VERSION = "caption-audit-gemini-1"  # bump when request building, validation or storing changes
PROMPT_FILE = "caption-audit-gemini-v1.md"
BASE_CANDIDATE = "gemini-medium"  # model, MEDIUM resolution, thinking low and prices
TEMPERATURE = None  # Gemini 3 default, as the label runner and judge
MAX_OUTPUT_TOKENS = 8192
DEFAULT_MAX_USD = 3.0
WRITER = "Gemini generateContent"
PRESENTATION = "derived infer-v1 JPEG, mediaResolution MEDIUM"
OPUS_AUDITOR = ca.MODEL_ID
SELF_AUDIT = {"claude/opus-5.5": ca.OPUS_ARM, "gemini/gemini-3.8-flash": ca.REFERENCE_ARM}
# Pre-flight estimate only (measure on the first live run).
EST_OUTPUT_TOKENS_PER_SET = 40
EST_OUTPUT_TOKENS_REFERENCE = 120
EST_THINKING_TOKENS = 600


def instructions_text() -> str:
    return (gemini.PROMPTS_DIR / PROMPT_FILE).read_text()


def candidate(base: dict, max_usd: float = DEFAULT_MAX_USD, prompt: str | None = None) -> dict:
    cand = {k: base[k] for k in ("model", "thinking_level", "price_input_usd_per_mtok", "price_output_usd_per_mtok",
                                 "price_date", "price_valid_until") if k in base}
    cand.update(name=NAME, runtime="gemini", prompt=PROMPT_FILE,
                prompt_text=prompt if prompt is not None else instructions_text(), profile="infer-v1",
                media_resolution="medium", max_output_tokens=MAX_OUTPUT_TOKENS, temperature=TEMPERATURE,
                max_usd_per_run=float(max_usd), model_id=f"gemini/{base['model']}")
    return cand


def audit_params(cand: dict, meta: dict) -> dict:
    """Prompt, resolution, arms, seed and runner version (plus the model settings) of a packet dir's audit."""
    return {"prompt_sha256": hashlib.sha256(cand["prompt_text"].encode()).hexdigest(), "prompt": cand["prompt"],
            "model": cand["model"], "media_resolution": cand["media_resolution"],
            "thinking_level": cand.get("thinking_level"), "temperature": cand.get("temperature"),
            "max_output_tokens": cand.get("max_output_tokens"), "profile": cand["profile"],
            "arms": sorted(meta["arms"]), "seed": int(meta["seed"]), "presentation": PRESENTATION,
            "writer": WRITER, "runner_version": RUNNER_VERSION, "gemini_runner_version": gemini.RUNNER_VERSION}


def params_hash(cand: dict, meta: dict) -> str:
    return store.params_hash(audit_params(cand, meta))


# ---------- packets ----------


def read_packets(d: Path) -> tuple[dict, dict, list[dict]]:
    """(packets.json, map.json, photos from batch-*.json in order)."""
    d = Path(d)
    meta = json.loads((d / "packets.json").read_text())
    mapping = json.loads((d / "map.json").read_text())
    photos = [p for path in sorted(d.glob("batch-*.json")) for p in json.loads(path.read_text())["photos"]]
    return meta, mapping, photos


def sets_text(photo: dict) -> str:
    """The blind caption sets as shown in the batch file (label, caption, tags, structured fields)."""
    sets = [{k: v for k, v in c.items() if k != "note"} for c in photo["captions"]]
    return "Caption sets:\n" + json.dumps(sets, ensure_ascii=False, indent=1)


# ---------- request and response ----------


def audit_schema(labels: list[str]) -> dict:
    """responseJsonSchema (only keywords Gemini accepts); `caption_audit.validate_line` enforces the rest."""
    en = ca.enums()
    conf = {"type": "string", "enum": list(ca.CONFIDENCE)}
    count = {"type": "integer", "minimum": 0}
    one = {"type": "object", "required": [*ca.COUNTS, "useful", "notes"], "properties": {
        **{c: count for c in ca.COUNTS}, "useful": {"type": "integer", "minimum": 1, "maximum": 5},
        "notes": {"type": "string"}}}
    ref = {"type": "object",
           "required": ["description", "medium", "digitiser", "colour_class", "rotate_cw", "confidence"],
           "properties": {"description": {"type": "string"},
                          **{f: {"type": "string", "enum": en[f]} for f in ("medium", "digitiser", "colour_class")},
                          "rotate_cw": {"type": "integer", "minimum": 0, "maximum": 270},
                          "confidence": {"type": "object", "required": list(ca.FIELDS),
                                         "properties": {f: conf for f in ca.FIELDS}}}}
    return {"type": "object", "required": ["reference", "sets"], "properties": {
        "reference": ref,
        "sets": {"type": "object", "required": list(labels), "properties": {lab: one for lab in labels}}}}


def build_request(cand: dict, photo: dict, jpeg: bytes) -> dict:
    import base64

    labels = [c["label"] for c in photo["captions"]]
    level = gemini._setting(cand, "media_resolution", gemini.MEDIA_RESOLUTION)
    parts = [{"text": cand["prompt_text"]},
             {"inlineData": {"mimeType": "image/jpeg", "data": base64.b64encode(jpeg).decode("ascii")},
              "mediaResolution": {"level": level}},
             {"text": sets_text(photo)}]
    gen = {"responseMimeType": "application/json", "responseJsonSchema": audit_schema(labels),
           "thinkingConfig": {"thinkingLevel": gemini._setting(cand, "thinking_level", gemini.THINKING_LEVEL)}}
    if cand.get("max_output_tokens"):
        gen["maxOutputTokens"] = int(cand["max_output_tokens"])
    if cand.get("temperature") is not None:
        gen["temperature"] = cand["temperature"]
    return {"contents": [{"role": "user", "parts": parts}], "generationConfig": gen}


def parse_response(data: dict, labels: list[str]) -> tuple[str, dict | None, str | None, str | None]:
    """(status, output, raw, error): ok only for a STOP reply that passes `caption_audit.validate_line`."""
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
    if not isinstance(output, dict):
        return "invalid", None, text, "not a JSON object"
    errs = ca.validate_line(output, labels, ca.enums())
    if errs:
        return "invalid", None, text, "; ".join(errs)
    if finish not in (None, "STOP"):
        return "invalid", None, text, f"finishReason={finish}"
    return "ok", output, None, None


def unblind(output: dict, photo: dict, mapping: dict[str, str]) -> dict:
    """{arm: {counts, useful, notes, label, shown}} as `caption_audit.ingest` stores it."""
    shown = {c["label"]: None if c["caption"] is None else {f: c.get(f) for f in ca.FIELDS}
             for c in photo["captions"]}
    return {arm: {**{f: output["sets"][lab][f] for f in (*ca.COUNTS, "useful")},
                  "notes": output["sets"][lab].get("notes", ""), "label": lab, "shown": shown.get(lab)}
            for lab, arm in mapping.items()}


# ---------- plan and run ----------


@dataclass
class Task:
    key: str
    asset_id: str
    stratum: str | None
    photo: dict


@dataclass
class Plan:
    tasks: list[Task]
    skipped: list[dict]  # {key, reason}
    cached: int


def plan(cfg: Config, d: Path, sample_rows: list[dict], items: list[dict], cand: dict, others: list[dict] = (),
         limit: int | None = None) -> tuple[Plan, dict, dict]:
    """Photos of the packet dir that are allowed for Gemini and not final in the store. No network, no spend."""
    meta, mapping, photos = read_packets(d)
    by_key = {r["key"]: r for r in sample_rows}
    unknown = sorted(p["key"] for p in photos if p["key"] not in by_key)
    if unknown:
        raise ValueError(f"packet keys not in the sample: {unknown[:10]}")
    by_id = {it["asset_id"]: it for it in items}
    wanted = [by_id[by_key[p["key"]]["asset_id"]] for p in photos if by_key[p["key"]]["asset_id"] in by_id]
    elig = privacy.filter_items(wanted, cfg, others=list(others), provider="gemini")
    allowed = {it["asset_id"] for it in elig.allowed}
    reasons = {s.asset_id: s.reason for s in elig.skipped}
    have = store.index(cfg, cand["model_id"], params_hash(cand, meta), ca.KIND)
    tasks, skipped, cached = [], [], 0
    for p in photos:
        row = by_key[p["key"]]
        a = row["asset_id"]
        if store.is_final(have.get(a)):
            cached += 1
        elif a not in allowed:
            skipped.append({"key": p["key"], "reason": reasons.get(a, "not_in_set")})
        else:
            tasks.append(Task(p["key"], a, row.get("stratum"), p))
    if limit is not None:
        tasks = tasks[:limit]
    return Plan(tasks, skipped, cached), meta, mapping


def estimate(cand: dict, p: Plan) -> privacy.Estimate:
    tin = sum(privacy.IMAGE_TOKENS[cand["media_resolution"]] + (len(cand["prompt_text"]) + len(sets_text(t.photo))) // 3
              for t in p.tasks)
    tout = sum(EST_OUTPUT_TOKENS_REFERENCE + EST_OUTPUT_TOKENS_PER_SET * len(t.photo["captions"]) + EST_THINKING_TOKENS
               for t in p.tasks)
    pin, pout = privacy.price(cand)
    return privacy.Estimate(len(p.tasks), tin, tout, tin * pin + tout * pout)


@dataclass
class RunResult:
    plan: Plan
    params_hash: str
    calls: int = 0
    usd: float = 0.0
    status: dict = field(default_factory=dict)
    stopped: str | None = None


def run(cfg: Config, d: Path, sample_rows: list[dict], items: list[dict], cand: dict, others: list[dict] = (),
        limit: int | None = None, log=lambda msg: print(msg, file=sys.stderr)) -> RunResult:
    """Audit every planned photo. Stops before a call (no record for it) once the run cap or the project budget
    is reached; `result.stopped` says why."""
    privacy.require_checklist()
    privacy.require_current_price(cand)
    p, meta, mapping = plan(cfg, d, sample_rows, items, cand, others, limit)
    for s in p.skipped:
        log(f"caption-audit-gemini: skipped {s['key']}: {s['reason']}")
    est = estimate(cand, p)
    privacy.check_budget(cand, est, already_spent=privacy.spent_total())
    ph = params_hash(cand, meta)
    res = RunResult(p, ph)
    log(f"caption-audit-gemini [{NAME} {ph}]: {len(p.tasks)} photos, {p.cached} cached, {len(p.skipped)} skipped; "
        f"estimate ${est.usd:.4f} (cap ${cand['max_usd_per_run']:.2f})")
    if not p.tasks:
        return res
    key = gemini.api_key()
    store.write_params(cfg, cand["model_id"], ph, {"candidate": NAME, "model_id": cand["model_id"],
                                                   "params_hash": ph, "params": audit_params(cand, meta)}, ca.KIND)
    run_id = uuid.uuid4().hex[:12]
    for t in p.tasks:
        privacy.require_current_price(cand)
        if res.usd >= float(cand["max_usd_per_run"]):
            res.stopped = f"run spend ${res.usd:.4f} reached --max-usd ${float(cand['max_usd_per_run']):.2f}"
            break
        spent = privacy.spent_total()
        if spent >= privacy.limits().max_total_usd:
            res.stopped = f"project spend ${spent:.4f} reached the budget ${privacy.limits().max_total_usd:.2f}"
            break
        path = gemini._check_image_path(cand, derive.derived_path(cfg, cand["profile"], t.asset_id), cfg)
        jpeg = path.read_bytes()
        gemini.check_derived_jpeg(jpeg)
        labels = [c["label"] for c in t.photo["captions"]]
        t0 = time.monotonic()
        data, error = gemini._post(cand, build_request(cand, t.photo, jpeg), key)
        latency_ms = int((time.monotonic() - t0) * 1000)
        if data is None:
            status, output, raw, err, usage, usd = "error", None, None, gemini._scrub(error, key), None, 0.0
        else:
            usage = gemini.usage_of(data)
            usd = gemini.usd_for(usage, cand)
            res.calls += 1
            res.usd += usd
            privacy.append_spend(NAME, 1, usd, str(cand.get("price_date", "")), run=run_id)
            status, output, raw, err = parse_response(data, labels)
            raw, err = gemini._scrub(raw, key), gemini._scrub(err, key)
        rec = {"asset_id": t.asset_id, "model_id": cand["model_id"], "params_hash": ph, "status": status,
               "kind": ca.KIND, "key": t.key, "stratum": t.stratum,
               "reference": output["reference"] if output else None,
               "sets": unblind(output, t.photo, mapping[t.key]) if output else None,
               "arm_sources": meta.get("sources") or {}, "writer": WRITER, "raw": raw, "error": err,
               "usage": usage, "usd": round(usd, 8), "run": run_id, "latency_ms": latency_ms}
        store.append(cfg, rec, ca.KIND)
        res.status[status] = res.status.get(status, 0) + 1
        if status != "ok":
            log(f"caption-audit-gemini: {t.key} {status}: {err}")
    return res


# ---------- two-auditor comparison ----------


def _by_arm(records: list[dict]) -> dict[str, dict[str, dict]]:
    out: dict[str, dict[str, dict]] = {}
    for r in records:
        for arm, s in r["sets"].items():
            out.setdefault(arm, {})[r["asset_id"]] = ca._metrics(s) if s.get("shown") is not None else ca.NO_CAPTION
    return out


def agreement(opus: list[dict], gem: list[dict]) -> dict[str, dict]:
    """Per arm over photos both auditors scored: mean |Opus - Gemini| of the error-rate flag and of useful, and
    Spearman of useful across photos."""
    o, g = _by_arm(opus), _by_arm(gem)
    out = {}
    for arm in sorted(set(o) | set(g)):
        common = sorted(set(o.get(arm, {})) & set(g.get(arm, {})))
        n = len(common)
        uo = [o[arm][a]["useful"] for a in common]
        ug = [g[arm][a]["useful"] for a in common]
        out[arm] = {"n": n,
                    "error_rate_mean_abs_diff": ca._r(sum(abs(o[arm][a]["error_rate"] - g[arm][a]["error_rate"])
                                                          for a in common) / n) if n else None,
                    "useful_mean_abs_diff": ca._r(sum(abs(x - y) for x, y in zip(uo, ug, strict=True)) / n)
                    if n else None,
                    "useful_spearman": ca._r(review.spearman(uo, ug)) if n else None}
    return out


def compare(opus_rep: dict, gem_rep: dict, opus: list[dict], gem: list[dict], meta: dict) -> dict:
    arms = sorted(set(opus_rep["arms"]) | set(gem_rep["arms"]))
    side = {}
    for arm in arms:
        side[arm] = {}
        for who, rep in (("opus", opus_rep), ("gemini", gem_rep)):
            a = rep["arms"].get(arm)
            side[arm][who] = None if a is None else {
                "n": a["n"], "no_caption": a["no_caption"], **{m: a[m] for m in ("error_rate", "useful", "wrong",
                                                                                   "invented", "missing")},
                "self_audit": arm in (rep.get("self_audit") or {})}
    return {**meta, "auditors": {"opus": opus_rep, "gemini": gem_rep}, "side_by_side": side,
            "agreement": agreement(opus, gem),
            "self_audit": {"opus": ca.OPUS_ARM, "gemini": ca.REFERENCE_ARM, "note": ca.SELF_AUDIT_NOTE},
            "rules": {"agreement": "per arm over photos both auditors scored: mean absolute difference of the "
                                   "error-rate flag and of useful; Spearman of useful across photos",
                      "self_audit": "a cell where the auditor scores its own model's arm (Opus on opus-v2, "
                                    "Gemini on gemini-medium-v2) is shown but not used for the rule"}}


def render_compare_md(rep: dict) -> str:
    f, ci = ca._f, ca._ci

    def cell(x, m):
        return "n/a" if x is None else f"{f(x[m]['mean'])} ({ci(x[m]['ci95'])})"

    def mark(x):
        return f" ({ca.SELF_AUDIT_NOTE})" if x and x["self_audit"] else ""

    o, g = rep["auditors"]["opus"], rep["auditors"]["gemini"]
    lines = [f"# Caption audit: {rep.get('set', '')} (Opus and Gemini auditors, blind)", "",
             (f"- Opus: `{o.get('auditor_model_id', o.get('opus_model_id'))}` "
              f"`{o.get('auditor_params_hash', o.get('opus_params_hash'))}`; {o['photos']} photos"),
             f"- Gemini: `{g.get('auditor_model_id')}` `{g.get('auditor_params_hash')}`; {g['photos']} photos", "",
             "## Per arm, both auditors (mean per photo, 95% CI)", "",
             "| arm | auditor | n | no caption | error rate | useful | wrong | invented | missing |",
             "|---|---|---|---|---|---|---|---|---|"]
    for arm, by in rep["side_by_side"].items():
        for who in ("opus", "gemini"):
            x = by[who]
            n = "n/a" if x is None else x["n"]
            nc = "n/a" if x is None else x["no_caption"]
            cells = " | ".join(cell(x, m) for m in ("error_rate", "useful", "wrong", "invented", "missing"))
            lines.append(f"| {arm} | {who}{mark(x)} | {n} | {nc} | {cells} |")
    lines += ["", "## Opus–Gemini agreement per arm", "",
              "| arm | n | error-rate flag mean abs diff | useful mean abs diff | useful Spearman |",
              "|---|---|---|---|---|"]
    for arm, a in rep["agreement"].items():
        lines.append(f"| {arm} | {a['n']} | {f(a['error_rate_mean_abs_diff'])} | {f(a['useful_mean_abs_diff'])} | "
                     f"{f(a['useful_spearman'])} |")
    lines += ["", "## Rules", "", *(f"- {k}: {v}" for k, v in rep["rules"].items()), ""]
    return "\n".join(lines) + "\n" + "\n".join(["## Opus auditor", "", ca.render_md(o), "## Gemini auditor", "",
                                                  ca.render_md(g)])
