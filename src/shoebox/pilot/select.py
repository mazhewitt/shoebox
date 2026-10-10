"""The pilot recipe: list bucket folders under a source root and pick evenly spaced files.

Selection is deterministic: candidates are sorted by relpath and picked at
`floor(i * (len - 1) / (n - 1) + 0.5)`; a single pick takes the middle (`(len - 1) // 2`).
Only the bucket folders are listed, never the whole share.
"""

import os
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

IMAGE_EXTS = frozenset({"jpg", "jpeg", "png", "heic", "heif", "webp", "tif", "tiff", "nef", "dng", "psd"})
RAW_EXTS = frozenset({"nef", "dng"})
JPG_EXTS = frozenset({"jpg", "jpeg"})
SCAN_EXTS = frozenset({"tif", "tiff", "psd"})
SKIP_DIRS = frozenset({"#recycle", "$RECYCLE.BIN", ".tmp.drivedownload"})
SKIP_FILES = frozenset({"thumbs.db"})
MAX_BYTES = 600 * 1024 * 1024
MAX_TOTAL = 50

Log = Callable[[str], None]


@dataclass(frozen=True)
class FileEntry:
    relpath: str
    bytes: int
    mtime: float

    @property
    def ext(self) -> str:
        return PurePosixPath(self.relpath).suffix.lower().lstrip(".")

    @property
    def folder(self) -> str:
        return str(PurePosixPath(self.relpath).parent)

    @property
    def stem_key(self) -> tuple[str, str]:
        p = PurePosixPath(self.relpath)
        return str(p.parent), p.stem.lower()


@dataclass(frozen=True)
class Bucket:
    """One stratum of the pilot recipe. `n` is per folder when `per_folder`, else the bucket total."""

    name: str
    folders: tuple[str, ...]
    n: int
    mode: str = "files"  # "files" or "pairs" (raw+jpg with the same base name, both taken)
    per_folder: bool = False
    spread_folders: bool = False  # pick `n` non-empty folders evenly, one file from each
    exts: frozenset[str] | None = None
    extra_files: tuple[str, ...] = ()  # always included (if present), on top of `n`
    exclude_words: tuple[str, ...] = ()
    want_exts: frozenset[str] = field(default_factory=frozenset)  # ensure one of these if any exist


def spread[T](seq: Sequence[T], n: int) -> list[T]:
    """Pick `n` evenly spaced elements of `seq` (all of it when `n >= len`)."""
    if n <= 0 or not seq:
        return []
    if n >= len(seq):
        return list(seq)
    if n == 1:
        return [seq[(len(seq) - 1) // 2]]
    return [seq[int(i * (len(seq) - 1) / (n - 1) + 0.5)] for i in range(n)]


def _skip_dir(name: str) -> bool:
    return name.startswith(".") or name in SKIP_DIRS or name.lower().endswith(".lrdata")


def walk(
    root: Path, folder: str, max_depth: int = 2, max_bytes: int = MAX_BYTES, log: Log | None = None
) -> list[FileEntry]:
    """Image files under `root/folder`, at most `max_depth` levels down, sorted by relpath.

    An unreadable folder or file is skipped and reported through `log`.
    """
    out: list[FileEntry] = []

    def visit(rel: str, depth: int) -> None:
        try:
            entries = list(os.scandir(root / rel))
        except (FileNotFoundError, NotADirectoryError):
            return
        except PermissionError as e:
            if log:
                log(f"folder unreadable, skipped: {rel} ({e.strerror})")
            return
        for e in entries:
            if e.is_dir(follow_symlinks=False):
                if depth < max_depth and not _skip_dir(e.name):
                    visit(f"{rel}/{e.name}", depth + 1)
                continue
            if e.name.startswith(".") or e.name.lower() in SKIP_FILES:
                continue
            fe_rel = f"{rel}/{e.name}"
            if PurePosixPath(e.name).suffix.lower().lstrip(".") not in IMAGE_EXTS:
                continue
            try:
                st = e.stat(follow_symlinks=False)
            except OSError as err:
                if log:
                    log(f"file unreadable, skipped: {fe_rel} ({err.strerror})")
                continue
            if st.st_size > max_bytes:
                continue
            out.append(FileEntry(fe_rel, st.st_size, st.st_mtime))

    visit(folder, 1)
    return sorted(out, key=lambda f: f.relpath)


def recipe_dirs(root: Path, recipe: Sequence[Bucket], max_depth: int = 2) -> list[str]:
    """Every folder `select` lists for `recipe` (relpaths, "" is the root), without stat'ing files.

    Includes bucket folders that do not exist yet and the parents of `extra_files`, so a
    snapshot of these listings can be taken before the first pick and catches anything a
    pick creates in them.
    """
    found: set[str] = {""}

    def visit(rel: str, depth: int) -> None:
        found.add(rel)
        if depth >= max_depth:
            return
        try:
            entries = list(os.scandir(root / rel))
        except OSError:
            return
        for e in entries:
            if e.is_dir(follow_symlinks=False) and not _skip_dir(e.name):
                visit(f"{rel}/{e.name}", depth + 1)

    for b in recipe:
        for folder in b.folders:
            visit(folder, 1)
        for rel in b.extra_files:
            parent = str(PurePosixPath(rel).parent)
            found.add("" if parent == "." else parent)
    return sorted(found)


def _stat_file(root: Path, rel: str, max_bytes: int) -> FileEntry | None:
    try:
        st = (root / rel).stat()
    except (FileNotFoundError, PermissionError):
        return None
    if st.st_size > max_bytes:
        return None
    return FileEntry(rel, st.st_size, st.st_mtime)


def _pairs(files: list[FileEntry]) -> list[tuple[FileEntry, FileEntry]]:
    by_key: dict[tuple[str, str], list[FileEntry]] = {}
    for f in files:
        by_key.setdefault(f.stem_key, []).append(f)
    pairs = []
    for members in by_key.values():
        raws = sorted((f for f in members if f.ext in RAW_EXTS), key=lambda f: f.relpath)
        jpgs = sorted((f for f in members if f.ext in JPG_EXTS), key=lambda f: f.relpath)
        if raws and jpgs:
            pairs.append((raws[0], jpgs[0]))
    return sorted(pairs, key=lambda p: p[0].relpath)


def _with_wanted(cands: list[FileEntry], chosen: list[FileEntry], want: frozenset[str]) -> list[FileEntry]:
    if not want or any(f.ext in want for f in chosen):
        return chosen
    wanted = [f for f in cands if f.ext in want]
    if not wanted or not chosen:
        return chosen
    w = spread(wanted, 1)[0]
    w_idx = cands.index(w)
    nearest = min(range(len(chosen)), key=lambda i: abs(cands.index(chosen[i]) - w_idx))
    chosen = chosen[:nearest] + [w] + chosen[nearest + 1 :]
    return sorted(chosen, key=lambda f: f.relpath)


def _bucket_picks(root: Path, b: Bucket, taken: set[str], max_bytes: int, log: Log) -> list[FileEntry]:
    listed: dict[str, list[FileEntry]] = {}
    for folder in b.folders:
        files = walk(root, folder, max_bytes=max_bytes, log=lambda m: log(f"[{b.name}] {m}"))
        if not (root / folder).is_dir():
            log(f"[{b.name}] folder missing: {folder}")
        elif not files:
            log(f"[{b.name}] folder has no image files: {folder}")
        files = [
            f
            for f in files
            if f.relpath not in taken
            and (b.exts is None or f.ext in b.exts)
            and not any(w in PurePosixPath(f.relpath).name.lower() for w in b.exclude_words)
        ]
        if files:
            listed[folder] = files

    picks: list[FileEntry] = []
    for rel in b.extra_files:
        f = _stat_file(root, rel, max_bytes)
        if f is None:
            log(f"[{b.name}] extra file missing or too large: {rel}")
        elif f.relpath not in taken:
            picks.append(f)
            taken.add(f.relpath)

    def take(cands: list[FileEntry], n: int) -> None:
        cands = [f for f in cands if f.relpath not in taken]
        if b.mode == "pairs":
            chosen = [f for pair in spread(_pairs(cands), n) for f in pair]
        else:
            chosen = _with_wanted(cands, spread(cands, n), b.want_exts)
        picks.extend(chosen)
        taken.update(f.relpath for f in chosen)

    if b.per_folder:
        for folder in b.folders:
            take(listed.get(folder, []), b.n)
    elif b.spread_folders:
        for folder in spread([f for f in b.folders if f in listed], b.n):
            take(listed[folder], 1)
    else:
        take(sorted((f for fs in listed.values() for f in fs), key=lambda f: f.relpath), b.n)
    return picks


def select(
    root: Path,
    recipe: Sequence[Bucket],
    max_total: int = MAX_TOTAL,
    max_bytes: int = MAX_BYTES,
    log: Log = print,
) -> tuple[list[tuple[str, FileEntry]], list[tuple[str, str]]]:
    """Run the recipe. Returns `[(bucket, file)]` in recipe order and `[(bucket, reason)]` for empty buckets."""
    taken: set[str] = set()
    picks: list[tuple[str, FileEntry]] = []
    skipped: list[tuple[str, str]] = []
    for b in recipe:
        got = _bucket_picks(root, b, taken, max_bytes, log)
        if not got:
            reason = "no eligible files in " + ", ".join(b.folders)
            log(f"[{b.name}] skipped: {reason}")
            skipped.append((b.name, reason))
        picks.extend((b.name, f) for f in got)
    if len(picks) > max_total:
        raise ValueError(f"recipe picked {len(picks)} files, over the cap of {max_total}")
    return picks, skipped


def assign_groups(items: list[dict], raw_exts: frozenset[str] = RAW_EXTS) -> None:
    """Set `group_id`/`group_rule` in place for same-folder siblings among `items`.

    `raw_exts` are the extensions counted as RAW (the pilot's NEF/DNG by default).
    """
    by_key: dict[tuple[str, str], list[dict]] = {}
    for it in items:
        it["group_id"] = None
        it["group_rule"] = None
        p = PurePosixPath(it["relpath"])
        by_key.setdefault((str(p.parent), p.stem.lower()), []).append(it)
    for members in by_key.values():
        exts = {m["ext"] for m in members}
        if exts & raw_exts and exts & JPG_EXTS:
            rule, kinds = "raw_jpeg_basename", raw_exts | JPG_EXTS
        elif exts & SCAN_EXTS and exts & JPG_EXTS:
            rule, kinds = "scan_export_basename", SCAN_EXTS | JPG_EXTS
        else:
            continue
        group = [m for m in members if m["ext"] in kinds]
        gid = "g-" + min(m["asset_id"] for m in group)[:12]
        for m in group:
            m["group_id"] = gid
            m["group_rule"] = rule
