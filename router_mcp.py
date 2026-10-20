#!/home/krzysiek/basal-env/bin/python
"""Serwer MCP routera modeli (stdio): recommend, pool, route, models, run.

BASAL_ROUTER_POOL=opencode → rekomendacja i pula darmowych modeli opencode CLI
BASAL_ROUTER_POOL=registry (domyślnie) → cała pula rejestru models.json
"""
import json
import os
import re
import subprocess

from mcp.server.mcpserver import MCPServer

import router

OPENCODE = "/home/krzysiek/.opencode/bin/opencode"
POOL = os.environ.get("BASAL_ROUTER_POOL", "registry")
FREE_RE = re.compile(r"(-free$|:free$)")
mcp = MCPServer("kilo-router")


def pool_ids():
    models = router.load_registry()["models"]
    if POOL != "opencode":
        return [m["id"] for m in models]
    known = {m["id"] for m in models
             if m.get("runner") == "opencode" and "basal" not in m["id"]}
    try:
        proc = subprocess.run([OPENCODE, "models"], capture_output=True, text=True, timeout=60)
        listed = {line.strip() for line in proc.stdout.splitlines() if line.strip()}
    except Exception:
        listed = set()
    return sorted(known | {x for x in listed if FREE_RE.search(x)})


@mcp.tool()
def pool() -> str:
    """Lista darmowych modeli dostępnych w tym kliencie (pula)."""
    ids = pool_ids()
    return json.dumps({"pool": POOL, "count": len(ids), "models": ids}, ensure_ascii=False, indent=1)


@mcp.tool()
def recommend(task: str) -> str:
    """Rekomenduje darmowy model do zadania z puli TEGO klienta; zwraca id do przełączenia modelu."""
    decision = router.route(task)
    data = router.load_registry()
    by_key = {m["key"]: m for m in data["models"]}
    ids = set(pool_ids())
    order = [decision["model"]] + [r["model"] for r in decision.get("ranking", [])]
    chosen = next((k for k in order if k in by_key and by_key[k]["id"] in ids),
                  data.get("default_model", data["models"][0]["key"]))
    entry = by_key[chosen]
    return json.dumps({
        "pool": POOL,
        "switch_to": entry["id"],
        "model_key": chosen,
        "how": f"opencode: /model {entry['id']}" if POOL == "opencode"
               else "kilo: wybierz model w pickerze (/models) albo router/auto",
        "deferred": decision["deferred"],
        "confidence": decision["confidence"],
        "ranking": [{"key": r["model"], "id": r["model_id"], "probability": r["probability"]}
                    for r in decision.get("ranking", [])],
    }, ensure_ascii=False, indent=1)


@mcp.tool()
def models() -> str:
    """Lista modeli z rejestru routera; karty opisują, do jakich zadań pasuje każdy model."""
    return json.dumps(
        [{k: m[k] for k in ("key", "id", "name", "card", "runner")}
         for m in router.load_registry()["models"]],
        ensure_ascii=False, indent=2,
    )


@mcp.tool()
def route(task: str) -> str:
    """Wybiera optymalny darmowy model do zadania. Zwraca model, confidence, ranking i flagę deferred."""
    return json.dumps(router.route(task), ensure_ascii=False, indent=2)


@mcp.tool()
def run(task: str, model: str = "", timeout_s: int = 600) -> str:
    """Uruchamia zadanie na wybranym modelu: darmowe przez opencode CLI, pozostałe przez klucz z providers.json."""
    key = model.strip() or router.route(task)["model"]
    try:
        return router.execute(key, task, timeout_s)
    except Exception as exc:
        return json.dumps({"error": str(exc), "model": key}, ensure_ascii=False)


if __name__ == "__main__":
    mcp.run()
