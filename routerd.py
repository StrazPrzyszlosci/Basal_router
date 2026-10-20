#!/home/krzysiek/basal-env/bin/python
"""Router as a single OpenAI-compatible endpoint (127.0.0.1:8788).

POST /v1/chat/completions: basal picks a model from the registry, routerd executes the task
(via opencode CLI, or straight to a provider from providers.json) and returns the answer in
OpenAI format. model="router/auto" = full routing, model="router/<key from models.json>" forces
a specific model. GET /panel serves the provider-key management UI.
"""
import json
import os
import re
import time
import uuid
from pathlib import Path

import httpx
import uvicorn
from starlette.applications import Starlette
from starlette.concurrency import run_in_threadpool
from starlette.responses import HTMLResponse, JSONResponse, StreamingResponse
from starlette.routing import Route

import router

PORT = 8788
VIRTUAL_MODEL = "router/auto"
PANEL_HTML = Path(__file__).resolve().parent / "panel.html"
NAME_RE = re.compile(r"^[a-z0-9-]{1,32}$")


def task_from_messages(messages):
    parts = []
    for message in messages:
        role = message.get("role", "user")
        content = message.get("content", "")
        if isinstance(content, list):
            content = " ".join(p.get("text", "") for p in content if isinstance(p, dict))
        parts.append(f"[{role}] {content}")
    task = "\n".join(parts).strip()
    if len(task) > 8000:
        task = task[:5000] + "\n(...)\n" + task[-2500:]
    return task


def resolve_requested(model_name):
    if not model_name or model_name in (VIRTUAL_MODEL, "auto", "router/free-best"):
        return None
    name = model_name.removeprefix("router/")
    for entry in router.load_registry()["models"]:
        if name in (entry["key"], entry["id"]):
            return entry["key"]
    return None


async def list_models(request):
    data = [{"id": VIRTUAL_MODEL, "object": "model", "owned_by": "kilo-router"}]
    for entry in router.load_registry()["models"]:
        data.append({"id": f"router/{entry['key']}", "object": "model", "owned_by": "kilo-router"})
    return JSONResponse({"object": "list", "data": data})


async def health(request):
    return JSONResponse({"status": "ok"})


async def chat_completions(request):
    body = await request.json()
    task = task_from_messages(body.get("messages", []))
    forced = resolve_requested(body.get("model", ""))
    try:
        if forced:
            decision = {"model": forced, "chosen_by_basal": forced,
                         "confidence": 1.0, "deferred": False, "forced": True,
                         "ranking": []}
        else:
            decision = await run_in_threadpool(router.route, task)
        candidates = [decision["model"]] + [
            r["model"] for r in decision.get("ranking", [])
            if r["model"] != decision["model"]
        ][:2]
        content = None
        fallback_errors = []
        for key in candidates:
            try:
                content = await run_in_threadpool(router.execute, key, task, 600)
                decision["model"] = key
                break
            except Exception as exc:
                fallback_errors.append(f"{key}: {exc}")
        if content is None:
            return JSONResponse(
                {"error": {"message": "; ".join(fallback_errors) or "no output",
                           "type": "router_error"}},
                status_code=502,
            )
    except Exception as exc:
        return JSONResponse(
            {"error": {"message": str(exc), "type": "router_error"}},
            status_code=500,
        )
    extra = {k: decision.get(k) for k in ("chosen_by_basal", "confidence", "deferred", "forced")}
    extra["executed_model"] = decision["model"]
    if fallback_errors:
        extra["fallback_errors"] = fallback_errors
    payload = {
        "id": "chatcmpl-" + uuid.uuid4().hex[:12],
        "object": "chat.completion",
        "created": int(time.time()),
        "model": decision["model"],
        "choices": [{
            "index": 0,
            "message": {"role": "assistant", "content": content},
            "finish_reason": "stop",
        }],
        "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
        "router": extra,
    }
    if body.get("stream"):
        chunk_id = payload["id"]

        async def stream():
            for delta, finish in (({"role": "assistant"}, None),
                                  ({"content": content}, None),
                                  ({}, "stop")):
                chunk = {"id": chunk_id, "object": "chat.completion.chunk",
                         "created": payload["created"], "model": payload["model"],
                         "choices": [{"index": 0, "delta": delta, "finish_reason": finish}]}
                yield b"data: " + json.dumps(chunk, ensure_ascii=False).encode() + b"\n\n"
            yield b"data: [DONE]\n\n"

        return StreamingResponse(stream(), media_type="text/event-stream")
    return JSONResponse(payload)


def panel_guard(request):
    host = request.headers.get("host", "")
    if not any(host.startswith(h) for h in ("127.0.0.1", "localhost", "[::1]")):
        return False
    if request.method == "POST" and request.headers.get("x-panel") != "1":
        return False
    return True


def mask_key(key):
    if not key:
        return ""
    if len(key) > 8:
        return "*" * (len(key) - 4) + key[-4:]
    return "*" * len(key)


def save_providers(providers):
    router.PROVIDERS_PATH.write_text(json.dumps(providers, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.chmod(router.PROVIDERS_PATH, 0o600)


def provider_state(name, cfg, registry):
    return {"name": name, "baseURL": cfg.get("baseURL", ""), "hasKey": bool(cfg.get("apiKey")),
            "keyMask": mask_key(cfg.get("apiKey", "")), "probe_model": cfg.get("probe_model", ""),
            "models": [m["key"] for m in registry if m.get("provider") == name]}


async def panel_page(request):
    if not panel_guard(request):
        return JSONResponse({"error": "forbidden"}, status_code=403)
    return HTMLResponse(PANEL_HTML.read_text(encoding="utf-8"))


async def panel_state(request):
    if not panel_guard(request):
        return JSONResponse({"error": "forbidden"}, status_code=403)
    registry = router.load_registry()["models"]
    return JSONResponse({"providers": [
        provider_state(name, cfg, registry)
        for name, cfg in router.load_providers().items() if not name.startswith("_")]})


async def panel_add_provider(request):
    if not panel_guard(request):
        return JSONResponse({"error": "forbidden"}, status_code=403)
    body = await request.json()
    name = str(body.get("name", "")).strip().lower()
    if not NAME_RE.match(name):
        return JSONResponse({"error": "name: lowercase letters, digits, hyphen (max 32)"}, status_code=400)
    providers = router.load_providers()
    if name in providers:
        return JSONResponse({"error": "provider already exists"}, status_code=409)
    providers[name] = {"baseURL": str(body.get("baseURL", "")).strip(),
                      "apiKey": "", "probe_model": str(body.get("probe_model", "")).strip()}
    save_providers(providers)
    return JSONResponse(provider_state(name, providers[name], router.load_registry()["models"]))


async def panel_save_provider(request):
    if not panel_guard(request):
        return JSONResponse({"error": "forbidden"}, status_code=403)
    name = request.path_params["name"]
    if not NAME_RE.match(name):
        return JSONResponse({"error": "bad name"}, status_code=400)
    body = await request.json()
    providers = router.load_providers()
    if name not in providers:
        return JSONResponse({"error": "unknown provider"}, status_code=404)
    providers[name]["apiKey"] = str(body.get("apiKey", ""))
    if str(body.get("baseURL", "")).strip():
        providers[name]["baseURL"] = str(body["baseURL"]).strip()
    if str(body.get("probe_model", "")).strip():
        providers[name]["probe_model"] = str(body["probe_model"]).strip()
    save_providers(providers)
    return JSONResponse(provider_state(name, providers[name], router.load_registry()["models"]))


async def panel_test_provider(request):
    if not panel_guard(request):
        return JSONResponse({"error": "forbidden"}, status_code=403)
    name = request.path_params["name"]
    cfg = router.load_providers().get(name) or {}
    if not cfg.get("apiKey"):
        return JSONResponse({"ok": False, "error": "no saved key"})
    registry = router.load_registry()["models"]
    probe = cfg.get("probe_model") or next(
        (m.get("model", m["id"]) for m in registry if m.get("provider") == name), None)
    if not probe:
        return JSONResponse({"ok": False, "error": "missing probe_model — set it in providers.json"})
    start = time.time()
    try:
        response = httpx.post(
            f"{cfg['baseURL'].rstrip('/')}/chat/completions",
            headers={"Authorization": f"Bearer {cfg['apiKey']}"},
            json={"model": probe, "messages": [{"role": "user", "content": "ping"}], "max_tokens": 1},
            timeout=30,
        )
    except Exception as exc:
        return JSONResponse({"ok": False, "error": str(exc)[:200]})
    latency = round((time.time() - start) * 1000)
    if response.status_code == 200:
        return JSONResponse({"ok": True, "model": probe, "latency_ms": latency})
    return JSONResponse({"ok": False, "model": probe, "status": response.status_code,
                         "error": response.text[:200], "latency_ms": latency})


app = Starlette(routes=[
    Route("/v1/models", list_models),
    Route("/v1/chat/completions", chat_completions, methods=["POST"]),
    Route("/health", health),
    Route("/panel", panel_page),
    Route("/panel/api/state", panel_state),
    Route("/panel/api/providers", panel_add_provider, methods=["POST"]),
    Route("/panel/api/providers/{name}", panel_save_provider, methods=["POST"]),
    Route("/panel/api/providers/{name}/test", panel_test_provider, methods=["POST"]),
])


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=PORT, log_level="warning")
