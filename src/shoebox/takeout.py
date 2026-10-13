"""Google Takeout landing (add-takeout-landing D1-D8): test, extract and hash the zips in one read-only pass.

The zips are read where they landed (a source root); everything is written under
`<shoebox_root>/originals/takeout/<account>/`: `<export>/<zip stem>/` holds each zip's own tree (D3; zips are not
merged, the same entry path in two zips is two files) with SMB-safe names (D8), and `<export>.manifest/` holds
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
import unicodedata
import zipfile
import zlib
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath

import blake3
import pyarrow as pa
import pyarrow.parquet as pq

from shoebox.config import Config, check_write_location

_CHUNK = 1024 * 1024
_ZIP_NAME = re.compile(r"^takeout-(\d{8}T\d{6}Z)-.*\.zip$")
_SCHEMA = pa.schema([("zip", pa.string()), ("entry", pa.string()), ("path", pa.string()), ("size", pa.int64()),
                     ("crc32", pa.int64()), ("blake3", pa.string())])
_IGNORED_DIRS = {"@eaDir", "@SynoEAStream", "@SynoResource", "#recycle"}
_IGNORED_FILES = {".DS_Store"}


class ZipFailed(Exception):
    """The zip cannot be fully extracted (unreadable, CRC, unsafe entry, conflicting names)."""


@dataclass
class Entry:
    zip: str
    entry: str  # the zip's own entry name
    path: str  # the name on disk, relative to the export folder: <zip stem>/<SMB-safe entry path>
    size: int
    crc32: int
    blake3: str


def export_of(zip_name: str) -> str | None:
    m = _ZIP_NAME.match(zip_name)
    return m.group(1) if m else None


def default_dest(cfg: Config, account: str) -> Path:
    return cfg.shoebox_root / "originals" / "takeout" / account


def zip_stem(zip_name: str) -> str:
    return zip_name.removesuffix(".zip")


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
    """D8: the entry's path as stored on disk, each component `c.rstrip(" .")` (SMB cannot show a name ending
    in a space or a dot). Refuses absolute paths, `..` and components that strip to nothing."""
    p = PurePosixPath(name)
    if p.is_absolute() or ".." in p.parts or "\\" in name or not p.parts:
        raise ZipFailed("unsafe entry path")
    parts = [c.rstrip(" .") for c in p.parts]
    if not all(parts):
        raise ZipFailed("unsafe entry path")
    return "/".join(parts)


def _disk_rel(zip_name: str, entry: str) -> str:
    return f"{zip_stem(zip_name)}/{_safe_relpath(entry)}"


def _nkey(rel: str) -> str:
    """Name-matching key: zip entry names and names on the SMB mount may differ in Unicode normalisation (NFC
    vs NFD), and the share is case-insensitive, so two names with one key are one file there."""
    return unicodedata.normalize("NFC", rel).casefold()


def _read_only(path: Path) -> None:
    try:
        mode = path.stat().st_mode
        os.chmod(path, mode & ~(stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH))
    except OSError:
        pass  # best effort over SMB (D4)


def _remove_tree(path: Path) -> None:
    """Remove a zip's own folder (read-only files included)."""
    def retry(func, p, _exc):
        os.chmod(p, 0o755 if os.path.isdir(p) else 0o644)
        func(p)

    if path.exists():
        shutil.rmtree(path, onexc=retry)


def _zip_file_paths(zip_path: Path) -> list[str]:
    """The zip's file entry paths from the central directory (no data read)."""
    with zipfile.ZipFile(zip_path) as zf:
        return [i.filename for i in zf.infolist() if not i.is_dir()]


def _extract_zip(zip_path: Path, tree: Path, tmp_dir: Path, progress=None) -> tuple[list[Entry], int]:
    """Extract every file entry of one zip into `<tree>/<zip stem>/`. Returns its entries and the bytes read.
    On any failure removes the zip's folder (this run's files and any earlier run's), then raises ZipFailed."""
    rows: list[Entry] = []
    seen: set[str] = set()
    nbytes = 0
    tmp = tmp_dir / f"{zip_path.name}.entry"
    try:
        try:
            with zipfile.ZipFile(zip_path) as zf:
                for info in zf.infolist():
                    if info.is_dir():
                        continue
                    rel = _disk_rel(zip_path.name, info.filename)
                    if _nkey(rel) in seen:
                        raise ZipFailed("two entries of the zip map to one name on disk")
                    seen.add(_nkey(rel))
                    digest = _copy_entry(zf, info, tmp)
                    nbytes += info.file_size
                    if progress:
                        progress(nbytes)
                    dest = tree / rel
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    os.replace(tmp, dest)
                    _read_only(dest)
                    rows.append(Entry(zip_path.name, info.filename, rel, info.file_size, info.CRC, digest))
        except (zipfile.BadZipFile, zipfile.LargeZipFile, OSError, EOFError, RuntimeError,
                NotImplementedError) as e:
            raise ZipFailed(type(e).__name__) from e
    except ZipFailed:
        tmp.unlink(missing_ok=True)
        _remove_tree(tree / zip_stem(zip_path.name))
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
    rows = [json.loads(line) for line in p.read_text().splitlines()]
    if any("entry" not in r for r in rows):
        return []  # a part from the merged layout (before D3/D8): the zip is redone
    return [Entry(zip_name, r["entry"], r["path"], r["size"], r["crc32"], r["blake3"]) for r in rows]


def _write_part(mdir: Path, zip_name: str, rows: list[Entry]) -> None:
    p = _part_path(mdir, zip_name)
    tmp = p.with_suffix(".tmp")
    tmp.write_text("".join(json.dumps({"entry": r.entry, "path": r.path, "size": r.size, "crc32": r.crc32,
                                       "blake3": r.blake3}) + "\n" for r in rows))
    os.replace(tmp, p)


def _intact(tree: Path, rows: list[Entry]) -> bool:
    for r in rows:
        try:
            if (tree / r.path).stat().st_size != r.size:
                return False
        except OSError:
            return False
    return True


def _write_manifest(mdir: Path, rows: list[Entry]) -> None:
    rows = sorted(rows, key=lambda e: e.path)
    table = pa.table({"zip": [e.zip for e in rows], "entry": [e.entry for e in rows],
                      "path": [e.path for e in rows], "size": [e.size for e in rows],
                      "crc32": [e.crc32 for e in rows], "blake3": [e.blake3 for e in rows]}, schema=_SCHEMA)
    tmp = mdir / "manifest.parquet.tmp"
    pq.write_table(table, tmp)
    os.replace(tmp, mdir / "manifest.parquet")


def _ignored(rel: str) -> bool:
    parts = PurePosixPath(rel).parts
    return parts[-1] in _IGNORED_FILES or any(p in _IGNORED_DIRS for p in parts[:-1])


def _index_tree(tree: Path) -> tuple[dict[str, list[str]], int]:
    """Files under the export folder by `_nkey`: (key -> on-disk relpaths, ignored count). On the NAS's
    case-sensitive filesystem IMG.JPG and img.jpg can both exist: both land under one key."""
    index: dict[str, list[str]] = {}
    ignored = 0
    for dirpath, _dirnames, filenames in os.walk(tree):
        rel_dir = Path(dirpath).relative_to(tree).as_posix()
        for name in filenames:
            rel = name if rel_dir == "." else f"{rel_dir}/{name}"
            if _ignored(rel):
                ignored += 1
            else:
                index.setdefault(_nkey(rel), []).append(rel)
    return index, ignored


def reconcile(mdir: Path, tree: Path, expected: dict[str, int] | None = None) -> list[str]:
    """D5 per zip folder: the zip's file entries (when given: zip name -> count) = files under its folder
    (minus ignored Synology metadata) = its manifest rows; every size matches; and the export folder holds
    nothing at the top level but zip folders. Returns mismatch descriptions (counts only, no entry names)."""
    rows = pq.read_table(mdir / "manifest.parquet").to_pylist()
    index, _ = _index_tree(tree) if tree.exists() else ({}, 0)
    on_disk = {rel for rels in index.values() for rel in rels}
    zip_names = sorted({r["zip"] for r in rows} | set(expected or {}))
    stems = {zip_stem(z): z for z in zip_names}
    rows_of = dict.fromkeys(zip_names, 0)
    for r in rows:
        rows_of[r["zip"]] += 1
    disk_of = dict.fromkeys(zip_names, 0)
    for rel in on_disk:
        top, _, rest = rel.partition("/")
        if rest and top in stems:
            disk_of[stems[top]] += 1
    unexpected = 0
    if tree.exists():
        unexpected = sum(1 for p in tree.iterdir() if p.name not in _IGNORED_DIRS | _IGNORED_FILES
                         and not (p.is_dir() and p.name in stems))
    problems = []
    for z in zip_names:
        if expected is not None and expected.get(z, 0) != rows_of[z]:
            problems.append(f"{z}: zip file entries {expected.get(z, 0)} != manifest rows {rows_of[z]}")
        if disk_of[z] != rows_of[z]:
            problems.append(f"{z}: files on disk {disk_of[z]} != manifest rows {rows_of[z]}")
    # match by NFC: the manifest path follows the zip entry, the SMB mount may list NFD names (D7)
    disk_nfc = {unicodedata.normalize("NFC", p): p for p in on_disk}
    listed = {unicodedata.normalize("NFC", r["path"]) for r in rows}
    found = [(r, disk_nfc.get(unicodedata.normalize("NFC", r["path"]))) for r in rows]
    missing = sum(1 for _, p in found if p is None)
    resized = sum(1 for r, p in found if p is not None and (tree / p).stat().st_size != r["size"])
    extra = sum(1 for p in disk_nfc if p not in listed)
    if missing:
        problems.append(f"{missing} manifest file(s) missing from the tree")
    if resized:
        problems.append(f"{resized} file(s) with a size other than the manifest")
    if extra:
        problems.append(f"{extra} file(s) in the tree not in the manifest")
    if unexpected:
        problems.append(f"{unexpected} unexpected top-level item(s) in the export folder (not a zip folder)")
    return problems


def _expected_files(zip_paths: list[Path]) -> dict[str, int]:
    """D5, counted independently of the part files: file entries per zip, from the central directories."""
    return {zp.name: len(_zip_file_paths(zp)) for zp in zip_paths}


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
    known: list[Entry] = []
    todo: list[Path] = []
    for zp in zip_paths:
        done = state.get(zp.name, {}).get("status") == "done"
        rows = _read_part(mdir, zp.name) if done else []
        if done and rows and _intact(tree, rows):
            known.extend(rows)
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
            _remove_part(mdir, zp.name)
            rows, nbytes = _extract_zip(zp, tree, mdir / "tmp", progress)
            _write_part(mdir, zp.name, rows)
            known.extend(rows)
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
    problems: list[str] = []
    if not failed:
        try:
            problems = reconcile(mdir, tree, _expected_files(zip_paths))
        except (zipfile.BadZipFile, OSError) as e:
            problems = [f"could not recount the zip entries ({type(e).__name__})"]
    done = total - failed
    print(f"export {ts}: {done}/{total} zip(s) done, {failed} failed; {len(known)} manifest rows")
    for p in problems:
        print(f"reconcile mismatch: {p}")
    if failed or problems:
        return 1
    print(f"export {ts} for {account} verified on the NAS; safe to delete from Drive")
    return 0


def _remove_part(mdir: Path, zip_name: str) -> None:
    _part_path(mdir, zip_name).unlink(missing_ok=True)


# --- verify-only mode (D7): the tree was extracted on the NAS (7-Zip), Shoebox proves it ------------------

_COUNTS = ("missing", "size_mismatch", "crc_mismatch", "conflicts")


def _pick(rels: list[str], rel: str) -> str:
    """The on-disk file for a zip entry: the one with the expected (NFC) name, else the first."""
    nfc = unicodedata.normalize("NFC", rel)
    return next((r for r in rels if unicodedata.normalize("NFC", r) == nfc), rels[0])


def _hash_file(path: Path) -> tuple[int, str]:
    """One streaming read in 1 MiB chunks: (CRC-32 as zlib computes it, BLAKE3 hex as `ids.asset_id`)."""
    crc, h = 0, blake3.blake3()
    with open(path, "rb") as f:
        while chunk := f.read(_CHUNK):
            crc = zlib.crc32(chunk, crc)
            h.update(chunk)
    return crc, h.hexdigest()


def _central_directory(zip_path: Path) -> list[zipfile.ZipInfo]:
    with zipfile.ZipFile(zip_path) as zf:
        return [i for i in zf.infolist() if not i.is_dir()]


def _check_zip_on_disk(zip_name: str, infos: list[zipfile.ZipInfo], tree: Path, index: dict[str, list[str]],
                       pool: ThreadPoolExecutor) -> tuple[list[Entry], dict[str, int]]:
    """Check every file entry of one zip against its own folder. Returns its rows and the per-kind failure
    counts."""
    counts = dict.fromkeys(_COUNTS, 0)
    rows: list[Entry] = []
    seen: set[str] = set()
    pending = []
    for info in infos:
        try:
            want = _disk_rel(zip_name, info.filename)
        except ZipFailed:
            counts["conflicts"] += 1
            continue
        key = _nkey(want)
        if key in seen:  # two entries of this zip map to one name on disk (D8 strip, case or NFC)
            counts["conflicts"] += 1
            continue
        seen.add(key)
        rels = index.get(key)
        if not rels:
            counts["missing"] += 1
            continue
        rel = _pick(rels, want)
        path = tree / rel
        try:
            size = path.stat().st_size
        except OSError:
            counts["missing"] += 1
            continue
        if size != info.file_size:
            counts["size_mismatch"] += 1
            continue
        pending.append((rel, info, pool.submit(_hash_file, path)))
    for rel, info, fut in pending:
        try:
            crc, digest = fut.result()
        except OSError:
            counts["missing"] += 1
            continue
        if crc != info.CRC:
            counts["crc_mismatch"] += 1
            continue
        rows.append(Entry(zip_name, info.filename, rel, info.file_size, info.CRC, digest))
    return rows, counts


def verify_extracted(cfg: Config, zips_dir: Path, account: str, export: str, dest_root: Path | None = None,
                     workers: int = 8, err=None) -> int:
    """D7: prove a tree extracted elsewhere (7-Zip on the NAS, one folder per zip) against the zips' central
    directories. Reads no zip data; streams each extracted file once for CRC-32 + BLAKE3. Returns the exit
    status."""
    err = err or sys.stderr
    zips_dir = Path(zips_dir).expanduser()
    dest_root = Path(dest_root).expanduser() if dest_root else default_dest(cfg, account)
    _check_dest(cfg, zips_dir, dest_root)  # refuse before reading anything
    tree, mdir = dest_root / export, dest_root / f"{export}.manifest"
    check_write_location(cfg, mdir)
    if not tree.is_dir():
        raise ValueError(f"the extracted tree of export {export} does not exist: {tree}")
    zip_paths = sorted(p for p in zips_dir.iterdir() if p.is_file() and export_of(p.name) == export)
    if not zip_paths:
        print("no Takeout zips found", file=err)
        return 1
    for d in (mdir, mdir / "parts"):
        d.mkdir(parents=True, exist_ok=True)

    total = len(zip_paths)
    print(f"export {export}: {total} zip(s); indexing the tree", file=err)
    index, ignored = _index_tree(tree)
    state = _latest_status(mdir)
    known: list[Entry] = []
    expected: dict[str, int] = {}
    failed = skipped = 0
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        for idx, zp in enumerate(zip_paths, 1):
            row = {"zip": zp.name, "size": None, "entries": 0, "status": "failed", **dict.fromkeys(_COUNTS, 0),
                   "ts": datetime.now(UTC).isoformat(timespec="seconds")}
            try:
                row["size"] = zp.stat().st_size
                infos = _central_directory(zp)
            except (zipfile.BadZipFile, zipfile.LargeZipFile, OSError, EOFError, ValueError) as e:
                row["reason"] = type(e).__name__
                failed += 1
                print(f"  [{idx}/{total}] FAILED ({row['reason']})", file=err)
                _append_status(mdir, row)
                continue
            expected[zp.name] = len(infos)
            if state.get(zp.name, {}).get("status") == "verified":
                prev = _read_part(mdir, zp.name)
                if prev and len(prev) == len(infos) and _intact(tree, prev):
                    known.extend(prev)
                    skipped += 1
                    continue
            t0 = time.monotonic()
            rows, counts = _check_zip_on_disk(zp.name, infos, tree, index, pool)
            row.update(counts, entries=len(infos))
            nbytes = sum(r.size for r in rows)
            secs = max(time.monotonic() - t0, 1e-6)
            if any(counts.values()):
                failed += 1
                print(f"  [{idx}/{total}] FAILED: " + ", ".join(f"{v} {k}" for k, v in counts.items() if v),
                      file=err)
            else:
                row["status"] = "verified"
                for r in rows:
                    _read_only(tree / r.path)
                _write_part(mdir, zp.name, rows)
                known.extend(rows)
                print(f"  [{idx}/{total}] verified: {len(rows)} file(s), {nbytes / 1e6:.0f} MB, "
                      f"{nbytes / 1e6 / secs:.1f} MB/s", file=err)
            _append_status(mdir, row)

    _write_manifest(mdir, known)
    problems: list[str] = []
    if not failed:
        problems = reconcile(mdir, tree, expected)
    print(f"export {export}: {total - failed}/{total} zip(s) verified ({skipped} already), {failed} failed; "
          f"{sum(expected.values())} file entries, {len(known)} manifest rows, {ignored} ignored NAS metadata "
          f"file(s)")
    for p in problems:
        print(f"reconcile mismatch: {p}")
    if failed or problems:
        return 1
    print(f"export {export} for {account} verified on the NAS; safe to delete from Drive")
    return 0
