import json
import subprocess
import sys
from datetime import date, timedelta
from pathlib import Path

import pytest

from shoebox import config, privacy
from shoebox.cli import main

AID = "a" * 64

# The unfilled template as committed (the repo copy may since have been filled in by the owner).
TEMPLATE = """\
billing_project_id = ""
paid_tier_with_prepay_balance = false
ai_studio_logging_off = false
log_retention_days = 0
no_datasets_or_feedback = false
confirmed_on = ""
"""


def write_checklist(path: Path, **over) -> Path:
    vals = {
        "billing_project_id": '"proj-123"',
        "paid_tier_with_prepay_balance": "true",
        "ai_studio_logging_off": "true",
        "log_retention_days": "7",
        "no_datasets_or_feedback": "true",
        "confirmed_on": f'"{date.today().isoformat()}"',
    }
    vals.update({k: v for k, v in over.items() if v is not None})
    for k, v in over.items():
        if v is None:
            vals.pop(k)
    path.write_text("".join(f"{k} = {v}\n" for k, v in vals.items()))
    return path


def cfg_with(tmp_path, **consent) -> config.Config:
    sources = {
        name: config.Source(
            name,
            tmp_path / name,
            "owner" if ok else "kid",
            synced=False,
            hosted_consent=ok,
        )
        for name, ok in (consent or {"importantfiles": True}).items()
    }
    return config.Config(shoebox_root=tmp_path / "shoebox", sources=sources)


def item(
    aid=AID, source="importantfiles", relpath="Pictures/a.jpg", group_id=None
) -> dict:
    return {"asset_id": aid, "source": source, "relpath": relpath, "group_id": group_id}


# ---------- checklist ----------


def test_filled_checklist_passes(tmp_path):
    results = privacy.check_checklist(write_checklist(tmp_path / "c.toml"))
    assert all(r.ok for r in results), results
    privacy.require_checklist(tmp_path / "c.toml")


def test_missing_checklist_refuses(tmp_path):
    with pytest.raises(privacy.PrivacyError, match="checklist_file"):
        privacy.require_checklist(tmp_path / "nope.toml")


def test_unfilled_template_refuses(tmp_path):
    failed = {
        r.item
        for r in privacy.check_checklist(_write(tmp_path / "c.toml", TEMPLATE))
        if not r.ok
    }
    assert failed == {
        "billing_project_id", "paid_tier_with_prepay_balance", "ai_studio_logging_off",
        "no_datasets_or_feedback", "log_retention_days", "confirmed_on",
    }  # fmt: skip


def test_repo_checklist_parses():
    results = privacy.check_checklist(privacy.CHECKLIST_PATH)
    assert {r.item for r in results} >= {
        "billing_project_id",
        "confirmed_on",
        "log_retention_days",
    }


@pytest.mark.parametrize(
    "over, bad",
    [
        ({"ai_studio_logging_off": "false"}, "ai_studio_logging_off"),
        ({"no_datasets_or_feedback": None}, "no_datasets_or_feedback"),
        ({"billing_project_id": '""'}, "billing_project_id"),
        ({"log_retention_days": "0"}, "log_retention_days"),
        ({"log_retention_days": "30"}, "log_retention_days"),
        ({"confirmed_on": '""'}, "confirmed_on"),
        (
            {"confirmed_on": f'"{(date.today() - timedelta(days=91)).isoformat()}"'},
            "confirmed_on",
        ),
        (
            {"confirmed_on": f'"{(date.today() + timedelta(days=2)).isoformat()}"'},
            "confirmed_on",
        ),
        ({"extra_new_item": "false"}, "extra_new_item"),
    ],
)
def test_unfilled_or_stale_checklist_names_the_item(tmp_path, over, bad):
    path = write_checklist(tmp_path / "c.toml", **over)
    with pytest.raises(privacy.PrivacyError, match=bad):
        privacy.require_checklist(path)


def test_checklist_toml_date_and_90_day_edge(tmp_path):
    edge = (date.today() - timedelta(days=90)).isoformat()
    path = write_checklist(tmp_path / "c.toml", confirmed_on=edge)  # bare TOML date
    privacy.require_checklist(path)


# ---------- never-send and consent ----------


def test_never_send_asset_and_glob_are_skipped_and_reported(tmp_path):
    ns = tmp_path / "never-send.txt"
    ns.write_text(f"# comment\n\n{'b' * 64}\nPictures/Kyra/**\n*.psd\n")
    never = privacy.load_never_send(ns)
    items = [
        item("b" * 64, relpath="Pictures/ok.jpg"),
        item("c" * 64, relpath="Pictures/Kyra/2019/x.jpg"),
        item("d" * 64, relpath="pictures/kyra/y.JPG"),  # case-insensitive share
        item("e" * 64, relpath="top.psd"),
        item(
            "f" * 64, relpath="Pictures/sub/deep.psd"
        ),  # no slash: any depth (gitignore)
        item(AID, relpath="Pictures/Kyrastuff.jpg"),
    ]
    elig = privacy.filter_items(items, cfg_with(tmp_path), never)
    assert [i["asset_id"] for i in elig.allowed] == [AID]
    assert elig.counts() == {"never_send": 5}
    assert "5 skipped (5 never_send)" in elig.summary()


@pytest.mark.parametrize(
    "entry",
    ["Pictures/Kyra", "Pictures/Kyra/", "/Pictures/Kyra", "Pictures/Kyra/*", "Kyra"],
)
def test_never_send_folder_entry_blocks_everything_below(tmp_path, entry):
    never = privacy.load_never_send(_write(tmp_path / "ns.txt", entry + "\n"))
    assert never.matches(None, "Pictures/Kyra/a.jpg")
    assert never.matches(None, "Pictures/Kyra/2019/b.jpg")
    assert never.matches(None, "pictures/kyra/2019/c.JPG")
    assert not never.matches(None, "Pictures/Kyrastuff/a.jpg")
    assert not never.matches(None, "Pictures/Kyra.jpg")


def test_never_send_anchored_entry_with_slash_is_relative_to_source_root(tmp_path):
    never = privacy.load_never_send(_write(tmp_path / "ns.txt", "Kyra/**\n"))
    assert never.matches(None, "Kyra/a.jpg")
    assert not never.matches(None, "Pictures/Kyra/a.jpg")


def test_never_send_unmatched_entries_are_reported(tmp_path):
    never = privacy.load_never_send(
        _write(tmp_path / "ns.txt", f"{'b' * 64}\n{'e' * 64}\nPictures/Kyra\nKyra/**\n")
    )
    items = [item("b" * 64), item("c" * 64, relpath="Pictures/Kyra/a.jpg")]
    assert never.unmatched(items) == ["e" * 64, "Kyra/**"]


def test_non_consented_and_unknown_source_skipped(tmp_path):
    cfg = cfg_with(tmp_path, importantfiles=True, kyra=False)
    items = [
        item("1" * 64),
        item("2" * 64, source="kyra"),
        item("3" * 64, source="mystery"),
    ]
    elig = privacy.filter_items(items, cfg, privacy.NeverSend())
    assert [i["asset_id"] for i in elig.allowed] == ["1" * 64]
    assert elig.counts() == {"no_consent": 1, "unknown_source": 1}


def test_copy_or_sibling_of_restricted_asset_is_skipped(tmp_path):
    cfg = cfg_with(tmp_path, importantfiles=True, kyra=False)
    never = privacy.load_never_send(_write(tmp_path / "ns.txt", "Pictures/raw.DNG\n"))
    items = [
        item("1" * 64, source="importantfiles"),
        item("1" * 64, source="kyra"),  # same bytes in a non-consented source
        item("2" * 64, relpath="Pictures/raw.DNG", group_id="g-2"),
        item("3" * 64, relpath="Pictures/raw.JPG", group_id="g-2"),  # its JPEG sibling
        item("4" * 64, relpath="Pictures/fine.jpg"),
    ]
    elig = privacy.filter_items(items, cfg, never)
    assert [i["asset_id"] for i in elig.allowed] == ["4" * 64]
    assert elig.counts() == {"copy_restricted": 2, "never_send": 1, "no_consent": 1}


def test_item_without_relpath_is_not_sent(tmp_path):
    elig = privacy.filter_items(
        [item(relpath=None)], cfg_with(tmp_path), privacy.NeverSend()
    )
    assert elig.counts() == {"no_relpath": 1}


def _write(path: Path, text: str) -> Path:
    path.write_text(text)
    return path


# ---------- spend ----------

CAND = {
    "media_resolution": "low",
    "est_prompt_tokens": 300,
    "est_output_tokens": 150,
    "est_thinking_tokens": 100,
    "price_input_usd_per_mtok": 0.75,
    "price_output_usd_per_mtok": 3.75,
    "price_valid_until": "2026-12-31",
    "max_usd_per_run": 2.0,
}


def test_price_requires_explicit_prices():
    for key in ("price_input_usd_per_mtok", "price_output_usd_per_mtok"):
        with pytest.raises(privacy.PrivacyError, match=key):
            privacy.price({k: v for k, v in CAND.items() if k != key})
    assert privacy.price(CAND) == pytest.approx((0.75e-6, 3.75e-6))


def test_price_past_valid_until_refuses():
    privacy.require_current_price(CAND, today=date(2026, 12, 31))
    with pytest.raises(privacy.PrivacyError, match="price_valid_until 2026-12-31"):
        privacy.require_current_price(CAND, today=date(2027, 1, 1))
    with pytest.raises(privacy.PrivacyError, match="price_valid_until"):
        privacy.require_current_price(
            {k: v for k, v in CAND.items() if k != "price_valid_until"}
        )
    with pytest.raises(privacy.PrivacyError, match="price_valid_until"):
        privacy.require_current_price({**CAND, "price_valid_until": "soon"})


def test_estimate_uses_media_resolution_tokens():
    est = privacy.estimate(CAND, 100)
    assert est.input_tokens == 100 * (280 + 300)
    assert est.output_tokens == 100 * 250
    assert est.usd == pytest.approx(58000 * 0.75e-6 + 25000 * 3.75e-6)
    assert (
        privacy.estimate({**CAND, "media_resolution": "medium"}, 1).input_tokens == 860
    )


def test_estimate_over_run_cap_refuses():
    est = privacy.estimate(CAND, 100_000)
    with pytest.raises(
        privacy.PrivacyError,
        match=r"max_usd_per_run \$2.00.*remaining project budget \$150.00",
    ):
        privacy.check_budget(CAND, est, already_spent=0.0, lim=privacy.Limits())


def test_estimate_over_remaining_budget_refuses():
    est = privacy.estimate(CAND, 100)  # ~$0.14
    with pytest.raises(privacy.PrivacyError, match="remaining project budget"):
        privacy.check_budget(CAND, est, already_spent=149.95, lim=privacy.Limits())
    privacy.check_budget(CAND, est, already_spent=100.0, lim=privacy.Limits())


def test_missing_run_cap_refuses():
    est = privacy.estimate(CAND, 1)
    with pytest.raises(privacy.PrivacyError, match="max_usd_per_run"):
        privacy.check_budget(
            {k: v for k, v in CAND.items() if k != "max_usd_per_run"},
            est,
            0.0,
            privacy.Limits(),
        )


def test_spend_log_roundtrip(tmp_path):
    log = tmp_path / "spend.jsonl"
    assert privacy.spent_total(log) == 0.0
    privacy.append_spend("gemini-low", 45, 0.25, "2026-10-10", path=log)
    privacy.append_spend("gemini-medium", 45, 0.5, "2026-10-10", path=log, run="r1")
    assert privacy.spent_total(log) == pytest.approx(0.75)
    first, second = (json.loads(x) for x in log.read_text().splitlines())
    assert set(first) == {"ts", "candidate", "n", "usd", "price_date"}
    assert second["run"] == "r1"


def test_corrupt_spend_log_fails_loudly(tmp_path):
    log = _write(tmp_path / "spend.jsonl", '{"usd": 1}\nnot json\n')
    with pytest.raises(privacy.PrivacyError, match="spend.jsonl:2"):
        privacy.spent_total(log)


def test_limits_from_hosted_table(tmp_path):
    p = _write(
        tmp_path / "s.toml", 'shoebox_root = "/x"\n[hosted]\nmax_usd_total = 20\n'
    )
    assert privacy.limits(p) == privacy.Limits(
        max_total_usd=20.0, checklist_max_age_days=90
    )
    assert privacy.limits(tmp_path / "none.toml") == privacy.Limits()


# ---------- CLI ----------


def test_cli_check_fails_on_template(tmp_path, capsys):
    assert (
        main(
            [
                "privacy",
                "check",
                "--checklist",
                str(_write(tmp_path / "c.toml", TEMPLATE)),
            ]
        )
        == 1
    )
    out = capsys.readouterr().out
    assert "FAIL  ai_studio_logging_off" in out


def test_cli_check_passes_on_filled(tmp_path, capsys):
    path = write_checklist(tmp_path / "c.toml")
    assert main(["privacy", "check", "--checklist", str(path)]) == 0
    assert "PASS  confirmed_on" in capsys.readouterr().out


def test_cli_exit_code_in_subprocess(tmp_path):
    path = _write(tmp_path / "c.toml", TEMPLATE)
    cmd = [
        sys.executable,
        "-m",
        "shoebox.cli",
        "privacy",
        "check",
        "--checklist",
        str(path),
    ]
    assert (
        subprocess.run(cmd, capture_output=True, text=True, check=False).returncode == 1
    )
    path.write_text(write_checklist(tmp_path / "ok.toml").read_text())
    assert (
        subprocess.run(cmd, capture_output=True, text=True, check=False).returncode == 0
    )


def test_copies_in_other_known_sets_restrict(tmp_path):
    sets = tmp_path / "sets"
    (sets / "other").mkdir(parents=True)
    restricted = [
        item("1" * 64, source="kyra"),
        item("9" * 64, source="kyra", group_id="g-5"),
    ]
    (sets / "other" / "items.jsonl").write_text(
        "".join(json.dumps(i) + "\n" for i in restricted)
    )
    others = privacy.known_items(sets)
    cfg = cfg_with(tmp_path, importantfiles=True, kyra=False)
    items = [item("1" * 64), item("5" * 64, group_id="g-5"), item("6" * 64)]
    elig = privacy.filter_items(items, cfg, privacy.NeverSend(), others=others)
    assert [i["asset_id"] for i in elig.allowed] == ["6" * 64]
    assert elig.counts() == {"copy_restricted": 2}
    assert privacy.known_items(tmp_path / "none") == []


def test_cli_items_report_matches_runner_rules(tmp_path, capsys, monkeypatch):
    """--items uses the runner's rules: copies in other sets restrict, and each skip is listed."""
    sets = tmp_path / "sets"
    (sets / "other").mkdir(parents=True)
    (sets / "other" / "items.jsonl").write_text(
        json.dumps(item("1" * 64, source="kyra")) + "\n"
    )
    monkeypatch.setattr(privacy, "SETS_DIR", sets)
    monkeypatch.setattr(
        privacy, "NEVER_SEND_PATH", _write(tmp_path / "ns.txt", "Kyra/**\n")
    )
    cfg = cfg_with(tmp_path, importantfiles=True, kyra=False)
    monkeypatch.setattr(config, "load", lambda *a, **k: cfg)
    items = _write(
        tmp_path / "items.jsonl",
        json.dumps(item("1" * 64)) + "\n" + json.dumps(item("2" * 64)) + "\n",
    )
    path = write_checklist(tmp_path / "c.toml")
    assert (
        main(["privacy", "check", "--checklist", str(path), "--items", str(items)]) == 0
    )
    out = capsys.readouterr().out
    assert "items: 1 allowed, 1 skipped (1 copy_restricted)" in out
    assert f"skip  {'1' * 64} importantfiles Pictures/a.jpg: copy_restricted" in out
    assert "WARN  never-send entry 'Kyra/**' matched no item" in out


# ---------- held-back paths and per-provider consent (design D14) ----------


def cfg_held(tmp_path, held_back=(), hosted_models=None) -> config.Config:
    src = config.Source(
        "importantfiles", tmp_path / "if", "owner", synced=False, hosted_consent=True,
        held_back=tuple(held_back), hosted_models=hosted_models,
    )
    return config.Config(shoebox_root=tmp_path / "shoebox", sources={"importantfiles": src})


@pytest.mark.parametrize(
    "relpath", ["Pictures/toren phone/a.jpg", "pictures/TOREN PHONE/sub/deep/b.jpg"]
)
def test_held_back_path_is_skipped(tmp_path, relpath):
    cfg = cfg_held(tmp_path, held_back=["Pictures/toren phone"])
    elig = privacy.filter_items(
        [item(relpath=relpath), item("2" * 64, relpath="Pictures/Greece/x.jpg")],
        cfg, privacy.NeverSend(),
    )
    assert [s.reason for s in elig.skipped] == ["held_back"]
    assert [i["asset_id"] for i in elig.allowed] == ["2" * 64]


def test_copy_or_sibling_of_held_back_is_copy_restricted(tmp_path):
    cfg = cfg_held(tmp_path, held_back=["Important documents"])
    held = item("1" * 64, relpath="Important documents/passport.jpg", group_id="g1")
    copy = item("1" * 64, relpath="Pictures/passport.jpg")
    sibling = item("3" * 64, relpath="Pictures/passport.dng", group_id="g1")
    elig = privacy.filter_items([copy, sibling], cfg, privacy.NeverSend(), others=[held])
    assert elig.allowed == []
    assert [s.reason for s in elig.skipped] == ["copy_restricted", "copy_restricted"]


def test_provider_consent_per_source(tmp_path):
    only_gemini = cfg_held(tmp_path)  # hosted_consent true, hosted_models absent
    assert only_gemini.sources["importantfiles"].hosted_models == ("gemini",)
    elig = privacy.filter_items([item()], only_gemini, privacy.NeverSend(), provider="claude")
    assert [s.reason for s in elig.skipped] == ["no_consent"]
    assert privacy.filter_items([item()], only_gemini, privacy.NeverSend()).allowed
    both = cfg_held(tmp_path, hosted_models=("gemini", "claude"))
    assert privacy.filter_items([item()], both, privacy.NeverSend(), provider="claude").allowed


def test_config_hosted_models_defaults_and_validation(tmp_path):
    base = 'shoebox_root = "/tmp/s"\n[sources.a]\nroot = "/tmp/a"\nperson = "o"\nsynced = false\n'
    p = _write(tmp_path / "a.toml", base + "hosted_consent = true\n")
    assert config.load(p).sources["a"].hosted_models == ("gemini",)
    assert config.load(p).sources["a"].held_back == ()
    p = _write(tmp_path / "b.toml", base + "hosted_consent = false\n")
    assert config.load(p).sources["a"].hosted_models == ()
    p = _write(tmp_path / "c.toml", base + 'hosted_consent = false\nhosted_models = ["claude"]\n')
    with pytest.raises(ValueError, match="hosted_models"):
        config.load(p)
    p = _write(tmp_path / "d.toml", base + 'hosted_consent = true\nhosted_models = ["claude"]\n'
               'held_back = ["X"]\n')
    s = config.load(p).sources["a"]
    assert (s.hosted_models, s.held_back) == (("claude",), ("X",))


def test_cli_provider_claude_writes_allowed_and_ignores_gemini_checklist(
    tmp_path, capsys, monkeypatch
):
    monkeypatch.setattr(privacy, "SETS_DIR", tmp_path / "nosets")
    monkeypatch.setattr(privacy, "NEVER_SEND_PATH", tmp_path / "none.txt")
    cfg = cfg_held(tmp_path, held_back=["Pictures/toren phone"], hosted_models=("gemini", "claude"))
    monkeypatch.setattr(config, "load", lambda *a, **k: cfg)
    ok, held = item("2" * 64), item("1" * 64, relpath="Pictures/toren phone/a.jpg")
    items = _write(tmp_path / "items.jsonl", json.dumps(ok) + "\n" + json.dumps(held) + "\n")
    bad = _write(tmp_path / "c.toml", TEMPLATE)
    out_path = tmp_path / "allowed.jsonl"
    argv = ["privacy", "check", "--checklist", str(bad), "--items", str(items)]
    assert main([*argv, "--provider", "claude", "--allowed-out", str(out_path)]) == 0
    assert "items: 1 allowed, 1 skipped (1 held_back)" in capsys.readouterr().out
    assert [json.loads(line) for line in out_path.read_text().splitlines()] == [ok]
    assert main(argv) == 1  # default provider gemini: the failing checklist still refuses


def test_repo_config_holds_back_other_libraries():
    cfg = config.load(config.REPO_ROOT / "config" / "shoebox.toml")
    elig = privacy.filter_items(
        [item("1" * 64, relpath="Pictures/toren phone/x.jpg"),
         item("2" * 64, relpath="Pictures/Greece/x.jpg"),
         item("3" * 64, relpath="Important documents/p.jpg")],
        cfg, privacy.NeverSend(), provider="claude",
    )
    assert [i["asset_id"] for i in elig.allowed] == ["2" * 64]
    assert [s.reason for s in elig.skipped] == ["held_back", "held_back"]
