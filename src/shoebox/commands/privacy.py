"""`shoebox privacy`: the hosted-call pre-flight (checklist, never-send, consent) and spend so far."""

import json
import sys
from collections import Counter
from pathlib import Path

from shoebox import config, privacy, worklist


def register(sub) -> None:
    p = sub.add_parser("privacy", help="hosted-call privacy pre-flight and spend")
    psub = p.add_subparsers(dest="privacy_command", required=True)

    c = psub.add_parser(
        "check", help="check evals/privacy-checklist.toml; exit 1 if any item fails"
    )
    c.add_argument(
        "--checklist",
        type=Path,
        default=None,
        help="default: evals/privacy-checklist.toml",
    )
    c.add_argument(
        "--items",
        type=Path,
        default=None,
        help="also report never-send/consent skips for these items, as the runner would",
    )
    c.add_argument(
        "--provider",
        choices=("gemini", "claude"),
        default="gemini",
        help="hosted provider the items would go to (default gemini); the checklist is Gemini's, "
        "so with claude a failing checklist is reported but does not fail the command",
    )
    c.add_argument(
        "--allowed-out",
        type=Path,
        default=None,
        help="write the allowed items as JSONL (requires --items)",
    )
    c.add_argument(
        "--all-frames",
        action="store_true",
        help="also check every asset in the latest frame of every configured source and print "
        "refused counts by reason and by restricting source (counts only, no paths or ids)",
    )
    c.set_defaults(func=cmd_check)

    s = psub.add_parser(
        "spend",
        help="cumulative hosted spend from evals/spend.jsonl against the budget",
    )
    s.set_defaults(func=cmd_spend)


def cmd_check(args) -> int:
    results = privacy.check_checklist(args.checklist)
    for r in results:
        print(f"{'PASS' if r.ok else 'FAIL'}  {r.item}: {r.detail}")
    failed = sum(not r.ok for r in results)
    provider = getattr(args, "provider", "gemini")
    allowed_out = getattr(args, "allowed_out", None)
    if allowed_out and not args.items:
        print("--allowed-out needs --items", file=sys.stderr)
        return 2
    if args.items:
        items = [
            json.loads(line)
            for line in args.items.read_text().splitlines()
            if line.strip()
        ]
        others = privacy.known_items()
        never = privacy.load_never_send()
        # The same rules the runner's preflight applies, so this report matches the run.
        elig = privacy.filter_items(
            items, config.load(), never, others=others, provider=provider
        )
        print(f"items: {elig.summary()}")
        _print_counts(
            elig.counts(),
            Counter(s.restricting_source or s.source for s in elig.skipped),
        )
        if allowed_out:
            allowed_out.parent.mkdir(parents=True, exist_ok=True)
            allowed_out.write_text("".join(json.dumps(it) + "\n" for it in elig.allowed))
            print(f"wrote {len(elig.allowed)} allowed item(s) to {allowed_out}")
        for s in elig.skipped:
            print(f"skip  {s.asset_id} {s.source} {s.relpath}: {s.reason}")
        for entry in never.unmatched([*items, *others]):
            print(
                f"WARN  never-send entry {entry!r} matched no item in {args.items} or evals/sets "
                "(paths are relative to a source root; a folder name blocks everything below it)"
            )
    if getattr(args, "all_frames", False):
        try:
            rep = worklist.frame_refusals(config.load(), provider=provider)
        except privacy.PrivacyError as e:
            print(f"frames: {e}", file=sys.stderr)
            return 1
        print(
            f"frames ({provider}): {rep['items']} location(s) of {rep['assets']} asset(s); "
            f"{rep['allowed']} allowed, {rep['items'] - rep['allowed']} refused "
            f"({rep['refused_assets']} asset(s))"
        )
        _print_counts(rep["by_reason"], rep["by_source"])
    if failed and provider != "gemini" and (args.items or getattr(args, "all_frames", False)):
        print(
            f"{failed} Gemini checklist item(s) failed (informational for --provider {provider})."
        )
        return 0
    if failed:
        print(
            f"{failed} checklist item(s) failed: hosted runs are refused.",
            file=sys.stderr,
        )
        return 1
    print("checklist confirmed.")
    return 0


def _print_counts(by_reason: dict, by_source: dict) -> None:
    """Refused counts by reason and by restricting source: counts only, never paths or asset ids."""
    for reason, n in sorted(by_reason.items()):
        print(f"  refused {reason}: {n}")
    for source, n in sorted(by_source.items(), key=lambda kv: str(kv[0])):
        print(f"  refused by source {source}: {n}")


def cmd_spend(args) -> int:
    lim = privacy.limits()
    spent = privacy.spent_total()
    print(
        f"spent ${spent:.4f} of ${lim.max_total_usd:.2f}; remaining ${lim.max_total_usd - spent:.4f}"
    )
    return 0
