# Wyniki badań konfiguracji routera

Data: 2026-10-20 06:07 · krzysiek-hpe820g3 (Intel i3-10110U, 4 wątki, RAM pod obciążeniem) · basal-2.0-mini 1.5B Q8_0 przez Ollamę · 10 zadań syntetycznych, 15 opcji w rejestrze

Porównanie źródeł kart modeli: **C1-manual-cards** (karty ręczne), **C2-net-cards** (karty z models.dev).

## Zbiorczo

| konfiguracja | top-1 | śr. pewność trafnych | śr. pewność błędnych | mediana [ms] | p90 [ms] |
|---|---|---|---|---|---|
| C1-manual-cards (karty ręczne) | 0.6 | 0.685 | 0.32 | 36709 | 37553 |
| C2-net-cards (karty z models.dev) | 0.0 | None | 0.172 | 63550 | 69317 |

## Progi automatyzacji (decyzja pewna, gdy confidence ≥ próg)

| konfiguracja | próg | % zautomatyzowanych | błędy wśród zautomatyzowanych |
|---|---|---|---|
| C1-manual-cards | 0.3 | 70% | 1 (rate 0.143) |
| C1-manual-cards | 0.45 | 60% | 1 (rate 0.167) |
| C1-manual-cards | 0.55 | 40% | 1 (rate 0.25) |
| C1-manual-cards | 0.7 | 30% | 0 (rate 0.0) |
| C2-net-cards | 0.3 | 0% | 0 (rate None) |
| C2-net-cards | 0.45 | 0% | 0 (rate None) |
| C2-net-cards | 0.55 | 0% | 0 (rate None) |
| C2-net-cards | 0.7 | 0% | 0 (rate None) |

## Per zadanie

| id | profil | gold | C1-manual-cards wybrane (pewność) | C2-net-cards wybrane (pewność) |
|---|---|---||---||---|
| t01 | code | deepseek-flash | deepseek-flash (0.45) | glm-flash (0.17) |
| t02 | code | deepseek-flash | deepseek-flash (0.37) | nemotron-lightning (0.17) |
| t03 | vision | nemotron-vl | nemotron-vl (0.96) | mimo-flash (0.26) |
| t04 | vision | nemotron-vl | nemotron-vl (0.96) | mimo-flash (0.17) |
| t05 | simple | mimo-flash | mimo-flash (0.45) | nim-nemotron-70b (0.18) |
| t06 | simple | minimax-flash | mimo-flash (0.29) | mimo-flash (0.18) |
| t07 | research | beam | nim-nemotron-70b (0.17) | nim-glm-flash (0.15) |
| t08 | reasoning | nemotron-ultra | nim-nemotron-70b (0.18) | nim-glm-flash (0.14) |
| t09 | long-context | longcat | longcat (0.91) | nemotron-lightning (0.15) |
| t10 | creative | big-pickle | or-free (0.64) | nim-glm-flash (0.15) |

Gold autorowany pod karty ręczne; rozbieżności C2 pokazują, jak zmiana źródła kart zmienia routing — do przeglądu, które goldy warto zrewidować.

Surowe pomiary: `experiments/results/last_run.json`; `eval_*.jsonl` to gotowy input dla `basal-calibrate` (gold w środku) i pod zbiór ewaluacyjny GEPA.
