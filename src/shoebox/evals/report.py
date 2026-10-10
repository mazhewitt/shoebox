"""Pilot report over Vally `results.jsonl` (from `vally eval` or `vally grade --output jsonl`).

Vally only averages per-trial scores, so everything statistical lives here: status counts, value
distributions, abstain rates, accuracy over labelled items only, pairwise agreement, latency, tokens, USD.
"""

import json
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date as Date
from itertools import combinations
from pathlib import Path

FIELDS = ("medium", "digitiser", "colour_class")
STATUSES = ("ok", "invalid", "error", "missing", "exec_error")
_KV = re.compile(
    r"(candidate|asset_id|model_id|params_hash|set|candidates_file)=([^,\s)\]]+)"
)


@dataclass
class Trial:
    candidate: str
    asset_id: str
    status: str  # ok | invalid | error (store record) | missing (replay key absent) | exec_error
    label: dict | None = None
    event: dict = field(default_factory=dict)  # the executor's custom `shoebox` event
    graders: list[dict] = field(default_factory=list)  # gradeResult.details[]
    error: str | None = None


def _shoebox_event(traj: dict) -> dict:
    for ev in traj.get("events") or []:
        if ev.get("type") == "custom" and isinstance(
            (ev.get("data") or {}).get("shoebox"), dict
        ):
            return ev["data"]["shoebox"]
    return {}


def parse_line(r: dict) -> Trial | None:
    """One results.jsonl line -> Trial; None for run summaries and other non-trial lines."""
    if r.get("type") not in (None, "trial-result") or (
        "trajectory" not in r and "status" not in r
    ):
        return None
    traj = r.get("trajectory") or None
    if traj is None:
        err = str(r.get("error") or "")
        kv = dict(_KV.findall(err))
        return Trial(
            candidate=r.get("model") or kv.get("candidate") or "?",
            asset_id=r.get("stimulus") or kv.get("asset_id") or "?",
            status="missing" if "missing key" in err else "exec_error",
            error=err,
            event={
                k: v
                for k, v in kv.items()
                if k in ("model_id", "params_hash", "set", "candidates_file")
                and v != "?"
            },
        )
    ev = _shoebox_event(traj)
    status = ev.get("status") or "exec_error"
    label = None
    if status == "ok":
        try:
            parsed = json.loads(traj.get("output") or "")
            label = parsed if isinstance(parsed, dict) else None
        except json.JSONDecodeError:
            label = None
    return Trial(
        candidate=r.get("model")
        or (traj.get("metadata") or {}).get("model")
        or ev.get("candidate")
        or "?",
        asset_id=r.get("stimulus")
        or (traj.get("stimulus") or {}).get("name")
        or ev.get("asset_id")
        or "?",
        status=status,
        label=label,
        event=ev,
        graders=list((r.get("gradeResult") or {}).get("details") or []),
    )


def load_trials(path: Path) -> list[Trial]:
    trials = []
    for line in Path(path).read_text().splitlines():
        if line.strip():
            t = parse_line(json.loads(line))
            if t is not None:
                trials.append(t)
    return trials


def percentile(values: list[float], q: float) -> float | None:
    """Linear-interpolated percentile, q in [0, 100]."""
    if not values:
        return None
    v = sorted(values)
    k = (len(v) - 1) * q / 100
    lo, hi = int(k), min(int(k) + 1, len(v) - 1)
    return v[lo] + (v[hi] - v[lo]) * (k - lo)


def _rate(num: int, den: int) -> float | None:
    return num / den if den else None


def _field_grader(t: Trial, f: str) -> dict | None:
    for d in t.graders:
        md = d.get("metadata") or {}
        if d.get("graderType", "field-match") == "field-match" and md.get("field") == f:
            return d
    return None


def candidate_summary(trials: list[Trial]) -> dict:
    n = len(trials)
    status = Counter(t.status for t in trials)
    ok = [t for t in trials if t.status == "ok"]
    keys = Counter(
        (t.event.get("model_id"), t.event.get("params_hash"))
        for t in trials
        if t.event.get("model_id")
    )
    model_id, params_hash = keys.most_common(1)[0][0] if keys else (None, None)

    fields = {}
    for f in FIELDS:
        dist = Counter(str((t.label or {}).get(f)) for t in ok)
        abstain = dist.get("unknown", 0)
        labelled = correct = answered = correct_answered = owner_unknown = 0
        for t in trials:
            g = _field_grader(t, f)
            md = (g or {}).get("metadata") or {}
            if md.get("owner_unknown") or md.get("expected") == "unknown":
                owner_unknown += 1  # the owner could not tell: nothing to score against
                continue
            if g is None or md.get("unlabelled", True):
                continue
            labelled += 1
            passed = bool(g.get("passed"))
            is_answer = t.status == "ok" and (t.label or {}).get(f) not in (
                None,
                "unknown",
            )
            correct += passed
            answered += is_answer
            correct_answered += passed and is_answer
        fields[f] = {
            "distribution": dict(sorted(dist.items(), key=lambda kv: (-kv[1], kv[0]))),
            "abstain_rate": _rate(abstain, len(ok)),
            "labelled_n": labelled,
            "owner_unknown_n": owner_unknown,
            "accuracy": _rate(correct, labelled),
            "coverage": _rate(answered, labelled),
            "precision": _rate(correct_answered, answered),
        }

    tags = {"tp": 0, "fp": 0, "fn": 0, "labelled_n": 0}
    for t in trials:
        for d in t.graders:
            md = d.get("metadata") or {}
            if d.get("graderType") == "set-match" and not md.get("unlabelled", True):
                tags["labelled_n"] += 1
                for k in ("tp", "fp", "fn"):
                    tags[k] += len(md.get(k) or [])
    tags["precision"] = _rate(tags["tp"], tags["tp"] + tags["fp"])
    tags["recall"] = _rate(tags["tp"], tags["tp"] + tags["fn"])

    lat = [
        float(t.event["latency_ms"])
        for t in trials
        if t.event.get("latency_ms") is not None
    ]
    mean_lat = sum(lat) / len(lat) if lat else None
    usd = sum(float(t.event.get("usd") or 0) for t in trials)
    with_record = [t for t in trials if t.status in ("ok", "invalid", "error")]
    return {
        "model_id": model_id,
        "params_hash": params_hash,
        "modes": sorted({t.event.get("mode") for t in trials if t.event.get("mode")}),
        "n": n,
        "assets": len({t.asset_id for t in trials}),
        "status_counts": {s: status.get(s, 0) for s in STATUSES},
        "validity_rate": _rate(len(ok), n),
        "fields": fields,
        "tags": tags,
        "latency_ms": {
            "n": len(lat),
            "p50": percentile(lat, 50),
            "p90": percentile(lat, 90),
            "mean": mean_lat,
        },
        "images_per_hour": 3_600_000 / mean_lat if mean_lat else None,
        "tokens": {
            "input": sum(int(t.event.get("input_tokens") or 0) for t in trials),
            "output": sum(int(t.event.get("output_tokens") or 0) for t in trials),
            "thinking": sum(int(t.event.get("thinking_tokens") or 0) for t in trials),
        },
        "usd": usd,
        "usd_per_1000": usd / len(with_record) * 1000 if with_record else None,
    }


def agreement(trials: list[Trial]) -> dict:
    """Per field: {a: {b: {agree, n, rate}}} over assets where both candidates have status ok."""
    by_cand: dict[str, dict[str, dict]] = defaultdict(dict)
    for t in trials:
        if t.status == "ok" and t.label is not None:
            by_cand[t.candidate].setdefault(t.asset_id, t.label)
    cands = sorted({t.candidate for t in trials})
    out: dict[str, dict] = {}
    for f in FIELDS:
        m: dict[str, dict] = {a: {} for a in cands}
        for a, b in combinations(cands, 2):
            common = by_cand[a].keys() & by_cand[b].keys()
            agree = sum(by_cand[a][k].get(f) == by_cand[b][k].get(f) for k in common)
            cell = {"agree": agree, "n": len(common), "rate": _rate(agree, len(common))}
            m[a][b] = m[b][a] = cell
        out[f] = m
    return out


def load_set_items(path: Path) -> dict | None:
    """The set's items as recorded by `shoebox eval gen` in candidates.json, or None if unreadable."""
    try:
        doc = json.loads(Path(path).read_text())
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(doc.get("items"), list):
        return None
    return doc


def resolve_set(trials: list[Trial], set_name: str | None) -> str:
    """The set named by the trials' executor events; an explicit name must agree with it."""
    seen = sorted({t.event["set"] for t in trials if t.event.get("set")})
    if len(seen) > 1:
        raise ValueError(f"results cover more than one set: {', '.join(seen)}")
    if seen and set_name and set_name != seen[0]:
        raise ValueError(f"--set {set_name} but the trials were run on set {seen[0]}")
    if seen:
        return seen[0]
    if set_name:
        return set_name
    raise ValueError("no set recorded in the trials; pass --set")


def build_report(
    trials: list[Trial],
    set_name: str | None,
    results_path: Path,
    run_date: str,
    candidates_file: Path | None = None,
) -> dict:
    set_name = resolve_set(trials, set_name)
    by_cand: dict[str, list[Trial]] = defaultdict(list)
    for t in trials:
        by_cand[t.candidate].append(t)
    cands = {c: candidate_summary(ts) for c, ts in sorted(by_cand.items())}
    modes = sorted({m for s in cands.values() for m in s["modes"]})

    blockers: list[str] = []
    if modes != ["replay"]:
        blockers.append(
            f"modes are {modes or 'unknown'}; gate runs must be replay only"
        )
    for c, s in cands.items():
        sc = s["status_counts"]
        if sc["missing"]:
            blockers.append(f"{c}: {sc['missing']} trials with a missing replay key")
        if sc["exec_error"]:
            blockers.append(f"{c}: {sc['exec_error']} trials where the executor failed")
        if sc["error"]:
            blockers.append(
                f"{c}: {sc['error']} stored error records (transport failures, not outputs)"
            )

    files = {
        t.event["candidates_file"] for t in trials if t.event.get("candidates_file")
    }
    if candidates_file is None and len(files) == 1:
        candidates_file = Path(next(iter(files)))
    doc = load_set_items(candidates_file) if candidates_file else None
    set_items = None
    if doc is None:
        blockers.append(
            "set completeness unverified: candidates.json with the set's items not found"
            + (f" ({candidates_file})" if candidates_file else "")
        )
    else:
        items = list(dict.fromkeys(doc["items"]))
        skipped = dict(doc.get("skipped_items") or {})
        set_items = {
            "n": len(items),
            "skipped": skipped,
            "candidates_file": str(candidates_file),
        }
        shas = {
            t.event.get("items_sha256") for t in trials if t.event.get("items_sha256")
        }
        if shas and shas != {doc.get("items_sha256")}:
            blockers.append(
                "candidates.json was regenerated with different set items since the run"
            )
        if skipped:
            blockers.append(
                f"{len(skipped)} set items skipped by `shoebox eval gen` (no stimulus)"
            )
        for c, ts in by_cand.items():
            ran = {t.asset_id for t in ts}
            not_run = [a for a in items if a not in ran]
            cands[c]["not_run"] = not_run
            if not_run:
                blockers.append(
                    f"{c}: no trial for {len(not_run)} of {len(items)} set items"
                )

    return {
        "set": set_name,
        "date": run_date,
        "results": str(results_path),
        "scored": False,
        "gates": None,
        "modes": modes,
        "set_items": set_items,
        "gate_eligible": not blockers,
        "gate_blockers": blockers,
        "notes": [
            "Pilot is unscored: no gates.toml. Accuracy is over owner-labelled items only (n given).",
            "Unlabelled trials pass in Vally (no skip verdict in 0.18.0) and are excluded here.",
            "Fields the owner labelled 'unknown' are not scored (owner_unknown_n).",
            "Precision = correct answers / answers; an abstain ('unknown') lowers coverage, not precision.",
            "images_per_hour = 3.6e6 / mean provider latency_ms (sequential, one image at a time).",
        ],
        "candidates": cands,
        "agreement": agreement(trials),
    }


def _fmt(v, pct: bool = False) -> str:
    if v is None:
        return "–"
    if pct:
        return f"{v * 100:.1f}%"
    if isinstance(v, float):
        return f"{v:,.4f}" if abs(v) < 1 else f"{v:,.1f}"
    return str(v)


def to_markdown(rep: dict) -> str:
    c = rep["candidates"]
    names = list(c)
    out = [
        f"# Eval run {rep['date']} — {rep['set']}",
        "",
        f"- Results: `{rep['results']}`",
        f"- Modes: {', '.join(rep['modes']) or '–'}; gate-eligible: {'yes' if rep['gate_eligible'] else 'no'}",
        "- Scored: no (pilot; no gates)",
    ]
    si = rep.get("set_items")
    if si:
        out.append(
            f"- Set items: {si['n']} (from `{si['candidates_file']}`), {len(si['skipped'])} skipped by gen"
        )
    out += [f"- Not gate-eligible: {b}" for b in rep.get("gate_blockers") or []]
    if si and si["skipped"]:
        out += [
            "",
            "## Items without a stimulus",
            "",
            "| asset_id | reason |",
            "|---|---|",
        ]
        out += [f"| `{a}` | {why} |" for a, why in si["skipped"].items()]
    out += [
        "",
        "## Candidates",
        "",
        (
            "| candidate | model_id | params_hash | n | ok | invalid | error | missing | exec_error | validity |"
            " p50 ms | p90 ms | img/h | in tok | out tok | USD |"
        ),
        "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for n in names:
        s = c[n]
        sc = s["status_counts"]
        out.append(
            f"| {n} | `{s['model_id']}` | `{s['params_hash']}` | {s['n']} | "
            + " | ".join(str(sc[k]) for k in STATUSES)
            + f" | {_fmt(s['validity_rate'], True)} | {_fmt(s['latency_ms']['p50'])} | {_fmt(s['latency_ms']['p90'])}"
            f" | {_fmt(s['images_per_hour'])} | {s['tokens']['input']} | {s['tokens']['output']} | {s['usd']:.4f} |"
        )
    for f in FIELDS:
        out += [
            "",
            f"## {f}",
            "",
            "| candidate | distribution (ok outputs) | abstain | labelled n | owner unknown | accuracy | coverage | precision |",
            "|---|---|---|---|---|---|---|---|",
        ]
        for n in names:
            s = c[n]["fields"][f]
            dist = ", ".join(f"{k}: {v}" for k, v in s["distribution"].items()) or "–"
            out.append(
                f"| {n} | {dist} | {_fmt(s['abstain_rate'], True)} | {s['labelled_n']} | {s['owner_unknown_n']} | "
                f"{_fmt(s['accuracy'], True)} | {_fmt(s['coverage'], True)} | {_fmt(s['precision'], True)} |"
            )
        if len(names) > 1:
            out += [
                "",
                f"Agreement on `{f}` (both ok):",
                "",
                "| | " + " | ".join(names) + " |",
                "|---" * (len(names) + 1) + "|",
            ]
            m = rep["agreement"][f]
            for a in names:
                cells = [
                    "–" if a == b else f"{_fmt(m[a][b]['rate'], True)} ({m[a][b]['n']})"
                    for b in names
                ]
                out.append(f"| {a} | " + " | ".join(cells) + " |")
    out += ["", "## Notes", ""] + [f"- {n}" for n in rep["notes"]]
    return "\n".join(out) + "\n"


def write_report(
    results_path: Path,
    set_name: str | None,
    out_dir: Path,
    run_date: str | None = None,
    candidates_file: Path | None = None,
) -> tuple[Path, Path, dict]:
    run_date = run_date or Date.today().isoformat()
    rep = build_report(
        load_trials(results_path),
        set_name,
        Path(results_path),
        run_date,
        candidates_file,
    )
    set_name = rep["set"]
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    jpath = out_dir / f"{run_date}_{set_name}.json"
    mpath = out_dir / f"{run_date}_{set_name}.md"
    jpath.write_text(json.dumps(rep, indent=2, sort_keys=False) + "\n")
    mpath.write_text(to_markdown(rep))
    return jpath, mpath, rep
