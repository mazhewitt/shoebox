"""Known-item search eval: queries from the owner's notes, search routes, rankings, Vally spec and report.

An early slice of tasks 6.4 (retrieval grader) and 7.2 (retrieval aggregation); see docs/evals.md,
"Known-item search". Layers, as for the label evals (design D1):

- `build_queries` / `write_queries`: one query per distinct owner note; relevant = the noted items and
  their capture-group siblings.
- `Sources` + `run_route`: every route ranks the whole corpus (the set's items) for every query.
  Rankings go to `<shoebox_root>/results/search/<set>/<route_dir>/<params_hash>.jsonl`.
- `generate_spec`: the Vally spec (one stimulus per query, `retrieval` grader) and `routes.json`.
- `build_report`: per-route tie-aware MRR, hit/recall/nDCG@k, pool coverage, ranks, a bootstrap CI and
  paired comparisons.

No query or caption text is ever written to a report: query_ids and asset_ids only.
"""

import hashlib
import json
import math
import os
import re
import sqlite3
import statistics
import sys
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import date as Date
from pathlib import Path

import numpy as np

from shoebox import store
from shoebox.config import REPO_ROOT, Config, check_write_location

QUERIES_FILE = "search-known-item.jsonl"
EXCLUDE_FILE = "exclude.jsonl"  # optional: asset_ids removed from the corpus (never a target)
DIAG_TOP = 10  # diagnostics: how many of the items ranked above the first target to keep
QUERY_SOURCE = "owner_notes"
EMBED_CANDIDATE = "eg2-bf16"
VLM_CANDIDATES = ("e4b-bf16", "e4b-q4", "g12b-q4", "g31b", "gemini-low", "gemini-medium")
IMAGE_DIMS = (768, 256)
BLEND_W = 0.5  # fixed, not tuned (there are no held-out dev queries yet)
BLEND_SENSITIVITY = (0.3, 0.7)  # reported as a sensitivity table only
RRF_K = 60
FTS_TOKENIZER = "porter unicode61"
FTS_VERSION = 1  # bump when the FTS query construction changes
K_VALUES = (1, 5, 10, 20)  # 20: the D11 search gate is on nDCG@20
PASS_K = 5  # the Vally verdict: pass iff a relevant item is in the top 5 under every tie order
BOOTSTRAP_RESAMPLES = 1000
BOOTSTRAP_SEED = 20261010
BASELINE_ROUTE = "eg2-image@768"
HEADER = "# generated — do not edit. Regenerate with `shoebox eval gen-search`."

# A plain English function-word list (no domain words such as "photo" or "scene": nothing here was
# chosen by looking at the queries). Query terms in this list are dropped before the FTS query is built.
_STOPWORDS_TEXT = """
a about above after again against all am an and any are as at be been before being below between both
but by can could did do does doing down during each few for from further had has have having he her here
hers herself him himself his how i if in into is it its itself just me more most my myself no nor not of
off on once only or other our ours ourselves out over own same she should so some such than that the
their theirs them themselves then there these they this those through to too under until up very was we
were what when where which while who whom why will with would you your yours yourself yourselves
"""
STOPWORDS = frozenset(_STOPWORDS_TEXT.split())

_ROUTE = re.compile(
    r"^(?:eg2-image@(?P<dim>\d+)"
    r"|(?P<kind>eg2-caption|eg2-blend|fts|hybrid)(?:@w(?P<w>[0-9.]+))?:(?P<cand>[A-Za-z0-9._-]+))$"
)
_TOKEN = re.compile(r"[^\W_]+")  # what FTS5's unicode61 tokenizer keeps: letters and digits


# --- small helpers ------------------------------------------------------------------------------

def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in Path(path).read_text().splitlines() if line.strip()]


def sha256_lines(values: Iterable[str]) -> str:
    return hashlib.sha256("\n".join(values).encode()).hexdigest()


def file_sha256(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_text(text)
    os.replace(tmp, path)


# --- queries ------------------------------------------------------------------------------------

def normalise_note(text: str) -> str:
    """The dedup key of a note: whitespace collapsed, case folded."""
    return " ".join(str(text or "").split()).casefold()


def query_id(note: str) -> str:
    """Stable id from the normalised note text (so the id never reveals the text)."""
    return "q-" + hashlib.sha256(normalise_note(note).encode()).hexdigest()[:12]


@dataclass
class QueryBuild:
    queries: list[dict]
    merged_groups: dict[str, list[str]] = field(default_factory=dict)  # query_id -> group keys merged
    items_without_note: list[str] = field(default_factory=list)


def build_queries(items: list[dict], labels: list[dict]) -> QueryBuild:
    """One query per distinct note text (normalised). An item's relevant set is itself plus its
    capture-group siblings (`group_id`). Items sharing a note text pool their relevant sets: a DNG+JPG
    pair with one note becomes one query; two different groups (or ungrouped items) with an identical
    note become one query whose relevant set is the union (recorded in `merged_groups`).
    `relevant_groups` partitions `relevant` into targets, one per capture group (or ungrouped item): the
    metrics score a pair as one target."""
    by_id = {}
    for it in items:
        by_id.setdefault(it["asset_id"], it)
    group_members: dict[str, list[str]] = {}
    for aid, it in by_id.items():
        if it.get("group_id"):
            group_members.setdefault(it["group_id"], []).append(aid)
    notes = {}
    for lab in labels:  # later lines win, as gen.load_labels
        notes[lab["asset_id"]] = lab.get("notes") or ""

    order: list[str] = []
    text: dict[str, str] = {}
    relevant: dict[str, set[str]] = {}
    groups: dict[str, set[str]] = {}
    without = []
    for aid, it in by_id.items():  # set order, so query order is stable
        note = " ".join(str(notes.get(aid, "")).split())
        if not note:
            without.append(aid)
            continue
        key = normalise_note(note)
        if key not in text:
            order.append(key)
            text[key] = note
            relevant[key], groups[key] = set(), set()
        gid = it.get("group_id")
        relevant[key].update(group_members[gid] if gid else [aid])
        groups[key].add(gid or f"item:{aid}")
    out = QueryBuild([])
    for key in order:
        qid = query_id(text[key])
        targets = sorted(sorted(group_members[g] if g in group_members else [g.removeprefix("item:")])
                         for g in groups[key])
        out.queries.append({"query_id": qid, "text": text[key], "relevant": sorted(relevant[key]),
                            "relevant_groups": targets, "source": QUERY_SOURCE})
        if len(groups[key]) > 1:
            out.merged_groups[qid] = sorted(groups[key])
    out.items_without_note = without
    return out


def write_queries(set_dir: Path) -> tuple[Path, QueryBuild]:
    set_dir = Path(set_dir)
    labels_path = set_dir / "labels.jsonl"
    if not labels_path.exists():
        raise FileNotFoundError(f"no owner labels at {labels_path}")
    qb = build_queries(read_jsonl(set_dir / "items.jsonl"), read_jsonl(labels_path))
    path = set_dir / QUERIES_FILE
    _atomic_write(path, "".join(json.dumps(q, ensure_ascii=False) + "\n" for q in qb.queries))
    return path, qb


# --- routes -------------------------------------------------------------------------------------

@dataclass(frozen=True)
class Route:
    name: str
    kind: str  # eg2-image | eg2-caption | eg2-blend | fts | hybrid
    cand: str | None = None
    dim: int = 768
    w: float | None = None

    @property
    def caption_based(self) -> bool:
        return self.kind != "eg2-image"

    @property
    def sensitivity(self) -> bool:
        """A blend at a non-primary weight: reported in the sensitivity table, not as a contender."""
        return self.kind == "eg2-blend" and self.w != BLEND_W

    @property
    def dir_name(self) -> str:
        """Filesystem-safe name (the shoebox_root share is SMB: no ':')."""
        return store.model_dir(self.name)


def _w_str(w: float) -> str:
    return f"{w:g}"


def parse_route(name: str) -> Route:
    m = _ROUTE.match(name.strip())
    if not m:
        raise ValueError(f"unknown route {name!r} (want eg2-image@768|256, eg2-caption:<cand>, eg2-blend:<cand>, "
                         "eg2-blend@w0.3:<cand>, fts:<cand>, hybrid:<cand>)")
    if m["dim"]:
        dim = int(m["dim"])
        if dim not in IMAGE_DIMS:
            raise ValueError(f"route {name!r}: dim must be one of {IMAGE_DIMS}")
        return Route(name, "eg2-image", dim=dim)
    kind, cand = m["kind"], m["cand"]
    if m["w"] is not None:
        if kind != "eg2-blend":
            raise ValueError(f"route {name!r}: only eg2-blend takes a weight")
        w = float(m["w"])
        if not 0 <= w <= 1:
            raise ValueError(f"route {name!r}: w must be in [0, 1]")
        if w == BLEND_W:
            raise ValueError(f"route {name!r}: the primary blend (w={BLEND_W}) is eg2-blend:{cand}")
        return Route(name, kind, cand, w=w)
    w = BLEND_W if kind in ("eg2-blend", "hybrid") else None
    return Route(name, kind, cand, w=w)


def default_routes(candidates: Iterable[str] = VLM_CANDIDATES, sensitivity: bool = True) -> list[str]:
    out = [f"eg2-image@{d}" for d in IMAGE_DIMS]
    for c in candidates:
        out += [f"eg2-caption:{c}", f"eg2-blend:{c}"]
        if sensitivity:
            out += [f"eg2-blend@w{_w_str(w)}:{c}" for w in BLEND_SENSITIVITY]
        out += [f"fts:{c}", f"hybrid:{c}"]
    return out


# --- scoring ------------------------------------------------------------------------------------

Scores = dict[str, float]  # asset_id -> score; an absent asset has no score and ranks last


def rank_scores(corpus: list[str], scores: Scores) -> tuple[list[str], list[float | None]]:
    """Full ranking of the corpus: score descending, ties by asset_id ascending; unscored items last,
    by asset_id."""
    scored = sorted((a for a in corpus if a in scores), key=lambda a: (-scores[a], a))
    rest = sorted(a for a in corpus if a not in scores)
    ranking = scored + rest
    return ranking, [scores.get(a) for a in ranking]


def truncate(v: np.ndarray, dim: int) -> np.ndarray:
    """MRL truncation: the first `dim` values, L2-normalised again (`shoebox.embed.truncate`)."""
    from shoebox import embed

    return embed.truncate(v, dim)


def cosine_scores(q: np.ndarray, vectors: dict[str, np.ndarray], dim: int = 768) -> Scores:
    """Cosine of a unit query with each stored vector, both truncated to `dim` and re-normalised."""
    qd = truncate(q, dim)
    return {a: float(truncate(v, dim) @ qd) for a, v in vectors.items()}


def blend_scores(image: Scores, caption: Scores, w: float) -> Scores:
    """w * cos(image) + (1 - w) * cos(caption); an item with no caption vector keeps its image score.
    An item with no image vector is unscored."""
    return {a: (w * s + (1 - w) * caption[a]) if a in caption else s for a, s in image.items()}


def fts_terms(text: str) -> list[str]:
    """Query terms: casefolded letter/digit runs (as FTS5 unicode61 splits them), stopwords removed,
    duplicates removed, in query order."""
    out = []
    for t in _TOKEN.findall(str(text).casefold()):
        if t not in STOPWORDS and t not in out:
            out.append(t)
    return out


def fts_query(text: str) -> str:
    """The FTS5 MATCH expression: each term as a quoted string, ORed. Empty when no term is left."""
    return " OR ".join(f'"{t}"' for t in fts_terms(text))


class FtsIndex:
    """An in-memory SQLite FTS5 index over one candidate's caption + tags (never on disk, never on ~/mnt).

    Only items with a non-empty caption are indexed. Score = -bm25() (default column weights), so higher is
    better; an unmatched or unindexed item has no score and ranks last."""

    def __init__(self, docs: dict[str, dict]):
        self.db = sqlite3.connect(":memory:")
        self.db.execute(f"CREATE VIRTUAL TABLE docs USING fts5(asset_id UNINDEXED, caption, tags, "
                        f"tokenize='{FTS_TOKENIZER}')")
        rows = [(a, d["caption"], " ".join(d.get("tags") or [])) for a, d in sorted(docs.items())
                if (d.get("caption") or "").strip()]
        self.db.executemany("INSERT INTO docs(asset_id, caption, tags) VALUES (?, ?, ?)", rows)
        self.n = len(rows)

    def scores(self, text: str) -> Scores:
        q = fts_query(text)
        if not q:
            return {}
        cur = self.db.execute("SELECT asset_id, bm25(docs) FROM docs WHERE docs MATCH ?", (q,))
        return {a: -float(s) for a, s in cur}

    def close(self) -> None:
        self.db.close()


def rrf_scores(rankings: list[tuple[list[str], list[float | None]]], k: int = RRF_K) -> Scores:
    """Reciprocal-rank fusion: sum over routes of 1 / (k + rank), rank 1-based. Only items a route actually
    scored count for that route (its unscored tail is not a retrieval)."""
    out: Scores = {}
    for ranking, scores in rankings:
        for i, (a, s) in enumerate(zip(ranking, scores, strict=True)):
            if s is not None:
                out[a] = out.get(a, 0.0) + 1.0 / (k + i + 1)
    return out


# --- sources ------------------------------------------------------------------------------------

@dataclass
class Sources:
    """Everything the routes read. Vectors are 768-d unit float32 arrays keyed by asset_id."""

    set_name: str
    corpus: list[str]  # the set's distinct asset_ids, sorted
    queries: list[dict]
    queries_sha256: str
    embed_query: Callable[[str], np.ndarray]  # query text -> 768-d unit vector (EG2 query prompt applied)
    eg2: dict  # {candidate, model_id, params_hash, query_prompt} of the image-only EG2 store
    image: dict[str, np.ndarray]
    labels: dict[str, dict] = field(default_factory=dict)  # cand -> {asset_id: {caption, tags}}
    label_source: dict[str, dict] = field(default_factory=dict)  # cand -> {candidate, model_id, params_hash}
    caption_vecs: dict[str, dict[str, np.ndarray]] = field(default_factory=dict)  # cand -> vectors
    caption_store: dict[str, dict] = field(default_factory=dict)  # cand -> {model_id, params_hash}
    exclude: dict | None = None  # {file, n, sha256} when the set has an exclude.jsonl
    _qvec: dict[str, np.ndarray] = field(default_factory=dict, repr=False)
    _fts: dict[str, FtsIndex] = field(default_factory=dict, repr=False)

    def query_vector(self, q: dict) -> np.ndarray:
        if q["query_id"] not in self._qvec:
            self._qvec[q["query_id"]] = np.asarray(self.embed_query(q["text"]), dtype=np.float32)
        return self._qvec[q["query_id"]]

    def fts(self, cand: str) -> FtsIndex:
        if cand not in self._fts:
            self._fts[cand] = FtsIndex(self.labels[cand])
        return self._fts[cand]

    @property
    def corpus_sha256(self) -> str:
        return sha256_lines(self.corpus)

    @property
    def corpus_info(self) -> dict:
        return corpus_info(self.corpus, self.queries, self.exclude)

    def missing(self, route: Route) -> str | None:
        """Why this route can't run from these sources, or None."""
        if route.kind in ("eg2-caption", "eg2-blend", "hybrid") and route.cand not in self.caption_vecs:
            return f"no EG2 caption embeddings for {route.cand} (run `shoebox embed run --caption-from {route.cand}`)"
        if route.kind in ("fts", "hybrid") and not self.labels.get(route.cand):
            return f"no captions in the label store of {route.cand}"
        return None


def route_scores(route: Route, q: dict, src: Sources) -> Scores:
    qv = src.query_vector(q) if route.kind != "fts" else None
    if route.kind == "eg2-image":
        return cosine_scores(qv, src.image, route.dim)
    if route.kind == "eg2-caption":
        return cosine_scores(qv, src.caption_vecs[route.cand])
    if route.kind == "eg2-blend":
        return blend_scores(cosine_scores(qv, src.image), cosine_scores(qv, src.caption_vecs[route.cand]), route.w)
    if route.kind == "fts":
        return src.fts(route.cand).scores(q["text"])
    if route.kind == "hybrid":
        blend = rank_scores(src.corpus, route_scores(parse_route(f"eg2-blend:{route.cand}"), q, src))
        fts = rank_scores(src.corpus, route_scores(parse_route(f"fts:{route.cand}"), q, src))
        return rrf_scores([blend, fts], RRF_K)
    raise ValueError(route.kind)


def run_route(route: Route, src: Sources) -> list[dict]:
    rows = []
    for q in src.queries:
        ranking, scores = rank_scores(src.corpus, route_scores(route, q, src))
        rows.append({"query_id": q["query_id"], "route": route.name, "ranking": ranking, "scores": scores})
    return rows


def query_diagnostics(query_id: str, route: str, ranking: list[str], scores: list[float | None],
                      relevant: Iterable[str]) -> dict:
    """The first target's rank (tie-aware bounds and expectation) and the items ranked above it in the stored
    order (at most DIAG_TOP, with their scores): what beats the target. asset_ids only, no text."""
    rel = set(relevant)
    m = query_metrics(ranking, rel, ks=(), scores=scores)
    first = next((i for i, a in enumerate(ranking) if a in rel), len(ranking))
    return {"query_id": query_id, "route": route, "rank_best": m["rank_best"], "rank_worst": m["rank_worst"],
            "rank_expected": m["rank_expected"],
            "above": [{"asset_id": a, "score": s} for a, s in zip(ranking[:min(first, DIAG_TOP)], scores,
                                                                  strict=False)]}


def route_definition(route: Route, src: Sources) -> dict:
    """Everything a route's rankings depend on; its params_hash is the hash of this."""
    d: dict = {"route": route.name, "kind": route.kind, "set": src.set_name,
               "queries_sha256": src.queries_sha256, "corpus_sha256": src.corpus_sha256,
               "eg2": {k: src.eg2.get(k) for k in ("candidate", "model_id", "params_hash", "query_prompt")},
               "tie_break": "score desc, asset_id asc; unscored last by asset_id"}
    if route.kind == "eg2-image":
        d["dim"] = route.dim
        d["mrl"] = "truncate the 768-d query and image vectors to dim, then L2-normalise"
    if route.cand:
        d["candidate"] = route.cand
    if route.kind in ("eg2-caption", "eg2-blend", "hybrid"):
        d["dim"] = 768
        d["caption_embeddings"] = src.caption_store[route.cand]
    if route.kind in ("eg2-blend", "hybrid"):
        d["w"] = route.w
        d["blend"] = "w*cos(image) + (1-w)*cos(caption); no caption vector -> image score"
    if route.kind in ("fts", "hybrid"):
        d["caption_source"] = src.label_source[route.cand]
        d["fts"] = {"engine": f"sqlite {sqlite3.sqlite_version} fts5 (in memory)", "tokenizer": FTS_TOKENIZER,
                    "columns": ["caption", "tags"], "score": "-bm25() default weights",
                    "query": "casefolded [^\\W_]+ terms, stopwords removed, deduped, each quoted, ORed",
                    "stopwords_sha256": sha256_lines(sorted(STOPWORDS)), "version": FTS_VERSION}
    if route.kind == "hybrid":
        d["rrf_k"] = RRF_K
        d["fused"] = [f"eg2-blend:{route.cand}", f"fts:{route.cand}"]
    if src.exclude is not None:  # only then: sets without exclusions keep their hashes
        d["exclude"] = dict(src.exclude)
    return d


def coverage(route: Route, src: Sources) -> dict:
    """Corpus items each route input lacks (not hashed: they follow from the hashed stores)."""
    out = {"corpus_n": len(src.corpus),
           "missing_image_vectors": sum(a not in src.image for a in src.corpus)}
    if route.cand and route.cand in src.caption_vecs:
        out["missing_caption_vectors"] = sum(a not in src.caption_vecs[route.cand] for a in src.corpus)
    if route.cand and route.cand in src.labels:
        out["missing_captions"] = sum(not (src.labels[route.cand].get(a) or {}).get("caption")
                                      for a in src.corpus)
    return out


def search_dir(cfg: Config, set_name: str) -> Path:
    return cfg.results_dir / "search" / set_name


def write_route(cfg: Config, route: Route, src: Sources, rows: list[dict]) -> dict:
    """Writes `<route_dir>/<params_hash>.jsonl` and `<params_hash>.params.json`; returns the index entry."""
    definition = route_definition(route, src)
    ph = store.params_hash(definition)
    d = search_dir(cfg, src.set_name) / route.dir_name
    check_write_location(cfg, d)
    path = d / f"{ph}.jsonl"
    _atomic_write(path, "".join(json.dumps(r) + "\n" for r in rows))
    cov = coverage(route, src)
    _atomic_write(d / f"{ph}.params.json", json.dumps(
        {"route": route.name, "params_hash": ph, "definition": definition, "coverage": cov,
         "n_queries": len(rows)}, indent=2, sort_keys=True) + "\n")
    relevant = {q["query_id"]: q["relevant"] for q in src.queries}
    diag = d / f"{ph}.diag.jsonl"
    _atomic_write(diag, "".join(json.dumps(query_diagnostics(r["query_id"], route.name, r["ranking"], r["scores"],
                                                             relevant[r["query_id"]])) + "\n" for r in rows))
    return {"params_hash": ph, "path": str(path), "diagnostics": str(diag), "queries_sha256": src.queries_sha256,
            "corpus_sha256": src.corpus_sha256, "corpus": src.corpus_info, "n_queries": len(rows), "coverage": cov,
            "kind": route.kind, "candidate": route.cand, "dim": definition.get("dim"), "w": route.w}


def update_index(cfg: Config, set_name: str, entries: dict[str, dict]) -> Path:
    """`results/search/<set>/index.json`: the latest rankings per route (read by `gen-search`)."""
    path = search_dir(cfg, set_name) / "index.json"
    check_write_location(cfg, path)
    doc = json.loads(path.read_text()) if path.exists() else {"set": set_name, "routes": {}}
    doc["routes"].update(entries)
    _atomic_write(path, json.dumps(doc, indent=2, sort_keys=True) + "\n")
    return path


# --- loading sources from the stores ------------------------------------------------------------

def _vectors(records: dict[str, dict], key: str) -> dict[str, np.ndarray]:
    from shoebox import embed

    out = {}
    for aid, r in records.items():
        block = (r.get("output") or {}).get(key) if r.get("status") == "ok" else None
        if block and block.get("vector_b64"):
            out[aid] = embed.normalise(embed.decode_vector(block["vector_b64"]))
    return out


def _label_docs(cfg: Config, source: dict) -> dict[str, dict]:
    out = {}
    for aid, r in store.index(cfg, source["model_id"], source["params_hash"]).items():
        o = (r.get("output") or {}) if r.get("status") == "ok" else {}
        cap = o.get("caption")
        if isinstance(cap, str) and cap.strip():
            tags = [t for t in o.get("tags") or [] if isinstance(t, str)]
            out[aid] = {"caption": cap.strip(), "tags": tags}
    return out


def load_corpus(set_dir: Path, queries: list[dict]) -> tuple[list[str], dict | None]:
    """The set's distinct asset_ids minus those in the optional `exclude.jsonl` (one
    `{asset_id, reason, decided_by, ts}` per line), and `{file, n, sha256}` of that file (None without one).
    Refuses to exclude a query's relevant item: exclusions remove distractors, never targets."""
    set_dir = Path(set_dir)
    corpus = sorted({it["asset_id"] for it in read_jsonl(set_dir / "items.jsonl")})
    path = set_dir / EXCLUDE_FILE
    if not path.exists():
        return corpus, None
    rows = read_jsonl(path)
    bad = [i for i, r in enumerate(rows, 1) if not isinstance(r.get("asset_id"), str) or not r["asset_id"]]
    if bad:
        raise ValueError(f"{path}: lines {bad} have no asset_id")
    excluded = {r["asset_id"] for r in rows}
    relevant = set().union(*(q["relevant"] for q in queries)) if queries else set()
    hit = sorted(excluded & relevant)
    if hit:
        raise ValueError(f"{path} excludes {len(hit)} relevant item(s) of the queries ({', '.join(hit[:3])}): "
                         "exclusions may only remove distractors")
    kept = [a for a in corpus if a not in excluded]
    return kept, {"file": EXCLUDE_FILE, "n": len(corpus) - len(kept), "sha256": file_sha256(path)}


def corpus_info(corpus: list[str], queries: list[dict], exclude: dict | None) -> dict:
    """Corpus size, targets (items relevant to some query), distractors (the rest) and excluded count."""
    relevant = set().union(*(q["relevant"] for q in queries)) if queries else set()
    targets = sum(a in relevant for a in corpus)
    return {"n": len(corpus), "targets": targets, "distractors": len(corpus) - targets,
            "excluded": exclude["n"] if exclude else 0}


def load_image_store(cfg: Config, embed_candidate: str = EMBED_CANDIDATE):
    """(resolved image-only EG2 candidate, {asset_id: 768-d unit vector}) from the embedding store."""
    from shoebox.commands.embed import resolve as resolve_embed

    res, _ = resolve_embed(embed_candidate, None)
    image = _vectors(store.index(cfg, res.model_id, res.params_hash, res.kind), "image")
    if not image:
        raise RuntimeError(f"no image embeddings in {res.store_dir(cfg)}: run `shoebox embed run`")
    return res, image


def load_sources(cfg: Config, set_name: str, routes: list[Route], sets_dir: Path = REPO_ROOT / "evals" / "sets",
                 embed_candidate: str = EMBED_CANDIDATE, log=lambda m: print(m, file=sys.stderr)) -> Sources:
    """Resolve the EG2 and label candidates the routes need (local Ollama only) and load their stores."""
    from shoebox import embed
    from shoebox.commands.embed import resolve as resolve_embed
    from shoebox.runners import resolve as resolve_label

    set_dir = Path(sets_dir) / set_name
    qpath = set_dir / QUERIES_FILE
    if not qpath.exists():
        raise FileNotFoundError(f"{qpath} missing: run `shoebox eval search-queries --set {set_name}`")
    queries = read_jsonl(qpath)
    corpus, exclude = load_corpus(set_dir, queries)
    keep = set(corpus)
    res, image = load_image_store(cfg, embed_candidate)
    # Only the corpus: the stores also hold other sets' items, which must not score (or sway FTS statistics).
    image = {a: v for a, v in image.items() if a in keep}

    def embed_query(text: str) -> np.ndarray:
        return embed.embed_query(res.cand, text, embed.NATIVE_DIM)

    src = Sources(set_name, corpus, queries, file_sha256(qpath), embed_query,
                  {"candidate": embed_candidate, "model_id": res.model_id, "params_hash": res.params_hash,
                   "query_prompt": res.cand.get("query_prompt")}, image, exclude=exclude)
    for cand in sorted({r.cand for r in routes if r.cand}):
        lres = resolve_label(cand)
        source = {"candidate": cand, "model_id": lres.model_id, "params_hash": lres.params_hash}
        src.label_source[cand] = source
        src.labels[cand] = {a: d for a, d in _label_docs(cfg, source).items() if a in keep}
        cres, _ = resolve_embed(embed_candidate, cand)
        vecs = _vectors(store.index(cfg, cres.model_id, cres.params_hash, cres.kind), "caption")
        vecs = {a: v for a, v in vecs.items() if a in keep}
        if vecs:
            src.caption_vecs[cand] = vecs
            src.caption_store[cand] = {"model_id": cres.model_id, "params_hash": cres.params_hash}
        else:
            log(f"{cand}: no caption vectors in {cres.store_dir(cfg)}")
    return src


@dataclass
class RunResult:
    written: dict[str, dict] = field(default_factory=dict)  # route -> index entry
    skipped: dict[str, str] = field(default_factory=dict)  # route -> reason
    index_path: Path | None = None


def run_routes(cfg: Config, src: Sources, routes: list[Route]) -> RunResult:
    out = RunResult()
    for route in routes:
        why = src.missing(route)
        if why:
            out.skipped[route.name] = why
            continue
        out.written[route.name] = write_route(cfg, route, src, run_route(route, src))
    if out.written:
        out.index_path = update_index(cfg, src.set_name, out.written)
    return out


# --- Vally spec -------------------------------------------------------------------------------

def _y(value) -> str:
    return json.dumps(value, ensure_ascii=True)


def build_spec(eval_name: str, set_name: str, queries: list[dict], routes_file: Path,
               ks: tuple[int, ...] = K_VALUES) -> str:
    desc = (f"Shoebox known-item search over set {set_name}: {len(queries)} queries ({QUERY_SOURCE}); "
            f"replay of stored rankings; pass = hit@{PASS_K} under every tie order (the metrics are in "
            "`shoebox eval search-report`)")
    lines = [
        HEADER,
        f"name: {_y(eval_name)}",
        f"description: {_y(desc)}",
        f"version: {_y(eval_name)}",
        "type: capability",
        "defaults:",
        "  runs: 1",
        f"  timeout: {_y('2m')}",
        "  executor:",
        "    name: shoebox-search",
        "    config:",
        f"      routes_file: {_y(str(routes_file))}",
        "scoring:",
        "  threshold: 1.0",
        "stimuli:",
    ]
    for q in queries:
        tags = {"source": q.get("source") or QUERY_SOURCE, "relevant_n": str(len(q["relevant"])),
                "targets_n": str(len(q.get("relevant_groups") or q["relevant"]))}
        config = {"relevant": q["relevant"], "k": list(ks)}
        if q.get("relevant_groups"):
            config["groups"] = q["relevant_groups"]
        lines += [
            f"  - name: {_y(q['query_id'])}",
            f"    prompt: {_y(q['text'])}",
            f"    tags: {_y(tags)}",
            "    graders:",
            "      - type: \"retrieval\"",
            f"        config: {_y(config)}",
        ]
    return "\n".join(lines) + "\n"


@dataclass
class GenSearchResult:
    spec_path: Path
    routes_path: Path
    stimuli: int
    routes: list[str]


def generate_spec(cfg: Config, set_name: str, route_names: list[str],
                  sets_dir: Path = REPO_ROOT / "evals" / "sets", out_dir: Path | None = None,
                  index_path: Path | None = None) -> GenSearchResult:
    """Writes `eval.yaml` and `routes.json` from the set's queries and the rankings `search-run` indexed.
    Refuses a route with no rankings, or whose rankings were made from other queries."""
    eval_name = f"{set_name}-search"
    out_dir = Path(out_dir) if out_dir else REPO_ROOT / "evals" / "vally" / "generated" / eval_name
    qpath = Path(sets_dir) / set_name / QUERIES_FILE
    queries = read_jsonl(qpath)
    qsha = file_sha256(qpath)
    corpus_doc = None
    if (Path(sets_dir) / set_name / "items.jsonl").exists():
        corpus, exclude = load_corpus(Path(sets_dir) / set_name, queries)
        corpus_doc = corpus_info(corpus, queries, exclude)
    index_path = Path(index_path) if index_path else search_dir(cfg, set_name) / "index.json"
    indexed = json.loads(index_path.read_text())["routes"] if index_path.exists() else {}
    routes, problems = {}, []
    for name in route_names:
        parse_route(name)
        e = indexed.get(name)
        if not e:
            problems.append(f"{name}: no rankings in {index_path} (run `shoebox eval search-run`)")
        elif e.get("queries_sha256") != qsha:
            problems.append(f"{name}: rankings were made from other queries (re-run `shoebox eval search-run`)")
        elif not Path(e["path"]).exists():
            problems.append(f"{name}: {e['path']} is missing")
        else:
            routes[name] = e
    if problems:
        raise ValueError("; ".join(problems))
    out_dir.mkdir(parents=True, exist_ok=True)
    routes_path = out_dir / "routes.json"
    doc = {"_comment": "generated — do not edit. Regenerate with `shoebox eval gen-search`.",
           "kind": "search", "set": set_name, "eval": eval_name, "queries_file": str(qpath.resolve()),
           "queries_sha256": qsha, "query_ids": [q["query_id"] for q in queries], "k": list(K_VALUES),
           "pass_k": PASS_K, "corpus": corpus_doc, "routes": routes}
    routes_path.write_text(json.dumps(doc, indent=2) + "\n")
    spec_path = out_dir / "eval.yaml"
    spec_path.write_text(build_spec(eval_name, set_name, queries, routes_path.resolve()))
    return GenSearchResult(spec_path, routes_path, len(queries), list(routes))


# --- metrics ------------------------------------------------------------------------------------

def tie_blocks(ranking: list[str], scores: list[float | None] | None) -> list[tuple[int, int]]:
    """(start, size) of the tie block of each position. A block is a run of equal scores (exact float
    equality; the stored ranking is score-sorted, so equal scores are adjacent), and the unscored tail
    (score None) is one block. With no scores every position is its own block (the stored order counts)."""
    n = len(ranking)
    if scores is None or len(scores) != n:
        return [(i, 1) for i in range(n)]
    out: list[tuple[int, int]] = []
    i = 0
    while i < n:
        j = i + 1
        while j < n and scores[j] == scores[i]:
            j += 1
        out += [(i, j - i)] * (j - i)
        i = j
    return out


def first_rank_dist(ranking: list[str], blocks: list[tuple[int, int]], target: set[str]) -> list[tuple[int, float]]:
    """Distribution of the 1-based rank of the first member of `target`, over uniformly random orders
    within tie blocks: [(rank, probability)]. Empty if no member is ranked. With r members among the n
    items of the first block holding one, starting at lo: P(offset j) = C(n-1-j, r-1) / C(n, r),
    computed as P(0) = r/n, P(j+1) = P(j) * (n-r-j) / (n-1-j) (the JS grader does the same)."""
    pos = next((i for i, a in enumerate(ranking) if a in target), None)
    if pos is None:
        return []
    lo, n = blocks[pos]
    r = sum(a in target for a in ranking[lo:lo + n])
    out, p = [], r / n
    for j in range(n - r + 1):
        out.append((lo + 1 + j, p))
        if j < n - r:
            p = p * (n - r - j) / (n - 1 - j)
    return out


def query_metrics(ranking: list[str], relevant: Iterable[str], ks: Iterable[int] = K_VALUES,
                  scores: list[float | None] | None = None, groups: list[list[str]] | None = None) -> dict:
    """Known-item metrics for one ranked list (binary relevance). Mirrors the `retrieval` Vally grader.

    Ties: every metric is its expectation over uniformly random orders within tie blocks of equal score
    (`tie_blocks`), so the stored asset_id tie-break decides nothing. rank_best / rank_worst bound the rank
    of the first relevant item over tie orders; tie_decided says they differ. rank_of_first_relevant is the
    rank in the stored order (for tracing only).

    Targets: `groups` partitions `relevant` into targets (a capture group: a DNG+JPG pair is ONE target,
    found when its first member is found). Default: each relevant item is its own target. rr / hit@k /
    rank use the first relevant item of any target. nDCG@k: gain 1/log2(rank+1) per target at the rank of
    its first member, normalised by the ideal of min(#targets, k) hits at the top. recall@k = targets in
    the top k / #targets. judged@k = relevant items in the top k / min(k, n_ranked): the fraction of the
    top k that carries a relevance judgment (every other item is assumed non-relevant, unjudged)."""
    rel = set(relevant)
    targets = [set(g) for g in groups] if groups else [{a} for a in sorted(rel)]
    if groups and set().union(*targets) != rel:
        raise ValueError("groups must partition relevant")
    blocks = tie_blocks(ranking, scores)
    first = next((i + 1 for i, a in enumerate(ranking) if a in rel), None)
    dist = first_rank_dist(ranking, blocks, rel)
    tdist = [first_rank_dist(ranking, blocks, t) for t in targets]
    best = dist[0][0] if dist else None
    worst = dist[-1][0] if dist else None
    out: dict = {"rank_of_first_relevant": first, "rank_best": best, "rank_worst": worst,
                 "rank_expected": sum(r * p for r, p in dist) if dist else None,
                 "tie_decided": best != worst,
                 "rr": sum(p / r for r, p in dist), "rr_best": 1.0 / best if best else 0.0,
                 "rr_worst": 1.0 / worst if worst else 0.0,
                 "relevant_n": len(rel), "targets_n": len(targets), "n_ranked": len(ranking)}
    for k in sorted(set(ks) | {PASS_K}):
        idcg = sum(1.0 / math.log2(i + 2) for i in range(min(len(targets), k)))
        dcg = sum(p / math.log2(r + 1) for d in tdist for r, p in d if r <= k)
        out[f"hit@{k}"] = sum(p for r, p in dist if r <= k)
        out[f"recall@{k}"] = sum(p for d in tdist for r, p in d if r <= k) / len(targets) if targets else 0.0
        out[f"ndcg@{k}"] = dcg / idcg if idcg else 0.0
        top_n = min(k, len(ranking))
        in_top = 0.0
        for i, a in enumerate(ranking):
            if a in rel:
                lo, n = blocks[i]
                in_top += min(1.0, max(0.0, (k - lo) / n))
        out[f"judged@{k}"] = in_top / top_n if top_n else 0.0
    return out


def bootstrap_ci(values: list[float], resamples: int = BOOTSTRAP_RESAMPLES, seed: int = BOOTSTRAP_SEED,
                 level: float = 0.95) -> tuple[float, float] | None:
    """Percentile bootstrap CI of the mean, resampling queries with replacement (fixed seed)."""
    if not values:
        return None
    v = np.asarray(values, dtype=np.float64)
    idx = np.random.default_rng(seed).integers(0, len(v), size=(resamples, len(v)))
    means = v[idx].mean(axis=1)
    lo, hi = np.percentile(means, [(1 - level) / 2 * 100, (1 + level) / 2 * 100])
    return float(lo), float(hi)


def sign_test(wins: int, losses: int) -> float:
    """Two-sided exact sign test (binomial, p = 0.5) over the non-tied pairs."""
    n = wins + losses
    if n == 0:
        return 1.0
    tail = sum(math.comb(n, i) for i in range(min(wins, losses) + 1)) / 2 ** n
    return min(1.0, 2 * tail)


def paired(a: dict[str, float], b: dict[str, float]) -> dict:
    """Route a vs route b on reciprocal rank, over the queries both have."""
    common = sorted(a.keys() & b.keys())
    wins = sum(a[q] > b[q] for q in common)
    losses = sum(a[q] < b[q] for q in common)
    return {"n": len(common), "wins": wins, "losses": losses, "ties": len(common) - wins - losses,
            "mean_rr_delta": (sum(a[q] - b[q] for q in common) / len(common)) if common else None,
            "sign_test_p": sign_test(wins, losses)}


# --- report -------------------------------------------------------------------------------------

_KV = re.compile(r"(route|query_id|params_hash|set|routes_file)=([^,\s)\]]+)")


@dataclass
class SearchTrial:
    route: str
    query_id: str
    status: str  # ok | missing | exec_error | unparsed
    ranking: list[str] | None = None
    scores: list[float | None] | None = None  # aligned with ranking; None when the output had none
    event: dict = field(default_factory=dict)
    grader: dict | None = None  # the retrieval grader's detail
    error: str | None = None


def _event(traj: dict) -> dict:
    for ev in traj.get("events") or []:
        sb = (ev.get("data") or {}).get("shoebox") if ev.get("type") == "custom" else None
        if isinstance(sb, dict):
            return sb
    return {}


def parse_trial(r: dict) -> SearchTrial | None:
    if r.get("type") not in (None, "trial-result") or ("trajectory" not in r and "status" not in r):
        return None
    traj = r.get("trajectory") or None
    if traj is None:
        err = str(r.get("error") or "")
        kv = dict(_KV.findall(err))
        return SearchTrial(r.get("model") or kv.get("route") or "?", r.get("stimulus") or kv.get("query_id") or "?",
                           "missing" if "missing key" in err else "exec_error", error=err,
                           event={k: v for k, v in kv.items() if k in ("set", "routes_file", "params_hash")})
    ev = _event(traj)
    route = r.get("model") or (traj.get("metadata") or {}).get("model") or ev.get("route") or "?"
    qid = r.get("stimulus") or (traj.get("stimulus") or {}).get("name") or ev.get("query_id") or "?"
    ranking = scores = None
    try:
        out = json.loads(traj.get("output") or "")
        if isinstance(out, dict) and isinstance(out.get("ranking"), list):
            ranking = [str(a) for a in out["ranking"]]
            sc = out.get("scores")
            if isinstance(sc, list) and len(sc) == len(ranking):
                scores = [None if v is None else float(v) for v in sc]
    except (json.JSONDecodeError, TypeError, ValueError):
        pass
    grader = next((d for d in (r.get("gradeResult") or {}).get("details") or []
                   if d.get("graderType", d.get("name")) == "retrieval"), None)
    return SearchTrial(route, qid, "ok" if ranking is not None else "unparsed", ranking, scores, ev, grader)


def load_trials(path: Path) -> list[SearchTrial]:
    out = []
    for line in Path(path).read_text().splitlines():
        if line.strip():
            t = parse_trial(json.loads(line))
            if t is not None:
                out.append(t)
    return out


def route_summary(per_query: dict[str, dict], corpus_n: int | None) -> dict:
    """Aggregate per-query metrics (each already a tie-expectation). A query with no relevant item ranked
    counts rank corpus_n + 1. mrr_best / mrr_worst: MRR if every tie broke for / against the relevant item."""
    n = len(per_query)
    if not n:
        return {"n_queries": 0}
    ms = list(per_query.values())
    worst = (corpus_n or max(m["n_ranked"] for m in ms)) + 1
    ranks = [m["rank_expected"] if m["rank_expected"] is not None else worst for m in ms]
    rr = [m["rr"] for m in ms]
    ci = bootstrap_ci(rr)

    def mean(key):
        return sum(float(m[key]) for m in ms) / n

    return {"n_queries": n, "mrr": sum(rr) / n, "mrr_ci95": list(ci) if ci else None,
            "mrr_best": mean("rr_best"), "mrr_worst": mean("rr_worst"),
            "tie_decided_queries": sum(bool(m["tie_decided"]) for m in ms),
            **{f"hit@{k}": mean(f"hit@{k}") for k in K_VALUES},
            **{f"ndcg@{k}": mean(f"ndcg@{k}") for k in K_VALUES},
            **{f"recall@{k}": mean(f"recall@{k}") for k in K_VALUES},
            "pool_coverage": {f"judged@{k}": mean(f"judged@{k}") for k in K_VALUES},
            "mean_rank": sum(ranks) / n, "median_rank": statistics.median(ranks)}


PER_QUERY_KEYS = ("rank_expected", "rank_best", "rank_worst", "tie_decided", "rank_of_first_relevant", "rr",
                  *(f"{m}@{k}" for m in ("hit", "recall", "ndcg") for k in K_VALUES))


def build_report(trials: list[SearchTrial], results_path: Path, run_date: str,
                 routes_file: Path | None = None) -> dict:
    blockers: list[str] = []
    files = {t.event["routes_file"] for t in trials if t.event.get("routes_file")}
    if routes_file is None and len(files) == 1:
        routes_file = Path(next(iter(files)))
    elif routes_file is None and len(files) > 1:
        raise ValueError(f"results name more than one routes.json: {sorted(files)}")
    if routes_file is None or not Path(routes_file).exists():
        raise ValueError(f"routes.json not found ({routes_file}); pass --routes-file")
    rdoc = json.loads(Path(routes_file).read_text())
    set_name = rdoc["set"]
    sets = {t.event["set"] for t in trials if t.event.get("set")}
    if sets - {set_name}:
        raise ValueError(f"trials name set(s) {sorted(sets)} but routes.json is for {set_name}")
    qpath = Path(rdoc["queries_file"])
    queries = {q["query_id"]: q for q in read_jsonl(qpath)} if qpath.exists() else {}
    if not queries:
        raise ValueError(f"queries file {qpath} not found")
    if file_sha256(qpath) != rdoc.get("queries_sha256"):
        blockers.append("the queries file changed since `gen-search`: regenerate and re-run")
    shas = {t.event.get("queries_sha256") for t in trials if t.event.get("queries_sha256")}
    if shas and shas != {rdoc.get("queries_sha256")}:
        blockers.append("trials were run against a routes.json made from other queries")

    by_route: dict[str, list[SearchTrial]] = {}
    for t in trials:
        by_route.setdefault(t.route, []).append(t)
    qids = list(rdoc.get("query_ids") or queries)
    routes_out: dict[str, dict] = {}
    rr_by_route: dict[str, dict[str, float]] = {}
    for name in sorted(by_route):
        ts = by_route[name]
        meta = (rdoc.get("routes") or {}).get(name) or {}
        per_query: dict[str, dict] = {}
        above: dict[str, list[str]] = {}
        mismatches = 0
        status: dict[str, int] = {}
        for t in ts:
            status[t.status] = status.get(t.status, 0) + 1
            if t.status != "ok" or t.query_id not in queries:
                continue
            q = queries[t.query_id]
            m = query_metrics(t.ranking, q["relevant"], scores=t.scores, groups=q.get("relevant_groups"))
            gm = (t.grader or {}).get("metadata") or {}
            if t.grader is not None and (gm.get("rank_of_first_relevant") != m["rank_of_first_relevant"]
                                         or any(abs(float(gm.get(k, -1)) - m[k]) > 1e-9
                                                for k in ("rr", f"hit@{PASS_K}", "ndcg@10"))):
                mismatches += 1
            if t.query_id not in per_query:
                rel = set(q["relevant"])
                first = next((i for i, a in enumerate(t.ranking) if a in rel), len(t.ranking))
                above[t.query_id] = t.ranking[:min(first, DIAG_TOP)]
            per_query.setdefault(t.query_id, m)
        not_run = [q for q in qids if q not in per_query]
        if not_run:
            blockers.append(f"{name}: no usable trial for {len(not_run)} of {len(qids)} queries")
        if mismatches:
            blockers.append(f"{name}: {mismatches} trials where the Vally grader disagrees with the Python metrics")
        if meta.get("params_hash") and any(t.event.get("params_hash") not in (None, meta["params_hash"])
                                            for t in ts):
            blockers.append(f"{name}: trials replayed rankings with another params_hash than routes.json")
        try:
            r = parse_route(name)
            kind = {"kind": r.kind, "candidate": r.cand, "dim": r.dim if r.kind == "eg2-image" else 768,
                    "w": r.w, "caption_based": r.caption_based, "sensitivity": r.sensitivity}
        except ValueError:
            kind = {"kind": "unknown", "caption_based": False, "sensitivity": False}
        cov = meta.get("coverage") or {}
        routes_out[name] = {**kind, "params_hash": meta.get("params_hash"), "status_counts": status,
                            "coverage": cov, **route_summary(per_query, cov.get("corpus_n")),
                            "not_run": not_run,
                            "per_query": {q: {**{k: m[k] for k in PER_QUERY_KEYS}, "above_target": above[q]}
                                          for q, m in sorted(per_query.items())}}
        rr_by_route[name] = {q: m["rr"] for q, m in per_query.items()}

    comparisons: dict[str, dict] = {}
    best = None
    if BASELINE_ROUTE in rr_by_route:
        base = rr_by_route[BASELINE_ROUTE]
        for name, rr in rr_by_route.items():
            if name != BASELINE_ROUTE:
                comparisons[name] = paired(rr, base)
        contenders = [n for n, r in routes_out.items() if r["caption_based"] and not r["sensitivity"]
                      and r.get("n_queries")]
        if contenders:  # highest MRR; a tie goes to the first route name
            best = min(contenders, key=lambda n: (-routes_out[n]["mrr"], n))

    cands = sorted({r["candidate"] for r in routes_out.values() if r.get("candidate")})
    per_cand = {}
    for c in cands:
        row = {}
        for kind, key in (("eg2-caption", "caption"), ("fts", "fts"), ("eg2-blend", "blend"), ("hybrid", "hybrid")):
            rr = routes_out.get(f"{kind}:{c}")
            row[key] = {"mrr": rr["mrr"], "hit@5": rr["hit@5"]} if rr and rr.get("n_queries") else None
        got = [v["mrr"] for v in row.values() if v]
        row["mean_mrr"] = sum(got) / len(got) if len(got) == 4 else None
        cov = next((routes_out[n]["coverage"] for n in (f"fts:{c}", f"eg2-blend:{c}") if n in routes_out), {})
        row["missing_captions"] = cov.get("missing_captions")
        row["missing_caption_vectors"] = cov.get("missing_caption_vectors")
        sens = {}
        for w in (BLEND_SENSITIVITY[0], BLEND_W, BLEND_SENSITIVITY[1]):
            n = f"eg2-blend:{c}" if w == BLEND_W else f"eg2-blend@w{_w_str(w)}:{c}"
            sens[_w_str(w)] = routes_out[n]["mrr"] if n in routes_out and routes_out[n].get("n_queries") else None
        row["blend_sensitivity_mrr"] = sens
        per_cand[c] = row

    return {
        "set": set_name, "eval": rdoc.get("eval"), "date": run_date, "results": str(results_path),
        "routes_file": str(routes_file), "queries_sha256": rdoc.get("queries_sha256"), "n_queries": len(qids),
        "corpus": rdoc.get("corpus"),
        "query_source": QUERY_SOURCE, "complete": not blockers, "blockers": blockers,
        "baseline": BASELINE_ROUTE, "best_caption_route": best,
        "best_vs_baseline": comparisons.get(best) if best else None,
        "routes": routes_out, "vs_baseline": comparisons, "candidates": per_cand,
        "settings": {"blend_w": BLEND_W, "blend_sensitivity": list(BLEND_SENSITIVITY), "rrf_k": RRF_K,
                     "fts_tokenizer": FTS_TOKENIZER, "k": list(K_VALUES), "pass_k": PASS_K,
                     "bootstrap": {"resamples": BOOTSTRAP_RESAMPLES, "seed": BOOTSTRAP_SEED, "level": 0.95}},
        "notes": [
            (
                "Known-item search: each query is an owner note; relevant = the noted item(s) and capture-group "
                "siblings, one target per capture group (a DNG+JPG pair is one target). Only those items were "
                "judged for the query: every other item is ASSUMED non-relevant without a judgment, so a photo "
                "that also fits a generic note counts as a miss. Pool coverage (judged@k: the judged fraction of "
                "the top k) is therefore about 1/k; the metrics are known-item metrics, not judged-pool metrics."
            ),
            (
                "Ties: every metric is its expectation over random orders within blocks of exactly equal score "
                "(the unscored tail is one block), so the stored asset_id tie-break decides nothing. "
                "mrr_best/mrr_worst bound MRR over tie orders; tie_decided_queries counts queries whose rank "
                "of the first relevant item depends on the tie order (common for hybrid RRF and FTS)."
            ),
            (
                f"Blend weight w={BLEND_W} is fixed, not tuned. The w=0.3/0.7 sensitivity table is NOT tuned on "
                "held-out data and must not be used to pick w from these queries."
            ),
            (
                "The best caption-based route is chosen on these same queries, so its sign-test p-value vs "
                f"{BASELINE_ROUTE} is optimistic (selection bias). n is small: treat all differences as indicative."
            ),
            f"MRR CI: percentile bootstrap over queries, {BOOTSTRAP_RESAMPLES} resamples, seed {BOOTSTRAP_SEED}.",
            "Items with no caption: blend uses their image score; caption/FTS routes rank them last.",
            (f"Vally's pass = hit@{PASS_K} under every tie order (score = expected hit@{PASS_K}); its overall "
            "score is not the metric."),
        ],
    }


def _f(v, digits: int = 3) -> str:
    if v is None:
        return "–"
    if isinstance(v, bool):
        return "yes" if v else "no"
    if isinstance(v, float):
        return f"{v:.{digits}f}"
    return str(v)


def to_markdown(rep: dict) -> str:
    out = [f"# Search eval {rep['date']} — {rep['set']} (known-item)", "",
           f"- Results: `{rep['results']}`", f"- Routes file: `{rep['routes_file']}`",
           f"- Queries: {rep['n_queries']} ({rep['query_source']}); complete: {_f(rep['complete'])}"]
    c = rep.get("corpus")
    if c:
        out.append(f"- Corpus: {c['n']} items ({c['targets']} targets, {c['distractors']} distractors); "
                   f"excluded: {c['excluded']}")
    out += [f"- Incomplete: {b}" for b in rep["blockers"]]
    out += ["", "## Routes", "",
            ("Metrics are expectations over random tie orders. MRR tie range = MRR if every tie broke against / "
            "for the relevant item; ties = queries whose first relevant rank depends on the tie order."), "",
            ("| route | n | MRR | MRR 95% CI | MRR tie range | ties | hit@1 | hit@5 | hit@10 | nDCG@10 | nDCG@20 "
            "| recall@20 | mean rank | median rank | params_hash |"),
            "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    order = sorted(rep["routes"], key=lambda n: (rep["routes"][n]["sensitivity"], -(rep["routes"][n].get("mrr") or 0), n))
    for n in order:
        r = rep["routes"][n]
        ci = r.get("mrr_ci95")
        label = f"{n} (sensitivity)" if r["sensitivity"] else n
        span = f"{r['mrr_worst']:.3f}–{r['mrr_best']:.3f}" if r.get("n_queries") else "–"
        out.append(f"| {label} | {r.get('n_queries', 0)} | {_f(r.get('mrr'))} | "
                   f"{f'{ci[0]:.3f}–{ci[1]:.3f}' if ci else '–'} | {span} | {_f(r.get('tie_decided_queries'))} | "
                   f"{_f(r.get('hit@1'))} | {_f(r.get('hit@5'))} | {_f(r.get('hit@10'))} | {_f(r.get('ndcg@10'))} | "
                   f"{_f(r.get('ndcg@20'))} | {_f(r.get('recall@20'))} | {_f(r.get('mean_rank'), 1)} | "
                   f"{_f(r.get('median_rank'), 1)} | `{r.get('params_hash')}` |")
    out += ["", "## Pool coverage", "",
            ("judged@k = the fraction of each route's top k that carries a relevance judgment (only the noted "
            "item(s) were judged; everything else is assumed non-relevant). Mean over queries."), "",
            "| route | " + " | ".join(f"judged@{k}" for k in K_VALUES) + " |",
            "|---|" + "---|" * len(K_VALUES)]
    for n in order:
        pc = rep["routes"][n].get("pool_coverage") or {}
        out.append(f"| {n} | " + " | ".join(_f(pc.get(f"judged@{k}")) for k in K_VALUES) + " |")
    best, cmp = rep["best_caption_route"], rep["best_vs_baseline"]
    out += ["", f"## Best caption-based route vs {rep['baseline']}", ""]
    if best and cmp:
        out.append(f"`{best}` vs `{rep['baseline']}` on reciprocal rank over {cmp['n']} queries: "
                   f"{cmp['wins']} wins, {cmp['losses']} losses, {cmp['ties']} ties; mean RR delta "
                   f"{_f(cmp['mean_rr_delta'])}; two-sided sign test p = {_f(cmp['sign_test_p'], 4)}. "
                   "Chosen on the same queries: the p-value is optimistic.")
    else:
        out.append("Not available (baseline or caption-based routes missing).")
    if rep["vs_baseline"]:
        out += ["", f"All routes vs {rep['baseline']} (reciprocal rank, paired):", "",
                "| route | wins | losses | ties | mean RR delta | sign-test p |", "|---|---|---|---|---|---|"]
        for n in order:
            c = rep["vs_baseline"].get(n)
            if c:
                out.append(f"| {n} | {c['wins']} | {c['losses']} | {c['ties']} | {_f(c['mean_rr_delta'])} | "
                           f"{_f(c['sign_test_p'], 4)} |")
    out += ["", "## Per labeller: whose captions make photos easiest to find", "",
            "MRR per route family (hit@5 in brackets); mean over the four when all four ran.", "",
            "| labeller | caption | fts | blend (w=0.5) | hybrid | mean MRR | items w/o caption | w/o caption vector |",
            "|---|---|---|---|---|---|---|---|"]

    def cell(v):
        return f"{v['mrr']:.3f} ({v['hit@5']:.2f})" if v else "–"

    cands = sorted(rep["candidates"], key=lambda c: -(rep["candidates"][c]["mean_mrr"] or -1))
    for c in cands:
        r = rep["candidates"][c]
        out.append(f"| {c} | {cell(r['caption'])} | {cell(r['fts'])} | {cell(r['blend'])} | {cell(r['hybrid'])} | "
                   f"{_f(r['mean_mrr'])} | {_f(r['missing_captions'])} | {_f(r['missing_caption_vectors'])} |")
    out += ["", "## Blend weight sensitivity (MRR) — not tuned on held-out data", "",
            "Shown only to see how much w matters. w=0.5 is the fixed primary; do not pick w from this table.", "",
            "| labeller | w=0.3 | w=0.5 | w=0.7 |", "|---|---|---|---|"]
    for c in cands:
        s = rep["candidates"][c]["blend_sensitivity_mrr"]
        out.append(f"| {c} | {_f(s.get('0.3'))} | {_f(s.get('0.5'))} | {_f(s.get('0.7'))} |")
    st = rep["settings"]
    out += ["", "## Settings", "",
            f"- Blend w = {st['blend_w']}; RRF k = {st['rrf_k']}; FTS5 tokenizer `{st['fts_tokenizer']}`.",
            f"- Bootstrap: {st['bootstrap']['resamples']} resamples, seed {st['bootstrap']['seed']}.",
            "", "## Notes", ""] + [f"- {n}" for n in rep["notes"]]
    return "\n".join(out) + "\n"


def write_report(results_path: Path, out_dir: Path, run_date: str | None = None,
                 routes_file: Path | None = None) -> tuple[Path, Path, dict]:
    run_date = run_date or Date.today().isoformat()
    rep = build_report(load_trials(results_path), Path(results_path), run_date, routes_file)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = f"{run_date}_{rep['set']}-search"
    jpath, mpath = out_dir / f"{stem}.json", out_dir / f"{stem}.md"
    jpath.write_text(json.dumps(rep, indent=2) + "\n")
    mpath.write_text(to_markdown(rep))
    return jpath, mpath, rep
