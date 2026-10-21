# Basal Router

> **One model to rule them all.**
>
> You pick **one model** — `Basal Router` (`auto`) — in every app (Kilo Code, opencode, hermes, anything
> that speaks OpenAI). Behind it a local [Basal 2.2](https://github.com/rkinas/basal) classifier
> (basal-2.0-mini 1.5B Q8_0 via Ollama) selects the best **free** model from the registry for each task,
> the router executes it (opencode CLI, or a provider with your own key) and returns the answer through a
> single OpenAI-compatible endpoint. No manual model or provider switching: one address, one model — all the
> free models behind it.

Decisions are calibrated probabilities (`confidence`, thresholds, deferring). Every decision is logged for
your own calibration (`basal-calibrate`) and for card optimisation (GEPA).

## Architecture

```
apps (Kilo Code / opencode / hermes / any OpenAI-compatible client)
        │
   http://127.0.0.1:8788/v1   (routerd.py, model "auto")
        │                       ├── fallback: next models from basal's ranking
        ├── basal-serve :8000  (choice decisions, calibration, /v1/systemone)
        │       └── ollama :11434 (basal-2.0-mini 1.5B Q8_0)
        └── execution: opencode run -m <model> | providers.json (NIM etc., keys kept locally)
```

## How it works (request lifecycle)

1. **Task assembly** — the request's `system`/`user` messages are joined into one task text; above
   8,000 characters it is trimmed head+tail (ollama mode reads at most 4,096 tokens of state — the
   full text still goes to the target model).
2. **Model resolution** — `model: "auto"` (or `router/auto`) means full routing;
   `model: "router/<key>"` forces one registry model.
3. **Settings** are read per request from `settings.json` (managed by the panel — no restarts):
   answer footer, decision logging, fallback, confidence floor, default model, execution timeout,
   state language, routing instructions.
4. **Candidate filtering** — only models executable *right now* take part: opencode/local models
   always, `direct` models only when their provider has a saved key. Without any key: 12 candidates;
   with an NVIDIA NIM key: 15. An empty set returns an explicit error with a `hint` — never a fake
   answer or an instruction-as-answer.
5. **The basal decision** — one `choice` question over the candidates (criteria: `name. card`,
   instructions from settings or the built-in default, `lang: pl|en`). basal renders its own prompt,
   sends it raw to Ollama and reads the **logprobs of the option codes**, producing a probability
   distribution over models plus a calibrated `confidence` (per-type and per-option-count
   temperatures fitted on our own 45 labelled decisions).
6. **Deferring** — `confidence` below the floor → `deferred: true` → the default model runs instead.
   Every decision is appended to `decisions.jsonl` (unless logging is off) in `basal-calibrate`-ready
   format.
7. **Execution** — runner `opencode`: `opencode run -m <id>` (the only way to reach opencode's free
   models outside opencode); runner `direct`: plain HTTPS to the provider with the key from
   `providers.json`. Timeout from settings (30-3600 s).
8. **Fallback** (toggleable) — on execution failure the router takes the next entries of basal's own
   ranking (up to 2 retries); the ranking is already part of the decision, so failover is free.
9. **Answer footer** (toggleable) — appended to the content:
   `--- [router] answered by <model> (confidence X). Why: ... Alternatives considered: ...`.
10. **Response** — OpenAI format (streaming emulated as a single chunk) plus the `router` field:
    `chosen_by_basal`, `confidence`, `deferred`, `executed_model`, `fallback_errors`.

## Management panel

Open http://127.0.0.1:8788/ — the root redirects to the panel. Everything applies from the next
request; keys and settings never leave the machine (`providers.json` / `settings.json`, chmod 600,
gitignored).

- **Providers** — save a key (masked in the API), `Test` (one-token probe request), `Models` (fetches
  the provider's `{baseURL}/models` catalog), `Import` (adds a catalog model to the registry as a
  `direct` entry with an auto-built card from models.dev data), `Delete` (blocked while registry
  models use the provider)
- **Registry** — live list with usability flags (needs-key models are inactive), model removal
  (blocked for the default model), and the 20-option cap counter (an ollama-mode limit)
- **Settings** — answer footer, decision logging, automatic fallback, confidence floor, default model,
  execution timeout, state language (pl/en), routing instructions (paste GEPA-evolved text here)

## Data loops

1. Every routing decision lands in `decisions.jsonl`; label it with `router.py annotate`.
2. Labelled decisions feed `basal-calibrate` → `CALIBRATION.custom.json`, served with
   `--calibration` (temperatures fitted; a certified 5% error threshold needs >=59 accepted FIT
   decisions — until then the confidence floor is the manual guard).
3. The benchmark and eval sets feed **GEPA**, which evolves the routing instructions and model cards
   from execution feedback; its result goes into the panel's routing-instructions setting (or the
   registry) and is re-measured. `router_graph.py` (LangGraph) wraps the loop as
   route -> run -> verify, with escalation to a stronger model when verification fails.

## Files

| File | Role |
|---|---|
| `routerd.py` | OpenAI-compatible endpoint (`/v1/chat/completions`, `/v1/models`); routing + fallback |
| `panel.html`, `/panel` | web panel for provider keys: http://127.0.0.1:8788/panel (save, masking, key test) |
| `router.py` | decisions via basal, execution, decision log, CLI (`route`/`models`/`annotate`) |
| `router_mcp.py` | MCP server `kilo-router` (recommend/pool/route/models/run) |
| `models.json` | production registry: English cards (role-based), runners; max 20 options |
| `models.manual.json` | hand-written cards v1 — research baseline (C1) |
| `models.manual-v2.json` | manual-v2 cards, Polish — research baseline (C4) |
| `models.net.json` | raw models.dev cards — research variant (C2) |
| `models.hybrid.json` | role + appended facts — research variant (C3) |
| `sync_cards.py` | rebuilds the models.dev variant of the cards |
| `providers.json` | **not in the repo** — provider API keys, chmod 600 |
| `kilo.json` | Kilo Code integration: `router/auto` provider + MCP + tool permissions |
| `.kilo/command/route.md` | the `/route <task>` command |
| `experiments/` | configuration research; results in `experiments/results/` and `RESULTS.md` |
| `opt/` | optimisation stack: GEPA harness, ABSTRAL-style architecture loop (arXiv:2603.22791), OpenEvolve scaffold, self-updating model discovery, benchmark enrichment, passive speed measurement |
| `RESULTS.md` | research results |
| `USAGE.md` | operations guide: services, clients, panel, research, calibration, optimisation |
| `ROADMAP.md` | architecture, phases (calibration, GEPA, OpenEvolve, LangGraph), vision status |
| `VISION.md` | the original product vision this repo implements |

## Quick start

```bash
# decision engine
basal-serve --model Remek/basal-2.0-mini-GGUF --mode ollama --ollama-model pdurlej/basal:2.0-1.5b --port 8000
# router (OpenAI-compatible endpoint)
~/basal-env/bin/python routerd.py
# single decision / MCP server for Kilo
~/basal-env/bin/python router.py route "task description"
~/basal-env/bin/python router_mcp.py
```

Client: `baseURL http://127.0.0.1:8788/v1`, model `auto` (force one: `router/<key>`).
The `router` field of the response carries basal's decision: `chosen_by_basal`, `confidence`, `deferred`,
`executed_model`, `fallback_errors`.

Note: **opencode free models have no direct API** — outside opencode they are reachable only through the
`opencode` CLI. routerd is exactly that bridge: Kilo Code, hermes and any other client use them through
`router/auto` without ever touching opencode; inside opencode itself the `recommend` tool returns a native
`/model` id.

## Security

- `providers.json` (keys) is chmod 600, outside the repo, never logged
- routerd listens on 127.0.0.1 only
- the panel checks the Host header (DNS-rebinding) and requires the `x-panel` header (CSRF)
- the registry contains only free/local models; enforcing no-paid-calls is on the roadmap

## Research

`experiments/run_experiments.py` measures routing accuracy, confidence, latency and automation thresholds
on a synthetic benchmark (`experiments/benchmark_tasks.jsonl`) across card configurations:
C1 hand-written, C2 raw models.dev, C3 hybrid, C4 manual-v2 (Polish), C5 manual-v2 (English).
Results: `RESULTS.md`; raw measurements: `experiments/results/` (`eval_*.jsonl` are ready input for
`basal-calibrate` and GEPA). Regenerate the report with `run_experiments.py report`.

Note: benchmark task texts are intentionally in Polish — they are the router's real-world input language
(the primary usage of this router), not repo prose. Historical eval files keep the exact prompts used in
each measurement.

## Timings (Intel i3-10110U, 2C/4T, 16 GB RAM under heavy swap)

| Operation | Typical time |
|---|---|
| `basal-serve` startup (warm cache) | ~8 s |
| First decision after start (model load) | ~19 s |
| Warm decision, short state (3 questions, ~370 tokens) | ~9 s |
| Routing decision, 15 options, concise role cards (C1/C4) | median ~38 s, p90 ~65 s |
| Routing decision, longer hybrid cards (C3) | median ~66 s |
| Execution via opencode CLI (free models) | 30-180 s |
| Full routed request (decision + execution + footer) | typically 1-4 min |
| `basal-calibrate` fit, 45 labelled decisions | ~30 min |
| GEPA micro-iteration (6-task seed eval + 1 reflection) | ~10-15 min |

Times scale with free RAM: ollama keeps the 1.7 GB model loaded (~1.1 GB RSS); when the system swaps
hard, a single decision can reach 2-3 minutes and long-running loops may get OOM-killed — the
research harness and GEPA both resume from their checkpoints (`progress_*.json`, `gepa_state.bin`).
On a quiet machine or with an NVIDIA GPU, decision latency drops by an order of magnitude.

## License and attribution

This repository is licensed under the **MIT License** (see `LICENSE`), chosen to be consistent with
the way the Hermes agent is used here. It builds on and invokes — as separate executables/services,
without modifying them — the following components:

- **Hermes Agent** (MIT, Copyright (c) 2025 Nous Research) — runs as an isolated sandboxed subprocess
  (`opt/hermes_sandbox/`) for model-registry updates only
- **Basal** decision engine (Apache-2.0) — runs as a local service (basal-serve)
- **opencode** CLI (MIT) — the execution path for opencode-hosted free models; those models are used
  through the official CLI, and the project deliberately performs **no synthetic benchmark calls**
  against them — performance is measured passively on real user requests only, per their Terms of
  Service
- Catalog and benchmark data come from the public **models.dev** and **OpenRouter** APIs, used under
  their respective terms

## Status

Working: endpoint, routing, execution via opencode CLI, fallback, MCP, key panel, decision log.
In progress: own calibration (`basal-calibrate`), C5 measurement of English cards.
Roadmap: rate-limit scheduler, model catalog discovery with benchmarks, web stats, GEPA loop.
Details and measurements on i3-10110U: `ROADMAP.md`, `RESULTS.md`.
