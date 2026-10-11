"""Worklist-driven caption runs (tasks 8.1, 8.2) and the refusal fallback (8.7). Fakes only: the Gemini batch
runner gets an in-memory transport, Ollama is replaced; no request leaves the process."""

import json

import pytest
from test_gemini import env  # noqa: F401 - a fixture (used by b)
from test_gemini_batch import AIDS, GOOD, FakeTransport, b  # noqa: F401 - b is a fixture

from shoebox import bulk, config, copies, privacy, runners, store
from shoebox.cli import main
from shoebox.commands import label as label_cmd
from shoebox.runners import gemini, ollama

LOC = {aid: ("importantfiles", f"Pictures/{aid[:4]}.jpg", aid) for aid in AIDS}


def header(sources=("importantfiles",)):
    return {"type": "header", "dedup_version": "test", "sources": sorted(sources),
            "frame_dates": {s: "2026-10-11" for s in sources}, "provider": "gemini", "profile": "infer-v1",
            "created_at": "2026-10-11T00:00:00+00:00"}


def capture(canon, members=None, queue="hosted", linked=()):
    members = list(members or [canon])
    return {"type": "capture", "capture_id": f"c-{canon[:12]}", "canonical_asset_id": canon,
            "members": members, "roles": {m: "member" for m in members},
            "linked": [{"asset_id": a, "role": "edited"} for a in linked], "eligible": queue == "hosted",
            "queue": queue, "refusal": None if queue == "hosted" else {"reason": "no_consent"}}


def write_worklist(cfg, name, caps, hdr=None):
    p = cfg.shoebox_root / "worklists" / f"{name}.jsonl"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("".join(json.dumps(r) + "\n" for r in ([hdr or header()] + caps)))
    return p


def label_rec(res, aid, status="ok", **extra):
    return {"asset_id": aid, "model_id": res.model_id, "params_hash": res.params_hash, "status": status,
            "output": GOOD if status == "ok" else None, "raw": None, "error": None, **extra}


@pytest.fixture
def wb(b, monkeypatch):  # noqa: F811
    """`b` from the batch tests plus the canonical locations (the copy index) and the CLI's config."""
    locs = list(LOC.values())
    b.locs = locs
    monkeypatch.setattr(privacy, "default_copy_index", lambda cfg: copies.CopyIndex.build(b.locs, {}))
    monkeypatch.setattr(label_cmd.config, "load", lambda *a, **k: b.cfg)
    b.t = FakeTransport()
    monkeypatch.setattr(bulk, "batch_transport", lambda: b.t)
    b.medium = runners.resolve("gemini-medium-v2")
    return b


@pytest.fixture
def fake_ollama(monkeypatch):
    """Ollama replaced: version, tags and show answer; `run_one` records the asset and returns a label."""
    calls: list[str] = []

    def _get(path):
        return {"version": "0.0-test"} if path == "/api/version" else {
            "models": [{"name": "gemma4:12b", "digest": "sha256:test"}]}

    def _post(path, body):
        return {"capabilities": ["vision"], "details": {"quantization_level": "Q4_K_M"}} if path == "/api/show" else {}

    def run_one(cand, item, image_path, cfg):
        calls.append(item["asset_id"])
        return {"status": "ok", "output": GOOD, "raw": None, "error": None,
                "usage": {"input_tokens": 1, "output_tokens": 1, "thinking_tokens": None}, "usd": 0.0}

    monkeypatch.setattr(ollama, "_get", _get)
    monkeypatch.setattr(ollama, "_post", _post)
    monkeypatch.setattr(ollama, "run_one", run_one)
    return calls


def bulk_args(path, *extra, candidate="gemini-batch-v2"):
    return ["label", "bulk", "--worklist", str(path), "--candidate", candidate, *extra]


# ---------- 8.1 the guard ----------


def test_inventory_frame_is_refused_before_any_request(wb, capsys):
    frame = wb.cfg.shoebox_root / "frame" / "frame-2026-10-11.parquet"
    frame.parent.mkdir(parents=True)
    import pyarrow as pa
    import pyarrow.parquet as pq

    pq.write_table(pa.table({"asset_id": AIDS, "source": ["importantfiles"] * 5}), frame)
    assert main(bulk_args(frame)) == 2
    assert "inventory frame" in capsys.readouterr().err
    assert wb.t.submits == [] and wb.t.polls == []


def test_frame_renamed_to_jsonl_is_still_refused(wb, capsys):
    import pyarrow as pa
    import pyarrow.parquet as pq

    p = wb.tmp / "sneaky.jsonl"
    pq.write_table(pa.table({"asset_id": AIDS}), p)
    assert main(bulk_args(p)) == 2
    assert "inventory frame" in capsys.readouterr().err
    assert wb.t.submits == []


def test_raw_items_list_is_refused(wb, capsys):
    p = wb.tmp / "items.jsonl"
    p.write_text("".join(json.dumps(it) + "\n" for it in wb.items))
    assert main(bulk_args(p)) == 2
    assert "not a worklist" in capsys.readouterr().err
    assert wb.t.submits == []


def test_worklist_missing_a_configured_source_is_refused_and_named(wb, capsys):
    wb.cfg.sources["takeout-jude"] = config.Source("takeout-jude", wb.tmp / "jude", "jude", False, False)
    p = write_worklist(wb.cfg, "partial", [capture(a) for a in AIDS])
    assert main(bulk_args(p)) == 2
    err = capsys.readouterr().err
    assert "takeout-jude" in err and "--partial-sources" in err
    assert wb.t.submits == []


def test_partial_sources_reason_lets_it_run_and_is_in_the_run_record(wb, capsys):
    wb.cfg.sources["takeout-jude"] = config.Source("takeout-jude", wb.tmp / "jude", "jude", False, False)
    p = write_worklist(wb.cfg, "partial", [capture(a) for a in AIDS])
    assert main(bulk_args(p, "--partial-sources", "Takeout not landed; staging B")) == 0
    assert sorted(a for s in wb.t.submits for a in s) == sorted(AIDS)
    (rec_path,) = (wb.cfg.results_dir / "bulk-runs" / "gemini-batch-v2").glob("*.json")
    rec = json.loads(rec_path.read_text())
    assert rec["partial_sources"] == "Takeout not landed; staging B"
    assert rec["missing_sources"] == ["takeout-jude"]
    assert rec["state"] == "done" and rec["counts"]["to_send"] == 5


def test_only_hosted_rows_go_to_a_hosted_candidate(wb):
    caps = [capture(AIDS[0]), capture(AIDS[1], queue="local"), capture(AIDS[2], queue="held"),
            capture(AIDS[3], queue="not_derived"), capture(AIDS[4])]
    p = write_worklist(wb.cfg, "mixed", caps)
    assert main(bulk_args(p)) == 0
    assert sorted(a for s in wb.t.submits for a in s) == sorted([AIDS[0], AIDS[4]])


def test_only_local_rows_go_to_a_local_candidate(wb, fake_ollama):
    caps = [capture(AIDS[0]), capture(AIDS[1], queue="local"), capture(AIDS[2], queue="held"),
            capture(AIDS[3], queue="not_derived")]
    p = write_worklist(wb.cfg, "mixed", caps)
    assert main(bulk_args(p, candidate="g12b-q4-v2")) == 0
    assert fake_ollama == [AIDS[1]]
    assert wb.t.submits == []


def test_eval_set_run_is_exempt_and_unchanged(wb, monkeypatch, capsys):
    """`label run --items` still takes a raw items list (an eval set), with no worklist header."""
    wb.cfg.sources["takeout-jude"] = config.Source("takeout-jude", wb.tmp / "jude", "jude", False, False)
    p = wb.tmp / "items.jsonl"
    p.write_text("".join(json.dumps(it) + "\n" for it in wb.items[:1]))
    calls = []
    monkeypatch.setattr(gemini, "run_one", lambda cand, item, image, cfg: calls.append(item["asset_id"]) or {
        "status": "ok", "output": GOOD, "raw": None, "error": None})
    assert main(["label", "run", "--candidate", "gemini-medium-v2", "--items", str(p)]) == 0
    assert calls == [AIDS[0]]


# ---------- 8.2 reuse before spend ----------


def test_new_byte_identical_copy_in_a_new_source_costs_nothing(wb, capsys):
    """Same asset_id, now also in a new source: its label (from the sync eval store) is reused."""
    store.append(wb.cfg, label_rec(wb.medium, AIDS[0]))
    wb.cfg.sources["takeout-jude"] = config.Source("takeout-jude", wb.tmp / "jude", "jude", False, True)
    wb.locs = [*wb.locs, ("takeout-jude", "Takeout/x.jpg", AIDS[0])]
    p = write_worklist(wb.cfg, "all", [capture(AIDS[0]), capture(AIDS[1])],
                       header(("importantfiles", "takeout-jude")))
    assert main(bulk_args(p)) == 0
    assert [a for s in wb.t.submits for a in s] == [AIDS[1]]
    assert "reused 1" in capsys.readouterr().out


def test_merge_keeps_a_members_label_and_sends_nothing(wb, capsys):
    """Two captures merged; the new canonical has no label but a member does: reused, no request."""
    store.append(wb.cfg, label_rec(wb.res, AIDS[1]))
    p = write_worklist(wb.cfg, "merged", [capture(AIDS[0], members=[AIDS[0], AIDS[1]])])
    assert main(bulk_args(p)) == 0
    assert wb.t.submits == [] and wb.t.polls == []
    assert "reused 1" in capsys.readouterr().out


def test_linked_edit_label_is_reused(wb):
    store.append(wb.cfg, label_rec(wb.medium, AIDS[2]))
    p = write_worklist(wb.cfg, "linked", [capture(AIDS[0], linked=[AIDS[2]])])
    assert main(bulk_args(p)) == 0
    assert wb.t.submits == []


def test_a_refused_label_is_not_resent_but_left_to_the_fallback(wb, capsys):
    """A refused sync record is no usable label, but sending the image to Gemini again would only be refused
    again (and paid for): the capture is counted as `fallback` and left to `label fallback`."""
    store.append(wb.cfg, label_rec(wb.medium, AIDS[0], "invalid",
                                   raw=json.dumps({"finishReason": None, "blockReason": "PROHIBITED_CONTENT",
                                                   "text": None})))
    p = write_worklist(wb.cfg, "r", [capture(AIDS[0]), capture(AIDS[1])])
    assert main(bulk_args(p)) == 0
    assert [a for s in wb.t.submits for a in s] == [AIDS[1]]
    assert "reused 0 -> fallback 1 -> to send 1" in capsys.readouterr().out


@pytest.mark.parametrize("rec,fallback", [
    ({"status": "invalid", "reason": "refused"}, 1),
    ({"status": "invalid", "reason": "truncated"}, 1),                        # no retry: unrecovered
    ({"status": "invalid", "reason": "truncated", "awaiting_retry": True}, 0),  # its retry has not run: not routed
    ({"status": "invalid", "error": "schema: bad"}, 0),                       # not routed: sent again
    ({"status": "error", "error": "HTTP 500"}, 0),
])
def test_plan_counts_falls_through_records_as_fallback(rec, fallback):
    caps = [capture(AIDS[0]), capture(AIDS[2])]
    plan = bulk.plan(caps, "hosted", {AIDS[0]: rec})
    n = plan.counts
    assert n["fallback"] == fallback
    assert n["reused"] + n["fallback"] + n["to_send"] == n["for_candidate"] == 2
    assert (AIDS[0] in [c["canonical_asset_id"] for c in plan.to_send]) == (not fallback)


def test_batch_refused_record_in_the_batch_store_is_not_resent(wb):
    store.append(wb.cfg, label_rec(wb.res, AIDS[0], "invalid", reason="refused"))
    p = write_worklist(wb.cfg, "r", [capture(AIDS[0])])
    assert main(bulk_args(p)) == 0
    assert wb.t.submits == []


def test_label_outside_the_chain_is_not_reused(wb, monkeypatch):
    store.append(wb.cfg, label_rec(wb.medium, AIDS[0]))
    p = write_worklist(wb.cfg, "c", [capture(AIDS[0])])
    assert main(bulk_args(p, "--label-sources", "gemini-batch-v2")) == 0
    assert [a for s in wb.t.submits for a in s] == [AIDS[0]]


def test_summary_counts_reconcile(wb, capsys):
    store.append(wb.cfg, label_rec(wb.medium, AIDS[0]))
    caps = [capture(AIDS[0], members=[AIDS[0], AIDS[1]]), capture(AIDS[2]), capture(AIDS[3], queue="local"),
            capture(AIDS[4], queue="held")]
    p = write_worklist(wb.cfg, "sum", caps)
    assert main(bulk_args(p, "--dry-run")) == 0
    out = capsys.readouterr().out
    assert wb.t.submits == []
    assert ("assets 5 -> captures 4 -> eligible 2 -> hosted 2 -> reused 1 -> fallback 0 -> to send 1" in out), out
    assert "local 1, held 1, not_derived 0" in out


def test_plan_counts_are_checked():
    caps = [capture(AIDS[0], members=[AIDS[0], AIDS[1]]), capture(AIDS[2])]
    plan = bulk.plan(caps, "hosted", {AIDS[1]: {"status": "ok"}})
    assert (plan.counts["captures"], plan.counts["reused"], plan.counts["to_send"]) == (2, 1, 1)
    assert [c["canonical_asset_id"] for c in plan.to_send] == [AIDS[2]]


def test_default_chain_for_batch_includes_the_sync_store():
    res = runners.resolve("gemini-batch-v2")
    assert bulk.chain_names(res, None) == ["gemini-batch-v2", "gemini-medium-v2"]
    assert bulk.chain_names(res, "gemini-medium-v2") == ["gemini-batch-v2", "gemini-medium-v2"]


# ---------- eligibility re-checked at send time ----------


def test_member_added_to_never_send_after_the_worklist_blocks_the_capture(wb, capsys):
    """The worklist said eligible; since then the RAW member went on the never-send list: nothing is sent
    for that capture (the canonical carries the capture as its group, the members are its siblings)."""
    p = write_worklist(wb.cfg, "w", [capture(AIDS[0], members=[AIDS[0], AIDS[1]]), capture(AIDS[2])])
    (wb.tmp / "never-send.txt").write_text(AIDS[1] + "\n")
    assert main(bulk_args(p)) == 0
    assert wb.t.submits == [[AIDS[2]]]
    assert f"skipped {AIDS[0]}: copy_restricted" in capsys.readouterr().err


def test_linked_asset_added_to_never_send_blocks_the_capture(wb):
    p = write_worklist(wb.cfg, "w", [capture(AIDS[0], linked=[AIDS[3]]), capture(AIDS[2])])
    (wb.tmp / "never-send.txt").write_text(AIDS[3] + "\n")
    assert main(bulk_args(p)) == 0
    assert wb.t.submits == [[AIDS[2]]]


def test_member_quarantined_pending_after_the_worklist_blocks_the_capture(wb, capsys):
    from shoebox import quarantine

    p = write_worklist(wb.cfg, "w", [capture(AIDS[0], members=[AIDS[0], AIDS[1]]), capture(AIDS[2])])
    quarantine.flag([AIDS[1]], "nudity")
    assert main(bulk_args(p)) == 0
    assert wb.t.submits == [[AIDS[2]]]
    assert f"skipped {AIDS[0]}: quarantined" in capsys.readouterr().err


def test_items_for_gives_the_capture_as_group_and_every_member_location_as_others(wb):
    wb.locs = [*wb.locs, ("importantfiles", "Raw/x.dng", AIDS[1])]
    items, others, unlocated = bulk.items_for([capture(AIDS[0], members=[AIDS[0], AIDS[1]], linked=[AIDS[3]])],
                                              wb.cfg)
    assert unlocated == []
    assert items == [{"asset_id": AIDS[0], "source": "importantfiles", "relpath": LOC[AIDS[0]][1],
                      "group_id": f"c-{AIDS[0][:12]}"}]
    assert sorted((o["asset_id"], o["relpath"]) for o in others) == sorted(
        [(AIDS[1], "Raw/x.dng"), (AIDS[1], LOC[AIDS[1]][1]), (AIDS[3], LOC[AIDS[3]][1])])
    assert {o["group_id"] for o in others} == {f"c-{AIDS[0][:12]}"}


# ---------- fail closed on a missing inventory ----------


def test_run_batch_with_a_source_missing_its_frame_refuses_and_names_it(wb, monkeypatch):
    from test_gemini_batch import run

    monkeypatch.setattr(privacy, "default_copy_index",
                        lambda cfg: copies.CopyIndex.build(wb.locs, {}, ("x",)))
    with pytest.raises(privacy.PrivacyError, match="x"):
        run(wb, wb.t)
    assert wb.t.submits == []


def test_label_bulk_with_a_source_missing_its_frame_refuses_and_names_it(wb, monkeypatch, capsys):
    monkeypatch.setattr(privacy, "default_copy_index",
                        lambda cfg: copies.CopyIndex.build(wb.locs, {}, ("x",)))
    p = write_worklist(wb.cfg, "w", [capture(a) for a in AIDS])
    assert main(bulk_args(p)) == 2
    err = capsys.readouterr().err
    assert "refused" in err and "source(s): x" in err
    assert wb.t.submits == [] and wb.t.polls == []


# ---------- 9.2 tiny images (evals/runs/2026-10-11_tiny-images.md) ----------


def tiny_derived(cfg, aid, side=72):
    """A synthetic 72 px image, derived as `shoebox derive` would record it (source dims in the index)."""
    import io

    from derived_fixture import write_derived
    from PIL import Image

    from shoebox import derive

    buf = io.BytesIO()
    Image.new("RGB", (side, side * 2 // 3), (90, 140, 200)).save(buf, "JPEG", quality=90)
    write_derived(cfg, aid, buf.getvalue())
    idx = derive.index_path(cfg, "infer-v1")
    last = json.loads(idx.read_text().splitlines()[-1])
    with open(idx, "a") as f:
        f.write(json.dumps({**last, "width": side, "height": side * 2 // 3, "src_width": side,
                            "src_height": side * 2 // 3}) + "\n")


def test_g12b_carries_the_tiny_rule_unhashed(fake_ollama, monkeypatch):
    res = runners.resolve("g12b-q4-v2")
    assert (res.cand["tiny_max_side"], res.cand["tiny_action"]) == (256, "route_hosted_else_mark")
    assert "tiny_max_side" not in res.params and "tiny_action" not in res.params
    cands = runners.load_candidates()
    for k in ("tiny_max_side", "tiny_action"):
        del cands["g12b-q4-v2"][k]
    monkeypatch.setattr(runners, "load_candidates", lambda config_dir=None: cands)
    bare = runners.resolve("g12b-q4-v2")
    assert (bare.params_hash, bare.retry.params_hash) == (res.params_hash, res.retry.params_hash)


def test_gemini_has_no_tiny_rule():
    for name in ("gemini-batch-v2", "gemini-medium-v2"):
        assert runners.tiny_rule(runners.resolve(name)) is None


def test_tiny_eligible_capture_is_routed_to_hosted_not_captioned_locally(wb, fake_ollama, capsys):
    tiny_derived(wb.cfg, AIDS[0])
    routed = {**capture(AIDS[0], queue="local"), "eligible": True}
    p = write_worklist(wb.cfg, "t", [routed, capture(AIDS[1], queue="local")])
    assert main(bulk_args(p, candidate="g12b-q4-v2")) == 0
    assert fake_ollama == [AIDS[1]]
    out = capsys.readouterr().out
    assert "tiny routed 1 -> to send 1" in out
    (rec_path,) = (wb.cfg.results_dir / "bulk-runs" / "g12b-q4-v2").glob("*.json")
    assert json.loads(rec_path.read_text())["counts"]["tiny_routed"] == 1


def test_tiny_ineligible_capture_is_captioned_locally_and_marked(wb, fake_ollama):
    tiny_derived(wb.cfg, AIDS[0])
    p = write_worklist(wb.cfg, "t", [capture(AIDS[0], queue="local"), capture(AIDS[1], queue="local")])
    assert main(bulk_args(p, candidate="g12b-q4-v2")) == 0
    assert sorted(fake_ollama) == sorted([AIDS[0], AIDS[1]])
    g12 = runners.resolve("g12b-q4-v2")
    idx = store.index(wb.cfg, g12.model_id, g12.params_hash)
    assert idx[AIDS[0]]["tiny"] is True and idx[AIDS[0]]["tiny_rule"] == "route_hosted_else_mark"
    assert idx[AIDS[0]]["tiny_max_side"] == 256
    assert "tiny" not in idx[AIDS[1]]  # 64 px wide derived, but no source dims recorded: not judged tiny


def test_fallback_marks_a_tiny_caption(wb, fake_ollama):
    """The rule is applied on every local path, not just bulk: a refused tiny image is captioned and marked."""
    tiny_derived(wb.cfg, AIDS[0])
    store.append(wb.cfg, label_rec(wb.res, AIDS[0], "invalid", reason="refused"))
    assert main(["label", "fallback", "--candidate", "g12b-q4-v2", "--primary", "gemini-batch-v2"]) == 0
    g12 = runners.resolve("g12b-q4-v2")
    assert store.index(wb.cfg, g12.model_id, g12.params_hash)[AIDS[0]]["tiny"] is True


def test_at_the_threshold_is_not_tiny(wb, fake_ollama):
    tiny_derived(wb.cfg, AIDS[0], side=256)
    p = write_worklist(wb.cfg, "t", [capture(AIDS[0], queue="local")])
    assert main(bulk_args(p, candidate="g12b-q4-v2")) == 0
    g12 = runners.resolve("g12b-q4-v2")
    assert "tiny" not in store.index(wb.cfg, g12.model_id, g12.params_hash)[AIDS[0]]


# ---------- refusal classification (8.7) ----------


@pytest.mark.parametrize("rec,want", [
    ({"status": "invalid", "reason": "refused"}, "refused"),
    ({"status": "invalid", "reason": "truncated"}, "truncated"),
    ({"status": "invalid", "model_id": "gemini/gemini-3.8-flash",
      "raw": json.dumps({"finishReason": None, "blockReason": "PROHIBITED_CONTENT", "text": None}),
      "error": "no output (finishReason=None, blockReason=PROHIBITED_CONTENT)"}, "refused"),
    ({"status": "invalid", "model_id": "gemini/gemini-3.8-flash",
      "raw": json.dumps({"finishReason": "SAFETY", "blockReason": None, "text": None})}, "refused"),
    ({"status": "invalid", "model_id": "gemini/gemini-3.8-flash", "raw": '{"caption": "Two adul',
      "error": "not JSON (finishReason=MAX_TOKENS): Unterminated string"}, "truncated"),
    ({"status": "invalid", "model_id": "gemini/gemini-3.8-flash", "raw": '{"caption": 3}',
      "error": "schema: caption: 3 is not of type 'string'"}, None),
    ({"status": "error", "model_id": "gemini/gemini-3.8-flash", "error": "HTTP 500"}, None),
    ({"status": "ok", "model_id": "gemini/gemini-3.8-flash"}, None),
    ({"status": "invalid", "model_id": "ollama/gemma4:12b",
      "raw": json.dumps({"finishReason": "SAFETY", "blockReason": None})}, None),
])
def test_refusal_reason(rec, want):
    assert runners.refusal_reason(rec) == want


def test_sync_refusal_falls_through_the_ordered_sources(wb):
    """A sync gemini-medium-v2 record has no `reason`; its raw says PROHIBITED_CONTENT, so it falls through."""
    raw = json.dumps({"finishReason": None, "blockReason": "PROHIBITED_CONTENT", "text": None})
    store.append(wb.cfg, label_rec(wb.medium, AIDS[0], "invalid", raw=raw,
                                   error="no output (finishReason=None, blockReason=PROHIBITED_CONTENT)"))
    fb = {"candidate": "fb", "model_id": "ollama/gemma4:12b", "params_hash": "f" * 16}
    store.append(wb.cfg, {**label_rec(wb.medium, AIDS[0]), "model_id": fb["model_id"],
                          "params_hash": fb["params_hash"]})
    idx = runners.source_index(wb.cfg, [runners.label_source(wb.medium), fb])
    assert idx[AIDS[0]]["model_id"] == "ollama/gemma4:12b"
    assert idx[AIDS[0]]["fell_through"] == ["gemini-medium-v2"]


# ---------- 8.7 fallback ----------


def _primary_records(wb):
    raw_block = json.dumps({"finishReason": None, "blockReason": "PROHIBITED_CONTENT", "text": None})
    store.append(wb.cfg, label_rec(wb.res, AIDS[0], "invalid", reason="refused"))       # batch refusal
    store.append(wb.cfg, label_rec(wb.medium, AIDS[1], "invalid", raw=raw_block))       # sync refusal (raw)
    store.append(wb.cfg, label_rec(wb.res, AIDS[2], "invalid", reason="truncated"))     # batch: no retry
    store.append(wb.cfg, label_rec(wb.medium, AIDS[3], "invalid", raw='{"caption": 3}',
                                   error="schema: caption: bad"))                       # schema-invalid
    store.append(wb.cfg, label_rec(wb.res, AIDS[4], "error", error="batch item error: x"))  # error


def test_fallback_selects_refused_and_unrecovered_truncated_only(wb, fake_ollama, capsys):
    _primary_records(wb)
    args = ["label", "fallback", "--candidate", "g12b-q4-v2", "--primary", "gemini-batch-v2,gemini-medium-v2"]
    assert main(args) == 0
    assert sorted(fake_ollama) == sorted([AIDS[0], AIDS[1], AIDS[2]])
    assert wb.t.submits == []


def test_fallback_is_idempotent(wb, fake_ollama):
    _primary_records(wb)
    args = ["label", "fallback", "--candidate", "g12b-q4-v2", "--primary", "gemini-batch-v2,gemini-medium-v2"]
    assert main(args) == 0
    n = len(fake_ollama)
    assert main(args) == 0
    assert len(fake_ollama) == n == 3


def test_fallback_label_resolves_to_the_12b_model(wb, fake_ollama):
    _primary_records(wb)
    assert main(["label", "fallback", "--candidate", "g12b-q4-v2", "--primary", "gemini-batch-v2"]) == 0
    g12 = runners.resolve("g12b-q4-v2")
    idx = runners.source_index(wb.cfg, runners.label_source([wb.res, wb.medium, g12]))
    assert idx[AIDS[0]]["model_id"] == g12.model_id == "ollama/gemma4:12b"
    assert idx[AIDS[0]]["status"] == "ok"
    assert idx[AIDS[1]]["model_id"] == wb.medium.model_id  # not in the primary chain given: not routed
    assert idx[AIDS[3]]["model_id"] == wb.medium.model_id  # schema-invalid: not routed, still reported


def test_fallback_does_not_run_quarantined_assets(wb, fake_ollama):
    from shoebox import quarantine

    _primary_records(wb)
    quarantine.flag([AIDS[0]], "nudity")
    assert main(["label", "fallback", "--candidate", "g12b-q4-v2", "--primary", "gemini-batch-v2"]) == 0
    assert AIDS[0] not in fake_ollama


def test_fallback_skips_an_asset_whose_same_pixel_copy_is_quarantined(wb, fake_ollama, monkeypatch, capsys):
    """Only the copy (another asset id, same pixels) is flagged; the refused asset is held all the same."""
    from shoebox import quarantine

    _primary_records(wb)
    copy_id = "ee" + "d" * 62
    monkeypatch.setattr(privacy, "default_copy_index", lambda cfg: copies.CopyIndex.build(
        [*wb.locs, ("importantfiles", "Copy/y.jpg", copy_id)], {AIDS[0]: "px1", copy_id: "px1"}))
    quarantine.flag([copy_id], "nudity")
    assert main(["label", "fallback", "--candidate", "g12b-q4-v2", "--primary", "gemini-batch-v2"]) == 0
    assert AIDS[0] not in fake_ollama and AIDS[2] in fake_ollama
    assert f"skipped {AIDS[0]}: quarantined for local stages" in capsys.readouterr().err


def test_fallback_refuses_a_hosted_candidate(wb, capsys):
    assert main(["label", "fallback", "--candidate", "gemini-medium-v2", "--primary", "gemini-batch-v2"]) == 2
    assert "local" in capsys.readouterr().err

