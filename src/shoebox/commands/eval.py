"""`shoebox eval`: generate Vally specs from a set (`gen`) and report on Vally results (`report`)."""

import json
import os
import sys
from pathlib import Path

from shoebox import config
from shoebox.config import REPO_ROOT


def register(sub) -> None:
    p = sub.add_parser(
        "eval", help="generate Vally eval specs and report on Vally results"
    )
    esub = p.add_subparsers(dest="eval_command", required=True)

    g = esub.add_parser(
        "gen", help="write evals/vally/generated/<set>/eval.yaml and candidates.json"
    )
    g.add_argument(
        "--set",
        required=True,
        dest="set_name",
        help="set name under evals/sets/, e.g. pilot-v0",
    )
    g.add_argument(
        "--candidates",
        required=True,
        help="comma-separated candidate names (config/candidates.toml)",
    )
    g.add_argument(
        "--mode",
        choices=["replay", "live"],
        default="replay",
        help="executor mode (gate runs: replay)",
    )
    g.add_argument("--sets-dir", type=Path, default=REPO_ROOT / "evals" / "sets")
    g.add_argument(
        "--out-dir", type=Path, default=None, help="default evals/vally/generated/<set>"
    )
    g.add_argument(
        "--resolutions",
        type=Path,
        default=None,
        help="JSON {candidate: {model_id, params_hash, store_dir}} used instead of `shoebox label resolve` "
        "(offline/tests)",
    )
    g.set_defaults(func=_gen)

    r = esub.add_parser(
        "report",
        help="write evals/runs/<date>_<set>.json and .md from a Vally results.jsonl",
    )
    r.add_argument(
        "--results",
        required=True,
        type=Path,
        help="results.jsonl from `vally eval` or `vally grade`",
    )
    r.add_argument(
        "--set",
        dest="set_name",
        default=None,
        help="expected set (default: the set recorded in the trials; must agree with it)",
    )
    r.add_argument(
        "--candidates-file",
        type=Path,
        default=None,
        help="candidates.json listing the set's items (default: the one recorded in the trials)",
    )
    r.add_argument("--date", default=None, help="run date YYYY-MM-DD (default today)")
    r.add_argument("--out-dir", type=Path, default=REPO_ROOT / "evals" / "runs")
    r.set_defaults(func=_report)


def _gen(args) -> int:
    from shoebox.evals import gen

    kwargs = {}
    if args.resolutions:
        table = json.loads(args.resolutions.read_text())

        def resolver(name: str) -> dict:
            if name not in table:
                raise KeyError(f"candidate {name} not in {args.resolutions}")
            return table[name]

        kwargs["resolver"] = resolver
    names = [c.strip() for c in args.candidates.split(",") if c.strip()]
    res = gen.generate(
        config.load(Path(os.environ.get("SHOEBOX_CONFIG", config.CONFIG_PATH))),
        args.set_name,
        names,
        mode=args.mode,
        sets_dir=args.sets_dir,
        out_dir=args.out_dir,
        **kwargs,
    )
    print(
        f"wrote {res.spec_path} ({res.stimuli} stimuli, {len(res.skipped_items)} items skipped)"
    )
    print(f"wrote {res.candidates_path}")
    return 0 if res.stimuli else 1


def _report(args) -> int:
    from shoebox.evals import report

    if not args.results.exists():
        print(f"no such results file: {args.results}", file=sys.stderr)
        return 2
    try:
        jpath, mpath, _ = report.write_report(
            args.results, args.set_name, args.out_dir, args.date, args.candidates_file
        )
    except ValueError as e:
        print(f"shoebox eval report: {e}", file=sys.stderr)
        return 2
    print(f"wrote {jpath}")
    print(f"wrote {mpath}")
    return 0
