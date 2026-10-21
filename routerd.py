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
import subprocess
import sys
import time
import uuid
from pathlib import Path

import httpx
import uvicorn
from starlette.applications import Starlette
from starlette.concurrency import run_in_threadpool
from starlette.responses import HTMLResponse, JSONResponse, RedirectResponse, StreamingResponse
from starlette.routing import Route

import router

try:
    import performance  # noqa: E402  (passive speed measurement; router.py already put opt/ on sys.path)
except Exception:
    performance = None

PORT = 8788
VIRTUAL_MODEL = "router/auto"
PANEL_HTML = Path(__file__).resolve().parent / "panel.html"
NAME_RE = re.compile(r"^[a-z0-9-]{1,32}$")


def task_text(messages):
    """Full, untrimmed task text — this is what the target model executes (roles preserved)."""
    parts = []
    for message in messages:
        role = message.get("role", "user")
        content = message.get("content", "")
        if isinstance(content, list):
            content = " ".join(p.get("text", "") for p in content if isinstance(p, dict))
        parts.append(f"[{role}] {content}")
    return "\n".join(parts).strip()


def trim_for_routing(text):
    """Basal (ollama mode) reads at most 4,096 tokens of state — trim head+tail for classification only."""
    if len(text) > 8000:
        return text[:5000] + "\n(...)\n" + text[-2500:]
    return text


def resolve_requested(model_name):
    if not model_name or model_name in (VIRTUAL_MODEL, "auto", "router/free-best"):
        return None
    name = model_name.removeprefix("router/")
    for entry in router.load_registry()["models"]:
        if name in (entry["key"], entry["id"]):
            return entry["key"]
    return None


async def list_models(request):
    usable = {m["key"] for m in router.usable_models()}
    data = [{"id": VIRTUAL_MODEL, "object": "model", "owned_by": "kilo-router", "available": True}]
    for entry in router.load_registry()["models"]:
        data.append({"id": f"router/{entry['key']}", "object": "model", "owned_by": "kilo-router",
                     "available": entry["key"] in usable})
    return JSONResponse({"object": "list", "data": data})


async def health(request):
    return JSONResponse({"status": "ok"})


def build_footer(decision):
    by_key = {m["key"]: m for m in router.load_registry()["models"]}
    entry = by_key.get(decision.get("model")) or {}
    model_id = entry.get("id", decision.get("model"))
    if decision.get("forced"):
        why = "forced by the request (model=router/<key>)"
    else:
        card = (entry.get("card") or "").split(". ")[0]
        why = f'basal matched the task to its registry card ("{card}")'
        if decision.get("deferred"):
            why = "confidence below the floor, the default model was used; " + why
    alternatives = ", ".join(
        f"{r['model']} {r['probability']:.2f}"
        for r in decision.get("ranking", [])[:3] if r["model"] != decision.get("model"))
    footer = (f"---\n[router] answered by {model_id} "
              f"(confidence {decision.get('confidence', 0):.2f}). Why: {why}.")
    if alternatives:
        footer += f" Alternatives considered: {alternatives}."
    return footer


def save_settings_file(settings):
    router.SETTINGS_PATH.write_text(json.dumps(settings, ensure_ascii=False, indent=2) + "\n",
                                     encoding="utf-8")
    os.chmod(router.SETTINGS_PATH, 0o600)


async def panel_get_settings(request):
    if not panel_guard(request):
        return JSONResponse({"error": "forbidden"}, status_code=403)
    settings = router.load_settings()
    return JSONResponse({"settings": {
        "answer_footer": settings.get("answer_footer", True),
        "log_decisions": settings.get("log_decisions", True),
        "confidence_floor": settings.get("confidence_floor"),
        "default_model": settings.get("default_model"),
        "fallback_enabled": settings.get("fallback_enabled", True),
        "exec_timeout_s": settings.get("exec_timeout_s", 600),
        "state_lang": settings.get("state_lang", "pl"),
        "routing_instructions": settings.get("routing_instructions", ""),
        "model_autorefresh": settings.get("model_autorefresh", True),
        "hermes_model": settings.get("hermes_model") or "auto",
        "hermes_enabled": settings.get("hermes_enabled", False),
        "measure_performance": settings.get("measure_performance", True),
    }})


async def panel_save_settings(request):
    if not panel_guard(request):
        return JSONResponse({"error": "forbidden"}, status_code=403)
    body = await request.json()
    current = router.load_settings()
    if "answer_footer" in body:
        current["answer_footer"] = bool(body["answer_footer"])
    if "log_decisions" in body:
        current["log_decisions"] = bool(body["log_decisions"])
    if "confidence_floor" in body:
        try:
            current["confidence_floor"] = float(body["confidence_floor"])
        except (TypeError, ValueError):
            return JSONResponse({"error": "confidence_floor must be a number"}, status_code=400)
    if "fallback_enabled" in body:
        current["fallback_enabled"] = bool(body["fallback_enabled"])
    if "exec_timeout_s" in body:
        try:
            value = float(body["exec_timeout_s"])
        except (TypeError, ValueError):
            return JSONResponse({"error": "exec_timeout_s must be a number"}, status_code=400)
        if not 30 <= value <= 3600:
            return JSONResponse({"error": "exec_timeout_s must be between 30 and 3600"}, status_code=400)
        current["exec_timeout_s"] = value
    if "state_lang" in body:
        if body["state_lang"] not in ("pl", "en"):
            return JSONResponse({"error": "state_lang must be 'pl' or 'en'"}, status_code=400)
        current["state_lang"] = body["state_lang"]
    if "routing_instructions" in body:
        text = str(body["routing_instructions"] or "")
        if len(text) > 4000:
            return JSONResponse({"error": "routing_instructions too long (max 4000 chars)"}, status_code=400)
        current["routing_instructions"] = text
    if "default_model" in body:
        key = str(body["default_model"] or "").strip()
        if key and key not in {m["key"] for m in router.load_registry()["models"]}:
            return JSONResponse({"error": f"unknown model key: {key}"}, status_code=400)
        current["default_model"] = key or None
    if "model_autorefresh" in body:
        current["model_autorefresh"] = bool(body["model_autorefresh"])
    if "hermes_model" in body:
        value = str(body["hermes_model"] or "auto")
        if value not in ("auto", "opencode-auto") and value not in {
                m["key"] for m in router.load_registry()["models"]}:
            return JSONResponse({"error": f"unknown hermes model: {value}"}, status_code=400)
        current["hermes_model"] = "auto" if value in ("auto",) else value
    if "hermes_enabled" in body:
        current["hermes_enabled"] = bool(body["hermes_enabled"])
    if "measure_performance" in body:
        current["measure_performance"] = bool(body["measure_performance"])
    save_settings_file(current)
    return await panel_get_settings(request)


async def chat_completions(request):
    body = await request.json()
    messages = body.get("messages", [])
    full_task = task_text(messages)
    routing_state = trim_for_routing(full_task)
    forced = resolve_requested(body.get("model", ""))
    try:
        if forced:
            decision = {"model": forced, "chosen_by_basal": forced,
                         "confidence": 1.0, "deferred": False, "forced": True,
                         "ranking": []}
        else:
            decision = await run_in_threadpool(router.route, routing_state)
        settings = router.load_settings()
        timeout_s = settings.get("exec_timeout_s", 600)
        candidates = [decision["model"]]
        if settings.get("fallback_enabled", True):
            candidates += [
                r["model"] for r in decision.get("ranking", [])
                if r["model"] != decision["model"]
            ][:2]
        content = None
        fallback_errors = []
        for key in candidates:
            try:
                content = await run_in_threadpool(router.execute, key, full_task, timeout_s,
                                                    messages=messages)
                decision["model"] = key
                break
            except Exception as exc:
                fallback_errors.append(f"{key}: {exc}")
        if content is None:
            return JSONResponse(
                {"error": {"message": "; ".join(fallback_errors) or "no output",
                           "type": "router_error", "hint": router.NO_MODELS_HINT}},
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
    if router.load_settings().get("answer_footer", True):
        content = f"{content}\n\n{build_footer(decision)}"
        extra["footer_appended"] = True
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
    if request.method in ("POST", "DELETE") and request.headers.get("x-panel") != "1":
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


def save_registry_file(data):
    router.REGISTRY_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n",
                                     encoding="utf-8")


async def panel_delete_provider(request):
    if not panel_guard(request):
        return JSONResponse({"error": "forbidden"}, status_code=403)
    name = request.path_params["name"]
    if not NAME_RE.match(name):
        return JSONResponse({"error": "bad name"}, status_code=400)
    dependent = [m["key"] for m in router.load_registry()["models"] if m.get("provider") == name]
    if dependent:
        return JSONResponse({"error": "registry models still use this provider: "
                                      + ", ".join(dependent), "remove_first": dependent}, status_code=409)
    providers = router.load_providers()
    if name not in providers:
        return JSONResponse({"error": "unknown provider"}, status_code=404)
    del providers[name]
    save_providers(providers)
    return JSONResponse({"deleted": name})


async def panel_provider_catalog(request):
    if not panel_guard(request):
        return JSONResponse({"error": "forbidden"}, status_code=403)
    name = request.path_params["name"]
    cfg = router.load_providers().get(name) or {}
    if not cfg.get("apiKey"):
        return JSONResponse({"error": "no saved key for this provider"}, status_code=409)
    try:
        response = httpx.get(f"{cfg['baseURL'].rstrip('/')}/models",
                             headers={"Authorization": f"Bearer {cfg['apiKey']}"},
                             timeout=30)
        response.raise_for_status()
        raw = response.json()
        raw = raw.get("data", raw) if isinstance(raw, dict) else raw
    except Exception as exc:
        return JSONResponse({"error": f"catalog fetch failed: {str(exc)[:150]}"}, status_code=502)
    registry_ids = {m["id"] for m in router.load_registry()["models"]}
    models = [{"id": m.get("id"), "owned_by": m.get("owned_by", ""),
               "imported": f"{name}/{m.get('id')}" in registry_ids}
              for m in raw if isinstance(m, dict) and m.get("id")]
    models.sort(key=lambda m: m["id"])
    return JSONResponse({"provider": name, "models": models[:100]})


async def panel_registry(request):
    if not panel_guard(request):
        return JSONResponse({"error": "forbidden"}, status_code=403)
    data = router.load_registry()
    usable = {m["key"] for m in router.usable_models()}
    speed = performance.stats() if performance else {}
    models = [{"key": m["key"], "id": m["id"], "runner": m.get("runner", "opencode"),
               "provider": m.get("provider"), "usable": m["key"] in usable,
               "speed": speed.get(m["key"]) or None,
               "card": m["card"][:120]} for m in data["models"]]
    return JSONResponse({"default_model": data.get("default_model"),
                         "confidence_floor": data.get("confidence_floor"),
                         "capacity": 20, "count": len(models), "models": models})


async def panel_registry_import(request):
    if not panel_guard(request):
        return JSONResponse({"error": "forbidden"}, status_code=403)
    body = await request.json()
    name = str(body.get("provider", "")).strip().lower()
    model_id = str(body.get("model", "")).strip()
    if not NAME_RE.match(name):
        return JSONResponse({"error": "bad provider name"}, status_code=400)
    if not model_id or len(model_id) > 120 or any(c.isspace() for c in model_id):
        return JSONResponse({"error": "bad model id"}, status_code=400)
    providers = router.load_providers()
    if name not in providers:
        return JSONResponse({"error": "unknown provider"}, status_code=404)
    if not providers[name].get("apiKey"):
        return JSONResponse({"error": "no saved key for this provider"}, status_code=409)
    data = router.load_registry()
    if len(data["models"]) >= 20:
        return JSONResponse({"error": "registry full (max 20 options — an ollama-mode limit)"},
                            status_code=409)
    entry_id = f"{name}/{model_id}"
    if any(m["id"] == entry_id for m in data["models"]):
        return JSONResponse({"error": "already in the registry"}, status_code=409)
    existing_keys = {m["key"] for m in data["models"]}
    key = re.sub(r"[^a-z0-9]+", "-", model_id.lower()).strip("-")[:32] or "imported"
    if key in existing_keys:
        key = f"{key}-{name}"[:32]
    if key in existing_keys:
        return JSONResponse({"error": "cannot derive a unique key"}, status_code=409)
    data["models"].append({
        "key": key, "id": entry_id, "name": model_id,
        "card": router.build_catalog_card(name, model_id),
        "runner": "direct", "provider": name, "model": model_id,
        "tags": ["direct", name],
        "meta": {"card_source": "panel import (models.dev or template)"},
    })
    save_registry_file(data)
    return JSONResponse({"imported": {"key": key, "id": entry_id}, "count": len(data["models"])})


async def panel_registry_remove(request):
    if not panel_guard(request):
        return JSONResponse({"error": "forbidden"}, status_code=403)
    body = await request.json()
    key = str(body.get("key", "")).strip()
    data = router.load_registry()
    settings = router.load_settings()
    if key == data.get("default_model") or key == settings.get("default_model"):
        return JSONResponse({"error": "cannot remove the default model"}, status_code=409)
    if key not in {m["key"] for m in data["models"]}:
        return JSONResponse({"error": "unknown key"}, status_code=404)
    data["models"] = [m for m in data["models"] if m["key"] != key]
    save_registry_file(data)
    return JSONResponse({"removed": key, "count": len(data["models"])})


async def panel_discovery_status(request):
    if not panel_guard(request):
        return JSONResponse({"error": "forbidden"}, status_code=403)
    path = Path(__file__).resolve().parent / "opt" / "discovery" / "last_scan.json"
    if not path.exists():
        return JSONResponse({"never": True})
    return JSONResponse(json.loads(path.read_text(encoding="utf-8")))


async def panel_discovery_refresh(request):
    if not panel_guard(request):
        return JSONResponse({"error": "forbidden"}, status_code=403)
    script = Path(__file__).resolve().parent / "opt" / "discovery.py"
    log = script.parent / "discovery" / "refresh.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    with log.open("a") as handle:
        subprocess.Popen([sys.executable, str(script), "refresh"],
                          stdout=handle, stderr=subprocess.STDOUT, start_new_session=True)
    return JSONResponse({"started": True})


async def root(request):
    return RedirectResponse("/panel")


app = Starlette(routes=[
    Route("/", root),
    Route("/v1/models", list_models),
    Route("/v1/chat/completions", chat_completions, methods=["POST"]),
    Route("/health", health),
    Route("/panel", panel_page),
    Route("/panel/api/state", panel_state),
    Route("/panel/api/settings", panel_get_settings),
    Route("/panel/api/settings", panel_save_settings, methods=["POST"]),
    Route("/panel/api/providers", panel_add_provider, methods=["POST"]),
    Route("/panel/api/providers/{name}", panel_save_provider, methods=["POST"]),
    Route("/panel/api/providers/{name}", panel_delete_provider, methods=["DELETE"]),
    Route("/panel/api/providers/{name}/catalog", panel_provider_catalog),
    Route("/panel/api/registry", panel_registry),
    Route("/panel/api/registry/import", panel_registry_import, methods=["POST"]),
    Route("/panel/api/registry/remove", panel_registry_remove, methods=["POST"]),
    Route("/panel/api/discovery", panel_discovery_status),
    Route("/panel/api/discovery/refresh", panel_discovery_refresh, methods=["POST"]),
    Route("/panel/api/providers/{name}/test", panel_test_provider, methods=["POST"]),
])


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=PORT, log_level="warning")
