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
| `RESULTS.md` | research results |
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

## Status

Working: endpoint, routing, execution via opencode CLI, fallback, MCP, key panel, decision log.
In progress: own calibration (`basal-calibrate`), C5 measurement of English cards.
Roadmap: rate-limit scheduler, model catalog discovery with benchmarks, web stats, GEPA loop.
Details and measurements on i3-10110U: `ROADMAP.md`, `RESULTS.md`.
