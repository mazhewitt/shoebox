"""End-to-end check of `shoebox label bulk` against a fake batch transport (task 10.1). No network, ever.

    uv run python tools/e2e_fake_batch.py --worklist <path> --candidate gemini-batch-v2 [--limit N]
        [--scratch DIR] [--runs-dir evals/runs]

1. An overlay shoebox root is built in a scratch dir: every entry of the real root is a symlink, except the
   directories the run writes (the candidate's label store dir, `results/batch-jobs/<candidate>/`,
   `results/bulk-runs/<candidate>/`), which are real dirs in the overlay (copies of the real ones if any).
   The spend log is redirected to a copy in the scratch dir.
2. The real CLI path (`shoebox.cli.main(["label", "bulk", ...])`) runs with `config.load` returning the
   overlay config and `bulk.batch_transport` returning a recording fake transport that answers every request
   with a schema-valid synthetic label. Constructing `HttpTransport`, reading the Gemini key or opening an
   httpx client raises.
3. Every asset id sent is re-checked independently of the run's own guards (quarantine rows at latest-row-
   wins over the capture and same-pixel copies, `privacy.filter_items` for gemini, an ok screen record under
   the current screen params, canonical of a hosted capture, no ok label in the chain before the run).
4. The summary counts are reconciled and `evals/runs/<date>_e2e-fake-batch.md` gets counts only plus the
   re-quoted batch USD for the real run (`worklist.batch_usd`), with price_date.
Afterwards the real results tree and the real spend log are checked unchanged. Exit 1 on any violation,
mismatch or write to the real tree; output is counts only (no paths of photos, ids or captions).
"""

import argparse
import contextlib
import hashlib
import io
import json
import os
import shutil
import sys
from collections import Counter
from dataclasses import dataclass, field, replace
from datetime import date, datetime
from pathlib import Path
from unittest import mock

from shoebox import (
    bulk,
    config,
    privacy,
    quarantine,
    runners,
    screened,
    store,
    worklist,
)

REPO_ROOT = config.REPO_ROOT


# ---------- scratch and overlay ----------


def check_scratch(scratch: Path, real_root: Path) -> Path:
    """Refuse a scratch dir under /Volumes, the repo, the real shoebox root, Desktop/Documents/iCloud, or a
    non-empty one."""
    s = scratch.expanduser().absolute()
    r = s.resolve() if s.exists() else s
    home = Path.home()
    bad = [Path("/Volumes"), REPO_ROOT.resolve(), Path(real_root).expanduser().resolve(), home / "Desktop",
           home / "Documents", home / "Library" / "Mobile Documents"]
    for b in bad:
        if s.is_relative_to(b) or r.is_relative_to(b):
            raise SystemExit(f"refusing scratch dir under {b}")
    if s.exists() and any(s.iterdir()):
        raise SystemExit("refusing a non-empty scratch dir")
    s.mkdir(parents=True, exist_ok=True)
    return s


def writable_paths(cfg: config.Config, res: runners.Resolved) -> list[Path]:
    """Paths (relative to shoebox_root) the bulk run writes for this candidate."""
    rel = lambda p: Path(p).relative_to(cfg.shoebox_root)
    from shoebox.runners import gemini_batch

    out = [rel(store.store_dir(cfg, res.model_id, res.params_hash, res.kind)),
           rel(gemini_batch.ledger_dir(cfg, res.name)),
           rel(cfg.results_dir / "bulk-runs" / res.name)]
    if res.retry:
        out.append(rel(store.store_dir(cfg, res.retry.model_id, res.retry.params_hash, res.kind)))
    return out


def build_overlay(real_root: Path, overlay: Path, writable: list[Path]) -> None:
    """Mirror `real_root` at `overlay` with symlinks, except along `writable` paths: their parents are real
    dirs (siblings symlinked) and each writable leaf is a real dir (a copy of the real one if it exists)."""
    overlay.mkdir(parents=True, exist_ok=True)
    real_root = Path(real_root).expanduser()
    leaves = {tuple(p.parts) for p in writable}
    _mirror(real_root, overlay, (), leaves)


def _mirror(real: Path, ov: Path, prefix: tuple, leaves: set[tuple]) -> None:
    here = {leaf[len(prefix)] for leaf in leaves if leaf[:len(prefix)] == prefix and len(leaf) > len(prefix)}
    names = set(os.listdir(real)) if real.is_dir() else set()
    for name in sorted(names | here):
        src, dst, key = real / name, ov / name, (*prefix, name)
        if name not in here:
            dst.symlink_to(src)
        elif key in leaves:
            if src.is_dir():
                shutil.copytree(src, dst, symlinks=True)
            else:
                dst.mkdir(parents=True)
        else:
            dst.mkdir()
            _mirror(src, dst, key, leaves)


def snapshot(root: Path) -> dict[str, tuple]:
    """`{relpath: (type, size, mtime_ns)}` of every entry under root (symlinks not followed)."""
    root = Path(root)
    out: dict[str, tuple] = {}
    if not root.exists():
        return out
    for d, dirs, files in os.walk(root, followlinks=False):
        for n in [*dirs, *files]:
            p = Path(d) / n
            st = p.lstat()
            out[str(p.relative_to(root))] = (st.st_mode >> 12, st.st_size, st.st_mtime_ns)
    st = root.lstat()
    out["."] = (st.st_mode >> 12, st.st_size, st.st_mtime_ns)
    return out


def snapshot_diff(before: dict, after: dict) -> dict[str, int]:
    return {"added": len(after.keys() - before.keys()), "removed": len(before.keys() - after.keys()),
            "changed": sum(before[k] != after[k] for k in before.keys() & after.keys())}


def file_digest(p: Path) -> str | None:
    p = Path(p)
    return hashlib.sha256(p.read_bytes()).hexdigest() if p.exists() else None


# ---------- the fake transport ----------

SYNTHETIC_LABEL = {
    "caption": "A synthetic caption for an end-to-end test. Nothing was sent.",
    "tags": ["synthetic", "test"],
    "medium": "print",
    "digitiser": "flatbed_scanner",
    "colour_class": "colour",
    "confidence": {"medium": 0.5, "digitiser": 0.5, "colour_class": 0.5},
    "rotate_cw": 0,
}


class Forbidden(RuntimeError):
    pass


def _forbid(what: str):
    def f(*a, **k):
        raise Forbidden(f"{what} is forbidden in the fake-batch e2e run")
    return f


class RecordingTransport:
    """The batch `Transport` in memory. Records request keys only (never bodies); every job succeeds at once
    and answers each request with `SYNTHETIC_LABEL` and plausible usage."""

    def __init__(self, cand: dict):
        est = privacy.estimate(cand, 1)
        self.usage = {"promptTokenCount": est.input_tokens,
                      "candidatesTokenCount": int(cand.get("est_output_tokens", 300)),
                      "thoughtsTokenCount": int(cand.get("est_thinking_tokens", 1000))}
        self.jobs: dict[str, dict] = {}
        self.sent: list[tuple[str, str]] = []  # (job_id, key)

    def submit(self, model, requests, display_name):
        jid = f"batches/fake{len(self.jobs)}"
        keys = [k for k, _ in requests]
        self.jobs[jid] = {"keys": keys, "display_name": display_name}
        self.sent += [(jid, k) for k in keys]
        return jid

    def poll(self, job_id):
        return "succeeded"

    def download(self, job_id):
        body = {"candidates": [{"content": {"role": "model", "parts": [{"text": json.dumps(SYNTHETIC_LABEL)}]},
                                "finishReason": "STOP"}], "usageMetadata": self.usage, "modelVersion": "fake"}
        return [{"key": k, "response": body} for k in self.jobs[job_id]["keys"]]

    def delete(self, job_id):
        pass

    def find(self, display_name):
        return next((j for j, v in self.jobs.items() if v["display_name"] == display_name), None)

    @property
    def sent_ids(self) -> list[str]:
        return [k for _, k in self.sent]


# ---------- independent checks ----------


def ok_ids(cfg: config.Config, names: list[str], kind: str = "labels") -> set[str]:
    """Asset ids with an ok record in any of the named candidates' stores (and their retry stores)."""
    out: set[str] = set()
    for n in names:
        res = runners.resolve(n)
        for r in [res, *([res.retry] if res.retry else [])]:
            out |= {a for a, rec in store.index(cfg, r.model_id, r.params_hash, kind).items()
                    if rec.get("status") == "ok"}
    return out


def final_ids(cfg: config.Config, res: runners.Resolved) -> set[str]:
    return {a for a, rec in store.index(cfg, res.model_id, res.params_hash, res.kind).items() if store.is_final(rec)}


def screened_ids(cfg: config.Config) -> set[str]:
    res = runners.resolve(screened.SCREEN_CANDIDATE)
    return {a for a, rec in store.index(cfg, res.model_id, res.params_hash, res.kind).items()
            if rec.get("status") == "ok"}


@dataclass
class Before:
    labelled: set[str]      # ok in the chain before the run
    final_own: set[str]     # final in the candidate's own store before the run
    screened: set[str]


def check_sent(sent: list[str], caps: list[dict], cfg: config.Config, before: Before,
               qrows: dict[str, dict] | None = None) -> Counter:
    """Violations by reason (counts only). Independent of `bulk`'s own guards."""
    v: Counter = Counter()
    qrows = quarantine.load() if qrows is None else qrows
    index = privacy.default_copy_index(cfg)
    by_canon = {c["canonical_asset_id"]: c for c in caps}
    member_of = {a: c for c in caps for a in bulk.capture_assets(c)}
    for aid, n in Counter(sent).items():
        if n > 1:
            v["sent_more_than_once"] += 1
    items, others = [], []
    for aid in dict.fromkeys(sent):
        cap = by_canon.get(aid)
        if cap is None:
            v["not_canonical" if aid in member_of else "not_in_worklist"] += 1
            cap = member_of.get(aid)
        if cap is not None:
            if cap.get("queue") != "hosted" or not cap.get("eligible"):
                v["ineligible_capture"] += 1
            group = bulk.capture_assets(cap)
        else:
            group = [aid]
        every = set()
        for a in group:
            every |= {a, *index.pixel_group(a)}
        if any((qrows.get(a) or {}).get("verdict", "include") != "include" for a in every):
            v["quarantined"] += 1
        if aid not in before.screened:
            v["unscreened"] += 1
        if any(a in before.labelled for a in group):
            v["already_labelled"] += 1
        if aid in before.final_own:
            v["already_final_in_candidate_store"] += 1
        gid = cap["capture_id"] if cap else f"solo-{aid}"
        locs = sorted(index.locations(aid))
        items.append({"asset_id": aid, "group_id": gid, **({"source": locs[0][0], "relpath": locs[0][1]}
                                                           if locs else {})})
        for a in group[1:]:
            others += [{"asset_id": a, "source": s, "relpath": r, "group_id": gid}
                       for s, r, _ in sorted(index.locations(a))] or [{"asset_id": a, "group_id": gid}]
    if items:
        try:
            elig = privacy.filter_items(items, cfg, others=[*privacy.known_items(), *others], provider="gemini")
            for s in elig.skipped:
                v[f"privacy_{s.reason}"] += 1
        except privacy.PrivacyError:
            v["privacy_refused_whole_run"] += len(items)
    return v


# ---------- counts ----------


def count_files(cfg: config.Config) -> int:
    """Media rows in the latest frame of every configured source (as the worklist summary counts files)."""
    from shoebox.dedup.groups import media_rows

    return len(media_rows(worklist.load_inputs(cfg).rows))


def reconcile(files: int, counts: dict, tally: dict, n_requests: int, limit: int | None) -> tuple[dict, list[str]]:
    """The chain files -> assets -> captures -> eligible -> reused -> to send, and to send == requests sent
    once every documented drop between them is accounted for. Returns (table, problems)."""
    probs = []
    c = counts
    if not files >= c["assets"] >= c["captures"] >= c["eligible"] >= c["hosted"]:
        probs.append("files >= assets >= captures >= eligible >= hosted does not hold")
    tiny = c.get("tiny_routed", 0)
    if c["reused"] + c["fallback"] + tiny + c["to_send"] != c["for_candidate"]:
        probs.append("reused + fallback + tiny_routed + to_send != captures for the candidate")
    sliced = min(c["to_send"], limit) if limit else c["to_send"]
    drops = {"unlocated": c.get("unlocated", 0), "recheck_skipped": c.get("recheck_skipped", 0),
             "batch_skipped": tally.get("skipped", 0), "batch_cached": tally.get("cached", 0)}
    not_submitted = tally.get("total_unsubmitted", 0)
    expected = sliced - sum(drops.values()) - not_submitted
    if c.get("sent") is not None and c["sent"] != sliced - drops["unlocated"] - drops["recheck_skipped"]:
        probs.append("run record `sent` does not match to_send less unlocated and recheck skips")
    if n_requests != expected:
        probs.append(f"requests sent {n_requests} != to send {sliced} less drops {sum(drops.values())} "
                     f"less not submitted {not_submitted}")
    if tally.get("submitted") is not None and tally["submitted"] != n_requests:
        probs.append("batch tally `submitted` != requests the transport received")
    table = {"files": files, "assets": c["assets"], "captures": c["captures"], "eligible": c["eligible"],
             "hosted": c["hosted"], "reused": c["reused"], "fallback": c["fallback"], "tiny_routed": tiny,
             "to_send": c["to_send"], "limit": limit, "to_send_this_run": sliced, **drops,
             "not_submitted": not_submitted, "requests_sent": n_requests}
    return table, probs


# ---------- the run ----------


@dataclass
class Result:
    rc: int
    bulk_rc: int | None
    table: dict = field(default_factory=dict)
    violations: Counter = field(default_factory=Counter)
    problems: list[str] = field(default_factory=list)
    real_results_diff: dict = field(default_factory=dict)
    spend_unchanged: bool = True
    report: Path | None = None


def run_e2e(real_cfg: config.Config, worklist_path: Path, candidate: str, scratch: Path, runs_dir: Path,
            limit: int | None = None, files: int | None = None, today: date | None = None) -> Result:
    res = runners.resolve(candidate)
    real_root = Path(real_cfg.shoebox_root).expanduser()
    real_snap = snapshot(real_cfg.results_dir)
    scratch = check_scratch(scratch, real_root)
    overlay = scratch / "root"
    build_overlay(real_root, overlay, writable_paths(real_cfg, res))
    ocfg = replace(real_cfg, shoebox_root=overlay)
    spend = scratch / "spend.jsonl"
    real_spend = Path(privacy.SPEND_PATH)
    if real_spend.exists():
        shutil.copy2(real_spend, spend)
    real_spend_digest = file_digest(real_spend)

    header, caps = bulk.load_worklist(Path(worklist_path))
    names = bulk.chain_names(res, None)
    before = Before(ok_ids(ocfg, names), final_ids(ocfg, res), screened_ids(ocfg))
    files = count_files(ocfg) if files is None else files

    from shoebox.cli import main as cli_main
    from shoebox.runners import gemini, gemini_batch

    unfinished = [j for j in gemini_batch.load_jobs(ocfg, res.name) if j.get("state") not in ("stored", *gemini_batch.RETURNED)]
    if unfinished:  # the fake cannot answer for real provider jobs; refuse rather than invent their results
        raise SystemExit(f"{len(unfinished)} unfinished batch job(s) in the real ledger; finish them first")
    transport = RecordingTransport(res.cand)
    log = io.StringIO()
    argv = ["label", "bulk", "--worklist", str(worklist_path), "--candidate", candidate,
            *(["--limit", str(limit)] if limit else [])]
    bulk_rc = None
    problems: list[str] = []
    with contextlib.ExitStack() as st:
        st.enter_context(mock.patch.object(config, "load", lambda *a, **k: ocfg))
        st.enter_context(mock.patch.object(bulk, "batch_transport", lambda: transport))
        st.enter_context(mock.patch.object(gemini_batch, "HttpTransport", _forbid("HttpTransport")))
        st.enter_context(mock.patch.object(gemini, "api_key", _forbid("reading the Gemini key")))
        st.enter_context(mock.patch("httpx.Client.__init__", _forbid("an httpx client")))
        st.enter_context(mock.patch.object(privacy, "SPEND_PATH", spend))
        st.enter_context(contextlib.redirect_stdout(log))
        st.enter_context(contextlib.redirect_stderr(log))
        try:
            bulk_rc = cli_main(argv)
        except AssertionError:
            problems.append("bulk.plan count assertion failed (e.g. a queue bulk.plan does not know)")
        except Forbidden as e:
            problems.append(str(e))
    (scratch / "bulk.log").write_text(log.getvalue())  # stays in scratch: it names asset ids

    recs = sorted((ocfg.results_dir / "bulk-runs" / res.name).glob("*.json"),
                  key=lambda p: p.stat().st_mtime_ns)
    rec = json.loads(recs[-1].read_text()) if recs and bulk_rc is not None else {}
    tally = dict(rec.get("tally") or {})
    if tally.get("refused"):
        tally["total_unsubmitted"] = max(0, (rec["counts"].get("sent", 0) - tally.get("skipped", 0)
                                             - tally.get("cached", 0)) - tally.get("submitted", 0))
    sent = transport.sent_ids
    violations = check_sent(sent, caps, ocfg, before)
    table: dict = {}
    if rec.get("counts"):
        table, probs = reconcile(files, rec["counts"], tally, len(sent), limit)
        problems += probs
        if tally.get("error"):
            problems.append(f"{tally['error']} synthetic label(s) stored as error")
        if tally.get("ok", 0) + tally.get("invalid", 0) != len(sent):
            problems.append("stored labels != requests sent")
    elif not problems:
        problems.append(f"no run record (bulk exit {bulk_rc})")

    real_diff = snapshot_diff(real_snap, snapshot(real_cfg.results_dir))
    spend_same = file_digest(real_spend) == real_spend_digest
    if any(real_diff.values()):
        problems.append("the real results tree changed")
    if not spend_same:
        problems.append("the real spend log changed")

    usd, note = worklist.batch_usd(res.cand, table.get("to_send", 0))
    try:
        privacy.require_current_price(res.cand)
        expired = None
    except privacy.PrivacyError as e:
        expired = str(e)
    out = Result(1 if (violations or problems or bulk_rc not in (0,)) else 0, bulk_rc, table, violations,
                 problems, real_diff, spend_same)
    runs_dir.mkdir(parents=True, exist_ok=True)
    day = (today or date.today()).isoformat()
    out.report = runs_dir / f"{day}_e2e-fake-batch.md"
    out.report.write_text(report_md(out, candidate, res, header, usd, note, expired, tally, len(transport.jobs)))
    return out


def report_md(r: Result, candidate: str, res, header: dict, usd: float, note: str | None, expired: str | None,
              tally: dict, n_jobs: int) -> str:
    t = r.table
    disc = res.cand.get("price_batch_discount", worklist.DEFAULT_BATCH_DISCOUNT)
    lines = [
        "# E2E: `shoebox label bulk` against a fake batch transport (task 10.1)",
        "",
        (f"- run: {datetime.now().isoformat(timespec='seconds')}; candidate {candidate} "
        f"({res.model_id}, params {res.params_hash})"),
        f"- worklist created {header.get('created_at')}; sources {', '.join(header.get('sources') or [])}",
        "- transport: in-process fake (no network; HttpTransport, the Gemini key and httpx were blocked)",
        f"- verdict: {'PASS' if r.rc == 0 else 'FAIL'} (bulk exit {r.bulk_rc})",
        "",
        "## Counts",
        "",
    ]
    if t:
        lines += [
            (f"files {t['files']} → assets {t['assets']} → captures {t['captures']} → eligible {t['eligible']} → "
            f"hosted {t['hosted']} → reused {t['reused']} → fallback {t['fallback']} → tiny routed "
            f"{t['tiny_routed']} → to send {t['to_send']}"),
            "",
            (f"this run: limit {t['limit']}, to send {t['to_send_this_run']}; dropped at send time: unlocated "
            f"{t['unlocated']}, recheck {t['recheck_skipped']}, batch skipped {t['batch_skipped']}, cached "
            f"{t['batch_cached']}, not submitted (cap/budget) {t['not_submitted']}; requests sent "
            f"{t['requests_sent']} in {n_jobs} job(s)"),
            "",
            (f"stored synthetic labels (overlay only): ok {tally.get('ok', 0)}, invalid {tally.get('invalid', 0)}, "
            f"error {tally.get('error', 0)}"),
            "",
        ]
    lines += [
        "## Checks",
        "",
        "violations among sent ids (quarantined, privacy, unscreened, non-canonical, ineligible, already "
        "labelled): " + (", ".join(f"{k} {v}" for k, v in sorted(r.violations.items())) or "none"),
        "",
        "real results tree: " + ("unchanged" if not any(r.real_results_diff.values()) else
                                 ", ".join(f"{k} {v}" for k, v in r.real_results_diff.items())),
        "",
        f"real spend log: {'byte-identical' if r.spend_unchanged else 'CHANGED'}",
        "",
        "problems: " + ("; ".join(r.problems) or "none"),
        "",
        "## Re-quoted cost of the real run",
        "",
        (f"No quote for {t.get('to_send', 0)} capture(s): price expired or unknown ({expired})." if expired else
         f"${usd:.4f} for {t.get('to_send', 0)} capture(s) to send with {candidate} at batch price "
         f"(discount {disc}; price_date {res.cand.get('price_date')}; price_valid_until "
         f"{res.cand.get('price_valid_until')})."),
    ]
    if note:
        lines += ["", f"Note: {note}."]
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--worklist", required=True, type=Path)
    ap.add_argument("--candidate", required=True)
    ap.add_argument("--limit", type=int)
    ap.add_argument("--scratch", type=Path,
                    default=Path.home() / "shoebox-work" / f"e2e-{datetime.now():%Y%m%dT%H%M%S}")
    ap.add_argument("--runs-dir", type=Path, default=REPO_ROOT / "evals" / "runs")
    args = ap.parse_args(argv)
    cfg = config.load()
    r = run_e2e(cfg, args.worklist, args.candidate, args.scratch, args.runs_dir, args.limit)
    print(f"bulk exit {r.bulk_rc}; counts: {json.dumps(r.table)}")
    print("violations: " + (json.dumps(dict(r.violations)) or "none"))
    print("problems: " + ("; ".join(r.problems) or "none"))
    print(f"report -> {r.report}; scratch -> {args.scratch}")
    return r.rc


if __name__ == "__main__":
    sys.exit(main())
