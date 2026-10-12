"""Task 8.6 live smoke batch: N eval-1k photos through `gemini-batch-v2` (owner-approved spend, about $0.03).

Picks a seeded N of the eval-1k items that have an ok `gemini-medium-v2` record and pass the privacy filter, and
runs them through the real batch runner and HTTP transport. Prints counts only; rerun to resume polling.

    uv run python tools/smoke_batch.py [--n 20] [--seed 20261012] [--max-poll-rounds R]
"""

import argparse
import hashlib
import json
import sys
from pathlib import Path

from shoebox import bulk, config, privacy, runners, store
from shoebox.runners import gemini_batch

ROOT = Path(__file__).resolve().parents[1]
ITEMS = ROOT / "evals" / "sets" / "eval-1k" / "items.jsonl"
SYNC = "gemini-medium-v2"


def pick(cfg, n: int, seed: int) -> list[dict]:
    items = [json.loads(line) for line in ITEMS.read_text().splitlines() if line]
    sync = runners.resolve(SYNC)
    have = store.index(cfg, sync.model_id, sync.params_hash, sync.kind)
    items = [it for it in items if store.is_final(have.get(it["asset_id"])) and have[it["asset_id"]]["status"] == "ok"]
    items.sort(key=lambda it: hashlib.sha256(f"{seed}:{it['asset_id']}".encode()).hexdigest())
    return privacy.filter_items(items[: n * 3], cfg, provider="gemini").allowed[:n]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--n", type=int, default=20)
    ap.add_argument("--seed", type=int, default=20261012)
    ap.add_argument("--max-poll-rounds", type=int, default=None)
    args = ap.parse_args(argv)
    cfg = config.load()
    res = runners.resolve("gemini-batch-v2")
    items = pick(cfg, args.n, args.seed)
    if len(items) != args.n:
        print(f"only {len(items)} eligible items", file=sys.stderr)
        return 1
    t = gemini_batch.run_batch(res, items, cfg, bulk.batch_transport(), max_poll_rounds=args.max_poll_rounds)
    print(json.dumps({"items": len(items), **{k: getattr(t, k) for k in (
        "cached", "skipped", "submitted", "jobs_submitted", "jobs_resumed", "open_jobs", "ok", "invalid", "error",
        "usd", "refused")}}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
