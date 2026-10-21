# Basal Router — usage guide

Everything you operate locally on one machine. Services first, then each tool.

## 0. Prerequisites: the three services

| Service | Port | Command |
|---|---|---|
| Ollama (engine host) | 11434 | `ollama serve` |
| basal-serve (decisions) | 8000 | `~/basal-env/bin/basal-serve --model Remek/basal-2.0-mini-GGUF --mode ollama --ollama-model pdurlej/basal:2.0-1.5b --port 8000 --calibration $PWD/experiments/results/CALIBRATION.custom.json` |
| routerd (router API + panel) | 8788 | `~/basal-env/bin/python routerd.py` |

Health checks:

```bash
curl -s localhost:11434/api/tags | head -c 200   # ollama
curl -s localhost:8000/health                    # basal-serve: {"status":"ok"}
curl -s localhost:8788/health                     # routerd: {"status":"ok"}
```

All three are persistent background processes managed by Kilo Code's process runner; after a reboot
restart them with the commands above.

## 1. Using the router

### Any OpenAI-compatible app (Kilo Code, hermes, curl, ...)

- `baseURL`: `http://127.0.0.1:8788/v1`, model `auto`
- force one model: `model: "router/<key>"` (keys: `router.py models`)
- the response field `router` carries the decision:
  `chosen_by_basal`, `confidence`, `deferred`, `executed_model`, `fallback_errors`
- `deferred: true` means: confidence below `confidence_floor` in `models.json` — the default model ran
- streaming is emulated (the whole answer arrives in one chunk)

```bash
curl -s localhost:8788/v1/chat/completions -H 'content-type: application/json' \
  -d '{"model":"auto","messages":[{"role":"user","content":"translate to English: dzień dobry"}]}'
```

### Kilo Code (in this directory)

- Model picker: `router/auto` — every Kilo prompt is routed by basal
- MCP tools: `kilo-router_recommend`, `kilo-router_pool`, `kilo-router_route`, `kilo-router_models`,
  `kilo-router_run` (permissions in the project `kilo.json`)
- `/route <task>` — the command wires recommend + run together
- restart the Kilo session after changing `kilo.json`; opencode free models have **no direct API** —
  from Kilo they are reachable only through this router

### opencode

- MCP server `basal-router` (in `~/.config/opencode/opencode.jsonc`) with `BASAL_ROUTER_POOL=opencode`
- `recommend(task)` returns `switch_to` — the exact id for `/model <id>`; `pool()` lists the free pool
- restart opencode after changing its config

### Command line

```bash
~/basal-env/bin/python router.py route "fix a bug: a bash loop never ends"
~/basal-env/bin/python router.py models
~/basal-env/bin/python router.py annotate          # label decisions.jsonl with gold
```

### Providers (panel)

Open http://127.0.0.1:8788/ — the panel loads automatically (the root redirects to `/panel`).
Per provider: paste a key, `Save` (chmod 600 `providers.json`, masked in the API), `Test` (a one-token
request to the probe model), `Models` (fetches the provider's `{baseURL}/models` catalog — needs a saved
key) and `Import` adds a picked catalog model to the registry as a `direct`-runner entry with an
auto-built card (real data from models.dev when available, a minimal template otherwise). `Delete`
removes a provider — blocked while registry models still use it. Add any OpenAI-compatible provider
from the form. NIM example: provider `nvidia`, probe `z-ai/glm-5.3-flash`. Until a key is saved, the
provider's registry models stay inactive.

### Settings (panel)

The settings card controls (all live from the next request — no restart; `settings.json`, chmod 600,
gitignored):
- **answer footer** (default on) — every routed answer ends with
  `--- [router] answered by <model> (confidence X). Why: ... Alternatives considered: ...`
- **log decisions** — append every decision to `decisions.jsonl` (your calibration dataset)
- **automatic fallback** (default on) — on execution failure try the next models from basal's
  ranking (up to 2)
- **confidence floor** — below this confidence the router defers to the default model
  (empty = the `models.json` value, 0.55)
- **default model** — the defer target (empty = the `models.json` value)
- **exec timeout** — per-execution timeout, 30..3600 s (default 600)
- **state language** — `pl` or `en`; the routing prompt language
- **routing instructions** — blank = the built-in default; paste GEPA-evolved instructions here

### Model registry (panel)

The registry card lists every model with its runner and usability (models whose provider has no saved
key are inactive), lets you remove models (blocked for the default model) and shows the 20-option cap
(an ollama-mode limit). Imported models can be removed freely; the shipped free models can be restored
from git history.

### Keyless operation

With no provider keys saved, routing considers **only models that need no key**: the opencode free
models and local Ollama. The `nim-*` entries join the candidate set automatically once their key is
saved in the panel (`router.py models` marks inactive entries; `/v1/models` carries an
`available` flag). opencode's free models are free of charge but **not anonymous** — they need an
active opencode session (`opencode auth list`); yours already has nvidia/google/openrouter configured
in opencode's own key store, separate from `providers.json`. If nothing can be executed, the router
returns an explicit error (HTTP 502 with a `hint` field naming the cause) — never an instruction-style
answer.

## 2. Orchestration: route -> run -> verify (LangGraph)

```bash
~/basal-env/bin/python router_graph.py "fix a bug: a bash loop never ends"
```

The graph routes the task, executes it on the chosen model, then verifies the answer with a basal
`noul` question; a rejected answer is retried once on `nemotron-ultra`. Output: JSON with
`decision`, `answer`, `verified`, `attempts`.

## 3. Research loop

```bash
# measure configurations (registry files pinned in the harness)
~/basal-env/bin/python experiments/run_experiments.py C1-manual-cards,C5-english-cards

# regenerate RESULTS.md from all eval sets (stale-gold sets are skipped)
~/basal-env/bin/python experiments/run_experiments.py report
```

Configs: C1 hand-written (v1) · C2 raw models.dev · C3 hybrid · C4 manual-v2 (PL) · C5 manual-v2 (EN,
production). Each run resumes from `experiments/results/progress_<config>.json` after a crash
(this machine swaps — OOM kills happen). Raw labelled decisions land in
`experiments/results/eval_<config>.jsonl` — ready input for calibration and GEPA.

## 4. Own calibration (basal-calibrate)

Input: one JSONL of `/v1/systemone` requests with `gold` inside each question (see
`experiments/calibration_input.jsonl` — built from the eval files). Then:

```bash
~/basal-env/bin/basal-calibrate experiments/calibration_input.jsonl \
  --model Remek/basal-2.0-mini-GGUF --mode ollama \
  --ollama-model pdurlej/basal:2.0-1.5b \
  --out experiments/results/CALIBRATION.custom.json \
  --save-rows experiments/results/calib_rows.jsonl
```

Wire it with `--calibration` (restart of basal-serve). Facts: temperatures fit from ~30 labels;
a 5% error certificate needs **>=59** accepted FIT decisions without an error (1%: >=299) — keep
labelling (`router.py annotate` + real usage) and refit; `--from-rows` reuses the collected
measurements. Until a certificate exists, `confidence_floor` in `models.json` is the manual guard
(currently 0.55).

## 5. GEPA (evolving instructions + cards)

Every rollout = one basal decision (~1 min here); every reflection proposal = one opencode call
through routerd. Hence the micro budget by default:

```bash
~/basal-env/bin/python opt/gepa_optimize.py --tasks 6 --max-metric-calls 18
```

- Seed: current `INSTRUCTIONS` + all 15 cards (`card:<key>` components)
- Output: `opt/gepa_result/result.json` — the best candidate is an **artifact**, never applied to
  `models.json` automatically; review it, paste what you accept, and re-measure (`run_experiments.py`)
- **OOM note:** on this machine the run can get killed mid-loop (heavy swap); it always resumes from
  `opt/gepa_result/gepa_state.bin` — just launch the same command again (checkpoints accumulate), or run
  it overnight. Progress lives in `opt/gepa_result/live.log`
- Scaling up: more `--tasks`, more `--max-metric-calls`, and/or a faster reflection LLM —
  swap `reflect_via_routerd` for a paid API call (a few cents per run)

## 6. ABSTRAL-style architecture learning (opt/abstral_loop.py)

Adapted from arXiv:2603.22791 (the paper has no public implementation). Unlike GEPA — which evolves
prompt *text* — this loop learns the *organisation* of the pipeline: verify on/off and its threshold,
the escalation model, max attempts, fallback, and the confidence floor. One cycle runs a task subset
through route -> run -> verify -> escalate, analyses the traces (routing accuracy, verification and
escalation rates, model calls = the cost register), then a free model proposes the smallest topology
change plus new K/R/T/P knowledge entries. Guardrails: every change is bounded, journaled in
`opt/abstral/change_log.json`, validated by the next cycle and auto-reverted if accuracy regresses by
more than 0.05; `--revert <cycle>` undoes any change manually.

```bash
~/basal-env/bin/python opt/abstral_loop.py --tasks 3      # one cycle (~12-25 min on this CPU)
~/basal-env/bin/python opt/abstral_loop.py --status
~/basal-env/bin/python opt/abstral_loop.py --revert 2
```

## 7. OpenEvolve (scaffold, run when you have a fast LLM)

`opt/openevolve_router.py` holds the evaluator (routing accuracy on the first 6 benchmark tasks) and
the current cards as the genome. A meaningful evolution is LLM-call-bound — days over free
endpoints on this CPU. When a fast endpoint is available:

```bash
# point OpenEvolve's LLM at it (OpenAI-compatible), then:
openevolve-run opt/openevolve_initial_program.py opt/openevolve_evaluator.py --config opt/openevolve_config.yaml
# baseline of the current cards, no evolution (6 basal calls):
~/basal-env/bin/python opt/openevolve_router.py --baseline
```

## 8. Hardware notes (i3-10110U, 16 GB, heavy swap)

- one decision: 30-70 s depending on swap pressure; `basal-serve` start: ~8 s (warm cache)
- keep `free -h` in mind: below ~3 GB available, processes get OOM-killed (the harness resumes)
- the whole stack (ollama, basal-serve, routerd) survives as Kilo-managed background processes;
  check with the background-process list of your Kilo session or `pgrep -af 'basal-serve|routerd'`
- upgrade path: engine 2.3 brings GPU/omni speedups only — irrelevant for this ollama/CPU setup;
  the meaningful upgrade is more RAM or a 4.5B model on an unloaded machine
