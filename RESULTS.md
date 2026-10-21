# Router configuration research results

Date: 2026-10-22 00:18 · krzysiek-hpe820g3 (Intel i3-10110U, 4 threads, RAM under load) · basal-2.0-mini 1.5B Q8_0 via Ollama · benchmark v2: 15 tasks, gold labels follow real models.dev profiles

Configs: **C1-manual-cards** (hand-written cards (v1)), **C3-hybrid-cards** (hybrid (role + facts)), **C4-manual-v2-cards** (manual-v2 cards (Polish)), **C5-english-cards** (manual-v2 cards (English, production)), **C6-benchmark-cards** (cards with real benchmark scores).

Report regenerated from all `eval_*.jsonl` measurement sets; latency columns omitted (machine-load dependent — raw numbers in `experiments/results/last_run.json`).

## Summary

| config | n | top-1 | mean conf (correct) | mean conf (wrong) |
|---|---|---|---|---|
| C1-manual-cards (hand-written cards (v1)) | 15 | 0.6 | 0.688 | 0.278 |
| C3-hybrid-cards (hybrid (role + facts)) | 15 | 0.533 | 0.512 | 0.304 |
| C4-manual-v2-cards (manual-v2 cards (Polish)) | 15 | 0.6 | 0.683 | 0.322 |
| C5-english-cards (manual-v2 cards (English, production)) | 15 | 0.6 | 0.686 | 0.412 |
| C6-benchmark-cards (cards with real benchmark scores) | 15 | 0.333 | 0.914 | 0.426 |

## Automation thresholds (a decision is automated when confidence >= threshold)

| config | threshold | automated % | errors among automated |
|---|---|---|---|
| C1-manual-cards | 0.3 | 60% | 1 (rate 0.111) |
| C1-manual-cards | 0.45 | 53% | 1 (rate 0.125) |
| C1-manual-cards | 0.55 | 40% | 1 (rate 0.167) |
| C1-manual-cards | 0.7 | 33% | 0 (rate 0.0) |
| C3-hybrid-cards | 0.3 | 53% | 3 (rate 0.375) |
| C3-hybrid-cards | 0.45 | 40% | 1 (rate 0.167) |
| C3-hybrid-cards | 0.55 | 40% | 1 (rate 0.167) |
| C3-hybrid-cards | 0.7 | 20% | 1 (rate 0.333) |
| C4-manual-v2-cards | 0.3 | 60% | 2 (rate 0.222) |
| C4-manual-v2-cards | 0.45 | 47% | 1 (rate 0.143) |
| C4-manual-v2-cards | 0.55 | 47% | 1 (rate 0.143) |
| C4-manual-v2-cards | 0.7 | 40% | 1 (rate 0.167) |
| C5-english-cards | 0.3 | 87% | 4 (rate 0.308) |
| C5-english-cards | 0.45 | 47% | 1 (rate 0.143) |
| C5-english-cards | 0.55 | 40% | 1 (rate 0.167) |
| C5-english-cards | 0.7 | 40% | 1 (rate 0.167) |
| C6-benchmark-cards | 0.3 | 80% | 7 (rate 0.583) |
| C6-benchmark-cards | 0.45 | 60% | 4 (rate 0.444) |
| C6-benchmark-cards | 0.55 | 47% | 2 (rate 0.286) |
| C6-benchmark-cards | 0.7 | 33% | 1 (rate 0.2) |

## Per task

| id | profile | gold | C1-manual-cards | C3-hybrid-cards | C4-manual-v2-cards | C5-english-cards | C6-benchmark-cards |
|---|---|---||---||---||---||---||---|
Note: C2-net-cards measured under older gold labels — excluded from this report (kept in git history and in the eval files).

| t01 | code | deepseek-flash | OK deepseek-flash (0.45) | OK deepseek-flash (0.21) | OK deepseek-flash (0.35) | OK deepseek-flash (0.34) | ERR |
| t02 | code | deepseek-flash | OK deepseek-flash (0.37) | MISS or-free (0.19) | OK deepseek-flash (0.21) | OK deepseek-flash (0.53) | MISS or-free (0.44) |
| t03 | vision | nemotron-vl | OK nemotron-vl (0.96) | OK nemotron-vl (0.84) | OK nemotron-vl (0.96) | OK nemotron-vl (0.87) | OK nemotron-vl (0.95) |
| t04 | vision | nemotron-vl | OK nemotron-vl (0.96) | OK nemotron-vl (0.81) | OK nemotron-vl (0.95) | OK nemotron-vl (0.94) | OK nemotron-vl (0.99) |
| t05 | simple | mimo-flash | OK mimo-flash (0.45) | OK mimo-flash (0.55) | OK mimo-flash (0.63) | OK mimo-flash (0.44) | MISS or-free (0.59) |
| t06 | simple | minimax-flash | MISS mimo-flash (0.29) | MISS or-free (0.36) | MISS mimo-flash (0.25) | MISS mimo-flash (0.33) | MISS or-free (0.38) |
| t07 | research | nemotron-ultra | MISS nim-nemotron-70b (0.17) | MISS minimax-flash (0.10) | MISS glm-flash (0.13) | MISS nim-nemotron-70b (0.22) | MISS xingchen-agi-xing4-0-29b-a4b-fre (0.15) |
| t08 | reasoning | nemotron-ultra | MISS nim-nemotron-70b (0.18) | MISS glm-flash (0.35) | MISS nemotron-lightning (0.16) | MISS nim-nemotron-70b (0.29) | MISS nim-nemotron-70b (0.25) |
| t09 | long-context | longcat | OK longcat (0.91) | OK longcat (0.25) | OK longcat (0.90) | OK longcat (0.87) | OK longcat (0.98) |
| t10 | creative | glm-flash | MISS or-free (0.64) | MISS or-free (0.75) | MISS or-free (0.80) | MISS or-free (0.90) | MISS or-free (0.94) |
| t11 | code | deepseek-flash | OK deepseek-flash (0.95) | OK deepseek-flash (0.61) | OK deepseek-flash (0.93) | OK deepseek-flash (0.85) | OK deepseek-flash (0.66) |
| t12 | agent | glm-flash | MISS nemotron-lightning (0.18) | MISS or-free (0.15) | MISS or-free (0.22) | MISS or-free (0.32) | MISS big-pickle (0.53) |
| t13 | math | nemotron-ultra | MISS or-free (0.21) | MISS mimo-flash (0.23) | MISS or-free (0.36) | MISS or-free (0.41) | MISS or-free (0.54) |
| t14 | simple | mimo-flash | OK mimo-flash (0.17) | OK mimo-flash (0.21) | OK mimo-flash (0.25) | OK mimo-flash (0.40) | MISS or-free (0.44) |
| t15 | vision | nemotron-vl | OK nemotron-vl (0.96) | OK nemotron-vl (0.60) | OK nemotron-vl (0.96) | OK nemotron-vl (0.94) | OK nemotron-vl (0.99) |

Benchmark task texts are intentionally Polish (the router's real input language). `eval_*.jsonl` files keep the exact prompts of each measurement and are ready input for `basal-calibrate` and GEPA.
