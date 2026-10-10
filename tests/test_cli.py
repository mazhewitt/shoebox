from shoebox.cli import build_parser


def test_parser_builds():
    assert build_parser().prog == "shoebox"
