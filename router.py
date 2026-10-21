#!/home/krzysiek/basal-env/bin/python
"""Free-model router on the Basal 2.2 decision engine (local basal-serve)."""
import json
import os
import subprocess
import sys
import time
import uuid
from pathlib import Path

import httpx

DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(DIR / "opt"))
try:
    import performance  # noqa: E402  (passive speed measurement; import-safe, no router import)
except Exception:
    performance = None
REGISTRY_PATH = DIR / "models.json"
LOG_PATH = DIR / "decisions.jsonl"
BASAL_URL = os.environ.get("BASAL_URL", "http://127.0.0.1:8000")
PROVIDERS_PATH = DIR / "providers.json"
SETTINGS_PATH = DIR / "settings.json"
OPENCODE = os.environ.get("OPENCODE_BIN", "/home/krzysiek/.opencode/bin/opencode")
HERMES = os.environ.get("HERMES_BIN", "/home/krzysiek/.local/bin/hermes")
HERMES_SANDBOX = DIR / "opt" / "hermes_sandbox" / "home"
NO_MODELS_HINT = ("No model could be executed. opencode free models require an active opencode session "
                  "(check `opencode auth list`); provider-key models (nim-*) need a key saved at "
                  "http://127.0.0.1:8788/panel (providers.json).")
INSTRUCTIONS = (
    "Choose the model that best handles the task described in the state. Consider the task type "
    "(code, text, image, research, long document), the required depth of reasoning, speed and cost. "
    "For simple tasks choose the fastest sufficient model."
)


def load_registry():
    data = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
    if len(data["models"]) > 20:
        raise SystemExit("ollama mode reads at most 20 options: trim models.json")
    return data


def load_settings():
    if SETTINGS_PATH.exists():
        try:
            return json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def usable_models(data=None):
    """Registry entries executable right now: opencode models always, direct models only with a
    saved key — minus models whose last calls all failed recently (availability cooldown)."""
    data = data or load_registry()
    settings = load_settings()
    skip_failing = settings.get("skip_recently_failing", True)
    providers = load_providers()
    usable = []
    for m in data["models"]:
        if m.get("runner", "opencode") == "direct":
            if not (providers.get(m.get("provider")) or {}).get("apiKey"):
                continue
        if skip_failing and performance is not None and performance.recently_failing(m["key"]):
            continue
        usable.append(m)
    return usable


def build_request(task, models, gold=None, instructions=None, lang="pl"):
    criteria = {m["key"]: f"{m['name']}. {m['card']}" for m in models}
    question = {"type": "choice", "instructions": instructions or INSTRUCTIONS, "criteria": criteria}
    if gold is not None:
        question["gold"] = gold
    return {"state": task, "lang": lang, "questions": {"model": question}}


DEVS_CACHE = Path("/tmp/kilo/modelsdev.json")


def load_modelsdev(max_age_days=7):
    if DEVS_CACHE.exists() and time.time() - DEVS_CACHE.stat().st_mtime < max_age_days * 86400:
        try:
            return json.loads(DEVS_CACHE.read_bytes())
        except Exception:
            pass
    try:
        data = httpx.get("https://models.dev/api.json", timeout=120).json()
        DEVS_CACHE.parent.mkdir(parents=True, exist_ok=True)
        DEVS_CACHE.write_bytes(json.dumps(data).encode())
        return data
    except Exception:
        return {}


def build_catalog_card(provider_name, model_id):
    """Best-effort card for an imported model: real data from models.dev, else a minimal template."""
    dev = load_modelsdev()
    entry = ((dev.get(provider_name) or {}).get("models") or {}).get(model_id)
    if entry is None:
        for prov in dev.values():
            if model_id in (prov.get("models") or {}):
                entry = prov["models"][model_id]
                break
    if not entry:
        return (f"Model served by your own {provider_name} key. "
                "Edit this card in models.json if routing needs more detail.")
    limit = entry.get("limit") or {}
    cost = entry.get("cost") or {}
    parts = [f"Context: {limit.get('context') or '?'}, output: {limit.get('output') or '?'}."]
    if cost.get("input") == 0 and cost.get("output") == 0:
        parts.append("Free.")
    caps = []
    if entry.get("reasoning"):
        caps.append("deep reasoning")
    if entry.get("tool_call"):
        caps.append("tools (function calling)")
    if "image" in (entry.get("modalities") or {}).get("input", []):
        caps.append("images (vision)")
    if caps:
        parts.append("Capabilities: " + "; ".join(caps) + ".")
    description = " ".join((entry.get("description") or "").split())
    if description:
        parts.append(description[:160] + ("…" if len(description) > 160 else ""))
    return " ".join(parts)


def route(task):
    data = load_registry()
    settings = load_settings()
    models = usable_models(data)
    if not models:
        raise RuntimeError(NO_MODELS_HINT)
    by_key = {m["key"]: m for m in models}
    instructions = settings.get("routing_instructions") or INSTRUCTIONS
    lang = settings.get("state_lang") or "pl"
    response = httpx.post(f"{BASAL_URL}/v1/systemone",
                          json=build_request(task, models, instructions=instructions, lang=lang), timeout=180)
    response.raise_for_status()
    body = response.json()
    answer = body["answers"]["model"]
    ranking = sorted(answer["probabilities"].items(), key=lambda item: -item[1])
    confidence = answer["confidence"]
    chosen = answer["choice"]
    floor = settings.get("confidence_floor", data.get("confidence_floor", 0.55))
    deferred = confidence < floor
    fallback = settings.get("default_model") or data.get("default_model") or models[0]["key"]
    if fallback not in by_key:
        fallback = models[0]["key"]
    final = fallback if deferred else chosen
    entry = build_request(task, models, gold=None, instructions=instructions, lang=lang)
    entry.update({
        "id": uuid.uuid4().hex[:8],
        "group": "router",
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "chosen": chosen,
        "confidence": confidence,
        "deferred": deferred,
        "final": final,
    })
    if settings.get("log_decisions", True):
        with LOG_PATH.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
    return {
        "model": final,
        "model_id": by_key[final]["id"],
        "runner": by_key[final].get("runner", "opencode"),
        "chosen_by_basal": chosen,
        "confidence": confidence,
        "confidence_floor": floor,
        "deferred": deferred,
        "ranking": [
            {"model": key, "model_id": by_key[key]["id"], "probability": round(p, 4)}
            for key, p in ranking[:5]
        ],
        "latency_ms": body.get("usage", {}).get("latency_ms"),
    }


def load_providers():
    if PROVIDERS_PATH.exists():
        return json.loads(PROVIDERS_PATH.read_text(encoding="utf-8"))
    return {}


def hermes_general_enabled():
    """Hermes is reserved for model-registry updates; every other use needs this explicit toggle."""
    return bool(load_settings().get("hermes_enabled", False))


def execute_hermes(model_key, task, timeout_s=600):
    """Run a task through the isolated Hermes agent: its own HERMES_HOME (no access to the user's
    Hermes installation, config or rules), a fresh scratch working directory, a hard turn and
    wall-clock budget. model_key may be "auto" — then basal routes the model per task."""
    model_key = model_key or "auto"
    scratch = HERMES_SANDBOX.parent / "scratch" / time.strftime("%Y%m%d-%H%M%S")
    scratch.mkdir(parents=True, exist_ok=True)
    env = {**os.environ, "HERMES_HOME": str(HERMES_SANDBOX)}
    budget = max(60, min(int(timeout_s), 900))
    proc = subprocess.run(
        [HERMES, "chat", "-Q", "--format", "text", "--query-file", "-",
         "--provider", "custom", "--base-url", "http://127.0.0.1:8788/v1",
         "--api-key", "router-local", "-m", f"router/{model_key}",
         "--in", str(scratch),
         "--ignore-user-config", "--ignore-rules", "--safe-mode"],
        input=task, capture_output=True, text=True,
        timeout=timeout_s + 60, env=env, cwd=str(scratch),
    )
    if proc.stdout.strip():
        return proc.stdout[-20000:]
    raise RuntimeError(f"hermes sandbox returned no output: {proc.stderr[-500:] or '(no stderr)'}")


def resolve_hermes_model(requested=None):
    """Model the sandbox agent works on: explicit request > the panel setting > auto (basal routes per task)."""
    return requested or load_settings().get("hermes_model") or "auto"


def _parse_opencode_jsonl(stdout):
    texts = []
    for line in stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except Exception:
            return None
        part = event.get("part") or {}
        if event.get("type") == "text" and part.get("text"):
            texts.append(part["text"])
    return "\n".join(texts) if texts else None


def execute(model_key, task, timeout_s=600, runner=None, workdir=None, messages=None):
    """Execute a task on the chosen model. `messages` carries the original OpenAI-format messages
    (roles, history, multimodal parts) for direct providers; the CLI runner gets the full task text.
    Every call is passively timed (no synthetic traffic) unless measure_performance is off."""
    entry = next((m for m in load_registry()["models"] if m["key"] == model_key), None)
    if entry is None:
        raise RuntimeError(f"unknown model: {model_key}")
    runner = runner or entry.get("runner", "opencode")
    started = time.time()
    ok, tokens = False, None
    try:
        if runner == "hermes":
            if not hermes_general_enabled():
                raise RuntimeError("Hermes is reserved for model-registry updates. Enable 'Hermes agent "
                                   "(general use)' in the panel settings to run other tasks with it.")
            result = execute_hermes(model_key or resolve_hermes_model(), task, timeout_s)
            ok = True
            return result
        if runner == "opencode":
            proc = subprocess.run(
                [OPENCODE, "run", "--format", "json", "-m", entry["id"], task],
                capture_output=True, text=True, timeout=timeout_s, cwd=workdir,
            )
            parsed = _parse_opencode_jsonl(proc.stdout) if proc.stdout else None
            if parsed or proc.stdout.strip():
                ok = True
                return (parsed or proc.stdout)[-20000:]
            raise RuntimeError(f"opencode returned no output: {proc.stderr[-500:] or '(no stderr)'}")
        if runner == "direct":
            provider = load_providers().get(entry.get("provider"), {})
            if not provider.get("apiKey"):
                raise RuntimeError(f"missing API key for '{entry.get('provider')}': fill in providers.json")
            response = httpx.post(
                f"{provider['baseURL'].rstrip('/')}/chat/completions",
                headers={"Authorization": f"Bearer {provider['apiKey']}"},
                json={"model": entry.get("model", entry["id"]),
                      "messages": messages or [{"role": "user", "content": task}]},
                timeout=timeout_s,
            )
            response.raise_for_status()
            body = response.json()
            tokens = (body.get("usage") or {}).get("completion_tokens")
            ok = True
            return body["choices"][0]["message"]["content"]
        raise RuntimeError(f"unknown runner: {runner}")
    finally:
        if performance is not None and load_settings().get("measure_performance", True):
            try:
                performance.record(model_key, runner, ok, time.time() - started, tokens)
            except Exception:
                pass


def annotate():
    lines = [json.loads(line) for line in LOG_PATH.read_text(encoding="utf-8").splitlines() if line.strip()]
    for entry in lines:
        question = entry["questions"]["model"]
        if question.get("gold"):
            continue
        print("\n---", entry["id"], entry["ts"])
        print(entry["state"][:400])
        print("basal:", entry["chosen"], f"conf={entry['confidence']:.2f}")
        print("options:", ", ".join(question["criteria"]))
        answer = input("gold (enter=accept basal's pick, s=skip): ").strip()
        if answer.lower() == "s":
            continue
        question["gold"] = answer or entry["chosen"]
    with LOG_PATH.open("w", encoding="utf-8") as handle:
        for entry in lines:
            handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
    print("saved", LOG_PATH)


def main():
    command = sys.argv[1] if len(sys.argv) > 1 else "route"
    if command == "route":
        task = " ".join(sys.argv[2:]).strip()
        if not task:
            raise SystemExit('usage: router.py route "task description"')
        print(json.dumps(route(task), ensure_ascii=False, indent=2))
    elif command == "models":
        usable = {m["key"] for m in usable_models()}
        for model in load_registry()["models"]:
            mark = "" if model["key"] in usable else "   [needs a key — inactive]"
            print(f"{model['key']:20} {model['id']}{mark}")
    elif command == "hermes":
        if not hermes_general_enabled():
            raise SystemExit("Hermes is reserved for model-registry updates. Enable 'Hermes agent "
                              "(general use)' in the panel settings to run other tasks with it.")
        rest = sys.argv[2:]
        keys = {m["key"] for m in load_registry()["models"]}
        requested = rest[0] if rest and (rest[0] in keys or rest[0] == "auto") else None
        task = " ".join(rest[1:] if requested else rest).strip()
        if not task:
            raise SystemExit('usage: router.py hermes [model_key|auto] "task"')
        model = resolve_hermes_model(requested)
        print(json.dumps({"model": model, "answer": execute_hermes(model, task)},
                         ensure_ascii=False, indent=2))
    elif command == "annotate":
        annotate()
    else:
        raise SystemExit("commands: route | models | annotate | hermes")


if __name__ == "__main__":
    main()
