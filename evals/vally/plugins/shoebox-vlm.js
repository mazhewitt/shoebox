// Vally executor plugin "shoebox-vlm": replays (or live-fills) the Shoebox label store.
//
// The Vally --model value is a Shoebox candidate name. candidates.json (written by
// `shoebox eval gen`) maps it to {model_id, params_hash, store_dir}.
//   replay: read the record for the stimulus's asset_id from store_dir/part-*.jsonl (first final record,
//           else latest error, torn lines skipped, as shoebox.store.index); a missing key throws.
//   live:   run `uv run shoebox label one --candidate <name> --asset-id <id>` in the repo root
//           (the Python runner writes through to the same store) and parse its stdout.
// Output is the label JSON (status ok) or "[shoebox:<status>] ..." for invalid/error.
// Every trajectory carries a custom event {shoebox: {set, candidates_file, asset_id, model_id, params_hash, status, ...}}.

import { spawn } from "node:child_process";
import { randomUUID } from "node:crypto";
import { readFileSync, readdirSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { computeMetrics } from "@microsoft/vally";

const NAME = "shoebox-vlm";
const MODES = ["replay", "live"];
const DEFAULT_REPO_ROOT = resolve(dirname(fileURLToPath(import.meta.url)), "..", "..", "..");

const candidatesCache = new Map(); // candidates_file -> parsed JSON
const storeCache = new Map(); // `${store_dir}\n${model_id}\n${params_hash}` -> Map(asset_id -> record)

function loadCandidates(file) {
  if (!candidatesCache.has(file)) {
    candidatesCache.set(file, JSON.parse(readFileSync(file, "utf8")));
  }
  return candidatesCache.get(file);
}

// Same rule as shoebox.store.index: the first final record (ok/invalid) per asset, else its latest error.
function better(prev, rec) {
  if (!prev) return true;
  return !(prev.status === "ok" || prev.status === "invalid");
}

export function loadStore(storeDir, modelId, paramsHash) {
  const key = `${storeDir}\n${modelId}\n${paramsHash}`;
  if (storeCache.has(key)) return storeCache.get(key);
  const byAsset = new Map();
  byAsset.torn = 0; // unparseable lines (a killed writer), skipped as shoebox.store.iter_records does
  let parts = [];
  try {
    parts = readdirSync(storeDir).filter((f) => /^part-\d+\.jsonl$/.test(f)).sort();
  } catch (err) {
    if (err.code !== "ENOENT") throw err;
  }
  for (const part of parts) {
    for (const line of readFileSync(join(storeDir, part), "utf8").split("\n")) {
      if (!line.trim()) continue;
      let rec;
      try {
        rec = JSON.parse(line);
      } catch {
        byAsset.torn += 1;
        continue;
      }
      if (!rec || typeof rec !== "object") {
        byAsset.torn += 1;
        continue;
      }
      if (rec.model_id !== modelId || rec.params_hash !== paramsHash) continue;
      if (better(byAsset.get(rec.asset_id), rec)) byAsset.set(rec.asset_id, rec);
    }
  }
  if (byAsset.torn) {
    console.error(`${NAME} replay: skipped ${byAsset.torn} torn line(s) in ${storeDir}`);
  }
  storeCache.set(key, byAsset);
  return byAsset;
}

// The report parses the key=value pairs back out of this message (an errored trial has no trajectory).
export function missingKeyError(candidate, assetId, cand, store, ctx = {}) {
  const torn = store?.torn ? ` (${store.torn} torn line(s) skipped in the store)` : "";
  return new Error(
    `${NAME} replay: missing key (candidate=${candidate}, asset_id=${assetId}, model_id=${cand.model_id}, ` +
      `params_hash=${cand.params_hash}) in ${cand.store_dir}${torn} [set=${ctx.set ?? "?"}, ` +
      `candidates_file=${ctx.candidates_file ?? "?"}]`,
  );
}

function runLabelOne(repoRoot, candidate, assetId) {
  return new Promise((resolvePromise, reject) => {
    const child = spawn("uv", ["run", "shoebox", "label", "one", "--candidate", candidate, "--asset-id", assetId], {
      cwd: repoRoot,
      stdio: ["ignore", "pipe", "pipe"],
    });
    let out = "";
    let err = "";
    child.stdout.on("data", (d) => (out += d));
    child.stderr.on("data", (d) => (err += d));
    child.on("error", reject);
    child.on("close", (code) => {
      if (code !== 0) {
        reject(new Error(`${NAME} live: shoebox label one exited ${code}: ${err.trim().slice(-500)}`));
        return;
      }
      const lines = out.trim().split("\n").filter((l) => l.trim().startsWith("{"));
      if (!lines.length) {
        reject(new Error(`${NAME} live: no JSON record on stdout of shoebox label one`));
        return;
      }
      resolvePromise(JSON.parse(lines[lines.length - 1]));
    });
  });
}

export function outputFor(rec) {
  if (rec.status === "ok") return JSON.stringify(rec.output);
  const detail = rec.status === "invalid" ? rec.raw ?? "" : rec.error ?? "";
  return `[shoebox:${rec.status}] ${detail}`.trim();
}

class ShoeboxVlm {
  name = NAME;
  supportsAttachments = true;

  validateConfig(config) {
    const c = config ?? {};
    if (!MODES.includes(c.mode)) throw new Error(`${NAME}: config.mode must be one of ${MODES.join("|")}`);
    if (typeof c.candidates_file !== "string" || !c.candidates_file) {
      throw new Error(`${NAME}: config.candidates_file is required`);
    }
  }

  async execute(stimulus, options) {
    const cfg = options.executorConfig ?? {};
    this.validateConfig(cfg);
    const candidate = options.model;
    if (!candidate) throw new Error(`${NAME}: no --model given (use the Shoebox candidate name)`);
    const cands = loadCandidates(cfg.candidates_file);
    const cand = cands.candidates?.[candidate];
    if (!cand) throw new Error(`${NAME}: candidate '${candidate}' is not in ${cfg.candidates_file}`);
    const assetId = stimulus.name;
    const t0 = new Date();

    let rec;
    if (cfg.mode === "replay") {
      const store = loadStore(cand.store_dir, cand.model_id, cand.params_hash);
      rec = store.get(assetId);
      if (!rec) {
        throw missingKeyError(candidate, assetId, cand, store, {
          set: cands.set,
          candidates_file: resolve(cfg.candidates_file),
        });
      }
    } else {
      rec = await runLabelOne(cands.repo_root ?? DEFAULT_REPO_ROOT, candidate, assetId);
      if (rec.model_id !== cand.model_id || rec.params_hash !== cand.params_hash) {
        throw new Error(
          `${NAME} live: record key (${rec.model_id}, ${rec.params_hash}) does not match candidates.json (${cand.model_id}, ${cand.params_hash}); regenerate`,
        );
      }
    }
    const t1 = new Date();
    const output = outputFor(rec);
    const usage = rec.usage ?? {};
    const events = [
      { type: "user_message", timestamp: t0, data: { content: stimulus.prompt ?? "" } },
      {
        type: "token_usage",
        timestamp: t1,
        data: { model: cand.model_id, inputTokens: usage.input_tokens ?? 0, outputTokens: usage.output_tokens ?? 0 },
      },
      {
        type: "custom",
        timestamp: t1,
        data: {
          shoebox: {
            set: cands.set ?? null,
            candidates_file: resolve(cfg.candidates_file),
            items_sha256: cands.items_sha256 ?? null,
            mode: cfg.mode,
            candidate,
            asset_id: assetId,
            model_id: rec.model_id,
            params_hash: rec.params_hash,
            status: rec.status,
            usd: rec.usd ?? 0,
            latency_ms: rec.latency_ms ?? null,
            input_tokens: usage.input_tokens ?? 0,
            output_tokens: usage.output_tokens ?? 0,
            thinking_tokens: usage.thinking_tokens ?? null,
            machine: rec.machine ?? null,
            runtime: rec.runtime ?? null,
            record_ts: rec.ts ?? null,
          },
        },
      },
    ];
    if (rec.status === "error") {
      events.push({ type: "error", timestamp: t1, data: { message: String(rec.error ?? "error"), type: "shoebox" } });
    }
    events.push({ type: "assistant_message", timestamp: t1, data: { content: output } });
    const sessionID = options.sessionID ?? randomUUID();
    return {
      id: randomUUID(),
      stimulus,
      events,
      output,
      workDir: options.workDir,
      metadata: { startedAt: t0, completedAt: t1, model: candidate, executor: NAME, skillsLoaded: [], sessionID },
      metrics: { ...computeMetrics(events), wallTimeMs: rec.latency_ms ?? t1.getTime() - t0.getTime() },
    };
  }

  async shutdown() {}
}

export function registerExecutors(registry) {
  registry.register(new ShoeboxVlm());
}
