"""`shoebox dedup`: groups and captures stores (6.1/6.2) and the caption-once worklist (6.4)."""

import sys
from datetime import date
from pathlib import Path

from shoebox import config, dedup, privacy, worklist
from shoebox.config import REPO_ROOT


def _candidate(name: str) -> dict:
    from shoebox.runners import load_candidates

    return load_candidates()[name]


def register(sub) -> None:
    p = sub.add_parser("dedup", help="dedup groups, captures and the caption worklist")
    dsub = p.add_subparsers(dest="dedup_command", required=True)
    for name, fn, help_ in (("groups", cmd_groups, "write exact/same_pixels groups to results/dedup-groups/"),
                            ("captures", cmd_captures, "write captures to results/capture/")):
        g = dsub.add_parser(name, help=help_)
        g.add_argument("--profile", default="infer-v1", help="derive profile of the pixel-hash store")
        g.set_defaults(func=fn)

    w = dsub.add_parser("worklist", help="one row per capture -> <shoebox_root>/worklists/<name>.jsonl, "
                        "summary -> evals/runs/<date>_worklist-<name>.md")
    w.add_argument("--provider", choices=("gemini", "claude"), default="gemini")
    w.add_argument("--candidate", default="gemini-batch-v2",
                   help="candidate whose prices (incl. price_batch_discount) re-quote the cost")
    w.add_argument("--name", default=None, help="default: <date>_<provider>")
    w.add_argument("--profile", default="infer-v1", help="derive profile (pixel hashes, derived images)")
    w.add_argument("--runs-dir", type=Path, default=REPO_ROOT / "evals" / "runs")
    w.set_defaults(func=cmd_worklist)


def _write_store(args, kind: str) -> int:
    cfg = config.load()
    try:
        inp = worklist.load_inputs(cfg, args.profile)
    except privacy.PrivacyError as e:
        print(e, file=sys.stderr)
        return 2
    if kind == "groups":
        groups = dedup.build_groups(inp.rows, inp.pixels)
        d = dedup.write_groups(cfg, groups, dedup.groups_params(inp.rows, inp.pixels, inp.pixel_key))
        print(f"{len(groups)} group(s) -> {d}")
    else:
        never = privacy.load_never_send()

        def consented(source: str, relpath: str) -> bool:
            item = {"source": source, "relpath": relpath}
            return privacy._own_restriction(item, cfg, never) is None

        caps = dedup.captures(inp.rows, inp.pixels, consented)
        d = dedup.write_captures(cfg, caps, dedup.capture_params(inp.rows, inp.pixels, inp.pixel_key))
        print(f"{len(caps)} capture(s) -> {d}")
    return 0


def cmd_groups(args) -> int:
    return _write_store(args, "groups")


def cmd_captures(args) -> int:
    return _write_store(args, "captures")


def cmd_worklist(args) -> int:
    cfg = config.load()
    cand = _candidate(args.candidate)
    try:
        wl = worklist.build(cfg, provider=args.provider, profile=args.profile)
    except privacy.PrivacyError as e:
        print(e, file=sys.stderr)
        return 2
    day = date.today().isoformat()
    name = args.name or f"{day}_{args.provider}"
    path = worklist.write(cfg, wl, name)
    args.runs_dir.mkdir(parents=True, exist_ok=True)
    md = args.runs_dir / f"{day}_worklist-{name}.md"
    md.write_text(worklist.summary_md(wl, name, args.candidate, cand))
    n = wl.counts
    print(f"{n['captures']} capture(s): hosted {n['hosted']}, local {n['local']}, held {n['held']}, "
          f"not_derived {n['not_derived']}, unscreened {n['unscreened']} -> {path}")
    print(f"summary -> {md}")
    return 0
