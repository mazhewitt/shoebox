"""Unit tests for the Vally plugins in evals/vally/plugins, driven through node."""

import json
import shutil
import subprocess

import pytest
from test_eval_helpers import label, record, write_jsonl

from shoebox.config import REPO_ROOT

VALLY = REPO_ROOT / "evals" / "vally"
pytestmark = pytest.mark.skipif(
    shutil.which("node") is None
    or not (VALLY / "node_modules" / "@microsoft" / "vally").exists(),
    reason="node or evals/vally/node_modules missing (run npm ci in evals/vally)",
)


def node(script: str) -> object:
    proc = subprocess.run(
        ["node", "--input-type=module", "-e", script],
        cwd=VALLY,
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


def traj(status, lab=None):
    out = json.dumps(lab) if status == "ok" else f"[shoebox:{status}] raw"
    return {
        "events": [{"type": "custom", "data": {"shoebox": {"status": status}}}],
        "output": out,
    }


def grade(cases: list[tuple[str, dict, dict]]) -> list[dict]:
    """cases: (grader name, trajectory, config) -> GraderResults."""
    script = f"""
import {{ registerGraders }} from "./plugins/shoebox-graders.js";
const g = {{}};
registerGraders({{ register: (x) => (g[x.metadata.name] = x) }});
const cases = {json.dumps(cases)};
const out = [];
for (const [name, trajectory, config] of cases) out.push(await g[name].grade({{ trajectory, config }}));
console.log(JSON.stringify(out));
"""
    return node(script)


def test_label_status():
    ok, inv, err = grade(
        [
            ("label-status", traj("ok", label()), {}),
            ("label-status", traj("invalid"), {}),
            ("label-status", traj("error"), {}),
        ]
    )
    assert ok["passed"] and not inv["passed"] and not err["passed"]
    assert inv["metadata"]["status"] == "invalid"


def test_field_match_labelled_abstain_unlabelled():
    match, wrong, abstain, invalid, unl = grade(
        [
            (
                "field-match",
                traj("ok", label()),
                {"field": "medium", "expected": "digital"},
            ),
            (
                "field-match",
                traj("ok", label(medium="print")),
                {"field": "medium", "expected": "digital"},
            ),
            (
                "field-match",
                traj("ok", label(medium="unknown")),
                {"field": "medium", "expected": "digital"},
            ),
            (
                "field-match",
                traj("invalid"),
                {"field": "medium", "expected": "digital"},
            ),
            ("field-match", traj("ok", label(medium="print")), {"field": "medium"}),
        ]
    )
    assert (
        match["passed"]
        and match["score"] == 1
        and match["metadata"]["unlabelled"] is False
    )
    assert not wrong["passed"] and wrong["metadata"]["predicted"] == "print"
    assert not abstain["passed"] and abstain["metadata"]["abstained"] is True
    assert not invalid["passed"] and invalid["metadata"]["predicted"] is None
    assert unl["passed"] and unl["metadata"] == {
        "field": "medium", "status": "ok", "predicted": "print", "abstained": False, "expected": None, "unlabelled": True,
    }  # fmt: skip


def test_set_match():
    labelled, unl = grade(
        [
            (
                "set-match",
                traj("ok", label(tags=["Beach", "dog"])),
                {"field": "tags", "expected": ["beach", "sea"]},
            ),
            ("set-match", traj("ok", label(tags=["beach"])), {"field": "tags"}),
        ]
    )
    md = labelled["metadata"]
    assert md["tp"] == ["beach"] and md["fp"] == ["dog"] and md["fn"] == ["sea"]
    assert not labelled["passed"] and labelled["score"] == pytest.approx(0.5)
    assert unl["passed"] and unl["metadata"]["unlabelled"] is True


def test_replay_store_prefers_final_records(tmp_path):
    rows = [
        record("a1", "m", "p", status="error"),
        record("a1", "m", "p", output=label(medium="print")),
        record(
            "a1", "m", "p", status="error"
        ),  # a later transport error does not shadow the ok record
        record(
            "a1", "m", "p", output=label(medium="film_slide")
        ),  # first final wins, as store.index
        record("a2", "m", "p", status="error"),
        record("a3", "other", "p", output=label()),  # another model's record is ignored
    ]
    write_jsonl(tmp_path / "part-0000.jsonl", rows)
    script = f"""
import {{ loadStore, outputFor }} from "./plugins/shoebox-vlm.js";
const s = loadStore({json.dumps(str(tmp_path))}, "m", "p");
console.log(JSON.stringify({{ keys: [...s.keys()].sort(), a1: s.get("a1").status, a2: s.get("a2").status,
  out: outputFor(s.get("a1")), err: outputFor(s.get("a2")) }}));
"""
    got = node(script)
    assert got["keys"] == ["a1", "a2"]
    assert got["a1"] == "ok" and got["a2"] == "error"
    assert json.loads(got["out"])["medium"] == "print"
    assert got["err"].startswith("[shoebox:error]")


def test_executor_validate_config():
    script = """
import { registerExecutors } from "./plugins/shoebox-vlm.js";
let ex; registerExecutors({ register: (e) => (ex = e) });
const res = [];
for (const c of [{ mode: "replay", candidates_file: "/x.json" }, { mode: "nope", candidates_file: "/x" }, { mode: "live" }]) {
  try { ex.validateConfig(c); res.push("ok"); } catch (e) { res.push("error"); }
}
console.log(JSON.stringify({ res, attachments: ex.supportsAttachments, name: ex.name }));
"""
    got = node(script)
    assert got == {
        "res": ["ok", "error", "error"],
        "attachments": True,
        "name": "shoebox-vlm",
    }


def test_replay_store_skips_torn_lines(tmp_path):
    good = json.dumps(record("a1", "m", "p", output=label()))
    torn = json.dumps(record("a2", "m", "p", output=label()))[
        :54
    ]  # a killed writer's last line
    (tmp_path / "part-0000.jsonl").write_text(good + "\n" + torn)
    script = f"""
import {{ loadStore, missingKeyError }} from "./plugins/shoebox-vlm.js";
const s = loadStore({json.dumps(str(tmp_path))}, "m", "p");
console.log(JSON.stringify({{ keys: [...s.keys()], torn: s.torn,
  msg: missingKeyError("c", "a2", {{ model_id: "m", params_hash: "p", store_dir: "/s" }}, s).message }}));
"""
    got = node(script)
    assert got["keys"] == ["a1"] and got["torn"] == 1
    assert "missing key" in got["msg"] and "1 torn line" in got["msg"]


def test_field_match_owner_unknown_is_unlabelled():
    (res,) = grade(
        [
            (
                "field-match",
                traj("ok", label(medium="unknown")),
                {"field": "medium", "expected": "unknown"},
            )
        ]
    )
    assert res["passed"] and res["metadata"]["unlabelled"] is True
    assert (
        res["metadata"]["owner_unknown"] is True and res["metadata"]["expected"] is None
    )
