# Wyniki badań konfiguracji routera

Data: 2026-10-20 12:00 · krzysiek-hpe820g3 (Intel i3-10110U, 4 wątki, RAM pod obciążeniem) · basal-2.0-mini 1.5B Q8_0 przez Ollamę · benchmark v2: 15 zadań, 15 opcji, goldy wg realnych profili models.dev

Przebiegi: **C1-manual-cards** (karty ręczne (v1)), **C3-hybrid-cards** (karty hybrydowe (rola + fakty)).

## Zbiorczo

| konfiguracja | top-1 | śr. pewność trafnych | śr. pewność błędnych | mediana [ms] | p90 [ms] |
|---|---|---|---|---|---|
| C1-manual-cards (karty ręczne (v1)) | 0.6 | 0.688 | 0.278 | 37066 | 40481 |
| C3-hybrid-cards (karty hybrydowe (rola + fakty)) | 0.533 | 0.512 | 0.304 | 66030 | 68902 |

## Progi automatyzacji (decyzja pewna, gdy confidence ≥ próg)

| konfiguracja | próg | % zautomatyzowanych | błędy wśród zautomatyzowanych |
|---|---|---|---|
| C1-manual-cards | 0.3 | 60% | 1 (rate 0.111) |
| C1-manual-cards | 0.45 | 53% | 1 (rate 0.125) |
| C1-manual-cards | 0.55 | 40% | 1 (rate 0.167) |
| C1-manual-cards | 0.7 | 33% | 0 (rate 0.0) |
| C3-hybrid-cards | 0.3 | 53% | 3 (rate 0.375) |
| C3-hybrid-cards | 0.45 | 40% | 1 (rate 0.167) |
| C3-hybrid-cards | 0.55 | 40% | 1 (rate 0.167) |
| C3-hybrid-cards | 0.7 | 20% | 1 (rate 0.333) |

## Per zadanie

| id | profil | gold | C1-manual-cards | C3-hybrid-cards |
|---|---|---||---||---|
| t01 | code | deepseek-flash | ✓ deepseek-flash (0.45) | ✓ deepseek-flash (0.21) |
| t02 | code | deepseek-flash | ✓ deepseek-flash (0.37) | ✗ or-free (0.19) |
| t03 | vision | nemotron-vl | ✓ nemotron-vl (0.96) | ✓ nemotron-vl (0.84) |
| t04 | vision | nemotron-vl | ✓ nemotron-vl (0.96) | ✓ nemotron-vl (0.81) |
| t05 | simple | mimo-flash | ✓ mimo-flash (0.45) | ✓ mimo-flash (0.55) |
| t06 | simple | minimax-flash | ✗ mimo-flash (0.29) | ✗ or-free (0.36) |
| t07 | research | nemotron-ultra | ✗ nim-nemotron-70b (0.17) | ✗ minimax-flash (0.10) |
| t08 | reasoning | nemotron-ultra | ✗ nim-nemotron-70b (0.18) | ✗ glm-flash (0.35) |
| t09 | long-context | longcat | ✓ longcat (0.91) | ✓ longcat (0.25) |
| t10 | creative | glm-flash | ✗ or-free (0.64) | ✗ or-free (0.75) |
| t11 | code | deepseek-flash | ✓ deepseek-flash (0.95) | ✓ deepseek-flash (0.61) |
| t12 | agent | glm-flash | ✗ nemotron-lightning (0.18) | ✗ or-free (0.15) |
| t13 | math | nemotron-ultra | ✗ or-free (0.21) | ✗ mimo-flash (0.23) |
| t14 | simple | mimo-flash | ✓ mimo-flash (0.17) | ✓ mimo-flash (0.21) |
| t15 | vision | nemotron-vl | ✓ nemotron-vl (0.96) | ✓ nemotron-vl (0.60) |

Goldy v2 wg realnych profili models.dev (rewizja: t07 beam→nemotron-ultra, t10 big-pickle→glm-flash). Historyczny wynik C2-net-cards na benchmarku v1: 0/10 (poprzedni run, w historii gita).

Surowe pomiary: `experiments/results/last_run.json`; `eval_*.jsonl` to gotowy input dla `basal-calibrate` i pod zbiór ewaluacyjny GEPA.
