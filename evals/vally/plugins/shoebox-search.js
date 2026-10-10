// Vally executor plugin "shoebox-search": replays stored search rankings (replay only; no model call).
//
// The Vally --model value is a Shoebox search route name (e.g. eg2-image@768, hybrid:g31b). routes.json
// (written by `shoebox eval gen-search`) maps it to {params_hash, path} of the rankings file written by
// `shoebox eval search-run`: one JSON line per query, {query_id, route, ranking: [asset_id...], scores}.
// The stimulus name is the query_id. Output is that line as JSON. A missing route or query throws.
// Every trajectory carries a custom event {shoebox: {kind: "search", set, routes_file, route, query_id, ...}}.

import { randomUUID } from "node:crypto";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { computeMetrics } from "@microsoft/vally";

const NAME = "shoebox-search";

const routesCache = new Map(); // routes_file -> parsed JSON
const rankingsCache = new Map(); // rankings path -> Map(query_id -> row)

function loadRoutes(file) {
  if (!routesCache.has(file)) routesCache.set(file, JSON.parse(readFileSync(file, "utf8")));
  return routesCache.get(file);
}

// Rankings by query_id. A torn (unparseable) line is skipped and counted; the first line per query wins.
export function loadRankings(path) {
  if (rankingsCache.has(path)) return rankingsCache.get(path);
  const byQuery = new Map();
  byQuery.torn = 0;
  let text = "";
  try {
    text = readFileSync(path, "utf8");
  } catch (err) {
    if (err.code !== "ENOENT") throw err;
  }
  for (const line of text.split("\n")) {
    if (!line.trim()) continue;
    let row;
    try {
      row = JSON.parse(line);
    } catch {
      byQuery.torn += 1;
      continue;
    }
    if (!row || typeof row.query_id !== "string" || !Array.isArray(row.ranking)) {
      byQuery.torn += 1;
      continue;
    }
    if (!byQuery.has(row.query_id)) byQuery.set(row.query_id, row);
  }
  rankingsCache.set(path, byQuery);
  return byQuery;
}

// `shoebox eval search-report` parses the key=value pairs back out (an errored trial has no trajectory).
export function missingKeyError(route, queryId, entry, ctx = {}) {
  const where = entry ? `params_hash=${entry.params_hash}) in ${entry.path}` : "params_hash=?) not in routes.json";
  return new Error(
    `${NAME} replay: missing key (route=${route}, query_id=${queryId}, ${where} ` +
      `[set=${ctx.set ?? "?"}, routes_file=${ctx.routes_file ?? "?"}]`,
  );
}

class ShoeboxSearch {
  name = NAME;
  supportsAttachments = false;

  validateConfig(config) {
    const c = config ?? {};
    if (typeof c.routes_file !== "string" || !c.routes_file) throw new Error(`${NAME}: config.routes_file is required`);
    if (c.mode !== undefined && c.mode !== "replay") throw new Error(`${NAME}: only replay mode exists`);
  }

  async execute(stimulus, options) {
    const cfg = options.executorConfig ?? {};
    this.validateConfig(cfg);
    const route = options.model;
    if (!route) throw new Error(`${NAME}: no --model given (use a search route name from routes.json)`);
    const routesFile = resolve(cfg.routes_file);
    const doc = loadRoutes(routesFile);
    const queryId = stimulus.name;
    const ctx = { set: doc.set, routes_file: routesFile };
    const entry = doc.routes?.[route];
    if (!entry) throw missingKeyError(route, queryId, null, ctx);
    const t0 = new Date();
    const rankings = loadRankings(entry.path);
    const row = rankings.get(queryId);
    if (!row) throw missingKeyError(route, queryId, entry, ctx);
    if (row.route !== undefined && row.route !== route) {
      throw new Error(`${NAME} replay: ${entry.path} holds route ${row.route}, not ${route}; regenerate`);
    }
    const t1 = new Date();
    const output = JSON.stringify({ query_id: row.query_id, route, ranking: row.ranking, scores: row.scores ?? null });
    const events = [
      { type: "user_message", timestamp: t0, data: { content: stimulus.prompt ?? "" } },
      {
        type: "custom",
        timestamp: t1,
        data: {
          shoebox: {
            kind: "search",
            set: doc.set ?? null,
            routes_file: routesFile,
            queries_sha256: doc.queries_sha256 ?? null,
            mode: "replay",
            route,
            query_id: queryId,
            params_hash: entry.params_hash ?? null,
            n_ranked: row.ranking.length,
          },
        },
      },
      { type: "assistant_message", timestamp: t1, data: { content: output } },
    ];
    return {
      id: randomUUID(),
      stimulus,
      events,
      output,
      workDir: options.workDir,
      metadata: {
        startedAt: t0,
        completedAt: t1,
        model: route,
        executor: NAME,
        skillsLoaded: [],
        sessionID: options.sessionID ?? randomUUID(),
      },
      metrics: { ...computeMetrics(events), wallTimeMs: t1.getTime() - t0.getTime() },
    };
  }

  async shutdown() {}
}

export function registerExecutors(registry) {
  registry.register(new ShoeboxSearch());
}
