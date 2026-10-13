"""Google Takeout landing (add-takeout-landing D1-D5): test, extract and hash the zips in one read-only pass.

The zips are read where they landed (a source root); everything is written under
`<shoebox_root>/originals/takeout/<account>/`: `<export>/` holds the merged tree and `<export>.manifest/` holds
`zips.jsonl` (per-zip status, latest row wins), `parts/<zip>.jsonl` (that zip's entries, kept so a resume
loses no rows), `tmp/` (in-flight entries) and `manifest.parquet` (one row per file).
Nothing printed to stdout names an entry.
"""

import json
import os
import re
import shutil
import stat
import sys
import time
import zipfile
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath

import blake3
import pyarrow as pa
import pyarrow.parquet as pq

from shoebox.config import Config, check_write_location

_CHUNK = 1024 * 1024
_ZIP_NAME = re.compile(r"^takeout-(\d{8}T\d{6}Z)-.*\.zip$")
_SCHEMA = pa.schema([("zip", pa.string()), ("path", pa.string()), ("size", pa.int64()),
                     ("crc32", pa.int64()), ("blake3", pa.string())])


class ZipFailed(Exception):
    """The zip cannot be fully extracted (unreadable, CRC, unsafe entry, conflicting duplicate)."""


@dataclass
class Entry:
    path: str
    size: int
    crc32: int
    blake3: str
    zips: list[str] = field(default_factory=list)


def export_of(zip_name: str) -> str | None:
    m = _ZIP_NAME.match(zip_name)
    return m.group(1) if m else None


def default_dest(cfg: Config, account: str) -> Path:
    return cfg.shoebox_root / "originals" / "takeout" / account


def _copy_entry(zf: zipfile.ZipFile, info: zipfile.ZipInfo, tmp: Path) -> str:
    """Stream one entry to `tmp` in 1 MiB chunks; zipfile checks the CRC at EOF. Returns the BLAKE3 hex
    (same hashing as `ids.asset_id`, on the same bytes)."""
    h = blake3.blake3()
    with zf.open(info) as src, open(tmp, "wb") as dst:
        while chunk := src.read(_CHUNK):
            h.update(chunk)
            dst.write(chunk)
    return h.hexdigest()


def _safe_relpath(name: str) -> str:
    p = PurePosixPath(name)
    if p.is_absolute() or ".." in p.parts or "\\" in name or not p.parts:
        raise ZipFailed("unsafe entry path")
    return str(p)


def _read_only(path: Path) -> None:
    try:
        mode = path.stat().st_mode
        os.chmod(path, mode & ~(stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH))
    except OSError:
        pass  # best effort over SMB (D4)


def _remove(path: Path) -> None:
    try:
        os.chmod(path, 0o644)
    except OSError:
        pass
    path.unlink(missing_ok=True)


def _key(rel: str) -> str:
    """Duplicate key: the destination (SMB share, APFS) is case-insensitive, so paths differing only in case
    are the same file."""
    return rel.casefold()


def _zip_file_paths(zip_path: Path) -> list[str]:
    """The zip's file entry paths from the central directory (no data read)."""
    with zipfile.ZipFile(zip_path) as zf:
        return [i.filename for i in zf.infolist() if not i.is_dir()]


def _extract_zip(zip_path: Path, tree: Path, tmp_dir: Path, known: dict[str, Entry],
                 progress=None, previous: list[str] = ()) -> tuple[list[Entry], int]:
    """Extract every file entry of one zip. Returns its entries and the bytes read. On any failure removes
    every file of this zip from the tree (this run's, an earlier run's in `previous`, and any infolist path)
    unless another done zip in `known` owns it, then raises ZipFailed."""
    written: list[Path] = []
    rows: list[Entry] = []
    nbytes = 0
    tmp = tmp_dir / f"{zip_path.name}.entry"
    try:
        try:
            with zipfile.ZipFile(zip_path) as zf:
                for info in zf.infolist():
                    if info.is_dir():
                        continue
                    rel = _safe_relpath(info.filename)
                    digest = _copy_entry(zf, info, tmp)
                    nbytes += info.file_size
                    if progress:
                        progress(nbytes)
                    prior = known.get(_key(rel))
                    if prior is not None and prior.zips and zip_path.name not in prior.zips:
                        if prior.blake3 != digest or prior.size != info.file_size:
                            raise ZipFailed("duplicate entry path with different bytes")
                        tmp.unlink()
                        rows.append(Entry(rel, info.file_size, info.CRC, digest, [zip_path.name]))
                        continue
                    dest = tree / rel
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    os.replace(tmp, dest)
                    written.append(dest)
                    _read_only(dest)
                    rows.append(Entry(rel, info.file_size, info.CRC, digest, [zip_path.name]))
        except (zipfile.BadZipFile, zipfile.LargeZipFile, OSError, EOFError, RuntimeError,
                NotImplementedError) as e:
            raise ZipFailed(type(e).__name__) from e
        paths = [_key(r.path) for r in rows]
        if len(set(paths)) != len(paths):
            raise ZipFailed("duplicate entry path within the zip")
    except ZipFailed:
        stale = [str(p.relative_to(tree).as_posix()) for p in written] + list(previous)
        try:
            stale += _zip_file_paths(zip_path)
        except (zipfile.BadZipFile, OSError, EOFError, RuntimeError, ValueError):
            pass
        for rel in stale:
            try:
                rel = _safe_relpath(rel)
            except ZipFailed:
                continue
            if _key(rel) not in known:
                _remove(tree / rel)
        tmp.unlink(missing_ok=True)
        raise
    return rows, nbytes


def _latest_status(mdir: Path) -> dict[str, dict]:
    rows: dict[str, dict] = {}
    f = mdir / "zips.jsonl"
    if f.exists():
        for line in f.read_text().splitlines():
            if line.strip():
                r = json.loads(line)
                rows[r["zip"]] = r
    return rows


def _append_status(mdir: Path, row: dict) -> None:
    with open(mdir / "zips.jsonl", "a") as f:
        f.write(json.dumps(row) + "\n")
        f.flush()
        os.fsync(f.fileno())


def _part_path(mdir: Path, zip_name: str) -> Path:
    return mdir / "parts" / f"{zip_name}.jsonl"


def _read_part(mdir: Path, zip_name: str) -> list[Entry]:
    p = _part_path(mdir, zip_name)
    if not p.exists():
        return []
    return [Entry(r["path"], r["size"], r["crc32"], r["blake3"], [zip_name])
            for r in map(json.loads, p.read_text().splitlines())]


def _write_part(mdir: Path, zip_name: str, rows: list[Entry]) -> None:
    p = _part_path(mdir, zip_name)
    tmp = p.with_suffix(".tmp")
    tmp.write_text("".join(json.dumps({"path": r.path, "size": r.size, "crc32": r.crc32, "blake3": r.blake3})
                           + "\n" for r in rows))
    os.replace(tmp, p)


def _intact(tree: Path, rows: list[Entry]) -> bool:
    for r in rows:
        try:
            if (tree / r.path).stat().st_size != r.size:
                return False
        except OSError:
            return False
    return True


def _merge(known: dict[str, Entry], rows: list[Entry]) -> None:
    for r in rows:
        prior = known.get(_key(r.path))
        if prior is None:
            known[_key(r.path)] = Entry(r.path, r.size, r.crc32, r.blake3, list(r.zips))
        else:
            prior.zips.extend(z for z in r.zips if z not in prior.zips)


def _write_manifest(mdir: Path, known: dict[str, Entry]) -> None:
    rows = sorted(known.values(), key=lambda e: e.path)
    table = pa.table({"zip": [",".join(sorted(e.zips)) for e in rows], "path": [e.path for e in rows],
                      "size": [e.size for e in rows], "crc32": [e.crc32 for e in rows],
                      "blake3": [e.blake3 for e in rows]}, schema=_SCHEMA)
    tmp = mdir / "manifest.parquet.tmp"
    pq.write_table(table, tmp)
    os.replace(tmp, mdir / "manifest.parquet")


def reconcile(mdir: Path, tree: Path, expected_files: int | None = None) -> list[str]:
    """D5: manifest rows = files on disk (= zip file entries, when given) and every size matches.
    Returns mismatch descriptions (counts only, no names); empty when it reconciles."""
    rows = pq.read_table(mdir / "manifest.parquet").to_pylist()
    on_disk = {str(p.relative_to(tree).as_posix()): p for p in tree.rglob("*") if p.is_file()} \
        if tree.exists() else {}
    problems = []
    listed = {r["path"] for r in rows}
    missing = sum(1 for r in rows if r["path"] not in on_disk)
    resized = sum(1 for r in rows if r["path"] in on_disk and on_disk[r["path"]].stat().st_size != r["size"])
    extra = sum(1 for p in on_disk if p not in listed)
    if expected_files is not None and expected_files != len(rows):
        problems.append(f"zip file entries {expected_files} != manifest rows {len(rows)}")
    if len(on_disk) != len(rows):
        problems.append(f"files on disk {len(on_disk)} != manifest rows {len(rows)}")
    if missing:
        problems.append(f"{missing} manifest file(s) missing from the tree")
    if resized:
        problems.append(f"{resized} file(s) with a size other than the manifest")
    if extra:
        problems.append(f"{extra} file(s) in the tree not in the manifest")
    return problems


def _check_dest(cfg: Config, zips_dir: Path, dest_root: Path) -> None:
    check_write_location(cfg, dest_root)
    if dest_root.expanduser().resolve().is_relative_to(zips_dir.expanduser().resolve()):
        raise ValueError(f"refusing to write under the zips folder: {dest_root}")


def verify(cfg: Config, zips_dir: Path, account: str, dest_root: Path | None = None,
           export: str | None = None, err=None) -> int:
    """Verify and extract every Takeout zip in `zips_dir` (or only `export`). Returns the exit status."""
    err = err or sys.stderr
    zips_dir = Path(zips_dir).expanduser()
    dest_root = Path(dest_root).expanduser() if dest_root else default_dest(cfg, account)
    _check_dest(cfg, zips_dir, dest_root)  # refuse before reading any zip

    groups: dict[str, list[Path]] = {}
    skipped = 0
    for p in sorted(zips_dir.iterdir()):
        ts = export_of(p.name) if p.is_file() else None
        if ts is None:
            skipped += p.is_file()
            continue
        if export is None or ts == export:
            groups.setdefault(ts, []).append(p)
    if skipped:
        print(f"{skipped} file(s) in the zips folder are not Takeout zips; ignored", file=err)
    if not groups:
        print("no Takeout zips found", file=err)
        return 1

    status = 0
    for ts, zip_paths in sorted(groups.items()):
        status |= _verify_export(cfg, ts, zip_paths, account, dest_root, err)
    return status


def _verify_export(cfg: Config, ts: str, zip_paths: list[Path], account: str, dest_root: Path, err) -> int:
    tree, mdir = dest_root / ts, dest_root / f"{ts}.manifest"
    for d in (tree, mdir, mdir / "parts", mdir / "tmp"):
        check_write_location(cfg, d)
        d.mkdir(parents=True, exist_ok=True)
    for stale in (mdir / "tmp").iterdir():
        stale.unlink()

    state = _latest_status(mdir)
    known: dict[str, Entry] = {}
    todo: list[Path] = []
    for zp in zip_paths:
        done = state.get(zp.name, {}).get("status") == "done"
        rows = _read_part(mdir, zp.name) if done else []
        if done and _intact(tree, rows):
            _merge(known, rows)
        else:
            todo.append(zp)

    failed = 0
    total = len(zip_paths)
    print(f"export {ts}: {total} zip(s), {total - len(todo)} already done", file=err)
    for zp in todo:
        idx = zip_paths.index(zp) + 1
        t0 = time.monotonic()

        def progress(n, idx=idx, t0=t0):
            if n % (256 * _CHUNK) < _CHUNK:
                rate = n / 1e6 / max(time.monotonic() - t0, 1e-6)
                print(f"  [{idx}/{total}] {n / 1e6:.0f} MB, {rate:.1f} MB/s", file=err)

        row = {"zip": zp.name, "size": None, "entries": 0, "status": "failed",
               "ts": datetime.now(UTC).isoformat(timespec="seconds")}
        try:
            row["size"] = zp.stat().st_size
            previous = [r.path for r in _read_part(mdir, zp.name)]
            _remove_part(mdir, zp.name)
            rows, nbytes = _extract_zip(zp, tree, mdir / "tmp", known, progress, previous)
            _write_part(mdir, zp.name, rows)
            _merge(known, rows)
            row.update(entries=len(rows), status="done")
            secs = max(time.monotonic() - t0, 1e-6)
            print(f"  [{idx}/{total}] done: {len(rows)} file(s), {nbytes / 1e6:.0f} MB, "
                  f"{nbytes / 1e6 / secs:.1f} MB/s", file=err)
        except (ZipFailed, OSError) as e:
            row["reason"] = str(e) if isinstance(e, ZipFailed) else type(e).__name__
            failed += 1
            print(f"  [{idx}/{total}] FAILED ({row['reason']})", file=err)
        _append_status(mdir, row)
    shutil.rmtree(mdir / "tmp", ignore_errors=True)

    _write_manifest(mdir, known)
    entries = sum(len(_read_part(mdir, zp.name)) for zp in zip_paths
                  if _latest_status(mdir).get(zp.name, {}).get("status") == "done")
    dups = entries - len(known)
    problems: list[str] = []
    if not failed:
        try:
            problems = reconcile(mdir, tree, _expected_files(zip_paths))
        except (zipfile.BadZipFile, OSError, ZipFailed) as e:
            problems = [f"could not recount the zip entries ({type(e).__name__})"]
    done = total - failed
    print(f"export {ts}: {done}/{total} zip(s) done, {failed} failed; {entries} file entries, "
          f"{dups} identical duplicate(s), {len(known)} manifest rows")
    for p in problems:
        print(f"reconcile mismatch: {p}")
    if failed or problems:
        return 1
    print(f"export {ts} for {account} verified on the NAS; safe to delete from Drive")
    return 0


def _expected_files(zip_paths: list[Path]) -> int:
    """D5, counted independently of the part files: distinct file entry paths across the zips' central
    directories (identical duplicates are one manifest row)."""
    return len({_key(_safe_relpath(n)) for zp in zip_paths for n in _zip_file_paths(zp)})


def _remove_part(mdir: Path, zip_name: str) -> None:
    _part_path(mdir, zip_name).unlink(missing_ok=True)
