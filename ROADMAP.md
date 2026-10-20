# Router darmowych modeli na Basal 2.2

Basal 2.2 (basal-2.0-mini 1.5B Q8_0 przez Ollamę) decyduje, który darmowy model ma obsłużyć zadanie.
Rejestr „ulubionych" modeli edytujesz w `models.json` (maks. 20 opcji — tyle odczytuje tryb ollama).

## Architektura

```
Kilo Code (agenci, /route) ──MCP stdio──> router_mcp.py
                                             │  route / models / run
                                             │  HTTP POST /v1/systemone
                                     basal-serve :8000 (decyzje choice, kalibracja)
                                             │  raw prompt + logprobs
                                     ollama serve :11434 (pdurlej/basal:2.0-1.5b, 1.7 GB)
                                             │
                                     router.py loguje decyzje ──> decisions.jsonl
                                     (po uzupełnieniu gold ──> basal-calibrate)
                                             │
                                     opencode run -m <model> (wykonanie zadania)
```

## Pliki

| Plik | Rola |
|---|---|
| `models.json` | rejestr modeli: klucz, id opencode, karta (to ją „widzi" basal), runner |
| `router.py` | route/models/annotate; loguje decyzje w formacie `basal-calibrate` |
| `router_mcp.py` | serwer MCP `kilo-router` (stdio): `route`, `models`, `run` |
| `kilo.json` | rejestracja MCP w Kilo (projekt) + uprawnienia narzędzi |
| `.kilo/command/route.md` | komenda `/route <zadanie>` |
| `decisions.jsonl` | log decyzji; pole `gold` uzupełniasz po fakcie |

## Użycie

```bash
~/basal-env/bin/python router.py route "napraw bug: pętla w bashu się nie kończy"
~/basal-env/bin/python router.py models
~/basal-env/bin/python router.py annotate        # uzupełnij gold w decisions.jsonl
```

W Kilo (po restarcie sesji w tym katalogu): `/route <zadanie>` albo narzędzia `kilo-router_route` / `kilo-router_run`.

## Endpoint OpenAI-compatible: routerd (127.0.0.1:8788, model "auto")

`routerd.py` to jeden punkt wejścia zgodny z OpenAI API — dla opencode, hermes, Kilo i wszystkiego, co mówi po OpenAI:

- `POST /v1/chat/completions` z `model: "auto"` → basal wybiera model z rejestru, routerd wykonuje
  (darmowe: `opencode run -m <id>`; własne klucze: bezpośrednio u providera z `providers.json`) i zwraca odpowiedź OpenAI
- `model: "router/<klucz>"` wymusza konkretny model; pole `router` w odpowiedzi niesie decyzję basal
  (chosen_by_basal, confidence, deferred, executed_model, fallback_errors)
- **fallback**: gdy wykonanie padnie, routerd bierze kolejne modele z rankingu basal (do 2 prób zapasowych)
- streaming jest emulowany (cała treść w jednym chunku)
- długi kontekst: do basal idzie skrócony opis zadania (głowa+ogon); pełna treść leci do modelu docelowego

Uruchamianie: `~/basal-env/bin/python routerd.py` (usługa lokalna, nasłuch tylko 127.0.0.1, klucze nie logowane).
Port 8788, bo 8787 zajęty przez Hermes WebUI (`~/.hermes/hermes-webui/server.py`).

**Klucze**: `providers.json` (chmod 600, nie commitować) — `{nazwa: {baseURL, apiKey}}`; puste apiKey wyłącza providera.

**Kilo jako klient**: `kilo.json` projektu ma provider `router` → w pickerze `router/auto`.
**opencode/hermes jako klient** — Custom/OpenAI-compatible endpoint `http://127.0.0.1:8788/v1`, model `auto`:

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

**Benchmarky w kartach**: skrypt sync z metrykami (models.dev, OpenRouter API) dopisze je do kart
`models.json` — bez ręcznego wymyślania liczb; potem GEPA douczy karty na Twoich danych.

## Stan vs pełna wizja (dokument „Basal Router")

| Element wizji | Stan |
|---|---|
| Jeden endpoint OpenAI-compat, model `auto` | **działa** (routerd, 8788) |
| Basal klasyfikuje zadanie i wybiera model | **działa** (choice z kart + log decyzji) |
| Darmowe modele przez opencode CLI | **działa** (runner opencode) |
| Własne klucze (NIM itd.) w bezpiecznym magazynie | **szkielet** (providers.json chmod 600, 127.0.0.1) |
| Fallback na błędy | **podstawowy** (kolejne z rankingu basal; bez mid-stream switching — streaming jest emulowany) |
| Katalog modeli z benchmarkami | następny krok (sync models.dev / OpenRouter) |
| Scheduler limitów RPM/TPM, 429 + Retry-After | do zrobienia |
| Uczenie się wydajności (opóźnienia, błędy, jakość) | zbieranie od dziś (decisions.jsonl) → GEPA/kalibracja |
| Panel WWW — klucze providerów | **działa** (`/panel` w routerd: zapis chmod 600, maskowanie, test klucza, dodawanie providerów) |
| Panel WWW — historia, limity, statystyki | do zrobienia (FastAPI + SQLite) |
| LiteLLM ≥1.83.9 | odroczone: NIM/OpenRouter/Groq/Cerebras są OpenAI-compat (wystarcza httpx); rozważyć przy providerach niekompatybilnych lub złożonych fallbackach |
| Blokada płatnych wywołań | rejestr zawiera tylko darmowe/własne; egzekwowanie per-provider w fazie schedulerowej |

## Pomiary na i3-10110U (2C/4T, RAM mocno obciążony)

- basal-serve start: 8 s; pierwszy request (zimny): 18,5 s; ten sam stan (cache): 8,3 s
- `llama-server` (Ollama): ~1,1 GB RSS; plik modelu 1,7 GB
- decyzja routera (1 pytanie choice, 12 opcji): patrz `latency_ms` w odpowiedzi

## Ograniczenia (tryb ollama)

- maks. 20 opcji na pytanie (limit logprobów Ollamy); >20 modeli = routing dwustopniowy
- kontekst 4 096 tokenów: bardzo długi opis zadania jest przycinany w środku
- wysłana kalibracja jest ostrożna: cel 1% odracza wszystko; używaj progu 5% lub własnej kalibracji

## Faza 1 — kalibracja na własnych danych (wbudowane w 2.2)

1. Zbieraj decyzje: `router.py route ...` (albo MCP) — każdy wpis ląduje w `decisions.jsonl`.
2. Uzupełnij `gold` (który model faktycznie byłby najlepszy): `router.py annotate`.
3. Dopasuj progi i temperatury:
   ```bash
   ~/basal-env/bin/basal-calibrate decisions.jsonl \
     --model Remek/basal-2.0-mini-GGUF --mode ollama \
     --ollama-model pdurlej/basal:2.0-1.5b
   ```
4. Podaj plik serwerowi: `basal-serve ... --calibration CALIBRATION.custom.json`
   (i podnieś `confidence_floor` w `models.json` do certyfikowanego progu).

Kilka etykiet zmienia temperatury, kilkaset certyfikuje próg 5% (dokumentacja Basal 2.2).

## Faza 2 — GEPA (github.com/gepa-ai/gepa)

GEPA ewoluuje prompty z użyciem feedbacku z wykonania. Tu optymalizuje dwie rzeczy,
iterując na `decisions.jsonl` z goldem jako zbiorze ewaluacyjnym (metryka: trafność routingu):
- `INSTRUCTIONS` w `router.py` (instrukcja wyboru modelu),
- karty modeli w `models.json` (to jest prompt, który basal czyta — mutacje kart
  poprawiające trafność zapisujesz z powrotem do rejestru).

## Faza 3 — OpenEvolve (github.com/codelion/openevolve)

Ewolucja kodu w stylu AlphaEvolve: pozwól mu ewoluować `router.py` + `models.json`
jako „genom" z fitnessem = trafność na `decisions.jsonl` (plus kary za czas/odroczenia).
Cięższe niż GEPA, sensowne gdy zbierze się kilkaset dobrych etykiet.

## Faza 4 — LangGraph (orkiestracja)

Graf: `route -> run (opencode) -> verify` — weryfikacja wyniku pytaniem `noul`
za generatorem (wzorzec „compress -> generate -> verify" z docs/TASKS.md Basal),
retry z mocniejszym modelem gdy verify odrzuci. Plus równoległe ścieżki
(dwa modele, potem basal `score` wybiera lepszą odpowiedź).

## Uwagi

- „Abstral" nie udało się zidentyfikować (GitHub/PyPI) — doprecyzuj, co masz na myśli.
- Ulubione modeli Kilo nie są zapisane w `kilo.json` (to stan UI) — są w `models.json`;
  modele bramy Kilo dodaj z `runner: "kilo"` (route je wskaże, przełączenie ręczne).
- `qwen-local` wymaga pobrania do Ollamy (`ollama pull`), obecnie w Ollamie jest tylko basal.
- Kilo Code **nie ma natywnego routera modeli** (grep po rozszerzeniu 7.8.15: zero wystąpień
  modelRouting/modelRouter/fallbackModel/autoModel; w SDK tylko pole `small_model`). Routing, który widać
  w pickerze Kilo w tym katalogu, to nasz `router/auto`. Kod rozszerzenia jest open source (MIT,
  github.com/Kilo-Org/kilocode), ale nie ma w nim logiki decyzyjnej wartej przenoszenie — kierunek integracji
  jest odwrotny: to Kilo konsumuje naszego routera (provider baseURL + MCP).
