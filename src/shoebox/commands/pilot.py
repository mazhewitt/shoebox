"""`shoebox pilot` (stub: to be implemented)."""


def register(sub) -> None:
    p = sub.add_parser("pilot", help="(not implemented yet)")
    p.set_defaults(func=lambda args: print("not implemented") or 1)
