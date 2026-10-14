"""`shoebox takeout flags` (add-takeout-landing D9) on a synthetic verified extract."""

import json

import pyarrow.parquet as pq
import pytest

from shoebox import config, quarantine, takeout

TS = "20261012T083010Z"
Z1 = "takeout-20261012T083010Z-001"
Z2 = "takeout-20261012T083010Z-002"
D = "Takeout/Google Photos/Photos from 2019"
PARTNER = {"googlePhotosOrigin": {"fromPartnerSharing": {}}}
LOCKED = {"googlePhotosOrigin": {"webUpload": {"computerUpload": {}}}, "inLockedFolder": True}
PLAIN = {"googlePhotosOrigin": {"mobileUpload": {}}}


@pytest.fixture
def env(tmp_path):
    src = config.Source("importantfiles", tmp_path / "ro", "owner", synced=True, hosted_consent=True)
    cfg = config.Config(shoebox_root=tmp_path / "shoebox", sources={"importantfiles": src})
    return cfg, tmp_path / "evals" / "quarantine.jsonl"


def tree(cfg):
    return takeout.default_dest(cfg, "owner") / TS


def build(cfg, files: dict[str, object], unlisted: tuple[str, ...] = ()) -> dict[str, str]:
    """Write files (dict/list -> JSON sidecar, else bytes) under the export tree and a manifest naming them all
    (except `unlisted`). Returns {path: asset_id}."""
    t = tree(cfg)
    rows, ids = [], {}
    for i, (rel, content) in enumerate(sorted(files.items())):
        p = t / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        data = json.dumps(content).encode() if isinstance(content, dict | list) else content
        p.write_bytes(data)
        ids[rel] = f"{i:064x}"
        if rel not in unlisted:
            rows.append(takeout.Entry(rel.split("/")[0] + ".zip", rel, rel, len(data), 0, ids[rel]))
    m = takeout.default_dest(cfg, "owner") / f"{TS}.manifest"
    m.mkdir(parents=True, exist_ok=True)
    takeout._write_manifest(m, rows)
    return ids


def side(title: str, flags: dict) -> dict:
    return {"title": title, "photoTakenTime": {"timestamp": "1"}, **flags}


def run(env, capsys, **kw):
    cfg, q = env
    code = takeout.flags(cfg, "owner", TS, quarantine_path=q, **kw)
    out = capsys.readouterr()
    return code, out.out


def verdicts(q) -> dict[str, tuple[str, str]]:
    return {a: (r["verdict"], r["verdict_by"]) for a, r in quarantine.load(q).items()}


EX = ("exclude", "owner-rule:takeout-partner-shared")
PR = ("private", "owner-rule:takeout-locked-folder")


def test_partner_and_locked(env, capsys):
    cfg, q = env
    ids = build(cfg, {
        f"{Z1}/{D}/IMG_P.jpg": b"p", f"{Z1}/{D}/IMG_P.jpg.supplemental-metadata.json": side("IMG_P.jpg", PARTNER),
        f"{Z1}/{D}/IMG_L.jpg": b"l", f"{Z1}/{D}/IMG_L.jpg.supplemental-metadata.json": side("IMG_L.jpg", LOCKED),
        f"{Z1}/{D}/IMG_N.jpg": b"n", f"{Z1}/{D}/IMG_N.jpg.supplemental-metadata.json": side("IMG_N.jpg", PLAIN),
        f"{Z1}/Takeout/Google Photos/Locked Folder/IMG_X.jpg": b"x",
        f"{Z1}/{D}/metadata.json": {"title": "IMG_P.jpg", **PARTNER},  # album metadata, not a sidecar
        f"{Z1}/{D}/IMG_M.jpg": b"m",
    })
    code, out = run(env, capsys)
    assert code == 0
    assert verdicts(q) == {ids[f"{Z1}/{D}/IMG_P.jpg"]: EX, ids[f"{Z1}/{D}/IMG_L.jpg"]: PR,
                           ids[f"{Z1}/Takeout/Google Photos/Locked Folder/IMG_X.jpg"]: PR}
    assert "IMG" not in out and "Locked Folder" not in out and "Photos from" not in out
    assert "3 sidecars read" in out and "3 verdicts written" in out


def test_truncated_sidecar_names(env, capsys):
    cfg, q = env
    long = "A" * 40
    ids = build(cfg, {
        f"{Z1}/{D}/{long}.jpg": b"a", f"{Z1}/{D}/{long}.jpg.supplemental-metad.json": side("x", PARTNER),
        f"{Z1}/{D}/{long}BCDEFGH.jpg": b"b", f"{Z1}/{D}/{long}BCDE.json": side("y", PARTNER),
    })
    code, _ = run(env, capsys)
    assert code == 0
    assert verdicts(q) == {ids[f"{Z1}/{D}/{long}.jpg"]: EX, ids[f"{Z1}/{D}/{long}BCDEFGH.jpg"]: EX}


def test_duplicate_numbering(env, capsys):
    cfg, q = env
    ids = build(cfg, {
        f"{Z1}/{D}/name.jpg": b"0", f"{Z1}/{D}/name.jpg.supplemental-metadata.json": side("name.jpg", PLAIN),
        f"{Z1}/{D}/name(1).jpg": b"1",
        f"{Z1}/{D}/name.jpg.supplemental-metadata(1).json": side("name.jpg", PARTNER),
    })
    code, _ = run(env, capsys)
    assert code == 0
    assert verdicts(q) == {ids[f"{Z1}/{D}/name(1).jpg"]: EX}


def test_companions_inherit_same_dir_same_zip_only(env, capsys):
    cfg, q = env
    ids = build(cfg, {
        f"{Z1}/{D}/IMG_1.HEIC": b"h", f"{Z1}/{D}/IMG_1.MOV": b"v", f"{Z1}/{D}/IMG_1-edited.HEIC": b"e",
        f"{Z1}/{D}/IMG_1.HEIC.supplemental-metadata.json": side("IMG_1.HEIC", LOCKED),
        f"{Z1}/{D}/IMG_10.HEIC": b"o",
        f"{Z1}/Takeout/Google Photos/Other/IMG_1.MOV": b"elsewhere",
        f"{Z2}/{D}/IMG_1.MOV": b"other zip",
    })
    code, out = run(env, capsys)
    assert code == 0
    assert verdicts(q) == {ids[f"{Z1}/{D}/IMG_1.HEIC"]: PR, ids[f"{Z1}/{D}/IMG_1.MOV"]: PR,
                           ids[f"{Z1}/{D}/IMG_1-edited.HEIC"]: PR}
    assert "2 companions" in out


def test_title_nfc_and_case_insensitive(env, capsys):
    import unicodedata
    cfg, q = env
    name = unicodedata.normalize("NFD", "Café.JPG")
    ids = build(cfg, {f"{Z1}/{D}/{name}": b"c", f"{Z1}/{D}/x.json": side("café.jpg", PARTNER)})
    assert run(env, capsys)[0] == 0
    assert verdicts(q) == {ids[f"{Z1}/{D}/{name}"]: EX}


def test_both_rules_exclude(env, capsys):
    cfg, q = env
    ids = build(cfg, {
        f"{Z1}/{D}/IMG_B.jpg": b"b",
        f"{Z1}/{D}/IMG_B.jpg.supplemental-metadata.json": side("IMG_B.jpg", {**PARTNER, "inLockedFolder": True}),
        f"{Z1}/Takeout/Google Photos/Locked Folder/IMG_C.jpg": b"c",
        f"{Z1}/Takeout/Google Photos/Locked Folder/IMG_C.jpg.supplemental-metadata.json": side("IMG_C.jpg", PARTNER),
    })
    assert run(env, capsys)[0] == 0
    assert verdicts(q) == {ids[f"{Z1}/{D}/IMG_B.jpg"]: EX,
                           ids[f"{Z1}/Takeout/Google Photos/Locked Folder/IMG_C.jpg"]: EX}


def test_same_bytes_in_both_rules_gets_exclude(env, capsys):
    cfg, q = env
    files = {f"{Z1}/{D}/IMG_P.jpg": b"p", f"{Z1}/{D}/IMG_P.jpg.supplemental-metadata.json": side("IMG_P.jpg", PARTNER),
             f"{Z2}/Takeout/Google Photos/Locked Folder/IMG_P.jpg": b"p"}
    ids = build(cfg, files)
    # one asset (same BLAKE3) at two paths
    m = takeout.default_dest(cfg, "owner") / f"{TS}.manifest"
    rows = [takeout.Entry(r["zip"], r["entry"], r["path"], r["size"], 0, ids[f"{Z1}/{D}/IMG_P.jpg"]
                          if r["path"].endswith("IMG_P.jpg") else r["blake3"])
            for r in pq.read_table(m / "manifest.parquet").to_pylist()]
    takeout._write_manifest(m, rows)
    assert run(env, capsys)[0] == 0
    assert verdicts(q) == {ids[f"{Z1}/{D}/IMG_P.jpg"]: EX}


def test_more_restrictive_existing_verdict_kept(env, capsys):
    cfg, q = env
    ids = build(cfg, {
        f"{Z1}/{D}/IMG_L.jpg": b"l", f"{Z1}/{D}/IMG_L.jpg.supplemental-metadata.json": side("IMG_L.jpg", LOCKED),
        f"{Z1}/{D}/IMG_K.jpg": b"k", f"{Z1}/{D}/IMG_K.jpg.supplemental-metadata.json": side("IMG_K.jpg", LOCKED),
    })
    a_l, a_k = ids[f"{Z1}/{D}/IMG_L.jpg"], ids[f"{Z1}/{D}/IMG_K.jpg"]
    quarantine.flag([a_l, a_k], "nudity", path=q)
    quarantine.record_verdict(a_l, "exclude", "owner", path=q)
    quarantine.record_verdict(a_k, "include", "owner", path=q)
    assert run(env, capsys)[0] == 0
    assert verdicts(q) == {a_l: ("exclude", "owner"), a_k: PR}
    assert quarantine.load(q)[a_k]["screen"] == "nudity"


def test_unmatched_flagged_sidecar_fails_after_writing(env, capsys):
    cfg, q = env
    ids = build(cfg, {
        f"{Z1}/{D}/IMG_P.jpg": b"p", f"{Z1}/{D}/IMG_P.jpg.supplemental-metadata.json": side("IMG_P.jpg", PARTNER),
        f"{Z1}/{D}/GONE.jpg.supplemental-metadata.json": side("GONE.jpg", PARTNER),
        f"{Z2}/{D}/ELSE.jpg": b"e",
        f"{Z1}/{D}/ELSE.jpg.supplemental-metadata.json": side("ELSE.jpg", LOCKED),  # media in another zip
        f"{Z1}/{D}/PLAIN.jpg.supplemental-metadata.json": side("PLAIN.jpg", PLAIN),  # unflagged: no matter
    })
    code, out = run(env, capsys)
    assert code == 1
    assert verdicts(q) == {ids[f"{Z1}/{D}/IMG_P.jpg"]: EX}
    assert "2 unmatched flagged sidecars" in out
    assert "GONE" not in out and "ELSE" not in out


def test_rerun_appends_nothing(env, capsys):
    cfg, q = env
    build(cfg, {
        f"{Z1}/{D}/IMG_P.jpg": b"p", f"{Z1}/{D}/IMG_P.jpg.supplemental-metadata.json": side("IMG_P.jpg", PARTNER),
        f"{Z1}/Takeout/Google Photos/Locked Folder/IMG_X.jpg": b"x",
    })
    assert run(env, capsys)[0] == 0
    before = q.read_bytes()
    code, out = run(env, capsys)
    assert code == 0 and q.read_bytes() == before
    assert "0 verdicts written" in out


def test_dry_run_writes_nothing(env, capsys):
    cfg, q = env
    build(cfg, {f"{Z1}/{D}/IMG_P.jpg": b"p",
                f"{Z1}/{D}/IMG_P.jpg.supplemental-metadata.json": side("IMG_P.jpg", PARTNER)})
    code, out = run(env, capsys, dry_run=True)
    assert code == 0 and not q.exists()
    assert "1 verdicts to write" in out


def test_flagged_media_missing_from_manifest_fails(env, capsys):
    cfg, _q = env
    build(cfg, {f"{Z1}/{D}/IMG_P.jpg": b"p",
                f"{Z1}/{D}/IMG_P.jpg.supplemental-metadata.json": side("IMG_P.jpg", PARTNER)},
          unlisted=(f"{Z1}/{D}/IMG_P.jpg",))
    code, out = run(env, capsys)
    assert code == 1 and "1 flagged media not in the manifest" in out


def test_cli(env, monkeypatch, capsys):
    from shoebox import cli
    from shoebox.commands import takeout as cmd

    args = cli.build_parser().parse_args(["takeout", "flags", "--account", "owner", "--export", TS,
                                          "--workers", "3", "--dry-run", "--quarantine", "/q.jsonl"])
    assert args.func is cmd.cmd_flags and args.workers == 3 and args.dry_run
    seen = {}
    monkeypatch.setattr(cmd.config, "load", lambda _p: env[0])
    monkeypatch.setattr(cmd.takeout, "flags", lambda *a, **kw: seen.update(a=a, kw=kw) or 0)
    assert args.func(args) == 0
    assert seen["a"][1:] == ("owner", TS) and seen["kw"]["workers"] == 3 and seen["kw"]["dry_run"]
    assert str(seen["kw"]["quarantine_path"]) == "/q.jsonl"
