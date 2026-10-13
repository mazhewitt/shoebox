"""An items file through `opus-api-v2` (Anthropic Message Batches, runners/anthropic_batch.py). Every item
passes `privacy.filter_items(provider="claude")`; spend counts against the Anthropic budget only.

    sops exec-env secrets/shoebox.sops.yaml 'uv run python tools/claude_batch.py --items <items.jsonl> --quote'
    sops exec-env secrets/shoebox.sops.yaml 'uv run python tools/claude_batch.py --items <items.jsonl>'

Prints counts only. The candidate's `max_usd_per_run` caps one invocation; rerun to continue past it or to
resume open batches (`--max-poll-rounds N` returns early and leaves them open).
"""

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

from shoebox import config, privacy, runners, store, worklist
from shoebox.runners import anthropic_batch

CANDIDATE = "opus-api-v2"


def main(argv=None, transport=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--items", type=Path, required=True)
    ap.add_argument("--candidate", default=CANDIDATE)
    ap.add_argument("--quote", action="store_true", help="print the eligible count and the batch-price quote only")
    ap.add_argument("--max-poll-rounds", type=int, default=None)
    args = ap.parse_args(argv)
    cfg = config.load()
    res = runners.resolve(args.candidate)
    if res.cand.get("runtime") != "anthropic-batch":
        ap.error(f"candidate {res.name} has runtime {res.cand.get('runtime')}, not anthropic-batch")
    items = [json.loads(line) for line in args.items.read_text().splitlines() if line]
    el = privacy.filter_items(items, cfg, provider=anthropic_batch.PROVIDER)
    done = store.index(cfg, res.model_id, res.params_hash, res.kind)
    todo = [it for it in el.allowed if not store.is_final(done.get(it["asset_id"]))]
    usd, _ = worklist.batch_usd(res.cand, len(todo))
    provider = res.cand.get("budget_provider", privacy.PROJECT_PROVIDER)
    print(json.dumps({"items": len(items), "eligible": len(el.allowed), "refused": dict(Counter(s.reason for s in el.skipped)),
                      "already_final": len(el.allowed) - len(todo), "to_send": len(todo), "quote_usd": round(usd, 4),
                      "run_cap_usd": res.cand.get("max_usd_per_run"), "budget_provider": provider,
                      "budget_spent_usd": round(privacy.spent_total(provider=provider), 4),
                      "budget_usd": privacy.limits().budget(provider)}))
    if args.quote or not todo:
        return 0
    t = anthropic_batch.run_batch(res, el.allowed, cfg, transport or anthropic_batch.HttpTransport(),
                                  max_poll_rounds=args.max_poll_rounds)
    print(json.dumps({k: getattr(t, k) for k in ("cached", "skipped", "submitted", "jobs_submitted", "jobs_resumed",
                                                 "open_jobs", "returned", "ok", "invalid", "error", "usd", "refused")}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
