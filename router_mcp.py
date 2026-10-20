#!/home/krzysiek/basal-env/bin/python
"""Router MCP server (stdio): recommend, pool, route, models, run.

BASAL_ROUTER_POOL=opencode → recommendations and the pool come from the opencode CLI free models
BASAL_ROUTER_POOL=registry (default) → the full models.json registry pool
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
    """Lists the free models available in this client (the pool)."""
    ids = pool_ids()
    return json.dumps({"pool": POOL, "count": len(ids), "models": ids}, ensure_ascii=False, indent=1)


@mcp.tool()
def recommend(task: str) -> str:
    """Recommends a free model for the task from THIS client's pool; returns the id to switch to."""
    decision = router.route(task)
    data = router.load_registry()
    by_key = {m["key"]: m for m in data["models"]}
    ids = set(pool_ids())
    order = [decision["model"]] + [r["model"] for r in decision.get("ranking", [])]
    chosen = next((k for k in order if k in by_key and by_key[k]["id"] in ids),
                  data.get("default_model", data["models"][0]["key"]))
    entry = by_key[chosen]
    runner = entry.get("runner", "opencode")
    if POOL == "opencode":
        how = f"opencode: /model {entry['id']}"
    else:
        how = ("kilo: models outside Kilo are reachable only through this router — use `router/auto` "
               "in the model picker or the `kilo-router_run` tool; opencode free models have no direct "
               "API (opencode CLI only)")
    return json.dumps({
        "pool": POOL,
        "switch_to": entry["id"],
        "model_key": chosen,
        "runner": runner,
        "execution": "opencode CLI (the only way outside opencode)" if runner == "opencode"
                     else "direct provider API with your key from providers.json",
        "how": how,
        "deferred": decision["deferred"],
        "confidence": decision["confidence"],
        "ranking": [{"key": r["model"], "id": r["model_id"], "probability": r["probability"]}
                    for r in decision.get("ranking", [])],
    }, ensure_ascii=False, indent=1)


@mcp.tool()
def models() -> str:
    """Lists the registry models; the cards describe which tasks each model fits."""
    return json.dumps(
        [{k: m[k] for k in ("key", "id", "name", "card", "runner")}
         for m in router.load_registry()["models"]],
        ensure_ascii=False, indent=2,
    )


@mcp.tool()
def route(task: str) -> str:
    """Picks the optimal free model for the task. Returns model, confidence, ranking and the deferred flag."""
    return json.dumps(router.route(task), ensure_ascii=False, indent=2)


@mcp.tool()
def run(task: str, model: str = "", timeout_s: int = 600) -> str:
    """Runs the task on the chosen model: free ones via opencode CLI, others via the key from providers.json."""
    key = model.strip() or router.route(task)["model"]
    try:
        return router.execute(key, task, timeout_s)
    except Exception as exc:
        return json.dumps({"error": str(exc), "model": key, "hint": router.NO_MODELS_HINT},
                          ensure_ascii=False)


if __name__ == "__main__":
    mcp.run()
