"""Hosted-call guards (design D9): privacy checklist, never-send list, consent, spend caps and spend log.

Every hosted runner calls these before sending anything. Nothing here talks to the network.
"""

import json
import os
import re
import tomllib
from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from pathlib import Path

from shoebox import config

EVALS_DIR = config.REPO_ROOT / "evals"
CHECKLIST_PATH = EVALS_DIR / "privacy-checklist.toml"
NEVER_SEND_PATH = EVALS_DIR / "never-send.txt"
SPEND_PATH = EVALS_DIR / "spend.jsonl"
SETS_DIR = EVALS_DIR / "sets"

DEFAULT_MAX_TOTAL_USD = 150.0
DEFAULT_CHECKLIST_MAX_AGE_DAYS = 90
RETENTION_DAYS = (1, 7)

# Gemini 3 image tokens per media_resolution level (research/mvp-models.md, Q1).
IMAGE_TOKENS = {"low": 280, "medium": 560, "high": 1120}

_ASSET_ID = re.compile(r"^[0-9a-f]{64}$")
_today = date.today  # tests swap this to cross a price boundary


class PrivacyError(RuntimeError):
    """A hosted run must not start (or continue)."""


# ---------- limits ([hosted] in config/shoebox.toml, with defaults) ----------


@dataclass(frozen=True)
class Limits:
    max_total_usd: float = DEFAULT_MAX_TOTAL_USD
    checklist_max_age_days: int = DEFAULT_CHECKLIST_MAX_AGE_DAYS


def limits(path: Path | None = None) -> Limits:
    """Project budget and checklist age from an optional `[hosted]` table in config/shoebox.toml."""
    path = Path(path or config.CONFIG_PATH)
    try:
        hosted = tomllib.loads(path.read_text()).get("hosted", {})
    except FileNotFoundError:
        hosted = {}
    return Limits(
        max_total_usd=float(hosted.get("max_usd_total", DEFAULT_MAX_TOTAL_USD)),
        checklist_max_age_days=int(
            hosted.get("checklist_max_age_days", DEFAULT_CHECKLIST_MAX_AGE_DAYS)
        ),
    )


# ---------- checklist ----------


@dataclass(frozen=True)
class CheckResult:
    item: str
    ok: bool
    detail: str


def _as_date(value) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str) and value.strip():
        try:
            return date.fromisoformat(value.strip())
        except ValueError:
            return None
    return None


def check_checklist(
    path: Path | None = None, today: date | None = None, max_age_days: int | None = None
) -> list[CheckResult]:
    """Every checklist item with pass/fail. Every boolean must be true, retention 1..7 days,
    a billing project set, and `confirmed_on` within `max_age_days` (not in the future)."""
    path = Path(path or CHECKLIST_PATH)
    today = today or date.today()
    max_age_days = (
        max_age_days if max_age_days is not None else limits().checklist_max_age_days
    )
    try:
        data = tomllib.loads(path.read_text())
    except FileNotFoundError:
        return [CheckResult("checklist_file", False, f"missing: {path}")]
    except tomllib.TOMLDecodeError as e:
        return [CheckResult("checklist_file", False, f"unreadable TOML: {e}")]

    results: list[CheckResult] = []
    project = data.get("billing_project_id")
    results.append(
        CheckResult(
            "billing_project_id",
            isinstance(project, str) and bool(project.strip()),
            "set" if isinstance(project, str) and project.strip() else "empty",
        )
    )
    known = {"billing_project_id", "log_retention_days", "confirmed_on"}
    bools = [k for k, v in data.items() if isinstance(v, bool)]
    for name in (
        "paid_tier_with_prepay_balance",
        "ai_studio_logging_off",
        "no_datasets_or_feedback",
    ):
        if name not in data:
            results.append(CheckResult(name, False, "missing"))
    for name in bools:
        results.append(
            CheckResult(name, data[name] is True, "true" if data[name] else "false")
        )
    for name, value in data.items():
        if name not in known and not isinstance(value, bool):
            results.append(
                CheckResult(
                    name,
                    False,
                    f"unexpected value {type(value).__name__}; expected true/false",
                )
            )

    days = data.get("log_retention_days")
    lo, hi = RETENTION_DAYS
    ok = isinstance(days, int) and not isinstance(days, bool) and lo <= days <= hi
    results.append(
        CheckResult("log_retention_days", ok, f"{days!r} (must be {lo}..{hi})")
    )

    confirmed = _as_date(data.get("confirmed_on"))
    if confirmed is None:
        results.append(
            CheckResult(
                "confirmed_on", False, f"not a date: {data.get('confirmed_on')!r}"
            )
        )
    else:
        age = (today - confirmed).days
        if age < 0:
            results.append(
                CheckResult("confirmed_on", False, f"{confirmed} is in the future")
            )
        else:
            results.append(
                CheckResult(
                    "confirmed_on",
                    age <= max_age_days,
                    f"{confirmed} ({age} days ago, max {max_age_days})",
                )
            )
    return results


def require_checklist(path: Path | None = None, today: date | None = None) -> None:
    """Raise PrivacyError naming every failing checklist item."""
    failed = [r for r in check_checklist(path, today) if not r.ok]
    if failed:
        names = ", ".join(f"{r.item} ({r.detail})" for r in failed)
        raise PrivacyError(
            f"privacy checklist not confirmed: {names}. Run `shoebox privacy check`."
        )


# ---------- never-send list and consent ----------


def _glob_regex(entry: str) -> re.Pattern:
    """gitignore-like: `**` crosses folders, `*` and `?` don't; an entry with no slash except a
    trailing one matches at any depth; a match on a folder covers everything below it, so
    "Pictures/Kyra" and "Pictures/Kyra/" both block the whole folder. Case-insensitive: the NAS
    shares are, and over-blocking is safe."""
    pattern = entry.strip().lstrip("/").rstrip("/")
    out, i = ([] if "/" in pattern else ["(?:.*/)?"]), 0
    while i < len(pattern):
        if pattern.startswith("**/", i):
            out.append("(?:.*/)?")
            i += 3
        elif pattern.startswith("**", i):
            out.append(".*")
            i += 2
        elif pattern[i] == "*":
            out.append("[^/]*")
            i += 1
        elif pattern[i] == "?":
            out.append("[^/]")
            i += 1
        elif pattern[i] == "[":
            j = pattern.find("]", i + 1)
            if j == -1:
                out.append(re.escape(pattern[i]))
                i += 1
            else:
                body = pattern[i + 1 : j]
                neg = body.startswith("!")
                body = body[1:] if neg else body
                out.append(
                    "[" + ("^" if neg else "") + body.replace("\\", "\\\\") + "]"
                )
                i = j + 1
        else:
            out.append(re.escape(pattern[i]))
            i += 1
    return re.compile("".join(out) + r"(?:/.*)?\Z", re.IGNORECASE)


@dataclass(frozen=True)
class NeverSend:
    asset_ids: frozenset[str] = frozenset()
    globs: tuple[str, ...] = ()
    _regexes: tuple[re.Pattern, ...] = field(default=(), repr=False, compare=False)

    def matches(self, asset_id: str | None, relpath: str | None) -> bool:
        if asset_id and asset_id.lower() in self.asset_ids:
            return True
        if relpath:
            rel = relpath.replace("\\", "/").lstrip("/")
            return any(r.match(rel) for r in self._regexes)
        return False

    def unmatched(self, items: list[dict]) -> list[str]:
        """Entries that match none of `items`: likely a typo or a path not relative to a source root."""
        ids = {str(it.get("asset_id") or "").lower() for it in items}
        rels = [
            str(it["relpath"]).replace("\\", "/").lstrip("/")
            for it in items
            if it.get("relpath")
        ]
        out = sorted(a for a in self.asset_ids if a not in ids)
        out += [
            g
            for g, r in zip(self.globs, self._regexes)
            if not any(r.match(rel) for rel in rels)
        ]
        return out


def load_never_send(path: Path | None = None) -> NeverSend:
    """Asset ids (BLAKE3 hex) and path globs relative to a source root; `#` lines ignored.
    A missing file is an empty list (the template ships in the repo)."""
    path = Path(path or NEVER_SEND_PATH)
    try:
        lines = path.read_text().splitlines()
    except FileNotFoundError:
        return NeverSend()
    ids, globs = set(), []
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if _ASSET_ID.match(line.lower()):
            ids.add(line.lower())
        else:
            globs.append(line)
    return NeverSend(frozenset(ids), tuple(globs), tuple(_glob_regex(g) for g in globs))


@dataclass(frozen=True)
class Skip:
    asset_id: str | None
    source: str | None
    relpath: str | None
    # never_send | unknown_source | no_consent (incl. provider not in hosted_models) | no_relpath
    # | held_back | copy_restricted
    reason: str


@dataclass
class Eligibility:
    allowed: list[dict]
    skipped: list[Skip]

    def counts(self) -> dict[str, int]:
        return dict(sorted(Counter(s.reason for s in self.skipped).items()))

    def summary(self) -> str:
        parts = ", ".join(f"{n} {reason}" for reason, n in self.counts().items())
        return f"{len(self.allowed)} allowed, {len(self.skipped)} skipped" + (
            f" ({parts})" if parts else ""
        )


def _held_back(source: config.Source, relpath: str) -> bool:
    rel = relpath.replace("\\", "/").lstrip("/")
    return any(_glob_regex(g).match(rel) for g in source.held_back)


def _own_restriction(
    item: dict, cfg: config.Config, never: NeverSend, provider: str = "gemini"
) -> str | None:
    if never.matches(item.get("asset_id"), item.get("relpath")):
        return "never_send"
    source = cfg.sources.get(item.get("source") or "")
    if source is None:
        return "unknown_source"
    if source.hosted_consent is not True or provider not in source.hosted_models:
        return "no_consent"
    if not item.get("relpath"):
        return "no_relpath"  # can't check path globs, so don't send
    if _held_back(source, item["relpath"]):
        return "held_back"
    return None


def known_items(sets_dir: Path | None = None) -> list[dict]:
    """Every item in `evals/sets/*/items.jsonl`: the known copies and siblings until a catalogue exists."""
    sets_dir = Path(sets_dir or SETS_DIR)
    out = []
    for path in sorted(sets_dir.glob("*/items.jsonl")):
        for line in path.read_text().splitlines():
            if line.strip():
                out.append(json.loads(line))
    return out


def filter_items(
    items: list[dict],
    cfg: config.Config,
    never: NeverSend | None = None,
    others: list[dict] = (),
    provider: str = "gemini",
) -> Eligibility:
    """Split items into allowed and skipped. A restriction on any copy (same asset_id) or sibling
    (same group_id, e.g. a raw/JPEG pair) restricts them all: eligible only if none is restricted.
    `others` are further known items (e.g. `known_items()`) consulted only for copies and siblings.
    `provider` is the hosted provider the items would go to; the source must list it in hosted_models."""
    never = never if never is not None else load_never_send()
    own = [_own_restriction(it, cfg, never, provider) for it in items]
    every = [
        *zip(items, own),
        *((o, _own_restriction(o, cfg, never, provider)) for o in others),
    ]
    bad_ids = {it.get("asset_id") for it, r in every if r and it.get("asset_id")}
    bad_groups = {it.get("group_id") for it, r in every if r and it.get("group_id")}
    allowed, skipped = [], []
    for it, reason in zip(items, own):
        if reason is None and (
            it.get("asset_id") in bad_ids or (it.get("group_id") or None) in bad_groups
        ):
            reason = "copy_restricted"
        if reason is None:
            allowed.append(it)
        else:
            skipped.append(
                Skip(it.get("asset_id"), it.get("source"), it.get("relpath"), reason)
            )
    return Eligibility(allowed, skipped)


# ---------- spend ----------


@dataclass(frozen=True)
class Estimate:
    n: int
    input_tokens: int
    output_tokens: int
    usd: float


def price(cand: dict) -> tuple[float, float]:
    """USD per input token and per output token (output includes thinking). No defaults: a hosted
    candidate states its prices."""
    out = []
    for key in ("price_input_usd_per_mtok", "price_output_usd_per_mtok"):
        if cand.get(key) is None:
            raise PrivacyError(
                f"candidate has no {key}: hosted runs need explicit prices"
            )
        out.append(float(cand[key]) / 1e6)
    return out[0], out[1]


def require_current_price(cand: dict, today: date | None = None) -> None:
    """Refuse once `price_valid_until` has passed (Gemini 3.8 Flash's introductory price ends
    2026-12-31, then doubles): recorded spend and the budget checks would undercount."""
    until = _as_date(cand.get("price_valid_until"))
    if until is None:
        raise PrivacyError(
            f"candidate price_valid_until is missing or not a date "
            f"({cand.get('price_valid_until')!r}): hosted prices need a review date"
        )
    today = today or _today()
    if today > until:
        raise PrivacyError(
            f"candidate prices expired: price_valid_until {until} is before today ({today}). "
            "Update the price_* keys from the current price list, then price_date and price_valid_until."
        )


def estimate(cand: dict, n: int) -> Estimate:
    """Pre-flight cost for n images from the candidate's token estimates."""
    res = cand.get("media_resolution", "high")
    if res not in IMAGE_TOKENS:
        raise PrivacyError(
            f"unknown media_resolution {res!r}; expected one of {sorted(IMAGE_TOKENS)}"
        )
    per_in = IMAGE_TOKENS[res] + int(cand.get("est_prompt_tokens", 1000))
    per_out = int(cand.get("est_output_tokens", 300)) + int(
        cand.get("est_thinking_tokens", 1000)
    )
    pin, pout = price(cand)
    return Estimate(n, n * per_in, n * per_out, n * (per_in * pin + per_out * pout))


def spent_total(path: Path | None = None) -> float:
    """Cumulative USD in the spend log. Unreadable lines fail loudly rather than undercount."""
    path = Path(path or SPEND_PATH)
    if not path.exists():
        return 0.0
    total = 0.0
    for i, line in enumerate(path.read_text().splitlines(), 1):
        if line.strip():
            try:
                total += float(json.loads(line)["usd"])
            except (ValueError, KeyError, TypeError) as e:
                raise PrivacyError(f"{path}:{i}: unreadable spend line ({e})") from e
    return total


def check_budget(
    cand: dict,
    est: Estimate,
    already_spent: float | None = None,
    lim: Limits | None = None,
) -> None:
    """Refuse if the estimate is over the run cap or the remaining project budget."""
    lim = lim or limits()
    spent = spent_total() if already_spent is None else already_spent
    remaining = lim.max_total_usd - spent
    cap = cand.get("max_usd_per_run")
    if cap is None:
        raise PrivacyError(
            "candidate has no max_usd_per_run: every hosted run needs a cap"
        )
    if est.usd > float(cap):
        raise PrivacyError(
            f"estimated ${est.usd:.4f} for {est.n} images is over max_usd_per_run ${float(cap):.2f} "
            f"(remaining project budget ${remaining:.2f})"
        )
    if est.usd > remaining:
        raise PrivacyError(
            f"estimated ${est.usd:.4f} for {est.n} images is over the remaining project budget "
            f"${remaining:.2f} (spent ${spent:.2f} of ${lim.max_total_usd:.2f})"
        )


def append_spend(
    candidate: str,
    n: int,
    usd: float,
    price_date: str,
    path: Path | None = None,
    ts: str | None = None,
    run: str | None = None,
) -> None:
    """Append {ts, candidate, n, usd, price_date[, run]} and fsync, so a killed process can't lose
    spend it has already been billed for. Hosted runners write one line per billed call."""
    path = Path(path or SPEND_PATH)
    path.parent.mkdir(parents=True, exist_ok=True)
    rec = {
        "ts": ts or datetime.now(UTC).isoformat(timespec="seconds"),
        "candidate": candidate,
        "n": n,
        "usd": round(usd, 8),
        "price_date": price_date,
    }
    if run:
        rec["run"] = run
    with path.open("a") as f:
        f.write(json.dumps(rec, sort_keys=True) + "\n")
        f.flush()
        os.fsync(f.fileno())
