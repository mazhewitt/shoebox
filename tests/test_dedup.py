"""Dedup: exact/same_pixels groups, capture rules, canonical member (design D6/D7, spec asset-dedup).

All inputs are synthetic frame rows and pixel-hash records; nothing reads a real source.
"""

import json

import pyarrow as pa
import pytest

from shoebox import dedup, inventory, store
from shoebox.config import Config


def aid(n: int) -> str:
    return f"{n:064x}"


def row(source, relpath, asset, kind=None, date=None, date_kind=None, exif=None, width=4000, height=3000):
    folder, _, name = relpath.rpartition("/")
    ext = name.rsplit(".", 1)[-1].lower()
    if kind is None:
        kind = "video" if ext in inventory.VIDEO_EXTS else "image"
    return {"source": source, "person": "maz", "relpath": relpath, "folder": folder, "name": name, "ext": ext,
            "kind": kind, "asset_id": asset, "status": "ok", "width": width, "height": height, "date": date,
            "date_kind": date_kind, "make": None, "model": None,
            "exif": json.dumps(exif) if exif is not None else None, "group_id": None, "group_rule": None,
            "dup_n": 1}


def px(**hashes):
    """{asset_id: pixel record} from keyword `a<n>=hash`."""
    return {aid(int(k[1:])): {"asset_id": aid(int(k[1:])), "status": "ok", "pixel_hash": h,
                              "width": 4000, "height": 3000} for k, h in hashes.items()}


def by_rule(caps, rule):
    return [c for c in caps if c["rule"] == rule]


def cap_of(caps, asset):
    hits = [c for c in caps if asset in {m["asset_id"] for m in c["members"]}]
    assert len(hits) == 1, hits
    return hits[0]


def roles(cap):
    return {m["asset_id"]: m["role"] for m in cap["members"]}


# --- 6.1 exact and same_pixels groups -----------------------------------------------------------------


def test_load_frames_accepts_tables_and_paths(tmp_path):
    rows = [row("a", "x/1.jpg", aid(1))]
    t = pa.Table.from_pylist(rows)
    p = tmp_path / "f.parquet"
    import pyarrow.parquet as pq
    pq.write_table(pa.Table.from_pylist([row("b", "y/2.jpg", aid(2))]), p)
    got = dedup.load_frames([t, p])
    assert [(r["source"], r["relpath"]) for r in got] == [("a", "x/1.jpg"), ("b", "y/2.jpg")]


def test_exact_group_is_one_asset_listing_every_location_across_sources():
    rows = [row("nas", "2004/a.jpg", aid(7)), row("takeout", "Photos/a.jpg", aid(7)), row("nas", "2004/b.jpg", aid(8))]
    groups = dedup.build_groups(rows, {})
    exact = [g for g in groups if g["type"] == "exact"]
    assert len(exact) == 1
    g = exact[0]
    assert g["group_id"] == "g-" + aid(7)[:12]
    assert g["members"] == [{"asset_id": aid(7), "locations": [["nas", "2004/a.jpg"], ["takeout", "Photos/a.jpg"]]}]


def test_same_pixels_links_distinct_assets_with_equal_pixel_hash():
    rows = [row("nas", "a/x.jpg", aid(5)), row("takeout", "b/x.jpg", aid(3)), row("nas", "c/y.jpg", aid(9))]
    groups = dedup.build_groups(rows, px(a5="h1", a3="h1", a9="h2"))
    sp = [g for g in groups if g["type"] == "same_pixels"]
    assert len(sp) == 1
    assert sp[0]["group_id"] == "g-" + aid(3)[:12]
    assert [m["asset_id"] for m in sp[0]["members"]] == [aid(3), aid(5)]


def test_reencoded_copy_is_not_grouped():
    rows = [row("nas", "a/x.jpg", aid(1)), row("nas", "b/x.jpg", aid(2))]
    assert dedup.build_groups(rows, px(a1="h1", a2="h2")) == []


def test_error_pixel_records_never_link():
    rows = [row("nas", "a/x.jpg", aid(1)), row("nas", "b/x.jpg", aid(2))]
    pixels = px(a1="h1", a2="h1")
    pixels[aid(2)]["status"] = "error"
    assert dedup.build_groups(rows, pixels) == []


def test_write_groups_to_keyed_store_and_rerun_is_idempotent(tmp_path):
    cfg = Config(shoebox_root=tmp_path / "shoebox", sources={})
    rows = [row("nas", "a/x.jpg", aid(1)), row("t", "b/x.jpg", aid(1)), row("nas", "c/y.jpg", aid(2))]
    pixels = px(a1="h", a2="h")
    groups = dedup.build_groups(rows, pixels)
    params = dedup.groups_params(rows, pixels, ("pixelhash-v1", "p" * 16))
    assert dedup.groups_params(rows, px(a1="h", a2="k"), ("pixelhash-v1", "p" * 16)) != params
    d = dedup.write_groups(cfg, groups, params)
    dedup.write_groups(cfg, groups, params)
    assert d.parent.parent.name == dedup.GROUPS_KIND
    recs = list(store.iter_dir(d))
    assert len(recs) == len(groups) == 2
    # aid(1) is both an exact group and the smallest member of a same_pixels group: one id, two keys
    assert {r["group_id"] for r in recs} == {g["group_id"] for g in groups} == {"g-" + aid(1)[:12]}
    assert {r["asset_id"] for r in recs} == {"exact/g-" + aid(1)[:12], "same_pixels/g-" + aid(1)[:12]}
    assert all(r["model_id"] == dedup.GROUPS_VERSION and r["status"] == "ok" for r in recs)


# --- 6.2 capture rules --------------------------------------------------------------------------------


def test_raw_jpeg_pair_same_folder():
    caps = dedup.build_captures([row("nas", "trip/DSC_1.NEF", aid(1)), row("nas", "trip/DSC_1.JPG", aid(2))], {})
    c = cap_of(caps, aid(1))
    assert c["rule"] == "raw_jpeg_basename" and c["rule_version"] == dedup.RULE_VERSIONS["raw_jpeg_basename"]
    assert roles(c) == {aid(1): "raw", aid(2): "primary"}


def test_same_stem_in_another_folder_is_not_grouped():
    caps = dedup.build_captures([row("nas", "a/DSC_1.NEF", aid(1)), row("nas", "b/DSC_1.JPG", aid(2))], {})
    assert all(c["rule"] == "single" for c in caps) and len(caps) == 2


def test_raw_jpeg_far_apart_exif_dates_stay_apart():
    caps = dedup.build_captures([
        row("nas", "t/DSC_1.NEF", aid(1), date="2004-05-06T07:08:09", date_kind="exif"),
        row("nas", "t/DSC_1.JPG", aid(2), date="2009-01-01T00:00:00", date_kind="exif"),
    ], {})
    assert len(caps) == 2


def test_raw_jpeg_close_exif_dates_pair():
    caps = dedup.build_captures([
        row("nas", "t/DSC_1.NEF", aid(1), date="2004-05-06T07:08:09", date_kind="exif"),
        row("nas", "t/DSC_1.JPG", aid(2), date="2004-05-06T07:08:10", date_kind="exif"),
    ], {})
    assert len(caps) == 1


def test_scan_export_pair():
    caps = dedup.build_captures([row("nas", "scans/s01.tif", aid(1)), row("nas", "scans/s01.jpg", aid(2))], {})
    c = cap_of(caps, aid(1))
    assert c["rule"] == "scan_export_basename"
    assert roles(c) == {aid(1): "primary", aid(2): "scan_export"}


def test_live_photo_pairs_still_and_clip():
    caps = dedup.build_captures([row("ip", "dcim/IMG_1.HEIC", aid(1)), row("ip", "dcim/IMG_1.MOV", aid(2))], {})
    c = cap_of(caps, aid(1))
    assert c["rule"] == "live_photo"
    assert roles(c) == {aid(1): "primary", aid(2): "motion_clip"}


def test_live_photo_tolerates_quicktime_utc_offset_but_not_other_gaps():
    near = [row("ip", "d/IMG_1.HEIC", aid(1), date="2020-07-01T14:00:00", date_kind="exif"),
            row("ip", "d/IMG_1.MOV", aid(2), date="2020-07-01T12:00:01", date_kind="exif")]
    assert len(dedup.build_captures(near, {})) == 1
    far = [near[0], row("ip", "d/IMG_1.MOV", aid(2), date="2020-07-01T12:07:30", date_kind="exif")]
    assert len(dedup.build_captures(far, {})) == 2


def test_motion_photo_from_xmp():
    exif = {"XMP-GCamera:MotionPhoto": 1, "XMP-GCamera:MotionPhotoVersion": 1}
    caps = dedup.build_captures([row("tk", "p/PXL_1.jpg", aid(1), exif=exif)], {})
    c = cap_of(caps, aid(1))
    assert c["rule"] == "motion_photo" and c["motion_source"] == "xmp"
    assert roles(c) == {aid(1): "primary"}


def test_motion_photo_microvideo_xmp():
    caps = dedup.build_captures([row("tk", "p/x.jpg", aid(1), exif={"XMP-GCamera:MicroVideo": 1})], {})
    assert caps[0]["rule"] == "motion_photo" and caps[0]["motion_source"] == "xmp"


@pytest.mark.parametrize("name", ["MVIMG_20180101_120000.jpg", "PXL_20210101_120000000.MP.jpg"])
def test_motion_photo_filename_only(name):
    caps = dedup.build_captures([row("tk", f"p/{name}", aid(1))], {})
    assert caps[0]["rule"] == "motion_photo" and caps[0]["motion_source"] == "filename_only"


def test_plain_jpeg_is_not_motion_photo():
    caps = dedup.build_captures([row("tk", "p/IMG_1.jpg", aid(1), exif={"IFD0:Make": "Apple"})], {})
    assert caps[0]["rule"] == "single"


@pytest.mark.parametrize("name", ["IMG_1-edited.jpg", "IMG_1_edited.JPG", "IMG_1-bearbeitet.jpg"])
def test_edited_copy_is_linked_not_a_member(name):
    caps = dedup.build_captures([row("tk", "p/IMG_1.jpg", aid(1)), row("tk", f"p/{name}", aid(2))], {})
    assert len(caps) == 1
    c = caps[0]
    assert roles(c) == {aid(1): "primary"}
    assert c["linked"] == [{"asset_id": aid(2), "role": "edited", "of": aid(1),
                            "rule": "edited", "rule_version": dedup.RULE_VERSIONS["edited"]}]
    assert c["group_size"] == 2


def test_edited_without_original_is_its_own_capture():
    caps = dedup.build_captures([row("tk", "p/IMG_1-edited.jpg", aid(2)), row("tk", "p/IMG_9.jpg", aid(3))], {})
    assert len(caps) == 2 and all(c["rule"] == "single" and c["linked"] == [] for c in caps)


def test_edited_with_original_in_another_folder_is_its_own_capture():
    caps = dedup.build_captures([row("tk", "a/IMG_1.jpg", aid(1)), row("tk", "b/IMG_1-edited.jpg", aid(2))], {})
    assert len(caps) == 2


def test_edited_original_inside_a_raw_pair_links_to_that_capture():
    caps = dedup.build_captures([row("n", "t/D_1.NEF", aid(1)), row("n", "t/D_1.JPG", aid(2)),
                                 row("n", "t/D_1-edited.jpg", aid(3))], {})
    assert len(caps) == 1 and caps[0]["rule"] == "raw_jpeg_basename"
    assert caps[0]["linked"][0]["asset_id"] == aid(3)


def test_burst_with_cover_is_one_capture_cover_primary():
    rows = [row("tk", "p/00000IMG_00000_BURST20190101123456789_COVER.jpg", aid(5)),
            row("tk", "p/00001IMG_00001_BURST20190101123456789.jpg", aid(1)),
            row("tk", "p/00002IMG_00002_BURST20190101123456789.jpg", aid(2))]
    caps = dedup.build_captures(rows, {})
    assert len(caps) == 1
    c = caps[0]
    assert c["rule"] == "burst"
    assert roles(c) == {aid(5): "primary", aid(1): "burst_member", aid(2): "burst_member"}


def test_burst_without_cover_stays_apart():
    rows = [row("tk", "p/00001IMG_00001_BURST20190101123456789.jpg", aid(1)),
            row("tk", "p/00002IMG_00002_BURST20190101123456789.jpg", aid(2))]
    caps = dedup.build_captures(rows, {})
    assert len(caps) == 2 and all(c["rule"] == "single" for c in caps)


def test_bursts_with_different_ids_or_folders_stay_apart():
    rows = [row("tk", "p/00000IMG_00000_BURST20190101123456789_COVER.jpg", aid(5)),
            row("tk", "p/00001IMG_00001_BURST20190101999999999.jpg", aid(1)),
            row("tk", "q/00002IMG_00002_BURST20190101123456789.jpg", aid(2))]
    assert len(dedup.build_captures(rows, {})) == 3


def test_every_asset_without_a_rule_is_a_single_capture_with_stable_id():
    rows = [row("n", "a/1.jpg", aid(1)), row("n", "a/2.jpg", aid(2)), row("n", "a/notes.txt", None, kind="other")]
    caps = dedup.build_captures(rows, {})
    assert sorted(m["asset_id"] for c in caps for m in c["members"]) == [aid(1), aid(2)]
    again = dedup.build_captures(list(reversed(rows)), {})
    assert caps == again
    assert all(c["capture_id"].startswith("c-") and len(c["capture_id"]) == 14 for c in caps)


def test_capture_id_derives_from_members():
    a = dedup.build_captures([row("n", "t/D.NEF", aid(1)), row("n", "t/D.JPG", aid(2))], {})
    b = dedup.build_captures([row("x", "u/D.NEF", aid(1)), row("x", "u/D.JPG", aid(2))], {})
    c = dedup.build_captures([row("n", "t/D.NEF", aid(1)), row("n", "t/D.JPG", aid(3))], {})
    assert a[0]["capture_id"] == b[0]["capture_id"] != c[0]["capture_id"]


def test_pair_copied_to_another_source_is_one_capture_with_all_locations():
    rows = [row("nas", "t/D.NEF", aid(1)), row("nas", "t/D.JPG", aid(2)),
            row("tk", "u/D.NEF", aid(1)), row("tk", "u/D.JPG", aid(2))]
    caps = dedup.build_captures(rows, {})
    assert len(caps) == 1
    m = {x["asset_id"]: x for x in caps[0]["members"]}
    assert m[aid(2)]["locations"] == [["nas", "t/D.JPG"], ["tk", "u/D.JPG"]]


def test_same_pixels_copies_join_one_capture():
    rows = [row("nas", "a/x.jpg", aid(1)), row("tk", "b/x.jpg", aid(2))]
    caps = dedup.build_captures(rows, px(a1="h", a2="h"))
    assert len(caps) == 1 and caps[0]["rule"] == "same_pixels"
    assert roles(caps[0]) == {aid(1): "primary", aid(2): "copy"}


def test_same_pixels_copy_of_a_paired_jpeg_joins_the_pair():
    rows = [row("nas", "t/D.NEF", aid(1)), row("nas", "t/D.JPG", aid(2)), row("tk", "u/export.jpg", aid(3))]
    caps = dedup.build_captures(rows, px(a2="h", a3="h"))
    assert len(caps) == 1 and caps[0]["rule"] == "raw_jpeg_basename"
    assert roles(caps[0]) == {aid(1): "raw", aid(2): "primary", aid(3): "copy"}


def test_conflicting_rule_groups_leave_the_assets_apart():
    # aid(2) is the JPG of a pair in one folder and the still of a Live Photo in another: no merge.
    rows = [row("n", "t/D.NEF", aid(1)), row("n", "t/D.JPG", aid(2)),
            row("i", "u/IMG.JPG", aid(2)), row("i", "u/IMG.MOV", aid(3))]
    caps = dedup.build_captures(rows, {})
    assert len(caps) == 3 and all(c["rule"] == "single" for c in caps)


def test_write_captures_kind_and_version(tmp_path):
    cfg = Config(shoebox_root=tmp_path / "shoebox", sources={})
    rows = [row("n", "t/D.NEF", aid(1)), row("n", "t/D.JPG", aid(2)), row("n", "t/E.jpg", aid(3))]
    caps = dedup.captures(rows, {}, consented=lambda s, r: True)
    d = dedup.write_captures(cfg, caps, dedup.capture_params(rows, {}, ("pixelhash-v1", "p" * 16)))
    assert d.parent.parent.name == dedup.CAPTURE_KIND == "capture"
    assert d.parent.name == dedup.CAPTURE_VERSION
    recs = store.index_records(store.iter_dir(d))
    assert set(recs) == {c["capture_id"] for c in caps}
    rec = recs[cap_of(caps, aid(1))["capture_id"]]
    assert rec["rule"] == "raw_jpeg_basename" and rec["canonical_asset_id"] == aid(2)


# --- 6.3 canonical member ----------------------------------------------------------------------------


def _canon(rows, pixels=None, consented=lambda s, r: True):
    caps = dedup.captures(rows, pixels or {}, consented=consented)
    assert len(caps) == 1, caps
    return caps[0]["canonical_asset_id"], caps[0]["canonical_reason"]


def test_canonical_single_member():
    assert _canon([row("n", "a/x.jpg", aid(1))]) == (aid(1), "single")


def test_canonical_step1_consented_copy():
    rows = [row("nas", "a/x.jpg", aid(1)), row("other", "b/x.jpg", aid(2))]
    assert _canon(rows, px(a1="h", a2="h"), consented=lambda s, r: s == "other") == (aid(2), "consent")


def test_canonical_step2_richest_metadata():
    gps = {"Composite:GPSLatitude": 51.5}
    rows = [row("n", "a/x.jpg", aid(1), date="2004-01-01T00:00:00", date_kind="mtime"),
            row("n", "b/x.jpg", aid(2), date="2004-01-01T00:00:00", date_kind="exif", exif=gps)]
    assert _canon(rows, px(a1="h", a2="h")) == (aid(2), "metadata")


def test_canonical_step3_resolution():
    rows = [row("n", "t/D.NEF", aid(1), width=6000, height=4000), row("n", "t/D.JPG", aid(2), width=1600, height=1200)]
    assert _canon(rows) == (aid(1), "resolution")


def test_canonical_step3_prefers_pixelhash_dimensions_over_frame():
    rows = [row("n", "a/x.jpg", aid(1), width=100, height=100), row("n", "b/x.jpg", aid(2), width=100, height=100)]
    pixels = px(a1="h", a2="h")
    pixels[aid(2)].update(width=5000, height=4000)
    assert _canon(rows, pixels) == (aid(2), "resolution")


def test_canonical_step4_processed_over_raw():
    rows = [row("n", "t/D.NEF", aid(1)), row("n", "t/D.JPG", aid(2))]
    assert _canon(rows) == (aid(2), "processed")


def test_canonical_step5_original_over_edited_name():
    rows = [row("n", "a/x-edited.jpg", aid(1)), row("t", "b/x.jpg", aid(2))]
    assert _canon(rows, px(a1="h", a2="h")) == (aid(2), "original")


def test_canonical_step6_smallest_asset_id():
    rows = [row("n", "a/x.jpg", aid(9)), row("t", "b/y.jpg", aid(4))]
    assert _canon(rows, px(a9="h", a4="h")) == (aid(4), "asset_id")


def test_canonical_never_a_motion_clip_or_linked_edit():
    rows = [row("i", "d/IMG_1.HEIC", aid(9), width=100, height=100), row("i", "d/IMG_1.MOV", aid(1)),
            row("i", "d/IMG_1-edited.jpg", aid(2), width=9000, height=9000)]
    assert _canon(rows)[0] == aid(9)


def test_canonical_is_deterministic_across_runs_and_input_order():
    rows = [row("nas", "a/x.jpg", aid(3)), row("tk", "b/x.jpg", aid(2)), row("n", "t/D.NEF", aid(5)),
            row("n", "t/D.JPG", aid(6)), row("n", "s/s.tif", aid(7)), row("n", "s/s.jpg", aid(8))]
    pixels = px(a3="h", a2="h")
    one = dedup.captures(rows, pixels, consented=lambda s, r: s != "tk")
    two = dedup.captures(list(reversed(rows)), pixels, consented=lambda s, r: s != "tk")
    assert one == two
    assert {c["canonical_reason"] for c in one} == {"consent", "processed", "asset_id"}  # scan TIFF is not RAW


def test_worklist_fields_present():
    caps = dedup.captures([row("n", "t/D.NEF", aid(1)), row("n", "t/D.JPG", aid(2))], {}, consented=lambda s, r: True)
    c = caps[0]
    for k in ("capture_id", "rule", "rule_version", "members", "linked", "canonical_asset_id",
              "canonical_reason", "group_size"):
        assert k in c
    assert c["group_size"] == 2


def test_same_pixels_never_joins_captures_of_different_rules():
    rows = [row("n", "t/D.NEF", aid(1)), row("n", "t/D.JPG", aid(2)),
            row("i", "u/IMG.JPG", aid(3)), row("i", "u/IMG.MOV", aid(4))]
    caps = dedup.build_captures(rows, px(a2="h", a3="h"))
    assert sorted(c["rule"] for c in caps) == ["live_photo", "raw_jpeg_basename"]
