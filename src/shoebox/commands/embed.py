"""`shoebox embed run|search`: EmbeddingGemma 2 image/caption vectors in the store, and a local text search."""

import dataclasses
import json
import sys
from pathlib import Path

from shoebox import config, embed, store
from shoebox.config import REPO_ROOT

DEFAULT_ITEMS = REPO_ROOT / "evals" / "sets" / "pilot-v0" / "items.jsonl"


def register(sub) -> None:
    p = sub.add_parser("embed", help="EmbeddingGemma 2 embeddings and search")
    s = p.add_subparsers(dest="embed_command", required=True)

    r = s.add_parser("run", help="embed the derived images (and captions) of an items file into the store")
    r.add_argument("--candidate", default="eg2-bf16")
    r.add_argument("--items", type=Path, required=True)
    r.add_argument("--caption-from", metavar="CANDIDATE", help="also embed this label candidate's captions")
    r.set_defaults(func=cmd_run)

    q = s.add_parser("search", help="rank stored embeddings against a text query")
    q.add_argument("query")
    q.add_argument("--candidate", default="eg2-bf16")
    q.add_argument("--caption-from", metavar="CANDIDATE", help="search the run made with these captions")
    q.add_argument("--k", type=int, default=5)
    q.add_argument("--mode", choices=embed.MODES, default="image")
    q.add_argument("--dim", type=int, choices=embed.MRL_DIMS, default=embed.NATIVE_DIM,
                   help="Matryoshka truncation applied at query time")
    q.add_argument("--items", type=Path, default=DEFAULT_ITEMS, help="for relpaths in the output")
    q.set_defaults(func=cmd_search)


def resolve(candidate: str, caption_from: str | None):
    """The embed candidate, with the caption source (if any) folded into its params and params_hash."""
    from shoebox.runners import resolve as resolve_candidate

    res = resolve_candidate(candidate)
    if res.cand.get("runtime") != "embed":
        raise SystemExit(f"candidate {candidate!r} has runtime {res.cand.get('runtime')!r}, not 'embed'")
    if not caption_from:
        return res, None
    source = embed.caption_source(caption_from)
    params = {**res.params, "caption_from": source}
    ph = store.params_hash(params)
    cand = {**res.cand, "caption_from": source, "params_hash": ph}
    return dataclasses.replace(res, cand=cand, params=params, params_hash=ph), source


def read_items(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def split_by_label(cfg, res, source: dict, items: list[dict]) -> tuple[list[dict], dict[str, dict]]:
    """(items to embed now, each with its caption or None; {asset_id: item} still waiting for a caption).

    An embed record is final and its params_hash only names the caption source, not which captions existed,
    so an asset is embedded only once its label record is final (`ok` or `invalid`): until then a caption
    may still come and would never be embedded. Already-embedded assets and ones with no derived image are
    left for the framework to count as cached or skipped.
    """
    from shoebox.runners import derived_image, source_index

    labels = source_index(cfg, source)
    captions = embed.load_captions(cfg, source)
    done = store.index(cfg, res.model_id, res.params_hash, res.kind)
    profile = res.cand.get("profile", "infer-v1")
    ready, waiting = [], {}
    for it in items:
        aid = it["asset_id"]
        label = labels.get(aid)
        if (not store.is_final(done.get(aid)) and (not store.is_final(label) or label.get("awaiting_retry"))
                and derived_image(cfg, profile, aid).exists()):
            waiting[aid] = it
        else:
            ready.append({**it, "caption": captions.get(aid)})
    return ready, waiting


def cmd_run(args) -> int:
    from shoebox.runners import run_items

    cfg = config.load()
    res, source = resolve(args.candidate, args.caption_from)
    items = read_items(args.items)
    waiting: dict[str, dict] = {}
    if source:
        items, waiting = split_by_label(cfg, res, source, items)
        print(f"captions: {sum(1 for it in items if it['caption'])} from {source['model_id']} "
              f"{source['params_hash']}", file=sys.stderr)
    print(f"{res.name}: {res.model_id} params_hash={res.params_hash} runtime={res.runtime}", file=sys.stderr)
    latencies: list[int] = []

    def progress(tally, item, rec):
        if rec["status"] == "ok" and rec.get("latency_ms") is not None:
            latencies.append(rec["latency_ms"])
        print(f"[{tally.ran}] {rec['status']:7} {rec['latency_ms']:>6} ms  {item['asset_id'][:12]}  "
              f"{item.get('relpath', '')}", file=sys.stderr)

    tally = run_items(res, items, cfg, progress)
    tally.total += len(waiting)
    for aid in waiting:
        tally.skip(aid, f"label record from {source['candidate']} not final yet (run `shoebox label run` first)")
    for aid, reason in tally.skipped_items:
        print(f"skipped {aid[:12]}: {reason}", file=sys.stderr)
    mean = round(sum(latencies) / len(latencies), 1) if latencies else None
    print(json.dumps({"candidate": res.name, "model_id": res.model_id, "params_hash": res.params_hash,
                      "store_dir": str(res.store_dir(cfg)), "total": tally.total, "ok": tally.ok,
                      "invalid": tally.invalid, "error": tally.error, "cached": tally.cached,
                      "skipped": tally.skipped, "mean_latency_ms": mean}))
    return 1 if tally.error else 0


def cmd_search(args) -> int:
    cfg = config.load()
    res, _ = resolve(args.candidate, args.caption_from)
    records = store.index(cfg, res.model_id, res.params_hash, res.kind)
    ids, img, cap, has_cap = embed.search_matrix(records, args.dim)
    if not ids:
        print(f"no embeddings in {res.store_dir(cfg)}; run `shoebox embed run` first", file=sys.stderr)
        return 1
    if args.mode != "image" and not has_cap.any():
        print("no caption embeddings in this run; use --caption-from or --mode image", file=sys.stderr)
        return 1
    q = embed.embed_query(res.cand, args.query, args.dim)
    relpaths = {it["asset_id"]: it.get("relpath", "") for it in read_items(args.items)} \
        if args.items.exists() else {}
    hits = embed.rank(q, ids, img, cap, has_cap, args.mode, args.k)
    print(f"# {res.name} {res.params_hash} mode={args.mode} dim={args.dim} n={len(ids)}")
    for i, h in enumerate(hits, 1):
        print(f"{i:>3}  {h.score:.4f}  {h.asset_id}  {relpaths.get(h.asset_id, '-')}")
    return 0
