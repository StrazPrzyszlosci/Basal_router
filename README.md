# Basal Router

> **One model to rule them all.**
>
> Wybierasz **jeden model** — `Basal Router` (`auto`) — we wszystkich aplikacjach (Kilo, opencode, hermes,
> cokolwiek mówiącego po OpenAI). Za nim lokalny klasyfikator [Basal 2.2](https://github.com/rkinas/basal)
> (basal-2.0-mini 1.5B Q8_0 przez Ollamę) dobiera do każdego zadania najlepszy **darmowy** model z rejestru,
> router wykonuje je (opencode CLI albo provider z własnym kluczem) i zwraca odpowiedź przez jeden endpoint
> zgodny z OpenAI API. Zero ręcznego przełączania modeli i providerów: jeden adres, jeden model — wszystkie
> darmowe modele za nim.

Lokalny router modeli AI: decyzje bazują na kalibrowanym prawdopodobieństwie (`confidence`, progi, odraczanie),
każda decyzja jest logowana pod własną kalibrację (`basal-calibrate`) i optymalizację kart (GEPA).

```
aplikacje (Kilo / opencode / hermes / cokolwiek OpenAI-compat)
        │
   http://127.0.0.1:8788/v1   (routerd.py, model "auto")
        │                       ├── fallback: kolejne modele z rankingu basal
        ├── basal-serve :8000  (decyzje choice, kalibracja, /v1/systemone)
        │       └── ollama :11434 (basal-2.0-mini 1.5B Q8_0)
        └── wykonanie: opencode run -m <model> | providers.json (NIM itd., klucze lokalnie)
```

## Pliki

| Plik | Rola |
|---|---|
| `routerd.py` | endpoint OpenAI-compat (`/v1/chat/completions`, `/v1/models`); routing + fallback |
| `panel.html`, `/panel` | panel WWW kluczy providerów: http://127.0.0.1:8788/panel (zapis, maskowanie, test klucza) |
| `router.py` | decyzje przez basal, wykonanie, log decyzji, CLI (`route`/`models`/`annotate`) |
| `router_mcp.py` | serwer MCP `kilo-router` (route/models/run) |
| `models.json` | rejestr produkcyjny: karty **hybrydowe** (rola + fakty z models.dev), runnery; max 20 opcji |
| `models.manual.json` | karty ręczne — baseline badań (C1) |
| `models.net.json` | karty wyłącznie z models.dev — wariant badawczy (C2) |
| `sync_cards.py` | przebudowa kart z danych models.dev |
| `providers.json` | **nie jest w repo** — klucze API providerów, chmod 600 |
| `kilo.json` | integracja z Kilo: provider `router/auto` + MCP + uprawnienia |
| `.kilo/command/route.md` | komenda `/route <zadanie>` |
| `experiments/` | badania konfiguracji; wyniki w `experiments/results/` i `RESULTS.md` |
| `ROADMAP.md` | architektura, fazy (kalibracja, GEPA, OpenEvolve, LangGraph), stan wizji |
| `RESULTS.md` | wyniki badań konfiguracji routera |

## Uruchomienie

```bash
# silnik decyzyjny
basal-serve --model Remek/basal-2.0-mini-GGUF --mode ollama --ollama-model pdurlej/basal:2.0-1.5b --port 8000
# router (endpoint OpenAI-compat)
~/basal-env/bin/python routerd.py
# lub pojedyncza decyzja / MCP dla Kilo
~/basal-env/bin/python router.py route "opis zadania"
~/basal-env/bin/python router_mcp.py
```

Klient: `baseURL http://127.0.0.1:8788/v1`, model `auto` (wymuszenie: `router/<klucz>`).
W odpowiedzi pole `router` niesie decyzję basal: `chosen_by_basal`, `confidence`, `deferred`, `executed_model`, `fallback_errors`.

## Bezpieczeństwo

- `providers.json` (klucze) trzyma chmod 600, poza repo, nigdy nie logowany
- routerd nasłuchuje wyłącznie na 127.0.0.1
- rejestr zawiera tylko modele darmowe/własne; brak egzekwowania płatnych wywołań (patrz ROADMAP)

## Badania

`experiments/run_experiments.py` mierzy dokładność routingu, pewność, opóźnienia i progi automatyzacji na syntetycznym zbiorze zadań (`experiments/benchmark_tasks.jsonl`) w różnych konfiguracjach (karty pełne/skrócone itd.). Wyniki: `RESULTS.md`, surowe pomiary: `experiments/results/` (plus `eval_*.jsonl` — gotowy zbiór pod `basal-calibrate` i GEPA).

## Status

Działa: endpoint, routing, wykonanie przez opencode CLI, fallback, MCP, log decyzji.
W budowie: scheduler limitów, discovery katalogu modeli z benchmarkami, panel WWW, pętla GEPA/kalibracji.
Szczegóły i pomiary na i3-10110U: `ROADMAP.md`, `RESULTS.md`.
