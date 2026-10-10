// Vally grader plugin: per-trial graders for Shoebox label-v1 outputs and search rankings.
//
//   label-status  pass iff the stored record's status is "ok" (invalid and error count as wrong).
//   field-match   {field, expected?}: one enum field vs the owner label. Predicted "unknown" = abstain
//                 (recorded, fails); expected "unknown" (owner could not tell) = unlabelled, owner_unknown=true.
//   set-match     {field="tags", expected?}: tag set vs the owner's tags; TP/FP/FN in metadata, score = F1.
//   retrieval     {relevant, groups?, k?}: a known-item search query (executor shoebox-search); rank, rr,
//                 hit@k, recall@k, nDCG@k, judged@k in metadata (tie-aware); pass iff hit@5 under every tie order.
//
// Vally 0.18.0 has no skip/not-applicable grader verdict (GraderResult is passed/score/status only, and
// status "error" means the grader broke). So a grader whose expected value is absent PASSES with
// metadata.unlabelled = true; `shoebox eval report` excludes those trials from accuracy. Vally's own
// overall score therefore over-counts on unlabelled items: use the Python report, not Vally's score.

const META = {
  behavior: {},
  determinism: "static",
  reference: "reference-based",
  temporalScope: "point-in-time",
  costProfile: "free",
};

export function shoeboxEvent(trajectory) {
  for (const ev of trajectory?.events ?? []) {
    if (ev.type === "custom" && ev.data && typeof ev.data.shoebox === "object") return ev.data.shoebox;
  }
  return null;
}

// -> {status, label} where label is the parsed label-v1 object for status ok, else null.
export function parseTrial(trajectory) {
  const ev = shoeboxEvent(trajectory);
  let status = ev?.status ?? null;
  let label = null;
  if (status === "ok" || status === null) {
    try {
      const parsed = JSON.parse(trajectory?.output ?? "");
      if (parsed && typeof parsed === "object" && !Array.isArray(parsed)) label = parsed;
    } catch {
      // not JSON
    }
    if (status === null) status = label ? "ok" : "unparsed";
  }
  return { status, label };
}

function isLabelled(expected) {
  return expected !== undefined && expected !== null && expected !== "";
}

const labelStatus = {
  metadata: { ...META, name: "label-status", description: "Passes iff the label record's status is ok" },
  async grade({ trajectory }) {
    const { status } = parseTrial(trajectory);
    const passed = status === "ok";
    return {
      name: "label-status",
      kind: "code",
      passed,
      score: passed ? 1 : 0,
      evidence: `status=${status}`,
      metadata: { status },
    };
  },
};

const fieldMatch = {
  metadata: { ...META, name: "field-match", description: "Compares one label field with the owner label" },
  defaultName(config) {
    return `field-match ${config?.field ?? ""}`.trim();
  },
  async grade({ trajectory, config }) {
    const field = config?.field;
    if (typeof field !== "string" || !field) throw new Error("field-match: config.field is required");
    const expected = config?.expected;
    const { status, label } = parseTrial(trajectory);
    const raw = label ? label[field] : undefined;
    const predicted = raw === undefined ? null : raw;
    const abstained = predicted === "unknown";
    const name = `field-match ${field}`;
    const base = { field, status, predicted, abstained };
    const ownerUnknown = expected === "unknown"; // the owner could not tell: nothing to score against
    if (!isLabelled(expected) || ownerUnknown) {
      return {
        name,
        kind: "code",
        passed: true,
        score: 1,
        evidence: `${field}: predicted=${predicted} (${ownerUnknown ? "owner unknown" : "unlabelled"}, not scored)`,
        metadata: { ...base, expected: null, unlabelled: true, ...(ownerUnknown ? { owner_unknown: true } : {}) },
      };
    }
    const passed = status === "ok" && predicted === expected;
    return {
      name,
      kind: "code",
      passed,
      score: passed ? 1 : 0,
      evidence: `${field}: predicted=${abstained ? "abstain" : predicted} expected=${expected} status=${status}`,
      metadata: { ...base, expected, unlabelled: false },
    };
  },
};

function tagSet(values) {
  if (!Array.isArray(values)) return [];
  return [...new Set(values.filter((v) => typeof v === "string").map((v) => v.trim().toLowerCase()))].sort();
}

const setMatch = {
  metadata: { ...META, name: "set-match", description: "Compares a tag set with the owner's tags" },
  defaultName(config) {
    return `set-match ${config?.field ?? "tags"}`;
  },
  async grade({ trajectory, config }) {
    const field = config?.field ?? "tags";
    const { status, label } = parseTrial(trajectory);
    const predicted = tagSet(label ? label[field] : []);
    const name = `set-match ${field}`;
    const expectedRaw = config?.expected;
    if (!Array.isArray(expectedRaw) || expectedRaw.length === 0) {
      return {
        name,
        kind: "code",
        passed: true,
        score: 1,
        evidence: `${field}: ${predicted.length} predicted (unlabelled, not scored)`,
        metadata: { field, status, predicted, expected: null, unlabelled: true, tp: [], fp: [], fn: [] },
      };
    }
    const expected = tagSet(expectedRaw);
    const exp = new Set(expected);
    const pred = new Set(predicted);
    const tp = predicted.filter((t) => exp.has(t));
    const fp = predicted.filter((t) => !exp.has(t));
    const fn = expected.filter((t) => !pred.has(t));
    const ok = status === "ok";
    const f1 = ok && tp.length ? (2 * tp.length) / (2 * tp.length + fp.length + fn.length) : 0;
    const passed = ok && fp.length === 0 && fn.length === 0;
    return {
      name,
      kind: "code",
      passed,
      score: f1,
      evidence: `${field}: tp=${tp.length} fp=${fp.length} fn=${fn.length} status=${status}`,
      metadata: { field, status, predicted, expected, unlabelled: false, tp, fp, fn },
    };
  },
};

// retrieval {relevant: [asset_id...], groups?: [[asset_id...]...], k?: [1, 5, 10, 20]}: one known-item search
// query. The trajectory output is the stored ranking {query_id, route, ranking: [asset_id...], scores} (executor
// shoebox-search). Binary relevance; ranks are 1-based. Mirrors `shoebox.evals.search.query_metrics`:
// - Ties: items with exactly equal scores form a block (the unscored tail, score null, is one block); every
//   metric is its expectation over random orders within blocks, so the stored asset_id tie-break decides
//   nothing. rank_best/rank_worst bound the first relevant rank; tie_decided says they differ.
// - Targets: `groups` partitions `relevant` (one capture group = one target, found at its first member);
//   default one target per item. nDCG@k = sum of 1/log2(rank+1) per target / ideal of min(#targets, k)
//   hits; recall@k = targets in the top k / #targets; judged@k = relevant items in the top k / min(k, n).
// Pass iff hit@5 == 1 (top 5 under every tie order); score = expected hit@5. The real metrics (MRR, nDCG,
// CIs) are in `shoebox eval search-report`, which recomputes them and checks they agree with this metadata.
export const RETRIEVAL_PASS_K = 5;

export function tieBlocks(ranking, scores) {
  const n = ranking.length;
  if (!Array.isArray(scores) || scores.length !== n) return ranking.map((_, i) => [i, 1]);
  const out = new Array(n);
  let i = 0;
  while (i < n) {
    let j = i + 1;
    while (j < n && scores[j] === scores[i]) j++;
    for (let x = i; x < j; x++) out[x] = [i, j - i];
    i = j;
  }
  return out;
}

// [[rank, probability]...] of the first member of `target` over random tie orders; [] if none is ranked.
export function firstRankDist(ranking, blocks, target) {
  const pos = ranking.findIndex((a) => target.has(a));
  if (pos < 0) return [];
  const [lo, n] = blocks[pos];
  let r = 0;
  for (let i = lo; i < lo + n; i++) if (target.has(ranking[i])) r++;
  const out = [];
  let p = r / n;
  for (let j = 0; j <= n - r; j++) {
    out.push([lo + 1 + j, p]);
    if (j < n - r) p = (p * (n - r - j)) / (n - 1 - j);
  }
  return out;
}

export function retrievalMetrics(ranking, relevant, ks = [1, 5, 10, 20], scores = null, groups = null) {
  const rel = new Set(relevant);
  const targets = Array.isArray(groups) && groups.length ? groups.map((g) => new Set(g)) : [...rel].sort().map((a) => new Set([a]));
  if (Array.isArray(groups) && groups.length) {
    const union = new Set(groups.flat());
    if (union.size !== rel.size || [...union].some((a) => !rel.has(a))) throw new Error("retrieval: groups must partition relevant");
  }
  const blocks = tieBlocks(ranking, scores);
  const idx = ranking.findIndex((a) => rel.has(a));
  const first = idx >= 0 ? idx + 1 : null;
  const dist = firstRankDist(ranking, blocks, rel);
  const tdist = targets.map((t) => firstRankDist(ranking, blocks, t));
  const best = dist.length ? dist[0][0] : null;
  const worst = dist.length ? dist[dist.length - 1][0] : null;
  let rr = 0;
  let rankExp = 0;
  for (const [r, p] of dist) {
    rr += p / r;
    rankExp += r * p;
  }
  const out = {
    rank_of_first_relevant: first,
    rank_best: best,
    rank_worst: worst,
    rank_expected: dist.length ? rankExp : null,
    tie_decided: best !== worst,
    rr,
    rr_best: best ? 1 / best : 0,
    rr_worst: worst ? 1 / worst : 0,
    relevant_n: rel.size,
    targets_n: targets.length,
    n_ranked: ranking.length,
  };
  const all = [...new Set([...ks, RETRIEVAL_PASS_K])].sort((a, b) => a - b);
  for (const k of all) {
    let idcg = 0;
    for (let i = 0; i < Math.min(targets.length, k); i++) idcg += 1 / Math.log2(i + 2);
    let dcg = 0;
    let found = 0;
    for (const d of tdist) {
      for (const [r, p] of d) {
        if (r <= k) {
          dcg += p / Math.log2(r + 1);
          found += p;
        }
      }
    }
    let hit = 0;
    for (const [r, p] of dist) if (r <= k) hit += p;
    const topN = Math.min(k, ranking.length);
    let inTop = 0;
    ranking.forEach((a, i) => {
      if (rel.has(a)) {
        const [lo, n] = blocks[i];
        inTop += Math.min(1, Math.max(0, (k - lo) / n));
      }
    });
    out[`hit@${k}`] = hit;
    out[`recall@${k}`] = targets.length ? found / targets.length : 0;
    out[`ndcg@${k}`] = idcg ? dcg / idcg : 0;
    out[`judged@${k}`] = topN ? inTop / topN : 0;
  }
  return out;
}

const retrieval = {
  metadata: { ...META, name: "retrieval", description: "Known-item retrieval: rank of the first relevant item" },
  async grade({ trajectory, config }) {
    const relevant = config?.relevant;
    if (!Array.isArray(relevant) || relevant.length === 0) {
      throw new Error("retrieval: config.relevant must be a non-empty array of asset_ids");
    }
    const ks = Array.isArray(config?.k) && config.k.length ? config.k.map(Number) : [1, 5, 10, 20];
    let ranking = null;
    let scores = null;
    try {
      const parsed = JSON.parse(trajectory?.output ?? "");
      if (parsed && Array.isArray(parsed.ranking)) {
        ranking = parsed.ranking.map(String);
        if (Array.isArray(parsed.scores) && parsed.scores.length === ranking.length) {
          scores = parsed.scores.map((v) => (v === null ? null : Number(v)));
        }
      }
    } catch {
      // not JSON
    }
    if (!ranking) {
      return {
        name: "retrieval",
        kind: "code",
        passed: false,
        score: 0,
        evidence: "no ranking in the trajectory output",
        metadata: { relevant, ranked: false },
      };
    }
    const groups = Array.isArray(config?.groups) && config.groups.length ? config.groups : null;
    const m = retrievalMetrics(ranking, relevant, ks, scores, groups);
    const score = m[`hit@${RETRIEVAL_PASS_K}`];
    const passed = score >= 1 - 1e-12;
    const rankText = m.tie_decided ? `${m.rank_best}-${m.rank_worst} (tied)` : `${m.rank_best ?? "none"}`;
    return {
      name: "retrieval",
      kind: "code",
      passed,
      score,
      evidence: `first relevant at rank ${rankText} of ${m.n_ranked} (expected rr=${m.rr.toFixed(3)})`,
      metadata: { ...m, relevant, ranked: true },
    };
  },
};

export function registerGraders(registry) {
  registry.register(labelStatus);
  registry.register(fieldMatch);
  registry.register(setMatch);
  registry.register(retrieval);
}
