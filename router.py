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
REGISTRY_PATH = DIR / "models.json"
LOG_PATH = DIR / "decisions.jsonl"
BASAL_URL = os.environ.get("BASAL_URL", "http://127.0.0.1:8000")
PROVIDERS_PATH = DIR / "providers.json"
SETTINGS_PATH = DIR / "settings.json"
OPENCODE = "/home/krzysiek/.opencode/bin/opencode"
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
    """Registry entries executable right now: opencode models always, direct models only with a saved key."""
    data = data or load_registry()
    providers = load_providers()
    usable = []
    for m in data["models"]:
        if m.get("runner", "opencode") == "direct":
            if not (providers.get(m.get("provider")) or {}).get("apiKey"):
                continue
        usable.append(m)
    return usable


def build_request(task, models, gold=None):
    criteria = {m["key"]: f"{m['name']}. {m['card']}" for m in models}
    question = {"type": "choice", "instructions": INSTRUCTIONS, "criteria": criteria}
    if gold is not None:
        question["gold"] = gold
    return {"state": task, "lang": "pl", "questions": {"model": question}}


def route(task):
    data = load_registry()
    settings = load_settings()
    models = usable_models(data)
    if not models:
        raise RuntimeError(NO_MODELS_HINT)
    by_key = {m["key"]: m for m in models}
    response = httpx.post(f"{BASAL_URL}/v1/systemone", json=build_request(task, models), timeout=180)
    response.raise_for_status()
    body = response.json()
    answer = body["answers"]["model"]
    ranking = sorted(answer["probabilities"].items(), key=lambda item: -item[1])
    confidence = answer["confidence"]
    chosen = answer["choice"]
    floor = settings.get("confidence_floor", data.get("confidence_floor", 0.55))
    deferred = confidence < floor
    final = data.get("default_model", models[0]["key"]) if deferred else chosen
    entry = build_request(task, models, gold=None)
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


def execute(model_key, task, timeout_s=600):
    entry = next((m for m in load_registry()["models"] if m["key"] == model_key), None)
    if entry is None:
        raise RuntimeError(f"unknown model: {model_key}")
    runner = entry.get("runner", "opencode")
    if runner == "opencode":
        proc = subprocess.run(
            [OPENCODE, "run", "-m", entry["id"], task],
            capture_output=True, text=True, timeout=timeout_s,
        )
        if proc.stdout.strip():
            return proc.stdout[-20000:]
        raise RuntimeError(f"opencode returned no output: {proc.stderr[-500:] or '(no stderr)'}")
    if runner == "direct":
        provider = load_providers().get(entry.get("provider"), {})
        if not provider.get("apiKey"):
            raise RuntimeError(f"missing API key for '{entry.get('provider')}': fill in providers.json")
        response = httpx.post(
            f"{provider['baseURL'].rstrip('/')}/chat/completions",
            headers={"Authorization": f"Bearer {provider['apiKey']}"},
            json={"model": entry.get("model", entry["id"]),
                  "messages": [{"role": "user", "content": task}]},
            timeout=timeout_s,
        )
        response.raise_for_status()
        return response.json()["choices"][0]["message"]["content"]
    raise RuntimeError(f"unknown runner: {runner}")


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
    elif command == "annotate":
        annotate()
    else:
        raise SystemExit("commands: route | models | annotate")


if __name__ == "__main__":
    main()
