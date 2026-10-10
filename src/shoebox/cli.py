"""`shoebox` command line. Each subcommand lives in `shoebox.commands.<name>` and exposes `register(subparsers)`."""

import argparse
import importlib
import sys

COMMANDS = ["pilot", "derive", "label", "embed", "privacy", "eval"]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="shoebox", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    for name in COMMANDS:
        importlib.import_module(f"shoebox.commands.{name}").register(sub)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args) or 0


if __name__ == "__main__":
    sys.exit(main())
