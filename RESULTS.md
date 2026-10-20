# Wyniki badań konfiguracji routera

Data: 2026-10-20 20:48 · krzysiek-hpe820g3 (Intel i3-10110U, 4 wątki, RAM pod obciążeniem) · basal-2.0-mini 1.5B Q8_0 przez Ollamę · benchmark v2: 15 zadań, 15 opcji, goldy wg realnych profili models.dev

Przebiegi: **C1-manual-cards** (karty ręczne (v1)), **C4-manual-v2-cards** (karty manual-v2 (produkcja)).

## Zbiorczo

| konfiguracja | top-1 | śr. pewność trafnych | śr. pewność błędnych | mediana [ms] | p90 [ms] |
|---|---|---|---|---|---|
| C1-manual-cards (karty ręczne (v1)) | 0.6 | 0.688 | 0.278 | 38059 | 65648 |
| C4-manual-v2-cards (karty manual-v2 (produkcja)) | 0.6 | 0.683 | 0.322 | 45195 | 51039 |

## Progi automatyzacji (decyzja pewna, gdy confidence ≥ próg)

| konfiguracja | próg | % zautomatyzowanych | błędy wśród zautomatyzowanych |
|---|---|---|---|
| C1-manual-cards | 0.3 | 60% | 1 (rate 0.111) |
| C1-manual-cards | 0.45 | 53% | 1 (rate 0.125) |
| C1-manual-cards | 0.55 | 40% | 1 (rate 0.167) |
| C1-manual-cards | 0.7 | 33% | 0 (rate 0.0) |
| C4-manual-v2-cards | 0.3 | 60% | 2 (rate 0.222) |
| C4-manual-v2-cards | 0.45 | 47% | 1 (rate 0.143) |
| C4-manual-v2-cards | 0.55 | 47% | 1 (rate 0.143) |
| C4-manual-v2-cards | 0.7 | 40% | 1 (rate 0.167) |

## Per zadanie

| id | profil | gold | C1-manual-cards | C4-manual-v2-cards |
|---|---|---||---||---|
| t01 | code | deepseek-flash | ✓ deepseek-flash (0.45) | ✓ deepseek-flash (0.35) |
| t02 | code | deepseek-flash | ✓ deepseek-flash (0.37) | ✓ deepseek-flash (0.21) |
| t03 | vision | nemotron-vl | ✓ nemotron-vl (0.96) | ✓ nemotron-vl (0.96) |
| t04 | vision | nemotron-vl | ✓ nemotron-vl (0.96) | ✓ nemotron-vl (0.95) |
| t05 | simple | mimo-flash | ✓ mimo-flash (0.45) | ✓ mimo-flash (0.63) |
| t06 | simple | minimax-flash | ✗ mimo-flash (0.29) | ✗ mimo-flash (0.25) |
| t07 | research | nemotron-ultra | ✗ nim-nemotron-70b (0.17) | ✗ glm-flash (0.13) |
| t08 | reasoning | nemotron-ultra | ✗ nim-nemotron-70b (0.18) | ✗ nemotron-lightning (0.16) |
| t09 | long-context | longcat | ✓ longcat (0.91) | ✓ longcat (0.90) |
| t10 | creative | glm-flash | ✗ or-free (0.64) | ✗ or-free (0.80) |
| t11 | code | deepseek-flash | ✓ deepseek-flash (0.95) | ✓ deepseek-flash (0.93) |
| t12 | agent | glm-flash | ✗ nemotron-lightning (0.18) | ✗ or-free (0.22) |
| t13 | math | nemotron-ultra | ✗ or-free (0.21) | ✗ or-free (0.36) |
| t14 | simple | mimo-flash | ✓ mimo-flash (0.17) | ✓ mimo-flash (0.25) |
| t15 | vision | nemotron-vl | ✓ nemotron-vl (0.96) | ✓ nemotron-vl (0.96) |

Goldy v2 wg realnych profili models.dev (rewizja: t07 beam→nemotron-ultra, t10 big-pickle→glm-flash). Historyczny wynik C2-net-cards na benchmarku v1: 0/10 (poprzedni run, w historii gita).

Surowe pomiary: `experiments/results/last_run.json`; `eval_*.jsonl` to gotowy input dla `basal-calibrate` i pod zbiór ewaluacyjny GEPA.
