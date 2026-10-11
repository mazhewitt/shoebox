"""Keyed, append-only output store (design D2/D3, docs/pilot.md section 5).

Records live at `<shoebox_root>/results/<kind>/<model_dir>/<params_hash>/part-NNNN.jsonl`, with
`params.json` alongside. A key is `(asset_id, model_id, params_hash)`: an `ok` or `invalid` record is
final (a cache hit, never overwritten); an `error` record may be retried by appending a new record.
"""

import hashlib
import json
import os
from collections.abc import Callable, Iterable, Iterator
from pathlib import Path

from shoebox.config import Config, check_write_location

PART_LINES = 5000
FINAL = ("ok", "invalid")
KEY_FIELDS = ("asset_id", "model_id", "params_hash", "status")

# store dir -> (last part path, its size, its line count); revalidated by size on each append
_tail: dict[Path, tuple[Path, int, int]] = {}


def canonical_json(obj) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def params_hash(params: dict) -> str:
    """First 16 hex of sha256 over canonical JSON of everything that affects output."""
    return hashlib.sha256(canonical_json(params).encode()).hexdigest()[:16]


def model_dir(model_id: str) -> str:
    return model_id.replace("/", "__").replace(":", "_")


def store_dir(cfg: Config, model_id: str, params_hash: str, kind: str = "labels") -> Path:
    return cfg.results_dir / kind / model_dir(model_id) / params_hash


def is_final(record: dict | None) -> bool:
    return bool(record) and record.get("status") in FINAL


def _parts(d: Path) -> list[Path]:
    return sorted(d.glob("part-*.jsonl")) if d.is_dir() else []


def iter_dir(d: Path) -> Iterator[dict]:
    """All records in one store dir, in write order. A torn (unparseable) line from a killed writer is skipped."""
    for part in _parts(d):
        with open(part, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(rec, dict) and isinstance(rec.get("asset_id"), str):
                    yield rec


def iter_records(cfg: Config, model_id: str, params_hash: str, kind: str = "labels") -> Iterator[dict]:
    """All records for one (model_id, params_hash) in write order (see `iter_dir`)."""
    return iter_dir(store_dir(cfg, model_id, params_hash, kind))


def index_records(records: Iterable[dict], key: Callable[[dict], str] | None = None) -> dict[str, dict]:
    """`{asset_id: record}`: the first final record per asset, else its latest error record.
    `key` replaces asset_id as the index key, e.g. `(query_id, asset_id)` for judgments."""
    out: dict[str, dict] = {}
    for r in records:
        k = r["asset_id"] if key is None else key(r)
        if not is_final(out.get(k)):
            out[k] = r
    return out


def index(cfg: Config, model_id: str, params_hash: str, kind: str = "labels") -> dict[str, dict]:
    """`{asset_id: record}` for one (model_id, params_hash) (see `index_records`)."""
    return index_records(iter_records(cfg, model_id, params_hash, kind))


def store_dirs(cfg: Config, kind: str = "labels") -> list[Path]:
    """Every `results/<kind>/<model_dir>/<params_hash>/` dir, sorted."""
    return sorted(d for d in (cfg.results_dir / kind).glob("*/*") if d.is_dir())


def lookup(cfg: Config, model_id: str, params_hash: str, asset_id: str, kind: str = "labels") -> dict | None:
    """The stored record for one key (see `index`), or None. Use `is_final` to decide on a cache hit."""
    return index(cfg, model_id, params_hash, kind).get(asset_id)


def write_params(cfg: Config, model_id: str, params_hash: str, doc: dict, kind: str = "labels") -> Path:
    """Write `params.json` once per store dir (first writer wins). Returns the store dir."""
    d = store_dir(cfg, model_id, params_hash, kind)
    check_write_location(cfg, d)
    d.mkdir(parents=True, exist_ok=True)
    target = d / "params.json"
    if not target.exists():
        tmp = d / f".params.json.{os.getpid()}.tmp"
        tmp.write_text(json.dumps(doc, indent=2, sort_keys=True, ensure_ascii=False) + "\n")
        os.replace(tmp, target)
    return d


def _count_lines(path: Path) -> int:
    with open(path, "rb") as f:
        return sum(chunk.count(b"\n") for chunk in iter(lambda: f.read(1 << 20), b""))


def _target_part(d: Path) -> Path:
    parts = _parts(d)
    if not parts:
        return d / "part-0000.jsonl"
    last = parts[-1]
    size = last.stat().st_size
    cached = _tail.get(d)
    lines = cached[2] if cached and cached[0] == last and cached[1] == size else _count_lines(last)
    _tail[d] = (last, size, lines)
    if lines >= PART_LINES:
        return d / f"part-{int(last.stem.split('-')[1]) + 1:04d}.jsonl"
    return last


def append(cfg: Config, record: dict, kind: str = "labels") -> Path:
    """Append one record (one JSON line) to its key's store dir. Returns the part file written."""
    return append_many(cfg, [record], kind)[0]


def append_many(cfg: Config, records: list[dict], kind: str = "labels") -> list[Path]:
    """Append records in order, one open/write/close per part file touched rather than per record (each
    costs tens of ms over SMB). Every record is checked before anything is written. Returns each
    record's part file."""
    for record in records:
        missing = [k for k in KEY_FIELDS if not record.get(k)]
        if missing:
            raise ValueError(f"record lacks key fields: {missing}")
    by_dir: dict[Path, list[int]] = {}
    for i, record in enumerate(records):
        by_dir.setdefault(store_dir(cfg, record["model_id"], record["params_hash"], kind), []).append(i)
    out: list[Path] = [Path()] * len(records)
    for d, idxs in by_dir.items():
        check_write_location(cfg, d)
        d.mkdir(parents=True, exist_ok=True)
        lines = [(json.dumps(records[i], ensure_ascii=False) + "\n").encode() for i in idxs]
        pos = 0
        while pos < len(lines):
            part = _target_part(d)
            cached = _tail.get(d)
            have = cached[2] if cached and cached[0] == part else 0
            take = max(1, PART_LINES - have)
            chunk = lines[pos:pos + take]
            data = b"".join(chunk)
            if part.exists() and part.stat().st_size:
                with open(part, "rb") as f:
                    f.seek(-1, os.SEEK_END)
                    if f.read(1) != b"\n":
                        data = b"\n" + data  # seal a torn line left by a killed writer
            fd = os.open(part, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644)
            try:
                view = memoryview(data)
                while view:
                    view = view[os.write(fd, view):]
            finally:
                os.close(fd)
            _tail[d] = (part, part.stat().st_size, have + data.count(b"\n"))
            for i in idxs[pos:pos + len(chunk)]:
                out[i] = part
            pos += len(chunk)
    return out
