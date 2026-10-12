"""An eval set through `gemini-batch-v2` (pool5k confirmation, `.scratch/bulk-captioning/02`). Eval sets are
exempt from the worklist guard (docs/bulk-captioning.md); every item still passes `privacy.filter_items`.

    uv run python tools/eval_batch.py --items evals/sets/pilot-v0-pool5k/items.jsonl --quote
    uv run python tools/eval_batch.py --items evals/sets/pilot-v0-pool5k/items.jsonl   # submit / resume

Prints counts only. The candidate's `max_usd_per_run` caps one invocation; rerun to continue past it.
"""

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

from shoebox import bulk, config, privacy, runners, store, worklist
from shoebox.runners import gemini_batch

CANDIDATE = "gemini-batch-v2"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--items", type=Path, required=True)
    ap.add_argument("--quote", action="store_true", help="print the eligible count and the batch-price quote only")
    ap.add_argument("--max-poll-rounds", type=int, default=None)
    args = ap.parse_args(argv)
    cfg = config.load()
    res = runners.resolve(CANDIDATE)
    items = [json.loads(line) for line in args.items.read_text().splitlines() if line]
    el = privacy.filter_items(items, cfg, provider="gemini")
    done = store.index(cfg, res.model_id, res.params_hash, res.kind)
    todo = [it for it in el.allowed if not store.is_final(done.get(it["asset_id"]))]
    usd, note = worklist.batch_usd(res.cand, len(todo))
    print(json.dumps({"items": len(items), "eligible": len(el.allowed), "refused": dict(Counter(s.reason for s in el.skipped)),
                      "already_final": len(el.allowed) - len(todo), "to_send": len(todo), "quote_usd": round(usd, 4),
                      "quote_basis": note}))
    if args.quote or not todo:
        return 0
    t = gemini_batch.run_batch(res, el.allowed, cfg, bulk.batch_transport(), max_poll_rounds=args.max_poll_rounds)
    print(json.dumps({k: getattr(t, k) for k in ("cached", "skipped", "submitted", "jobs_submitted", "jobs_resumed",
                                                 "open_jobs", "ok", "invalid", "error", "usd", "refused")}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
