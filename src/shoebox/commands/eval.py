"""`shoebox eval` (stub: to be implemented)."""


def register(sub) -> None:
    p = sub.add_parser("eval", help="(not implemented yet)")
    p.set_defaults(func=lambda args: print("not implemented") or 1)
