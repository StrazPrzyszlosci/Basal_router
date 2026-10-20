#!/home/krzysiek/basal-env/bin/python
"""Router jako jeden endpoint zgodny z OpenAI API (127.0.0.1:8081).

POST /v1/chat/completions: basal wybiera model z rejestru, routerd wykonuje
zadanie (opencode CLI albo bezpośrednio u providera z providers.json) i zwraca
odpowiedź w formacie OpenAI. model="router/free-best" = pełny routing,
model="router/<klucz z models.json>" = wymuszenie konkretnego modelu.
"""
import json
import time
import uuid

import uvicorn
from starlette.applications import Starlette
from starlette.concurrency import run_in_threadpool
from starlette.responses import JSONResponse, StreamingResponse
from starlette.routing import Route

import router

PORT = 8788
VIRTUAL_MODEL = "router/auto"


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
                {"error": {"message": "; ".join(fallback_errors) or "brak wyniku",
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


app = Starlette(routes=[
    Route("/v1/models", list_models),
    Route("/v1/chat/completions", chat_completions, methods=["POST"]),
    Route("/health", health),
])


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=PORT, log_level="warning")
