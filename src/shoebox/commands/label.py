"""`shoebox label`: run a label candidate over items into the keyed store (cache-aware), or read one record."""

import argparse
import json
import sys
from pathlib import Path

from shoebox import config, runners
from shoebox.config import REPO_ROOT

DEFAULT_ITEMS = REPO_ROOT / "evals" / "sets" / "pilot-v0" / "items.jsonl"


def register(sub) -> None:
    p = sub.add_parser("label", help="run label candidates into the keyed output store")
    lsub = p.add_subparsers(dest="label_command", required=True)

    r = lsub.add_parser("run", help="label every item not already in the store")
    r.add_argument("--candidate", required=True)
    r.add_argument("--items", type=Path, required=True, help="items JSONL (docs/pilot.md section 1)")
    r.add_argument("--limit", type=int, help="only the first N items")
    r.set_defaults(func=cmd_run)

    o = lsub.add_parser("one", help="print one asset's record as JSON (runs it if missing)")
    o.add_argument("--candidate", required=True)
    o.add_argument("--asset-id", required=True)
    o.add_argument("--items", type=Path, default=DEFAULT_ITEMS)
    o.set_defaults(func=cmd_one)

    v = lsub.add_parser("resolve", help="print the candidate's model_id, params_hash and store_dir")
    v.add_argument("--candidate", required=True)
    v.set_defaults(func=cmd_resolve)

    rp = lsub.add_parser("replay", help="rebuild the candidate's store from an older store of it (no inference)")
    rp.add_argument("--candidate", required=True)
    rp.add_argument("--from-hash", required=True, help="params_hash of the older store of this candidate")
    rp.set_defaults(func=cmd_replay)

    ls = lsub.add_parser("list", help="list candidate names")
    ls.set_defaults(func=cmd_list)


def _err(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


def read_items(path: Path) -> list[dict]:
    with open(path, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def _resolve(name: str) -> runners.Resolved | None:
    try:
        return runners.resolve(name, runners.CONFIG_DIR)
    except Exception as e:  # noqa: BLE001 - unknown name, bad config, or the runtime (e.g. Ollama) unreachable
        _err(f"cannot resolve candidate {name!r}: {e}")
        return None


def _line(t: runners.Tally) -> str:
    retried = f"  truncated->retried {t.retried} (recovered {t.recovered})" if t.retried else ""
    return (f"ok {t.ok}  cached {t.cached}  skipped {t.skipped}  invalid {t.invalid}  error {t.error}{retried}"
            f"  | {t.per_hour():.0f} img/h")


def cmd_run(args: argparse.Namespace) -> int:
    res = _resolve(args.candidate)
    if res is None:
        return 2
    cfg = config.load()
    items = read_items(args.items)[: args.limit] if args.limit else read_items(args.items)
    _err(f"{res.name}: {res.model_id} params {res.params_hash}, {len(items)} items -> {res.store_dir(cfg)}")

    def progress(t: runners.Tally, item: dict, rec: dict) -> None:
        n = t.total - t.cached - t.skipped
        _err(f"[{t.ran}/{n}] {item['asset_id'][:12]} {rec['status']:<7} {rec.get('latency_ms') or 0:>6} ms"
             f"  | {_line(t)}")

    try:
        tally = runners.run_items(res, items, cfg, progress)
    except KeyboardInterrupt:
        _err("interrupted: completed records are stored; re-run to resume")
        return 130
    except Exception as e:  # noqa: BLE001 - a runtime's preflight refused the run
        _err(f"refused: {e}")
        return 2
    for aid, reason in tally.skipped_items:
        _err(f"skipped {aid}: {reason}")
    print(f"{res.name}: {_line(tally)}")
    return 1 if tally.error else 0


def cmd_one(args: argparse.Namespace) -> int:
    res = _resolve(args.candidate)
    if res is None:
        return 2
    cfg = config.load()
    items = read_items(args.items) if args.items.exists() else []
    try:
        rec = runners.label_one(res, args.asset_id, items, cfg)
    except (LookupError, runners.Skipped) as e:
        _err(str(e))
        return 3
    except Exception as e:  # noqa: BLE001 - a runtime's preflight refused the run
        _err(f"refused: {e}")
        return 2
    print(json.dumps(rec, ensure_ascii=False))
    return 0


def cmd_replay(args: argparse.Namespace) -> int:
    res = _resolve(args.candidate)
    if res is None:
        return 2
    cfg = config.load()
    from shoebox import store

    old_dir = store.store_dir(cfg, res.model_id, args.from_hash, res.kind)
    if not (old_dir / "params.json").exists():
        _err(f"no store at {old_dir}")
        return 3
    try:
        counts = runners.replay(res, old_dir, cfg)
    except ValueError as e:
        _err(f"refused: {e}")
        return 2
    print(json.dumps({"candidate": res.name, "from": args.from_hash, "to": res.params_hash, **counts}))
    return 0


def cmd_resolve(args: argparse.Namespace) -> int:
    res = _resolve(args.candidate)
    if res is None:
        return 2
    print(json.dumps({"candidate": res.name, "model_id": res.model_id, "params_hash": res.params_hash,
                      "store_dir": str(res.store_dir(config.load()))}))
    return 0


def cmd_list(args: argparse.Namespace) -> int:
    for name, cand in runners.load_candidates(runners.CONFIG_DIR).items():
        if cand.get("runtime") != "embed":  # embedders are `shoebox embed` candidates
            print(name)
    return 0
