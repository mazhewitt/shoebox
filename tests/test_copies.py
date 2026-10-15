"""Cross-source copy index (design D5): latest frame of every configured source, plus same-pixel links."""

from datetime import date

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from shoebox import config, copies, store

A, B, C = "a" * 64, "b" * 64, "c" * 64


def cfg(tmp_path, **added) -> config.Config:
    sources = {
        n: config.Source(n, tmp_path / n, "owner", synced=False, hosted_consent=True, added=d)
        for n, d in (added or {"importantfiles": date(2026, 10, 10)}).items()
    }
    return config.Config(shoebox_root=tmp_path / "shoebox", sources=sources)


def write_frame(c: config.Config, day: str, rows: list[tuple]) -> None:
    """A synthetic frame with just the columns the index reads."""
    d = c.shoebox_root / "frame"
    d.mkdir(parents=True, exist_ok=True)
    s = pa.string()
    t = pa.table({"source": [r[0] for r in rows], "relpath": [r[1] for r in rows],
                  "asset_id": [r[2] for r in rows]},
                 schema=pa.schema([("source", s), ("relpath", s), ("asset_id", s)]))
    pq.write_table(t, d / f"frame-{day}.parquet")


def write_pixels(c: config.Config, recs: dict[str, str], params="p1") -> None:
    for aid, ph in recs.items():
        store.append(c, {"asset_id": aid, "model_id": "pixelhash-v1", "params_hash": params, "status": "ok",
                         "pixel_hash": ph}, kind="pixelhash")


def test_byte_copies_across_sources(tmp_path):
    c = cfg(tmp_path, importantfiles=date(2026, 10, 10), jude=date(2026, 10, 10))
    write_frame(c, "2026-10-10", [("importantfiles", "P/a.jpg", A), ("importantfiles", "P/b.jpg", B)])
    write_frame(c, "2026-10-12", [("jude", "x/a.jpg", A), ("jude", "x/none", None)])
    idx = copies.load(c)
    assert idx.missing == ()
    assert sorted(idx.locations(A)) == [("importantfiles", "P/a.jpg", A), ("jude", "x/a.jpg", A)]
    assert idx.locations(B) == [("importantfiles", "P/b.jpg", B)]
    assert idx.locations(C) == []


def test_latest_frame_per_source_wins(tmp_path):
    c = cfg(tmp_path)
    write_frame(c, "2026-10-10", [("importantfiles", "old/a.jpg", A)])
    write_frame(c, "2026-10-11", [("importantfiles", "new/a.jpg", A)])
    assert copies.load(c).locations(A) == [("importantfiles", "new/a.jpg", A)]


def test_unconfigured_source_rows_ignored(tmp_path):
    c = cfg(tmp_path)
    write_frame(c, "2026-10-10", [("importantfiles", "a.jpg", A), ("gone", "a.jpg", A)])
    assert copies.load(c).locations(A) == [("importantfiles", "a.jpg", A)]


def test_same_pixel_links(tmp_path):
    c = cfg(tmp_path)
    write_frame(c, "2026-10-10", [("importantfiles", "a.jpg", A), ("importantfiles", "b.jpg", B),
                                  ("importantfiles", "c.jpg", C)])
    write_pixels(c, {A: "px1", B: "px1", C: "px2"})
    idx = copies.load(c)
    assert idx.pixel_group(A) == {A, B}
    assert idx.pixel_group(C) == {C}
    assert sorted(idx.copies(A)) == [("importantfiles", "a.jpg", A), ("importantfiles", "b.jpg", B)]


def test_pixel_hashes_from_different_params_do_not_link(tmp_path):
    c = cfg(tmp_path)
    write_frame(c, "2026-10-10", [("importantfiles", "a.jpg", A)])
    write_pixels(c, {A: "px1"}, params="p1")
    write_pixels(c, {B: "px1"}, params="p2")
    assert copies.load(c).pixel_group(A) == {A}


def test_source_without_frame_is_missing(tmp_path):
    c = cfg(tmp_path, importantfiles=date(2026, 10, 10), jude=date(2026, 10, 11))
    write_frame(c, "2026-10-10", [("importantfiles", "a.jpg", A)])
    assert copies.load(c).missing == ("jude",)


def test_frame_older_than_config_entry_is_missing(tmp_path):
    c = cfg(tmp_path, importantfiles=date(2026, 10, 10), jude=date(2026, 10, 12))
    write_frame(c, "2026-10-11", [("importantfiles", "a.jpg", A), ("jude", "a.jpg", A)])
    assert copies.load(c).missing == ("jude",)


def test_no_frame_dir_all_missing(tmp_path):
    assert copies.load(cfg(tmp_path)).missing == ("importantfiles",)


def test_config_added_key(tmp_path):
    base = ('shoebox_root = "/tmp/s"\n[sources.a]\nroot = "/tmp/a"\nperson = "o"\nsynced = false\n'
            "hosted_consent = true\n")
    p = tmp_path / "a.toml"
    p.write_text(base + "added = 2026-10-10\n")
    assert config.load(p).sources["a"].added == date(2026, 10, 10)
    p.write_text(base + 'added = "2026-10-09"\n')
    assert config.load(p).sources["a"].added == date(2026, 10, 9)
    p.write_text(base)
    assert config.load(p).sources["a"].added is None
    p.write_text(base + 'added = "soon"\n')
    with pytest.raises(ValueError, match="added"):
        config.load(p)


def test_repo_config_records_added():
    cfg_ = config.load(config.REPO_ROOT / "config" / "shoebox.toml")
    assert cfg_.sources["importantfiles"].added == date(2026, 10, 10)


# ---------- pixel-hash coverage (same-pixel copies only count if hashed) ----------


def write_kind_frame(c: config.Config, day: str, rows: list[tuple]) -> None:
    """(source, relpath, asset_id, kind) rows."""
    d = c.shoebox_root / "frame"
    d.mkdir(parents=True, exist_ok=True)
    pq.write_table(pa.table({k: [r[i] for r in rows] for i, k in enumerate(("source", "relpath", "asset_id", "kind"))}),
                   d / f"frame-{day}.parquet")


def write_derive_index(c: config.Config, recs: dict[str, str], profile="infer-v1") -> None:
    import json

    p = c.derived_dir / profile / "index.jsonl"
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a") as f:
        for aid, status in recs.items():
            f.write(json.dumps({"asset_id": aid, "status": status}) + "\n")


def test_unhashed_and_undecodable_image_rows(tmp_path):
    D, V = "d" * 64, "e" * 64
    c = cfg(tmp_path)
    write_kind_frame(c, "2026-10-10", [("importantfiles", "a.jpg", A, "image"), ("importantfiles", "b.jpg", B, "image"),
                                       ("importantfiles", "c.tif", C, "image"), ("importantfiles", "d.jpg", D, "image"),
                                       ("importantfiles", "v.mp4", V, "video")])
    write_pixels(c, {A: "px1"})
    write_derive_index(c, {B: "ok", C: "error", D: "error"})
    write_derive_index(c, {D: "ok"})  # latest record wins: D was re-derived, but has no pixel hash yet
    idx = copies.load(c)
    assert sorted(idx.unhashed) == [("importantfiles", "b.jpg", B), ("importantfiles", "d.jpg", D)]
    assert idx.undecodable == (("importantfiles", "c.tif", C),)


def test_frame_without_kind_column_counts_every_row_as_image(tmp_path):
    c = cfg(tmp_path)
    write_frame(c, "2026-10-10", [("importantfiles", "a.jpg", A)])
    assert copies.load(c).unhashed == (("importantfiles", "a.jpg", A),)


def test_repo_takeout_source_fails_closed_before_its_first_frame(tmp_path):
    """add-takeout-landing 2.1: the registered Takeout source counts as missing (hosted sends refuse) until it has a
    frame dated on or after its `added` date."""
    import dataclasses

    repo = config.load(config.REPO_ROOT / "config" / "shoebox.toml")
    src = repo.sources["takeout-owner"]
    assert (src.person, src.synced, src.hosted_consent, src.added) == ("owner", False, True, date(2026, 10, 15))
    c = dataclasses.replace(repo, shoebox_root=tmp_path)
    write_frame(c, "2026-10-14", [("importantfiles", "a.jpg", A), ("takeout-owner", "z/a.jpg", A)])
    assert copies.load(c).missing == ("takeout-owner",)
    write_frame(c, "2026-10-15", [("takeout-owner", "z/a.jpg", A)])
    assert copies.load(c).missing == ()
