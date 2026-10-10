"""The search executor and `retrieval` grader through node, and a Vally replay end to end on synthetic data:
search-queries -> search-run (fake vectors) -> gen-search -> lint -> eval -> grade -> search-report."""

import json
import os
import shutil
import subprocess
import sys

import pytest
from test_eval_helpers import write_jsonl
from test_eval_search import fake_cfg, unit

from shoebox.config import REPO_ROOT
from shoebox.evals import search

VALLY_DIR = REPO_ROOT / "evals" / "vally"
VALLY = VALLY_DIR / "node_modules" / ".bin" / "vally"
EXECUTOR = VALLY_DIR / "plugins" / "shoebox-search.js"
GRADERS = VALLY_DIR / "plugins" / "shoebox-graders.js"
pytestmark = pytest.mark.skipif(
    shutil.which("node") is None or not VALLY.exists(),
    reason="node or evals/vally/node_modules missing (run npm ci in evals/vally)",
)


def node(script: str):
    proc = subprocess.run(["node", "--input-type=module", "-e", script], cwd=VALLY_DIR, capture_output=True,
                          text=True, check=False)
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


def run(cmd, env, stdin=None, check=True):
    proc = subprocess.run([str(c) for c in cmd], env=env, capture_output=True, text=True, stdin=stdin,
                          check=False, cwd=VALLY_DIR)
    if check:
        assert proc.returncode == 0, f"{cmd}\n{proc.stdout[-3000:]}\n{proc.stderr[-3000:]}"
    return proc


def shoebox(*args):
    return [sys.executable, "-m", "shoebox.cli", *args]


TIE = 1 / 61 + 1 / 62
CASES = [  # (ranking, relevant, scores, groups)
    (["x", "a", "y", "b", "z"], ["a", "b"], None, None),
    (["a"], ["a"], None, None),
    (["x", "y"], ["a"], None, None),
    ([f"x{i}" for i in range(8)] + ["a", "b"], ["b", "a"], None, None),
    # RRF-style exact tie at the top, the distractor stored first
    (["x", "a", "c"], ["a"], [TIE, 1 / 62 + 1 / 61, 0.01], None),
    # relevant in the unscored tail
    (["x", "y", "p", "a", "q", "r", "s"], ["a"], [3.0, 1.5, None, None, None, None, None], None),
    # a pair as one target, sibling in a tie block
    (["a1", "x", "a2", "y"] + [f"z{i}" for i in range(20)], ["a1", "a2"], [0.9, 0.5, 0.5, 0.5] + [0.1] * 20,
     [["a1", "a2"]]),
    # two groups (merged note), tied
    (["x", "c1", "d1", "c2", "y"], ["c1", "c2", "d1"], [0.9, 0.8, 0.8, 0.8, 0.8], [["c1", "c2"], ["d1"]]),
]


def test_retrieval_grader_matches_python_metrics():
    script = f"""
import {{ registerGraders, retrievalMetrics }} from "./plugins/shoebox-graders.js";
const g = {{}};
registerGraders({{ register: (x) => (g[x.metadata.name] = x) }});
const cases = {json.dumps(CASES)};
const out = [];
for (const [ranking, relevant, scores, groups] of cases) {{
  const trajectory = {{ output: JSON.stringify({{ query_id: "q", route: "r", ranking, scores }}) }};
  const config = {{ relevant, k: [1, 5, 10, 20] }};
  if (groups) config.groups = groups;
  out.push(await g["retrieval"].grade({{ trajectory, config }}));
}}
out.push(await g["retrieval"].grade({{ trajectory: {{ output: "nope" }}, config: {{ relevant: ["a"] }} }}));
let threw = false;
try {{ await g["retrieval"].grade({{ trajectory: {{ output: "{{}}" }}, config: {{}} }}); }} catch {{ threw = true; }}
console.log(JSON.stringify({{ out, threw }}));
"""
    res = node(script)
    assert res["threw"] is True
    *graded, unparsed = res["out"]
    for (ranking, relevant, scores, groups), g in zip(CASES, graded, strict=True):
        py = search.query_metrics(ranking, relevant, scores=scores, groups=groups)
        md = g["metadata"]
        for k, v in py.items():
            assert md[k] == pytest.approx(v, abs=1e-12), k
        assert g["passed"] is (py["hit@5"] == 1.0) and g["score"] == pytest.approx(py["hit@5"])
    assert graded[4]["metadata"]["tie_decided"] is True and graded[4]["passed"] is True
    assert graded[5]["metadata"]["hit@5"] == pytest.approx(3 / 5) and graded[5]["passed"] is False
    assert unparsed["passed"] is False and unparsed["metadata"]["ranked"] is False


def test_executor_load_rankings_skips_torn_lines(tmp_path):
    p = tmp_path / "r.jsonl"
    p.write_text('{"query_id": "q1", "route": "r", "ranking": ["a"]}\n{"query_id": "q2", "ran\n'
                 '{"query_id": "q1", "route": "r", "ranking": ["b"]}\n')
    script = f"""
import {{ loadRankings }} from "./plugins/shoebox-search.js";
const m = loadRankings({json.dumps(str(p))});
console.log(JSON.stringify({{ keys: [...m.keys()], q1: m.get("q1").ranking, torn: m.torn }}));
"""
    assert node(script) == {"keys": ["q1"], "q1": ["a"], "torn": 1}


def _world(tmp_path):
    cfg, cfg_path = fake_cfg(tmp_path)
    sets = tmp_path / "sets"
    items = [{"asset_id": a, "group_id": g} for a, g in (("a1", "g1"), ("a2", "g1"), ("b1", None), ("c1", None))]
    write_jsonl(sets / "s" / "items.jsonl", items)
    write_jsonl(sets / "s" / "labels.jsonl", [
        {"asset_id": "a1", "notes": "bridge over canal"}, {"asset_id": "a2", "notes": "bridge over canal"},
        {"asset_id": "b1", "notes": "red rose"}, {"asset_id": "c1", "notes": "small dog"}])
    return cfg, cfg_path, sets


def test_vally_search_replay_regrade_report(tmp_path):
    cfg, cfg_path, sets = _world(tmp_path)
    env = {**os.environ, "SHOEBOX_CONFIG": str(cfg_path), "VALLY_TELEMETRY_OPTOUT": "1"}
    run(shoebox("eval", "search-queries", "--set", "s", "--sets-dir", sets), env)
    qpath = sets / "s" / search.QUERIES_FILE
    queries = search.read_jsonl(qpath)
    assert [q["relevant"] for q in queries] == [["a1", "a2"], ["b1"], ["c1"]]

    # search-run with fake vectors (the real one embeds queries with the local EG2)
    qvec = {"bridge over canal": unit((0, 1.0)), "red rose": unit((1, 1.0)), "small dog": unit((3, 1.0))}
    image = {"a1": unit((0, 1.0)), "a2": unit((0, 0.5), (1, 0.5)), "b1": unit((3, 1.0)), "c1": unit((1, 1.0))}
    src = search.Sources("s", ["a1", "a2", "b1", "c1"], queries, search.file_sha256(qpath), lambda t: qvec[t],
                         {"candidate": "eg2", "model_id": "m", "params_hash": "img"}, image)
    src.labels["cA"] = {"b1": {"caption": "A red rose.", "tags": []}, "c1": {"caption": "A small dog.", "tags": []}}
    src.label_source["cA"] = {"candidate": "cA", "model_id": "m/cA", "params_hash": "lab"}
    src.caption_vecs["cA"] = {"b1": unit((1, 1.0)), "c1": unit((3, 1.0))}
    src.caption_store["cA"] = {"model_id": "m", "params_hash": "cap"}
    routes = ["eg2-image@768", "hybrid:cA", "fts:cA"]
    search.run_routes(cfg, src, [search.parse_route(r) for r in routes])

    gen = tmp_path / "generated"
    run(shoebox("eval", "gen-search", "--set", "s", "--sets-dir", sets, "--out-dir", gen), env)
    spec = gen / "eval.yaml"
    run([VALLY, "lint", "-e", spec, "--grader-plugin", GRADERS, "--strict"], env)

    out = tmp_path / "vally-out"
    run([VALLY, "eval", "-e", spec, "--executor-plugin", EXECUTOR, "--grader-plugin", GRADERS,
         "--model", ",".join(routes), "--output-dir", out, "--workers", "2"], env, check=False)
    (res,) = list(out.glob("*/results.jsonl"))
    rows = [json.loads(line) for line in res.read_text().splitlines() if line.strip()]
    trials = [r for r in rows if r.get("type") != "run-summary"]
    assert len(trials) == 9 and all(r["status"] == "success" for r in trials)
    by = {(r["model"], r["stimulus"]): r for r in trials}
    qid = {q["text"]: q["query_id"] for q in queries}
    # image route: "small dog" query points at b1's image -> c1 is last among scored items
    img_dog = by[("eg2-image@768", qid["small dog"])]
    md = img_dog["gradeResult"]["details"][0]["metadata"]
    assert md["rank_of_first_relevant"] > 1
    assert by[("hybrid:cA", qid["small dog"])]["gradeResult"]["details"][0]["metadata"]["rr"] > md["rr"]

    # re-grade with no executor: the rankings store is moved away first
    shutil.move(tmp_path / "root" / "results", tmp_path / "results-moved")
    with open(res) as fin:
        g = run([VALLY, "grade", "-e", spec, "--grader-plugin", GRADERS, "--output", "jsonl"], env, stdin=fin,
                check=False)
    regraded = tmp_path / "regraded.jsonl"
    regraded.write_text(g.stdout)
    assert len([ln for ln in g.stdout.splitlines() if ln.strip()]) >= 9

    # a relevance fix in the spec changes the verdict with no executor: point "small dog" at b1 instead
    dog_cfg = json.dumps({"relevant": ["c1"], "k": list(search.K_VALUES), "groups": [["c1"]]})
    text = spec.read_text()
    assert text.count(dog_cfg) == 1
    fixed = tmp_path / "fixed.yaml"
    fixed.write_text(text.replace(dog_cfg, json.dumps({"relevant": ["b1"], "k": list(search.K_VALUES)})))
    with open(res) as fin:
        g2 = run([VALLY, "grade", "-e", fixed, "--grader-plugin", GRADERS, "--output", "jsonl"], env, stdin=fin,
                 check=False)

    def verdicts(lines):
        trials = [search.parse_trial(json.loads(ln)) for ln in lines if ln.strip()]
        return {(t.route, t.query_id): t.grader for t in trials if t is not None and t.grader is not None}

    before, after = verdicts(g.stdout.splitlines()), verdicts(g2.stdout.splitlines())
    key = ("eg2-image@768", qid["small dog"])
    assert before[key]["metadata"]["relevant"] == ["c1"] and after[key]["metadata"]["relevant"] == ["b1"]
    # the image route ranks b1 first for the dog query (its image is the dog direction), c1 later
    assert after[key]["metadata"]["rank_of_first_relevant"] == 1
    assert before[key]["metadata"]["rank_of_first_relevant"] != 1
    assert after[key]["metadata"]["rr"] != before[key]["metadata"]["rr"]
    others = [k for k in before if k[1] != qid["small dog"]]
    assert others and all(before[k]["metadata"]["rr"] == after[k]["metadata"]["rr"] for k in others)

    runs = tmp_path / "runs"
    for results in (res, regraded):
        run(shoebox("eval", "search-report", "--results", results, "--date", "2026-10-10", "--out-dir", runs), env)
        rep = json.loads((runs / "2026-10-10_s-search.json").read_text())
        assert rep["complete"] is True, rep["blockers"]
        assert set(rep["routes"]) == set(routes) and rep["n_queries"] == 3
        assert rep["routes"]["hybrid:cA"]["n_queries"] == 3
        assert rep["routes"]["eg2-image@768"]["per_query"][qid["small dog"]]["rr"] == pytest.approx(md["rr"])
        text = (runs / "2026-10-10_s-search.md").read_text() + json.dumps(rep)
        assert not any(q["text"] in text for q in queries)  # query_ids only


def test_vally_search_unknown_route_errors(tmp_path):
    cfg, cfg_path, sets = _world(tmp_path)
    env = {**os.environ, "SHOEBOX_CONFIG": str(cfg_path), "VALLY_TELEMETRY_OPTOUT": "1"}
    run(shoebox("eval", "search-queries", "--set", "s", "--sets-dir", sets), env)
    qpath = sets / "s" / search.QUERIES_FILE
    queries = search.read_jsonl(qpath)
    src = search.Sources("s", ["a1", "a2", "b1", "c1"], queries, search.file_sha256(qpath),
                         lambda t: unit((0, 1.0)), {"candidate": "eg2", "model_id": "m", "params_hash": "img"},
                         {a: unit((i, 1.0)) for i, a in enumerate(["a1", "a2", "b1", "c1"])})
    search.run_routes(cfg, src, [search.parse_route("eg2-image@768")])
    gen = tmp_path / "generated"
    run(shoebox("eval", "gen-search", "--set", "s", "--sets-dir", sets, "--out-dir", gen), env)
    out = tmp_path / "vally-out"
    p = run([VALLY, "eval", "-e", gen / "eval.yaml", "--executor-plugin", EXECUTOR, "--grader-plugin", GRADERS,
             "--model", "eg2-image@256", "--output-dir", out], env, check=False)
    assert p.returncode != 0
    (res,) = list(out.glob("*/results.jsonl"))
    trials = [json.loads(ln) for ln in res.read_text().splitlines() if ln.strip()]
    trials = [t for t in trials if t.get("type") != "run-summary"]
    assert len(trials) == 3 and all(t["status"] == "error" and "missing key" in t["error"] for t in trials)
    runs = tmp_path / "runs"
    run(shoebox("eval", "search-report", "--results", res, "--date", "2026-10-10", "--out-dir", runs), env)
    rep = json.loads((runs / "2026-10-10_s-search.json").read_text())
    assert rep["complete"] is False and rep["routes"]["eg2-image@256"]["status_counts"] == {"missing": 3}
