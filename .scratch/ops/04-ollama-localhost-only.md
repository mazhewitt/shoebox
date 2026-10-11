# Bind Ollama to localhost on the laptop (and the Studio)

Status: ready-for-human (owner confirms what else on the LAN uses each Ollama before it is rebound)
Reported: 2026-10-11
Reporter: claude (build-planning session)

## Why

- **Task 9.5** (`openspec/changes/add-eval-harness/tasks.md`): Ollama is listening on all interfaces.
- **Still true on 2026-10-11:** `lsof -iTCP:11434 -sTCP:LISTEN` shows `TCP *:11434`.
- **The pilot notes** (`evals/runs/2026-10-10_pilot-v0-notes.md`) show the cost: another LAN client (192.168.158.129, a VM) kept reloading `gemma4:e4b-nvfp4` during the benchmark runs and contaminated the latencies.
- **The risk.** An unauthenticated model server on the LAN can be used by anything on it.
- **Shoebox itself** already talks only to `http://127.0.0.1:11434`, with proxies ignored (`shoebox.runners.ollama.HOST`).

## What

- **Identify the 192.168.158.129 VM** and what it needs from the laptop's Ollama. It is likely radiobox's dev VM; check before cutting it off.
  - If it needs Ollama, give it its own instance, or keep it and record that.
- **Laptop:** set `OLLAMA_HOST=127.0.0.1:11434` (launchctl env, or the Ollama app setting) and restart Ollama.
- **Mac Studio:**
  - The **current M1 Studio** hosts radiobox's Ollama, which radiobox reaches over the LAN, so binding it to localhost would break radiobox. Leave it alone. It isn't a Shoebox machine (decision 10).
  - The **new M5 Ultra Studio** (`ingest/04`): bind to localhost from day one, unless radiobox moves there too. If it does, decide together with radiobox's needs.

## Done when

- `lsof -nP -iTCP:11434 -sTCP:LISTEN` on the laptop shows `127.0.0.1:11434` only.
- `shoebox label run` still works.
- The VM's fate is recorded below.

## Depends on

Nothing.

## Comments
