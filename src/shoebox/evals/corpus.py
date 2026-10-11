"""A reproducible, screened draw of an eval corpus (`shoebox eval corpus-draw`; design D8 of add-eval-harness).

Base = a set's items minus its exclude.jsonl. Targets are always in (unless the rules or the screen drop them);
the rest are drawn, in a seeded order, from base items already labelled (final) by one label candidate. Every
item must pass the privacy rules for both hosted providers, and a model/owner screen (screen.jsonl) whose
`exclude` verdicts are substituted by the next eligible candidate. Same inputs give byte-identical outputs.
"""

import hashlib
import json
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from shoebox import privacy, store
from shoebox.config import Config, check_write_location
from shoebox.evals import search
from shoebox.evals.pool import _folder, _sha256, _write

PROVIDERS = ("gemini", "claude")
SCREEN_FILE = "screen.jsonl"
QUEUE_FILE = "screen-queue.jsonl"
VERDICTS = ("ok", "exclude")
CATEGORIES = (None, "id_document", "medical", "financial", "other_sensitive")
QUEUE_KEYS = ("asset_id", "source", "relpath", "group_id", "role")


@dataclass
class CorpusResult:
    out_dir: Path
    written: bool
    counts: dict = field(default_factory=dict)
    queue_path: Path | None = None
    queue_len: int = 0
    to_screen: int = 0


def resolve_labels(cfg: Config, name: str) -> tuple[dict, dict[str, dict]]:
    """(label source {candidate, model_id, params_hash[, retry_params_hash]}, {asset_id: record})."""
    from shoebox import runners

    res = runners.resolve(name)
    return runners.label_source(res), runners.label_index(cfg, res)


def _read_jsonl(path: Path) -> list[dict]:
    return [json.loads(x) for x in Path(path).read_text().splitlines() if x.strip()]


def read_screen(path: Path) -> dict[str, dict]:
    """{asset_id: verdict row}; the last line for an asset_id wins."""
    out = {}
    if not Path(path).exists():
        return out
    for i, r in enumerate(_read_jsonl(path), 1):
        if not r.get("asset_id") or r.get("verdict") not in VERDICTS or r.get("category") not in CATEGORIES:
            raise ValueError(f"{path}:{i}: needs asset_id, verdict in {VERDICTS}, category in {CATEGORIES}")
        out[r["asset_id"]] = r
    return out


def rule_reasons(items: list[dict], cfg: Config, others: list[dict], never) -> dict[str, str]:
    """{asset_id: first failing reason} over both providers (gemini checked first)."""
    out: dict[str, str] = {}
    for p in PROVIDERS:
        for s in privacy.filter_items(items, cfg, never=never, others=others, provider=p).skipped:
            out.setdefault(s.asset_id, s.reason)
    return out


def order(asset_ids: list[str], seed: int) -> list[str]:
    """Seeded order by sha256(seed:asset_id). An item's place doesn't depend on which other items are present,
    so adding a never-send entry or a held-back folder removes items without reshuffling the rest (and the
    screen verdicts already given still cover the front of the order)."""
    return sorted(asset_ids, key=lambda a: (hashlib.sha256(f"{seed}:{a}".encode()).hexdigest(), a))


def _jsonl(rows: list[dict]) -> str:
    return "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows)


def draw_corpus(cfg: Config, base_dir: Path, out_dir: Path, labels: dict[str, dict], labelled_by: dict,
                n: int, seed: int, buffer: int = 80, screen_path: Path | None = None,
                others: list[dict] = (), never=None) -> CorpusResult:
    base_dir, out_dir = Path(base_dir), Path(out_dir)
    screen_path = Path(screen_path) if screen_path else out_dir / SCREEN_FILE
    check_write_location(cfg, out_dir)
    never = never if never is not None else privacy.load_never_send()

    base_path, excl_path = base_dir / "items.jsonl", base_dir / search.EXCLUDE_FILE
    excluded = {r["asset_id"] for r in _read_jsonl(excl_path)} if excl_path.exists() else set()
    base: dict[str, dict] = {}
    for it in _read_jsonl(base_path):
        if it["asset_id"] not in excluded:
            base.setdefault(it["asset_id"], it)
    targets = sorted(a for a, it in base.items() if it.get("role") == "target")
    cand_ids = sorted(a for a, it in base.items() if it.get("role") != "target" and store.is_final(labels.get(a)))

    reasons = rule_reasons([base[a] for a in [*targets, *cand_ids]], cfg, list(others), never)
    rule_skips = dict(sorted(Counter(reasons[a] for a in cand_ids if a in reasons).items()))
    dropped = [{"asset_id": a, "reason": reasons[a]} for a in targets if a in reasons]

    screen = read_screen(screen_path)
    bad_groups = {base[a].get("group_id") for a, r in screen.items()
                  if r["verdict"] == "exclude" and a in base and base[a].get("group_id")}

    def group_out(a: str) -> bool:
        return screen.get(a, {}).get("verdict") != "exclude" and (base[a].get("group_id") or None) in bad_groups

    kept_targets, unscreened_targets, n_screened = [], [], 0
    excl_cats: Counter = Counter()
    for a in targets:
        if a in reasons:
            continue
        v = screen.get(a)
        if v and v["verdict"] == "exclude":
            n_screened += 1
            excl_cats[v["category"] or "uncategorised"] += 1
            dropped.append({"asset_id": a, "reason": "screen", "category": v["category"]})
        elif group_out(a):
            dropped.append({"asset_id": a, "reason": "screen_group"})
        else:
            n_screened += v is not None
            kept_targets.append(a)
            if v is None:
                unscreened_targets.append(a)
    needed = n - len(kept_targets)
    if needed < 0:
        raise ValueError(f"n={n} is fewer than the {len(kept_targets)} targets kept")

    walk = order([a for a in cand_ids if a not in reasons], seed)
    taken, subs, pending, n_group = [], [], [], 0
    stopped_at = None
    for i, a in enumerate(walk):
        if len(taken) >= needed:
            break
        if group_out(a):
            n_group += 1
            continue
        v = screen.get(a)
        if v is None:
            stopped_at = i
            break
        n_screened += 1
        if v["verdict"] == "exclude":
            excl_cats[v["category"] or "uncategorised"] += 1
            subs.append({"asset_id": a, "category": v["category"], "note": v.get("note"), "replaced_by": None})
            pending.append(subs[-1])
        else:
            taken.append(a)
            for s in pending:
                s["replaced_by"] = a
            pending = []

    counts = {"base_items": len(base), "base_excluded": len(excluded),
              "targets": len(targets), "candidates": len(cand_ids), "rule_skips": rule_skips,
              "rule_eligible": len(walk), "screened": n_screened,
              "screen_excludes": dict(sorted(excl_cats.items())), "screen_group": n_group,
              "targets_dropped": dropped, "targets_kept": len(kept_targets), "drawn": len(taken),
              "substitutions": subs}
    out_dir.mkdir(parents=True, exist_ok=True)
    queue = out_dir / QUEUE_FILE

    if stopped_at is not None or unscreened_targets:
        # Queue only what is unscreened, walking on past screened items: screened-ok ones further on still count
        # towards `needed`, so a gap in the verdicts doesn't send the whole remainder back for screening.
        want, ok_ahead, rest = needed - len(taken) + buffer, 0, []
        for a in walk[stopped_at:] if stopped_at is not None else []:
            if ok_ahead + len(rest) >= want:
                break
            if group_out(a):
                continue
            v = screen.get(a)
            if v is None:
                rest.append(a)
            elif v["verdict"] == "ok":
                ok_ahead += 1
        q_ids = [*unscreened_targets, *rest]
        rows = [{k: base[a].get(k) for k in QUEUE_KEYS[:-1]} | {"role": "target" if a in targets else "corpus"}
                for a in q_ids]
        _write(queue, _jsonl(rows))
        return CorpusResult(out_dir, False, counts, queue, len(rows), len(unscreened_targets) + (needed - len(taken)))
    if len(taken) < needed:
        raise ValueError(f"only {len(taken)} screened-ok candidates of the {needed} needed "
                         f"({len(walk)} rule-eligible); lower --n or label more items")

    items = [{**base[a], "role": "target"} for a in kept_targets] + [{**base[a], "role": "corpus"} for a in taken]
    draw = {"name": out_dir.name, "base": {"name": base_dir.name, "items_sha256": _sha256(base_path),
                                          "exclude_sha256": _sha256(excl_path) if excl_path.exists() else None},
            "labelled_by": labelled_by, "seed": seed, "n": n, "buffer": buffer, "counts": counts,
            "screen": {"file": screen_path.name, "sha256": _sha256(screen_path) if screen_path.exists() else None}}
    _write(out_dir / "items.jsonl", _jsonl(items))
    _write(out_dir / "draw.json", json.dumps(draw, indent=2, ensure_ascii=False) + "\n")
    _write(out_dir / "README.md", readme(out_dir.name, draw, items))
    queue.unlink(missing_ok=True)
    return CorpusResult(out_dir, True, counts)


def readme(name: str, draw: dict, items: list[dict]) -> str:
    c = draw["counts"]
    lines = [f"# {name}", "",
             (f"Eval corpus: the targets of `{draw['base']['name']}` plus {c['drawn']} items drawn in a seeded "
              f"order (seed {draw['seed']}) from base items already labelled by `{draw['labelled_by']['candidate']}`, "
              f"each allowed by the privacy rules for {' and '.join(PROVIDERS)} and screened (screen.jsonl). "
              "Written by `shoebox eval corpus-draw`; details in `draw.json`. No image bytes and no file names "
              "here."), "",
             f"- Items: {len(items)} (requested {draw['n']})", (f"- Targets kept: {c['targets_kept']} of {c['targets']}"
              f" ({len(c['targets_dropped'])} dropped)"),
             f"- Candidates (final label, non-target): {c['candidates']}; rule-eligible: {c['rule_eligible']}",
             f"- Screened: {c['screened']}; group-mates of excluded items skipped: {c['screen_group']}", ""]
    lines += ["## Items by folder", "", "| folder | files |", "|---|---|"]
    folders = Counter(_folder(it["relpath"]) for it in items)
    lines += [f"| {k} | {v} |" for k, v in sorted(folders.items(), key=lambda kv: (-kv[1], kv[0]))]
    lines += ["", "## Rule skips (candidates)", "", "| reason | items |", "|---|---|"]
    lines += [f"| {k} | {v} |" for k, v in c["rule_skips"].items()]
    lines += ["", "## Substitutions (screen excludes)", "", "| category | count |", "|---|---|"]
    subs = Counter(s["category"] or "uncategorised" for s in c["substitutions"])
    lines += [f"| {k} | {v} |" for k, v in sorted(subs.items())]
    lines += ["", "Per-item notes stay in screen.jsonl.", ""]
    return "\n".join(lines)
