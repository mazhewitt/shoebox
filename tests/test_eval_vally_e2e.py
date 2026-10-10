"""End to end with the real Vally CLI and synthetic data: gen -> lint -> eval (replay) -> label -> grade -> report."""

import json
import os
import shutil
import subprocess
import sys

import pytest
from test_eval_helpers import make_world, write_jsonl

from shoebox.config import REPO_ROOT

VALLY_DIR = REPO_ROOT / "evals" / "vally"
VALLY = VALLY_DIR / "node_modules" / ".bin" / "vally"
EXECUTOR = VALLY_DIR / "plugins" / "shoebox-vlm.js"
GRADERS = VALLY_DIR / "plugins" / "shoebox-graders.js"
pytestmark = pytest.mark.skipif(
    shutil.which("node") is None or not VALLY.exists(),
    reason="evals/vally/node_modules missing (npm ci)",
)


def run(cmd, env, stdin=None, check=True):
    proc = subprocess.run(
        [str(c) for c in cmd],
        env=env,
        capture_output=True,
        text=True,
        stdin=stdin,
        check=False,
        cwd=VALLY_DIR,
    )
    if check:
        assert proc.returncode == 0, (
            f"{cmd}\n{proc.stdout[-3000:]}\n{proc.stderr[-3000:]}"
        )
    return proc


def shoebox(*args):
    return [sys.executable, "-m", "shoebox.cli", *args]


def results_file(out_dir):
    (path,) = list(out_dir.glob("*/results.jsonl"))
    return path


def trials(path):
    rows = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    return [r for r in rows if r.get("type") != "run-summary"]


def verdicts(path):
    """{(candidate, asset_id): {grader name: passed}}"""
    out = {}
    for r in trials(path):
        t = r.get("trajectory") or {}
        key = (
            r.get("model") or t["metadata"]["model"],
            r.get("stimulus") or t["stimulus"]["name"],
        )
        out[key] = {
            d["name"]: d["passed"]
            for d in (r.get("gradeResult") or {}).get("details", [])
        }
    return out


def test_vally_replay_regrade_report(tmp_path):
    world = make_world(tmp_path)
    env = {
        **os.environ,
        "SHOEBOX_CONFIG": str(world["cfg_path"]),
        "VALLY_TELEMETRY_OPTOUT": "1",
    }
    gen_dir = tmp_path / "generated"
    gen_args = [
        "eval", "gen", "--set", "pilot-v0", "--candidates", "candA,candB", "--sets-dir", world["sets"],
        "--out-dir", gen_dir, "--resolutions", world["resolutions"],
    ]  # fmt: skip
    run(shoebox(*gen_args), env)
    spec = gen_dir / "eval.yaml"

    run([VALLY, "lint", "-e", spec, "--grader-plugin", GRADERS, "--strict"], env)

    out = tmp_path / "vally-out"
    run(
        [VALLY, "eval", "-e", spec, "--executor-plugin", EXECUTOR, "--grader-plugin", GRADERS,
         "--model", "candA,candB", "--output-dir", out, "--workers", "2"],
        env,
        check=False,  # candB has an invalid record, so the Vally verdict fails; trials still all ran
    )  # fmt: skip
    res = results_file(out)
    rows = trials(res)
    assert len(rows) == 6 and all(r["status"] == "success" for r in rows)
    before = verdicts(res)
    a0 = world["ok_ids"][0]
    assert (
        before[("candB", a0)]["field-match medium"] is True
    )  # unlabelled: passes, not scored
    assert (
        before[("candB", world["ok_ids"][2])]["label-status"] is False
    )  # invalid counts as wrong
    ev = next(e for e in rows[0]["trajectory"]["events"] if e["type"] == "custom")[
        "data"
    ]["shoebox"]
    assert {
        "asset_id",
        "model_id",
        "params_hash",
        "status",
        "usd",
        "latency_ms",
    } <= set(ev)

    # Owner labels arrive: regenerate and re-grade with no executor (the stores are moved away to prove it).
    write_jsonl(
        world["sets"] / "pilot-v0" / "labels.jsonl",
        [
            {
                "asset_id": a0,
                "medium": "digital",
                "digitiser": "camera",
                "colour_class": "colour",
                "notes": "",
            }
        ],
    )
    run(shoebox(*gen_args), env)
    shutil.move(world["root"] / "results", tmp_path / "results-moved")
    with open(res) as fin:
        g = run(
            [
                VALLY,
                "grade",
                "-e",
                spec,
                "--grader-plugin",
                GRADERS,
                "--output",
                "jsonl",
            ],
            env,
            stdin=fin,
            check=False,
        )
    regraded = tmp_path / "regraded.jsonl"
    regraded.write_text(g.stdout)
    after = verdicts(regraded)
    assert len(after) == 6
    assert (
        after[("candB", a0)]["field-match medium"] is False
    )  # predicted print, label digital
    assert after[("candA", a0)]["field-match medium"] is True

    runs = tmp_path / "runs"
    run(
        shoebox(
            "eval",
            "report",
            "--results",
            regraded,
            "--date",
            "2026-10-10",
            "--out-dir",
            runs,
        ),
        env,
    )
    rep = json.loads((runs / "2026-10-10_pilot-v0.json").read_text())
    a, b = rep["candidates"]["candA"], rep["candidates"]["candB"]
    assert (
        a["fields"]["medium"]["labelled_n"] == 1
        and a["fields"]["medium"]["accuracy"] == 1.0
    )
    assert b["fields"]["medium"]["accuracy"] == 0.0
    assert b["status_counts"]["invalid"] == 1 and b["validity_rate"] == pytest.approx(
        2 / 3
    )
    assert b["model_id"] == "ollama/fake:1" and b["params_hash"] == "pb"
    assert rep["agreement"]["medium"]["candA"]["candB"]["n"] == 2
    # the world has one item whose derive failed: gen skipped it, so the run cannot pass a gate
    assert rep["set"] == "pilot-v0" and rep["set_items"]["n"] == 4
    assert rep["set_items"]["skipped"] == {world["ids"][-1]: "no ok derived image"}
    assert a["not_run"] == [world["ids"][-1]]
    assert rep["gate_eligible"] is False
    assert rep["gate_blockers"] == [
        "1 set items skipped by `shoebox eval gen` (no stimulus)",
        "candA: no trial for 1 of 4 set items",
        "candB: no trial for 1 of 4 set items",
    ]


def test_vally_replay_missing_key_errors(tmp_path):
    world = make_world(tmp_path)
    env = {
        **os.environ,
        "SHOEBOX_CONFIG": str(world["cfg_path"]),
        "VALLY_TELEMETRY_OPTOUT": "1",
    }
    gen_dir = tmp_path / "generated"
    run(
        shoebox("eval", "gen", "--set", "pilot-v0", "--candidates", "candC", "--sets-dir", world["sets"],
                "--out-dir", gen_dir, "--resolutions", world["resolutions"]),
        env,
    )  # fmt: skip
    out = tmp_path / "vally-out"
    p = run(
        [VALLY, "eval", "-e", gen_dir / "eval.yaml", "--executor-plugin", EXECUTOR, "--grader-plugin", GRADERS,
         "--model", "candC", "--output-dir", out],
        env,
        check=False,
    )  # fmt: skip
    assert p.returncode != 0
    rows = trials(results_file(out))
    assert len(rows) == 3
    assert all(r["status"] == "error" and "missing key" in r["error"] for r in rows)
    runs = tmp_path / "runs"
    run(
        shoebox(
            "eval",
            "report",
            "--results",
            results_file(out),
            "--date",
            "2026-10-10",
            "--out-dir",
            runs,
        ),
        env,
    )
    rep = json.loads((runs / "2026-10-10_pilot-v0.json").read_text())
    assert rep["candidates"]["candC"]["status_counts"]["missing"] == 3
    assert rep["gate_eligible"] is False
    # set and candidates.json are recovered from the error messages (errored trials have no trajectory)
    assert rep["set"] == "pilot-v0" and rep["set_items"]["n"] == 4


def test_vally_live_writes_through_then_replay_matches(tmp_path):
    """Live mode runs `uv run shoebox label one ...`. A fake `uv` stands in for the label runner and writes
    through with the real `shoebox.store.append`; a later replay run must return the same outputs."""
    world = make_world(tmp_path)
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    calls = tmp_path / "calls.txt"
    fake = fake_bin / "uv"
    fake.write_text(
        f"#!{sys.executable}\n"
        "import json, os, sys\n"
        "from shoebox import config, store\n"
        f"open({str(calls)!r}, 'a').write(' '.join(sys.argv[1:]) + '\\n')\n"
        "aid = sys.argv[sys.argv.index('--asset-id') + 1]\n"
        "out = {'caption': 'live ' + aid[:8], 'tags': [], 'medium': 'digital', 'digitiser': 'camera',\n"
        "       'colour_class': 'mono', 'confidence': {'medium': 1, 'digitiser': 1, 'colour_class': 1}}\n"
        "rec = {'asset_id': aid, 'model_id': 'rules-v1', 'params_hash': 'pc', 'status': 'ok', 'output': out,\n"
        "       'usage': {'input_tokens': 1, 'output_tokens': 1}, 'latency_ms': 5, 'usd': 0}\n"
        "store.append(config.load(os.environ['SHOEBOX_CONFIG']), rec)\n"
        "print(json.dumps(rec))\n"
    )
    fake.chmod(0o755)
    env = {
        **os.environ,
        "PATH": f"{fake_bin}{os.pathsep}{os.environ['PATH']}",
        "SHOEBOX_CONFIG": str(world["cfg_path"]),
        "VALLY_TELEMETRY_OPTOUT": "1",
    }

    def gen_and_run(mode):
        gen_dir = tmp_path / f"generated-{mode}"
        run(
            shoebox("eval", "gen", "--set", "pilot-v0", "--candidates", "candC", "--mode", mode, "--sets-dir",
                    world["sets"], "--out-dir", gen_dir, "--resolutions", world["resolutions"]),
            env,
        )  # fmt: skip
        out = tmp_path / f"vally-out-{mode}"
        run(
            [VALLY, "eval", "-e", gen_dir / "eval.yaml", "--executor-plugin", EXECUTOR, "--grader-plugin",
             GRADERS, "--model", "candC", "--output-dir", out, "--workers", "1"],
            env,
        )  # fmt: skip
        rows = trials(results_file(out))
        assert len(rows) == 3 and all(r["status"] == "success" for r in rows)
        return {r["stimulus"]: r["trajectory"] for r in rows}

    live = gen_and_run("live")
    ev = next(e for e in next(iter(live.values()))["events"] if e["type"] == "custom")[
        "data"
    ]["shoebox"]
    assert ev["mode"] == "live" and ev["status"] == "ok"
    lines = calls.read_text().splitlines()
    assert len(lines) == 3 and all(
        ln.startswith("run shoebox label one --candidate candC --asset-id ")
        for ln in lines
    )
    stored = (
        (world["root"] / "results/labels/rules-v1/pc/part-0000.jsonl")
        .read_text()
        .splitlines()
    )
    assert len(stored) == 3  # written through under (asset_id, rules-v1, pc)

    replay = gen_and_run("replay")
    assert len(calls.read_text().splitlines()) == 3  # replay spawned nothing
    assert set(replay) == set(live) == set(world["ok_ids"])
    for aid in live:
        assert json.loads(replay[aid]["output"]) == json.loads(live[aid]["output"])
        assert json.loads(replay[aid]["output"])["caption"] == "live " + aid[:8]
