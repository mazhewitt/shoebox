"""`shoebox privacy` (stub: to be implemented)."""


def register(sub) -> None:
    p = sub.add_parser("privacy", help="(not implemented yet)")
    p.set_defaults(func=lambda args: print("not implemented") or 1)
