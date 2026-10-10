"""Read-only guard for synced sources, and the sources-untouched snapshot/verify.

A snapshot records, per source:
- the sorted entry names of every folder the pilot recipe lists (taken from the
  recipe, so it can be taken before the first pick), of each folder holding a pilot
  item, and of the source root;
- `bytes` and `mtime` of every file in those folders;
- the blake3 of every pilot item, when `items.jsonl` exists.

Verify re-measures exactly what the snapshot holds. Nothing is written under a source
root: reading uses `os.stat`, `os.scandir` and plain `open(..., "rb")`.

A folder listing alone is not trusted: the NAS's SMB listing of a very large folder came
back truncated, differently from run to run (1000, 1900 or 10000 entries for one
unchanged folder). So verify stats every entry whose presence differs: an entry is
removed only if it no longer stats, and new only if it was born after the snapshot
started (Shoebox writing there would create it; Cloud Sync keeps old mtimes, not birth times).
"""

import os
import socket
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath

from shoebox.config import Config, Source
from shoebox.ids import asset_id


class SourceWritable(RuntimeError):
    """A synced source root is writable by this process."""


def is_writable(path: Path) -> bool:
    """True if this process could write under `path` (checked without creating anything)."""
    if os.statvfs(path).f_flag & os.ST_RDONLY:
        return False
    return os.access(path, os.W_OK)


def check_read_only(source: Source) -> None:
    """Refuse a synced source whose root is writable; unsynced sources are allowed."""
    if not source.root.is_dir():
        raise FileNotFoundError(f"source {source.name!r} root not found: {source.root}")
    if source.synced and is_writable(source.root):
        raise SourceWritable(
            f"refusing: source {source.name!r} is synced but its root {source.root} is writable by this "
            "process; mount it read-only (or mark the source synced = false)"
        )


def _key(source: str, rel: str) -> str:
    return f"{source}:{rel}"


def _split(key: str) -> tuple[str, str]:
    source, rel = key.split(":", 1)
    return source, rel


def _file_record(path: Path, hash_it: bool) -> dict:
    try:
        st = path.stat()
    except FileNotFoundError:
        return {"missing": True}
    except PermissionError:
        return {"unreadable": True}
    rec: dict = {"bytes": st.st_size, "mtime": st.st_mtime}
    if hash_it:
        rec["blake3"] = asset_id(path)
    return rec


def _listing(path: Path) -> tuple[list[str] | None, list[str]]:
    """Sorted entry names and the names of plain files; `None` when the folder is unreadable."""
    try:
        entries = list(os.scandir(path))
    except (FileNotFoundError, NotADirectoryError):
        return [], []
    except PermissionError:
        return None, []
    files = [e.name for e in entries if e.is_file(follow_symlinks=False)]
    return sorted(e.name for e in entries), files


def measure(
    cfg: Config, dirs: Iterable[str], files: Iterable[str], hashed: Iterable[str], stat_dir_files: bool = True
) -> dict:
    """Snapshot of folder listings (`source:rel` keys) and file records; `hashed` keys also get a blake3.

    With `stat_dir_files`, every plain file in a listed folder also gets a `bytes`/`mtime` record.
    """
    started = datetime.now(UTC).isoformat(timespec="seconds")
    hashed = set(hashed)
    want_files = set(files) | hashed
    listings: dict[str, list[str] | None] = {}
    for key in sorted(set(dirs)):
        source, rel = _split(key)
        names, plain = _listing(cfg.sources[source].root / rel)
        listings[key] = names
        if stat_dir_files:
            want_files.update(_key(source, f"{rel}/{n}" if rel else n) for n in plain)
    records = {}
    for key in sorted(want_files):
        source, rel = _split(key)
        records[key] = _file_record(cfg.sources[source].root / rel, key in hashed)
    return {
        "started": started,
        "taken": datetime.now(UTC).isoformat(timespec="seconds"),
        "machine": socket.gethostname(),
        "files": records,
        "dirs": listings,
    }


def take_snapshot(cfg: Config, items: list[dict], recipe_dirs: dict[str, list[str]] | None = None) -> dict:
    """Snapshot the pilot items (hashed), their folders, each source root and `recipe_dirs` (per source)."""
    hashed = [_key(it["source"], it["relpath"]) for it in items]
    dirs: set[str] = set()
    for it in items:
        parent = str(PurePosixPath(it["relpath"]).parent)
        dirs.add(_key(it["source"], "" if parent == "." else parent))
        dirs.add(_key(it["source"], ""))
    for source, rels in (recipe_dirs or {}).items():
        dirs.update(_key(source, rel) for rel in rels)
        dirs.add(_key(source, ""))
    return measure(cfg, dirs, (), hashed)


def remeasure(cfg: Config, before: dict) -> dict:
    """Measure again exactly the folders and files recorded in `before`, and stat each folder entry whose
    presence differs from the snapshot's listing (`entries`: key -> {exists, born})."""
    hashed = [k for k, rec in before["files"].items() if "blake3" in rec]
    after = measure(cfg, before["dirs"], before["files"], hashed, stat_dir_files=False)
    entries: dict[str, dict] = {}
    for key, b_names in before["dirs"].items():
        a_names = after["dirs"].get(key)
        if b_names is None or a_names is None:
            continue
        source, rel = _split(key)
        for name in set(a_names) ^ set(b_names):
            try:
                st = os.stat(cfg.sources[source].root / rel / name, follow_symlinks=False)
            except OSError:
                entries[_entry(key, name)] = {"exists": False, "born": None}
                continue
            entries[_entry(key, name)] = {"exists": True, "born": getattr(st, "st_birthtime", st.st_mtime)}
    after["entries"] = entries
    return after


def _entry(dir_key: str, name: str) -> str:
    source, rel = _split(dir_key)
    return _key(source, f"{rel}/{name}" if rel else name)


def diff_snapshots(before: dict, after: dict) -> list[str]:
    """Human-readable differences; empty means untouched. Only fields present in `before` are compared."""
    problems: list[str] = []
    for key, b in before["files"].items():
        a = after["files"].get(key, {"missing": True})
        if a.get("missing") and not b.get("missing"):
            problems.append(f"{key}: missing")
            continue
        if b.get("missing") and not a.get("missing"):
            problems.append(f"{key}: appeared")
            continue
        for field in ("bytes", "mtime", "blake3", "unreadable"):
            if field in b and a.get(field) != b.get(field):
                problems.append(f"{key}: {field} changed {b.get(field)!r} -> {a.get(field)!r}")
    entries = after.get("entries")  # present when `after` came from `remeasure`
    when = before.get("started") or before.get("taken")
    since = datetime.fromisoformat(when).timestamp() if when else None
    for key, b_names in before["dirs"].items():
        a_names = after["dirs"].get(key, [])
        if b_names is None or a_names is None:
            if (b_names is None) != (a_names is None):
                problems.append(f"{key}/: readability changed")
            continue
        added = sorted(set(a_names) - set(b_names))
        removed = sorted(set(b_names) - set(a_names))
        if entries is not None:  # a listing difference counts only when the entry itself agrees
            added = [n for n in added if not _old_entry(entries.get(_entry(key, n)), since)]
            removed = [n for n in removed if not (entries.get(_entry(key, n)) or {}).get("exists")]
        if added:
            problems.append(f"{key}/: new entries {added}")
        if removed:
            problems.append(f"{key}/: removed entries {removed}")
    return problems


def _old_entry(entry: dict | None, since: float | None) -> bool:
    """An entry that exists and was born before the snapshot started: only a listing omitted it then."""
    return bool(since is not None and entry and entry["exists"] and entry["born"] is not None
                and entry["born"] < since)
