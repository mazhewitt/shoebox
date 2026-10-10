"""`shoebox label` (stub: to be implemented)."""


def register(sub) -> None:
    p = sub.add_parser("label", help="(not implemented yet)")
    p.set_defaults(func=lambda args: print("not implemented") or 1)
