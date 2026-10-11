"""Owner review page and ingest for screen-flagged assets (content-screen spec, design D4). Synthetic only.

The page and thumbnails are never opened here: tests look at file names, sizes and the page's JSON data."""

import json
import re
from pathlib import Path

import pytest
from derived_fixture import write_derived

from shoebox import config, quarantine
from shoebox.cli import main
from shoebox.screenreview import ingest as ing
from shoebox.screenreview import page as pg


def aid(i: int) -> str:
    return f"{i:02d}" + "e" * 62


def jpeg(w=900, h=600) -> bytes:
    import pyvips

    return pyvips.Image.black(w, h, bands=3).jpegsave_buffer()


@pytest.fixture
def env(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    cfg = config.Config(shoebox_root=tmp_path / "shoebox", sources={})
    for i in range(6):
        write_derived(cfg, aid(i), jpeg())
    quarantine.flag([aid(0), aid(1)], "nudity")
    quarantine.flag([aid(2)], "document")
    return cfg, home


def page_data(out: Path) -> dict:
    html = (out / "index.html").read_text()
    m = re.search(r'<script type="application/json" id="data">(.*?)</script>', html, re.DOTALL)
    assert m
    return json.loads(m.group(1))


def build(cfg, out, **kw):
    return pg.build_page(cfg, out, quarantine.load(), **kw)


# ---------- page ----------


def test_page_from_stub_flag_list(env):
    cfg, home = env
    quarantine.record_verdict(aid(2), "include", "owner")  # already ruled: not on the page
    out = home / "shoebox-review" / "screen"
    res = build(cfg, out)
    assert (res.flagged, res.sampled, res.no_derived) == (2, 0, 0)
    data = page_data(out)
    assert [(it["n"], it["screen"]) for it in data["items"]] == [(1, "nudity"), (2, "nudity")]
    assert data["page_id"] == res.page_id
    html = (out / "index.html").read_text()
    for a in (aid(0), aid(1)):
        assert a not in html  # asset ids live only in the page-map
    for word in ("blur(", "localStorage", "Download answers", "mark page as include", "keydown"):
        assert word in html
    assert "http://" not in html and "https://" not in html and "<link" not in html
    thumbs = sorted(p.name for p in (out / "img").iterdir())
    assert thumbs == ["0001.jpg", "0002.jpg"]
    assert all((out / "img" / t).stat().st_size > 0 for t in thumbs)
    pm = json.loads(res.page_map.read_text())
    assert out not in res.page_map.parents and res.page_map.parent == out.parent
    assert pm["page_id"] == res.page_id
    assert {k: v["asset_id"] for k, v in pm["items"].items()} == {"1": aid(0), "2": aid(1)}


def test_thumbnails_are_small(env):
    import pyvips

    cfg, home = env
    out = home / "r"
    build(cfg, out)
    im = pyvips.Image.new_from_file(str(out / "img" / "0001.jpg"))
    assert max(im.width, im.height) <= pg.THUMB_PX


def test_seeded_unflagged_sample_for_recall(env):
    cfg, home = env
    unflagged = [aid(3), aid(4), aid(5), aid(0)]  # aid(0) is flagged: never counted as a sample
    a = build(cfg, home / "a", unflagged=unflagged, sample=2, seed=7)
    b = build(cfg, home / "b", unflagged=unflagged, sample=2, seed=7)
    assert (a.flagged, a.sampled) == (3, 2)
    pa, pb = (json.loads(r.page_map.read_text()) for r in (a, b))
    assert pa["items"] == pb["items"]
    sampled = {v["asset_id"] for v in pa["items"].values() if not v["flagged"]}
    assert len(sampled) == 2 and sampled <= {aid(3), aid(4), aid(5)}
    assert {it["screen"] for it in page_data(home / "a")["items"]} == {"nudity", "document", "sample"}


def test_missing_derived_is_counted_not_listed(env):
    cfg, home = env
    quarantine.flag([aid(9)], "nudity")
    res = build(cfg, home / "r")
    assert (res.flagged, res.no_derived) == (3, 1)
    assert aid(9) not in json.dumps(json.loads(res.page_map.read_text())["items"])


@pytest.mark.parametrize("where", ["repo", "Desktop", "Documents", "icloud", "volumes"])
def test_refused_locations(env, where):
    cfg, home = env
    out = {"repo": config.REPO_ROOT / "tmp-review", "Desktop": home / "Desktop" / "r",
           "Documents": home / "Documents" / "x" / "r",
           "icloud": home / "Library" / "Mobile Documents" / "com~apple~CloudDocs" / "r",
           "volumes": Path("/Volumes/photo/r")}[where]
    with pytest.raises(ValueError, match="refus"):
        build(cfg, out)
    assert not out.exists()
    with pytest.raises(ValueError, match="refus"):  # the page-map is checked too
        build(cfg, home / "ok", page_map=out / "map.json")


def test_refuses_page_map_inside_page_folder(env):
    cfg, home = env
    with pytest.raises(ValueError, match="outside"):
        build(cfg, home / "r", page_map=home / "r" / "page-map.json")


def test_default_output_location(env):
    _cfg, home = env
    assert pg.default_out() == home / "shoebox-review" / "screen"
    assert home / "shoebox-review" / "screen" not in pg.default_page_map().parents


# ---------- ingest ----------


def answers(res, verdicts: dict[int, str], page_id=None) -> dict:
    return {"page_id": page_id or res.page_id, "verdicts": {str(k): v for k, v in verdicts.items()}}


def test_ingest_page_id_mismatch_refused(env):
    cfg, home = env
    res = build(cfg, home / "r")
    before = (quarantine.QUARANTINE_PATH).read_text()
    with pytest.raises(ing.IngestError, match="page_id"):
        ing.ingest(answers(res, {1: "include", 2: "include", 3: "include"}, page_id="other"), res.page_map)
    assert quarantine.QUARANTINE_PATH.read_text() == before


@pytest.mark.parametrize("bad", [{1: "include", 2: "exclude"}, {1: "include", 2: "exclude", 3: "maybe"}])
def test_ingest_incomplete_or_invalid_refused(env, bad):
    cfg, home = env
    res = build(cfg, home / "r")
    before = quarantine.QUARANTINE_PATH.read_text()
    with pytest.raises(ing.IngestError):
        ing.ingest(answers(res, bad), res.page_map)
    assert quarantine.QUARANTINE_PATH.read_text() == before


def test_ingest_writes_owner_verdicts_and_applies_to_group(env):
    cfg, home = env
    res = build(cfg, home / "r")
    pm = json.loads(res.page_map.read_text())
    n_of = {v["asset_id"]: int(k) for k, v in pm["items"].items()}
    groups = {aid(0): {aid(0), aid(4)}, aid(1): {aid(1), aid(5)}}
    r = ing.ingest(answers(res, {n_of[aid(0)]: "private", n_of[aid(1)]: "exclude", n_of[aid(2)]: "include"}),
                   res.page_map, groups=groups)
    assert r.counts == {"private": 1, "exclude": 1, "include": 1}
    assert r.group_members == 2
    rows = quarantine.load()
    assert {a: rows[a]["verdict"] for a in (aid(0), aid(4), aid(1), aid(5), aid(2))} == {
        aid(0): "private", aid(4): "private", aid(1): "exclude", aid(5): "exclude", aid(2): "include"}
    assert all(rows[a]["verdict_by"] == "owner" and rows[a]["verdict_at"] for a in rows)
    assert rows[aid(4)]["screen"] == "nudity"
    assert (home / "r" / "img").is_dir()  # kept unless asked


def test_ingest_sampled_unflagged_recorded(env):
    cfg, home = env
    res = build(cfg, home / "r", unflagged=[aid(3)], sample=1)
    pm = json.loads(res.page_map.read_text())
    verdicts = {int(k): ("exclude" if v["asset_id"] == aid(3) else "include") for k, v in pm["items"].items()}
    ing.ingest(answers(res, verdicts), res.page_map)
    row = quarantine.load()[aid(3)]
    assert (row["verdict"], row["verdict_by"]) == ("exclude", "owner")


def test_ingest_can_delete_thumbnails(env):
    cfg, home = env
    res = build(cfg, home / "r")
    pm = json.loads(res.page_map.read_text())
    ing.ingest(answers(res, {int(k): "include" for k in pm["items"]}), res.page_map, delete_thumbnails=True)
    assert not (home / "r" / "img").exists()
    assert (home / "r" / "index.html").exists()


# ---------- commands ----------


def test_commands_print_counts_only(env, monkeypatch, capsys):
    from shoebox.commands import screen

    cfg, home = env
    monkeypatch.setattr(screen.config, "load", lambda *a, **k: cfg)
    assert main(["screen", "review-page"]) == 0
    out = capsys.readouterr()
    assert aid(0) not in out.out + out.err and "3 flagged" in out.out
    pm = json.loads(pg.default_page_map().read_text())
    ans = home / "answers.json"
    ans.write_text(json.dumps({"page_id": pm["page_id"], "verdicts": {k: "include" for k in pm["items"]}}))
    assert main(["screen", "ingest", str(ans), "--delete-thumbnails"]) == 0
    out = capsys.readouterr()
    assert "3 include" in out.out and aid(0) not in out.out + out.err
    assert not (pg.default_out() / "img").exists()
    assert all(r["verdict"] == "include" for r in quarantine.load().values())


def test_command_refusal_exit_2(env, monkeypatch, capsys):
    from shoebox.commands import screen

    cfg, home = env
    monkeypatch.setattr(screen.config, "load", lambda *a, **k: cfg)
    assert main(["screen", "review-page", "--out", str(home / "Desktop" / "r")]) == 2
    assert "refus" in capsys.readouterr().err
    pm = home / "pm.json"
    build(cfg, home / "r", page_map=pm)
    ans = home / "a.json"
    ans.write_text(json.dumps({"page_id": "nope", "verdicts": {}}))
    assert main(["screen", "ingest", str(ans), "--page-map", str(pm)]) == 2


def test_page_script_parses_with_node(env, tmp_path):
    import shutil
    import subprocess

    node = shutil.which("node")
    if not node:
        pytest.skip("node not installed")
    cfg, home = env
    build(cfg, home / "r")
    script = (home / "r" / "index.html").read_text().rsplit("<script>", 1)[1].split("</script>", 1)[0]
    js = tmp_path / "page.js"
    js.write_text(script)
    subprocess.run([node, "--check", str(js)], check=True)


def test_documented_commands_run_as_written(env, monkeypatch, capsys):
    import shlex

    from shoebox.commands import screen

    cfg, home = env
    monkeypatch.setattr(screen.config, "load", lambda *a, **k: cfg)
    doc = (config.REPO_ROOT / "docs" / "screen.md").read_text()
    cmds = [b.strip() for b in re.findall(r"```\n(.*?)```", doc, re.DOTALL)]
    assert [c.split()[:3] for c in cmds] == [["shoebox", "screen", "review-page"], ["shoebox", "screen", "ingest"]]
    assert main(shlex.split(cmds[0])[1:]) == 0
    pm = json.loads(pg.default_page_map().read_text())
    ans = home / "Downloads" / f"screen-answers-{pm['page_id']}.json"
    ans.parent.mkdir()
    ans.write_text(json.dumps({"page_id": pm["page_id"], "verdicts": {k: "private" for k in pm["items"]}}))
    assert main(shlex.split(cmds[1].replace("<page id>", pm["page_id"]))[1:]) == 0
    assert {r["verdict"] for r in quarantine.load().values()} == {"private"}
    assert not (pg.default_out() / "img").exists()
    capsys.readouterr()
