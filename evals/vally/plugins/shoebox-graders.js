// Vally grader plugin: per-trial graders for Shoebox label-v1 outputs.
//
//   label-status  pass iff the stored record's status is "ok" (invalid and error count as wrong).
//   field-match   {field, expected?}: one enum field vs the owner label. Predicted "unknown" = abstain
//                 (recorded, fails); expected "unknown" (owner could not tell) = unlabelled, owner_unknown=true.
//   set-match     {field="tags", expected?}: tag set vs the owner's tags; TP/FP/FN in metadata, score = F1.
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

export function registerGraders(registry) {
  registry.register(labelStatus);
  registry.register(fieldMatch);
  registry.register(setMatch);
}
