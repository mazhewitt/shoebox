"""Task 8.8: every `shoebox label` command in docs/bulk-captioning.md runs, in order, via `shoebox.cli.main`,
against the fake batch transport and a fake Ollama (synthetic worklists and derived images)."""

import json
import re
import shlex

from test_bulk import capture, fake_ollama, header, wb, write_worklist  # noqa: F401 - fixtures
from test_gemini import env  # noqa: F401 - a fixture (used by b)
from test_gemini_batch import AIDS, b, ok_body, refused_body  # noqa: F401 - b is a fixture

from shoebox import config, runners
from shoebox.cli import main
from shoebox.config import REPO_ROOT

DOC = REPO_ROOT / "docs" / "bulk-captioning.md"


def documented_commands() -> list[list[str]]:
    blocks = re.findall(r"```sh\n(.*?)```", DOC.read_text(), re.DOTALL)
    return [shlex.split(line) for blk in blocks for line in blk.splitlines() if line.startswith("shoebox label ")]


def test_docs_list_the_bulk_and_fallback_commands():
    cmds = [" ".join(c[:3]) for c in documented_commands()]
    assert "shoebox label bulk" in cmds and "shoebox label fallback" in cmds
    assert any("--partial-sources" in c for c in documented_commands())


def test_documented_commands_run_against_fakes(wb, fake_ollama, capsys):  # noqa: F811
    wb.cfg.sources["takeout-jude"] = config.Source("takeout-jude", wb.tmp / "jude", "jude", False, False)
    wb.t.respond = lambda aid: refused_body() if aid == AIDS[1] else ok_body()
    both = header(("importantfiles", "takeout-jude"))
    write_worklist(wb.cfg, "all-sources", [capture(AIDS[0]), capture(AIDS[1]), capture(AIDS[2]),
                                           capture(AIDS[3], queue="local")], both)
    write_worklist(wb.cfg, "importantfiles-only", [capture(AIDS[0]), capture(AIDS[4])])

    for argv in documented_commands():
        assert argv[0] == "shoebox"
        assert main(argv[1:]) == 0, argv

    sent = [a for s in wb.t.submits for a in s]
    # all-sources sent 0,1,2 once; the resume sent nothing; importantfiles-only reused 0 and sent only 4
    assert sorted(sent) == sorted([AIDS[0], AIDS[1], AIDS[2], AIDS[4]]) and len(sent) == len(set(sent))
    assert sorted(fake_ollama) == sorted([AIDS[3], AIDS[1]])  # local queue, then the refused one
    chain = [runners.resolve(n) for n in ("gemini-batch-v2", "gemini-medium-v2", "g12b-q4-v2")]
    idx = runners.source_index(wb.cfg, runners.label_source(chain))
    assert idx[AIDS[1]]["model_id"] == "ollama/gemma4:12b" and idx[AIDS[1]]["fell_through"] == ["gemini-batch-v2"]
    records = [json.loads(p.read_text()) for p in (wb.cfg.results_dir / "bulk-runs" / "gemini-batch-v2").glob("*")]
    assert any(r.get("partial_sources", "") and r["missing_sources"] == ["takeout-jude"] for r in records)
