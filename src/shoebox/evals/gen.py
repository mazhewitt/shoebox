"""Generates a Vally eval spec (`eval.yaml`) and `candidates.json` from a pilot set. Never edit the output."""

import hashlib
import json
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from shoebox.config import REPO_ROOT, Config
from shoebox.derive import load_index
from shoebox.runners import CONFIG_DIR, load_candidates

PROFILE = "infer-v1"
FIELDS = ("medium", "digitiser", "colour_class")
LABEL_RUNTIMES_SKIPPED = ("embed",)
PROMPT = (
    "Label this photo with the label-v1 schema (caption, tags, medium, digitiser, colour_class, confidence). "
    "The candidate's own prompt is applied by the Shoebox label runner."
)
HEADER = "# generated — do not edit. Regenerate with `shoebox eval gen`."

Resolver = Callable[[str], dict]
_CLI = "import sys; from shoebox.cli import main; sys.exit(main())"


@dataclass
class GenResult:
    spec_path: Path
    candidates_path: Path
    stimuli: int
    skipped_items: dict[str, str] = field(default_factory=dict)  # asset_id -> reason
    skipped_candidates: dict[str, str] = field(default_factory=dict)  # name -> reason


def read_jsonl(path: Path) -> list[dict]:
    return [
        json.loads(line) for line in Path(path).read_text().splitlines() if line.strip()
    ]


def load_labels(path: Path) -> dict[str, dict]:
    """Owner labels by asset_id (later lines win). Missing file -> no labels."""
    if not Path(path).exists():
        return {}
    return {r["asset_id"]: r for r in read_jsonl(path)}


def _y(value) -> str:
    """A YAML scalar or flow collection. JSON is valid YAML, so json.dumps is used for quoting."""
    return json.dumps(value, ensure_ascii=True)


def _tags(item: dict, labelled: bool) -> dict[str, str]:
    return {
        "bucket": str(item.get("bucket") or "none"),
        "ext": str(item.get("ext") or "none"),
        "group_rule": str(item.get("group_rule") or "none"),
        "labelled": "yes" if labelled else "no",
    }


def _graders(label: dict | None) -> list[dict]:
    graders: list[dict] = [{"type": "label-status"}]
    for f in FIELDS:
        cfg: dict = {"field": f}
        if label and label.get(f):
            cfg["expected"] = label[f]
        graders.append({"type": "field-match", "config": cfg})
    if label and label.get("tags"):
        graders.append(
            {
                "type": "set-match",
                "config": {"field": "tags", "expected": label["tags"]},
            }
        )
    return graders


def build_spec(
    set_name: str,
    stimuli: list[dict],
    mode: str,
    candidates_file: Path,
    n_items: int | None = None,
) -> str:
    """Renders eval.yaml. Each stimulus dict has name, attachment, tags and graders."""
    n_items = len(stimuli) if n_items is None else n_items
    desc = (
        f"Shoebox label-v1 over set {set_name} ({mode}): {len(stimuli)} of {n_items} set items, "
        f"{n_items - len(stimuli)} skipped (see candidates.json)"
    )
    lines = [
        HEADER,
        f"name: {_y(set_name)}",
        f"description: {_y(desc)}",
        f"version: {_y(set_name)}",
        "type: capability",
        "defaults:",
        "  runs: 1",
        f"  timeout: {_y('10m' if mode == 'live' else '2m')}",
        "  executor:",
        "    name: shoebox-vlm",
        "    config:",
        f"      mode: {_y(mode)}",
        f"      candidates_file: {_y(str(candidates_file))}",
        "scoring:",
        "  threshold: 1.0",
        "stimuli:",
    ]
    for s in stimuli:
        lines += [
            f"  - name: {_y(s['name'])}",
            f"    prompt: {_y(PROMPT)}",
            f"    attachments: [{_y(s['attachment'])}]",
            f"    tags: {_y(s['tags'])}",
            "    graders:",
        ]
        for g in s["graders"]:
            lines.append(f"      - type: {_y(g['type'])}")
            if "config" in g:
                lines.append(f"        config: {_y(g['config'])}")
    return "\n".join(lines) + "\n"


def subprocess_resolver(name: str) -> dict:
    """Resolves a candidate via `shoebox label resolve` in a child process (same interpreter, repo root)."""
    proc = subprocess.run(
        [sys.executable, "-c", _CLI, "label", "resolve", "--candidate", name],
        cwd=REPO_ROOT,
        capture_output=True,
        check=False,
        text=True,
    )
    if proc.returncode != 0:
        raise RuntimeError(
            f"shoebox label resolve --candidate {name} failed: {proc.stderr.strip()[-500:]}"
        )
    lines = [ln for ln in proc.stdout.splitlines() if ln.strip().startswith("{")]
    if not lines:
        raise RuntimeError(f"shoebox label resolve --candidate {name} printed no JSON")
    return json.loads(lines[-1])


def generate(
    cfg: Config,
    set_name: str,
    candidates: list[str],
    mode: str = "replay",
    sets_dir: Path = REPO_ROOT / "evals" / "sets",
    out_dir: Path | None = None,
    resolver: Resolver = subprocess_resolver,
    config_dir: Path = CONFIG_DIR,
    log: Callable[[str], None] = lambda m: print(m, file=sys.stderr),
) -> GenResult:
    if mode not in ("replay", "live"):
        raise ValueError(f"mode must be replay or live, not {mode!r}")
    set_dir = Path(sets_dir) / set_name
    out_dir = (
        Path(out_dir)
        if out_dir
        else REPO_ROOT / "evals" / "vally" / "generated" / set_name
    )
    items = read_jsonl(set_dir / "items.jsonl")
    labels = load_labels(set_dir / "labels.jsonl")
    index = load_index(cfg, PROFILE)

    result = GenResult(out_dir / "eval.yaml", out_dir / "candidates.json", 0)
    stimuli = []
    set_ids = list(dict.fromkeys(item["asset_id"] for item in items))
    seen: set[str] = set()
    for item in items:
        aid = item["asset_id"]
        if aid in seen:
            continue
        seen.add(aid)
        rec = index.get(aid)
        if not rec or rec.get("status") != "ok":
            result.skipped_items[aid] = "no ok derived image"
            continue
        path = Path(rec["path"])
        path = path if path.is_absolute() else cfg.shoebox_root / path
        if not path.exists():
            result.skipped_items[aid] = f"derived file missing: {path}"
            continue
        label = labels.get(aid)
        stimuli.append(
            {
                "name": aid,
                "attachment": str(path),
                "tags": _tags(item, label is not None),
                "graders": _graders(label),
            }
        )
    result.stimuli = len(stimuli)

    runtimes = {n: c.get("runtime", "") for n, c in load_candidates(config_dir).items()}
    resolved: dict[str, dict] = {}
    for name in candidates:
        if runtimes.get(name) in LABEL_RUNTIMES_SKIPPED:
            result.skipped_candidates[name] = (
                f"runtime {runtimes[name]} is not a VLM label candidate"
            )
            log(
                f"skipping candidate {name}: runtime {runtimes[name]} is not a VLM label candidate"
            )
            continue
        r = resolver(name)
        store_dir = Path(r["store_dir"])
        if not store_dir.is_absolute():
            store_dir = cfg.shoebox_root / store_dir
        resolved[name] = {
            "candidate": name,
            "model_id": r["model_id"],
            "params_hash": r["params_hash"],
            "store_dir": str(store_dir),
        }
    if not resolved:
        raise ValueError("no VLM label candidates to evaluate")

    out_dir.mkdir(parents=True, exist_ok=True)
    cands_doc = {
        "_comment": "generated — do not edit. Regenerate with `shoebox eval gen`.",
        "set": set_name,
        "mode": mode,
        "repo_root": str(REPO_ROOT),
        "candidates": resolved,
        # every set item and why some have no stimulus, so the report can check completeness
        "items": set_ids,
        "items_sha256": hashlib.sha256("\n".join(set_ids).encode()).hexdigest(),
        "skipped_items": result.skipped_items,
    }
    result.candidates_path.write_text(json.dumps(cands_doc, indent=2) + "\n")
    result.spec_path.write_text(
        build_spec(
            set_name, stimuli, mode, result.candidates_path.resolve(), len(set_ids)
        )
    )
    for aid, why in result.skipped_items.items():
        log(f"skipping item {aid}: {why}")
    return result
