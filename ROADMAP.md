# Basal Router — roadmap and research log

Basal 2.2 (basal-2.0-mini 1.5B Q8_0 via Ollama) decides which free model handles a task.
The registry of "favourite" models lives in `models.json` (max 20 options — an ollama-mode limit).

## Architecture

```
Kilo Code (agents, /route) ──MCP stdio──> router_mcp.py
                                             │  recommend / pool / route / models / run
                                             │  HTTP POST /v1/systemone
                                     basal-serve :8000 (choice decisions, calibration)
                                             │  raw prompt + logprobs
                                     ollama serve :11434 (pdurlej/basal:2.0-1.5b, 1.7 GB)
                                             │
                                     router.py logs decisions ──> decisions.jsonl
                                     (after filling gold ──> basal-calibrate)
                                             │
                                     opencode run -m <model> (execution)
```

## Usage

```bash
~/basal-env/bin/python router.py route "fix a bug: a bash loop never ends"
~/basel-env/bin/python router.py models
~/basel-env/bin/python router.py annotate        # fill gold in decisions.jsonl
~/basel-env/bin/python experiments/run_experiments.py report   # regenerate RESULTS.md from eval files
```

In Kilo Code (after a session restart in this directory): `/route <task>` or the `kilo-router_*` tools.
In opencode: the `basal-router` MCP server (pool of opencode free models, `recommend` returns the id for `/model`).

## Measurements on i3-10110U (2C/4T, RAM under load)

- basal-serve start: 8 s; first request (cold): 18.5 s; same state (cached): ~9 s
- `llama-server` (Ollama): ~1.1 GB RSS; model file 1.7 GB
- one routing decision (1 choice question, 15 options): 30-70 s depending on swap pressure
- the measurement stack is kept alive by Kilo Code's background-process runner

## ollama-mode limits

- max 20 options per question (Ollama's logprob limit); >20 models would need two-stage routing
- 4,096-token context: very long task descriptions are cut in the middle
- the shipped calibration is cautious: a 1% target defers everything; use the 5% target or your own fit

## Research: card configurations

| Config | Cards | Registry file | Result |
|---|---|---|---|
| C1 | hand-written v1 | `models.manual.json` | 60% top-1, errors at conf < 0.3 |
| C2 | raw models.dev metadata | `models.net.json` | 0% top-1, all conf 0.14-0.26, 2x slower |
| C3 | role + appended facts | `models.hybrid.json` | 53% top-1, blurred signal |
| C4 | manual-v2, Polish | `models.manual-v2.json` | 60% top-1 |
| C5 | manual-v2, English | `models.json` | measuring |

Key findings: concise, role-differentiated cards win at 1.5B scale; uniform fact tails dilute the signal
and double latency; raw provider metadata carries no routing signal at all. Facts belong in `meta`
(for GEPA to weave in later), not in every card. Registry gap: no creative-writing model
(the one persistent high-confidence miss, task t10).

## Phase 1 — calibration on your own data (built into Basal 2.2)

1. Collect decisions: `router.py route ...` (or MCP) — every call lands in `decisions.jsonl`.
2. Fill `gold` (which model would actually have been best): `router.py annotate`.
3. Fit thresholds and temperatures:
   ```bash
   ~/basal-env/bin/basal-calibrate experiments/calibration_input.jsonl \
     --model Remek/basal-2.0-mini-GGUF --mode ollama \
     --ollama-model pdurlej/basal:2.0-1.5b \
     --out experiments/results/CALIBRATION.custom.json
   ```
4. Serve it: `basal-serve ... --calibration experiments/results/CALIBRATION.custom.json`
   and raise `confidence_floor` in `models.json` to the certified threshold.

The current calibration input is built from the labelled eval sets (C1+C3+C4 = 45 decisions, gold inside).

## Phase 2 — GEPA (github.com/gepa-ai/gepa)

GEPA evolves prompts using execution feedback. Here it optimises two things, iterating on the labelled
`eval_*.jsonl` sets (metric: routing accuracy):
- `INSTRUCTIONS` in `router.py` (the model-selection instruction),
- the model cards in the registry (they are the prompt basal reads; improvements go back to the registry).

## Phase 3 — OpenEvolve (github.com/codelion/openevolve)

AlphaEvolve-style code evolution: let it evolve `router.py` + the registry as a "genome" with
fitness = accuracy on the labelled sets (plus penalties for latency and deferrals). Worth it once a few
hundred good labels exist.

## Phase 4 — LangGraph (orchestration)

Graph: `route -> run (opencode) -> verify` — verify the result with a `noul` question behind the generator
(the "compress -> generate -> verify" pattern from Basal's docs/TASKS.md), retry with a stronger model when
verify rejects; plus parallel paths (two models, then basal `score` picks the better answer).

## OpenAI-compatible endpoint: routerd (127.0.0.1:8788, model "auto")

`routerd.py` is the single entry point for opencode, hermes, Kilo and anything OpenAI-speaking:

- `POST /v1/chat/completions` with `model: "auto"` → basal picks a model, routerd executes it
  (free: `opencode run -m <id>`; own keys: straight to the provider from `providers.json`) and returns an
  OpenAI-format answer
- `model: "router/<key>"` forces a model; the `router` field carries basal's decision
  (chosen_by_basal, confidence, deferred, executed_model, fallback_errors)
- **fallback**: on execution failure routerd takes the next models from basal's ranking (up to 2 retries)
- streaming is emulated (whole content in one chunk)
- long context: basal sees a trimmed task description (head+tail); the full text goes to the target model

Run: `~/basal-env/bin/python routerd.py` (local service, 127.0.0.1 only, keys never logged).

**Keys**: `providers.json` (chmod 600, never commit) — `{name: {baseURL, apiKey}}`; empty apiKey disables
a provider. The web panel manages them at http://127.0.0.1:8788/panel (save, masking, one-token key test,
adding OpenAI-compatible providers). Port 8788, because 8787 is taken by your Hermes WebUI.

**Kilo Code as a client**: the project `kilo.json` defines the `router` provider → pick `router/auto` in
the model picker. **Kilo Code has no native model router** (grep over the 7.8.15 extension: zero hits for
modelRouting/modelRouter/fallbackModel/autoModel; only the static `small_model` field in the SDK). The
router you see in the picker is ours. The extension code is MIT (github.com/Kilo-Org/kilocode) but has
no decision logic worth porting — the integration direction is the opposite: Kilo consumes our router.

**opencode/hermes as a client** — custom/OpenAI-compatible endpoint `http://127.0.0.1:8788/v1`, model `auto`:

```jsonc
{
  "provider": {
    "basal-router": {
      "npm": "@ai-sdk/openai-compatible",
      "name": "Basal Router",
      "options": {"baseURL": "http://127.0.0.1:8788/v1"},
      "models": {"auto": {"name": "Basal Auto"}}
    }
  }
}
```

**Benchmarks in cards**: a sync script can pull metrics (models.dev, OpenRouter API) into card text —
without hand-inventing numbers. Then GEPA refines the cards on your own data. The exact NVIDIA entries
come from `kilo models nvidia` (client id vs NIM API model name differ, e.g. `nvidia/nvidia/llama-3.1-nemotron-70b-instruct`).

## Vision status (VISION.md)

| Vision item | Status |
|---|---|
| One OpenAI-compatible endpoint, model `auto` | **works** (routerd, 8788) |
| Basal classifies the task and picks the model | **works** (choice over cards + decision log) |
| Free models via opencode CLI | **works** (opencode runner) |
| Own keys (NIM etc.) in a secure store | **skeleton done** (providers.json chmod 600, 127.0.0.1, key panel) |
| Fallback on errors | **basic** (next from basal's ranking; no mid-stream switching — streaming is emulated) |
| Model catalog with benchmarks | next step (models.dev / OpenRouter sync) |
| RPM/TPM scheduler, 429 + Retry-After | to do |
| Learning from performance (latency, errors, quality) | collecting since day one (decisions.jsonl) → GEPA/calibration |
| Web stats panel (FastAPI + SQLite) | to do |
| LiteLLM ≥1.83.9 | deferred: NIM/OpenRouter/Groq/Cerebras are OpenAI-compatible (httpx is enough); revisit for non-compatible providers or complex fallbacks |
| Blocking paid calls | the registry holds only free/own models; per-provider enforcement in the scheduler phase |

## Notes

- "Abstral" could not be identified (GitHub/PyPI) — needs clarification.
- Kilo Code favourites are UI state, not config — they live in `models.json`; add Kilo-gateway models
  with `runner: "kilo"` (route will name them, switching stays manual).
- `qwen-local` must be pulled into Ollama first; currently only basal is there.
