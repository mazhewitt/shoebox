"""Which assets have already left the machine: the retro-check set (spec content-screen, task 9.3).

The union of every asset in a hosted model's result store (any kind: labels, judgments, audits, ...)
and every asset named in the eval sets whose images were shown to Claude (packets, pools, contact
sheets). It errs toward including too much: screening is local and cheap.
"""

import re
from collections.abc import Iterable
from pathlib import Path

from shoebox import store
from shoebox.config import REPO_ROOT, Config

# Store model dirs written by hosted runtimes (`store.model_dir` of their model ids).
HOSTED_MODEL_DIR_PREFIXES = ("gemini__", "gemini-batch__", "claude__")
# Eval sets whose items or pools went to Gemini or were shown to Claude.
SHOWN_SETS = ("pilot-v0", "eval-1k")
SETS_DIR = REPO_ROOT / "evals" / "sets"

_ASSET_ID = re.compile(r"\b[0-9a-f]{64}\b")


def hosted_store_ids(cfg: Config) -> set[str]:
    """Every asset_id in a hosted model's store dir, across all result kinds."""
    out: set[str] = set()
    if not cfg.results_dir.is_dir():
        return out
    for kind in sorted(p for p in cfg.results_dir.iterdir() if p.is_dir()):
        for mdir in sorted(kind.iterdir()):
            if mdir.is_dir() and mdir.name.startswith(HOSTED_MODEL_DIR_PREFIXES):
                for d in sorted(p for p in mdir.iterdir() if p.is_dir()):
                    out.update(r["asset_id"] for r in store.iter_dir(d))
    return out


def set_ids(sets: Iterable[str] = SHOWN_SETS, sets_dir: Path = SETS_DIR) -> set[str]:
    """Every asset id (64 lowercase hex) appearing in any JSON/JSONL file of the named eval sets."""
    out: set[str] = set()
    for name in sets:
        for path in sorted((Path(sets_dir) / name).glob("*.json*")):
            out.update(_ASSET_ID.findall(path.read_text()))
    return out


def retro_ids(cfg: Config, sets: Iterable[str] = SHOWN_SETS, sets_dir: Path = SETS_DIR) -> set[str]:
    return hosted_store_ids(cfg) | set_ids(sets, sets_dir)
