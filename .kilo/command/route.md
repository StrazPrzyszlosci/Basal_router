---
description: Wybierz model routerem basal i wykonaj zadanie
---
Przekaż zadanie narzędziu MCP `kilo-router_recommend` z tekstem zadania: "$ARGUMENTS".
Przedstaw użytkownikowi rekomendowany model (pola switch_to i how), confidence oraz trzy najlepsze pozycje rankingu.
Jeśli deferred=true, powiedz, że router nie był pewny i rekomenduje model domyślny.
Jeśli użytkownik chce od razu wykonać zadanie, użyj `kilo-router_run` z parametrem model = model_key z rekomendacji.
