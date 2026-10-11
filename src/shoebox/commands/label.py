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

    bk = lsub.add_parser("bulk", help="caption a dedup worklist (guarded; reuses labels before any request)")
    bk.add_argument("--worklist", required=True,
                    help="worklist name (<shoebox_root>/worklists/<name>.jsonl) or path; never a frame")
    bk.add_argument("--candidate", required=True, help="hosted (gemini-batch, gemini) or local (ollama)")
    bk.add_argument("--partial-sources", metavar="REASON",
                    help="run a worklist that lacks a configured source; the reason is logged in the run record")
    bk.add_argument("--label-sources", metavar="A,B,...",
                    help="ordered label-source chain to reuse from (default: the candidate's label_sources; "
                         "gemini-batch-v2: gemini-batch-v2,gemini-medium-v2)")
    bk.add_argument("--limit", type=int, help="only the first N captures to send")
    bk.add_argument("--dry-run", action="store_true", help="print the counts; send nothing")
    bk.add_argument("--max-poll-rounds", type=int, help="batch: stop polling after N rounds (rerun to resume)")
    bk.set_defaults(func=cmd_bulk)

    fb = lsub.add_parser("fallback", help="local caption for refused / unrecovered truncated primary labels")
    fb.add_argument("--candidate", required=True, help="a local (ollama) candidate, e.g. g12b-q4-v2")
    fb.add_argument("--primary", required=True, metavar="A,B,...",
                    help="ordered primary chain, e.g. gemini-batch-v2,gemini-medium-v2")
    fb.add_argument("--limit", type=int)
    fb.set_defaults(func=cmd_fallback)

    ls = lsub.add_parser("list", help="list candidate names")
    ls.set_defaults(func=cmd_list)

    ig = lsub.add_parser("ingest", help="store an external candidate's labels-*.jsonl (e.g. Opus subagents)")
    ig.add_argument("--candidate", required=True, help="a runtime = \"external\" candidate")
    ig.add_argument("--dir", required=True, type=Path, help="dir of labels-*.jsonl (and map.json for keys)")
    ig.add_argument("--items", type=Path, help="refuse asset_ids not in this items JSONL")
    ig.set_defaults(func=cmd_ingest)

    pk = lsub.add_parser("packets", help="1024px thumbnails and batch files for an external labeller")
    pk.add_argument("--items", type=Path, required=True)
    pk.add_argument("--out", type=Path, required=True, help="scratch dir (never under evals/)")
    pk.add_argument("--per-batch", type=int, default=25)
    pk.add_argument("--provider", default="claude", help="hosted provider the images go to (privacy filter)")
    pk.add_argument("--prompt", default="label-v2.md", help="prompt file in config/prompts named in the batches")
    pk.set_defaults(func=cmd_packets)


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


def cmd_ingest(args: argparse.Namespace) -> int:
    from shoebox.runners import external

    res = _resolve(args.candidate)
    if res is None:
        return 2
    try:
        items = read_items(args.items) if args.items else None
        out = external.ingest(config.load(), res, args.dir, items)
    except (external.IngestError, FileNotFoundError) as e:
        _err(f"shoebox label ingest: {e}")
        return 1
    print(f"{res.name}: ok {out.ok}  invalid {out.invalid}  already {out.already} -> {res.params_hash}")
    return 0


def cmd_packets(args: argparse.Namespace) -> int:
    from shoebox import privacy
    from shoebox.runners import external

    try:
        res = external.packets(config.load(), read_items(args.items), args.out, args.per_batch, args.provider,
                               privacy.known_items(), prompt_file=runners.CONFIG_DIR / "prompts" / args.prompt)
    except (ValueError, FileNotFoundError) as e:
        _err(f"shoebox label packets: {e}")
        return 2
    for s in res.skipped:
        _err(f"skipped {s['asset_id']}: {s['reason']}")
    print(f"wrote {len(res.keys)} photos in {len(res.batches)} batches to {args.out}; {len(res.skipped)} skipped")
    return 0


def cmd_bulk(args: argparse.Namespace) -> int:
    from shoebox import bulk, privacy
    from shoebox.runners import _now

    cfg = config.load()
    path = bulk.resolve_path(cfg, args.worklist)
    res = _resolve(args.candidate)
    if res is None:
        return 2
    try:
        queue = bulk.queue_for(res)
        header, caps = bulk.load_worklist(path)
        missing = bulk.check_sources(header, cfg, args.partial_sources)
        names = bulk.chain_names(res, args.label_sources)
        plan = bulk.plan(caps, queue, bulk.chain_index(cfg, names, runners.resolve))
    except (bulk.Refused, OSError, KeyError) as e:
        _err(f"refused: {e}")
        return 2
    if missing:
        _err(f"partial sources: worklist lacks {', '.join(missing)} ({args.partial_sources})")
    print(f"{res.name}: {bulk.summary_line(plan.counts)}")
    if args.dry_run:
        return 0
    send = plan.to_send[: args.limit] if args.limit else plan.to_send
    items, unlocated = bulk.items_for(send, cfg)
    for aid in unlocated:
        _err(f"skipped {aid}: canonical asset not in the current frames (stale worklist?)")
    record = {"started": _now(), "candidate": res.name, "model_id": res.model_id, "params_hash": res.params_hash,
              "worklist": str(path), "worklist_created_at": header.get("created_at"),
              "worklist_sources": header.get("sources"), "configured_sources": sorted(cfg.sources),
              "missing_sources": missing, "partial_sources": args.partial_sources if missing else None,
              "label_sources": names, "counts": {**plan.counts, "unlocated": len(unlocated), "sent": len(items)},
              "state": "started"}
    rec_path = bulk.write_run_record(cfg, res.name, record)
    tally_doc: dict = {}
    if items and res.cand["runtime"] == "gemini-batch":
        from shoebox.runners import gemini_batch

        try:
            t = gemini_batch.run_batch(res, items, cfg, bulk.batch_transport(),
                                       max_poll_rounds=args.max_poll_rounds)
        except privacy.PrivacyError as e:
            _err(f"refused: {e}")
            bulk.write_run_record(cfg, res.name, {**record, "state": "refused", "error": str(e)}, rec_path)
            return 2
        for aid, reason in t.skipped_items:
            _err(f"skipped {aid}: {reason}")
        tally_doc = {k: getattr(t, k) for k in ("cached", "skipped", "submitted", "jobs_submitted", "jobs_resumed",
                                                 "open_jobs", "returned", "ok", "invalid", "error", "usd", "refused")}
        print(f"{res.name}: ok {t.ok}  invalid {t.invalid}  error {t.error}  cached {t.cached}  skipped {t.skipped}"
              f"  jobs {t.jobs_submitted} (+{t.jobs_resumed} resumed, {t.open_jobs} open)  ${t.usd:.4f}"
              + (f"  NOT SUBMITTED: {t.refused}" if t.refused else ""))
    elif items:
        try:
            t = runners.run_items(res, items, cfg)
        except Exception as e:  # noqa: BLE001 - a runtime's preflight refused the run
            _err(f"refused: {e}")
            bulk.write_run_record(cfg, res.name, {**record, "state": "refused", "error": str(e)}, rec_path)
            return 2
        for aid, reason in t.skipped_items:
            _err(f"skipped {aid}: {reason}")
        tally_doc = {k: getattr(t, k) for k in ("ok", "invalid", "error", "cached", "skipped", "retried", "recovered")}
        print(f"{res.name}: {_line(t)}")
    bulk.write_run_record(cfg, res.name, {**record, "state": "done", "finished": _now(), "tally": tally_doc},
                          rec_path)
    print(f"run record -> {rec_path}")
    return 1 if tally_doc.get("error") else 0


def cmd_fallback(args: argparse.Namespace) -> int:
    from shoebox import bulk

    cfg = config.load()
    res = _resolve(args.candidate)
    if res is None:
        return 2
    if res.cand["runtime"] not in bulk.LOCAL_RUNTIMES:
        _err(f"refused: the fallback must be a local candidate ({', '.join(bulk.LOCAL_RUNTIMES)}); "
             f"{res.name} is {res.cand['runtime']}")
        return 2
    primary = []
    for name in (n.strip() for n in args.primary.split(",") if n.strip()):
        r = _resolve(name)
        if r is None:
            return 2
        primary.append(r)
    selected = bulk.fallback_selection(cfg, primary)
    held = bulk.held_locally(selected, cfg)
    for aid in sorted(held):
        _err(f"skipped {aid}: quarantined for local stages")
    todo = [a for a in selected if a not in held]
    todo = todo[: args.limit] if args.limit else todo
    print(f"{res.name}: {len(selected)} refused or unrecovered record(s) in {args.primary}; {len(held)} held; "
          f"running {len(todo)}")
    t = runners.run_items(res, [{"asset_id": a} for a in todo], cfg)
    for aid, reason in t.skipped_items:
        _err(f"skipped {aid}: {reason}")
    print(f"{res.name}: {_line(t)}")
    return 1 if t.error else 0
