---
description: Wybierz model routerem basal i wykonaj zadanie
---
Przekaż zadanie do routera narzędziem MCP `kilo-router_route` z tekstem zadania: "$ARGUMENTS".
Jeśli decyzja nie jest odroczona (deferred=false), uruchom zadanie narzędziem `kilo-router_run` z tekstem zadania i parametrem model ustawionym na wartość "model" z decyzji routera.
Przedstaw użytkownikowi: wybrany model (model_id), confidence, trzy najlepsze pozycje rankingu oraz wynik wykonania zadania.
Jeśli deferred=true, powiedz, że router nie był pewny (confidence poniżej progu), podaj model domyślny z decyzji i zapytaj, czy go użyć.
