#!/home/krzysiek/basal-env/bin/python
"""Serwer MCP routera modeli (stdio): route, models, run."""
import json

from mcp.server.mcpserver import MCPServer

import router

mcp = MCPServer("kilo-router")


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
