# evals/vally

The Vally execution layer for Shoebox evals. The full guide is [`docs/evals.md`](../../docs/evals.md).

- `package.json`: pins `@microsoft/vally-cli` and `@microsoft/vally` to exactly 0.18.0. Its npm scripts set `VALLY_TELEMETRY_OPTOUT=1`.
- `plugins/shoebox-vlm.js`: the executor `shoebox-vlm`. In `replay` mode it reads the label store, and a missing key is an error. In `live` mode it runs `shoebox label one`.
- `plugins/shoebox-graders.js`: the graders `label-status`, `field-match` and `set-match`.
- `generated/<set>/`: `eval.yaml` and `candidates.json`, written by `uv run shoebox eval gen`. Don't edit them.

```sh
npm ci
npm run lint
npm run eval -- --model rules-v1,e4b-bf16
npm run -s grade < <results.jsonl> > regraded.jsonl
npm run serve -- <output-dir>
```

Plugin paths passed to Vally must be absolute, because Vally resolves relative ones against the eval spec's directory.
