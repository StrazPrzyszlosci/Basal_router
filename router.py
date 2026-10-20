#!/home/krzysiek/basal-env/bin/python
"""Router darmowych modeli na silniku decyzyjnym Basal 2.2 (lokalny basal-serve)."""
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
OPENCODE = "/home/krzysiek/.opencode/bin/opencode"
INSTRUCTIONS = (
    "Wybierz model, który najlepiej obsłuży zadanie opisane w stanie. Uwzględnij typ zadania "
    "(kod, tekst, obraz, research, długi dokument), wymaganą głębokość rozumowania, szybkość "
    "i koszt. Dla prostych zadań wybierz najszybszy wystarczający model."
)


def load_registry():
    data = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
    if len(data["models"]) > 20:
        raise SystemExit("tryb ollama odczytuje co najwyżej 20 opcji: zredukuj models.json")
    return data


def build_request(task, models, gold=None):
    criteria = {m["key"]: f"{m['name']}. {m['card']}" for m in models}
    question = {"type": "choice", "instructions": INSTRUCTIONS, "criteria": criteria}
    if gold is not None:
        question["gold"] = gold
    return {"state": task, "lang": "pl", "questions": {"model": question}}


def route(task):
    data = load_registry()
    models = data["models"]
    by_key = {m["key"]: m for m in models}
    response = httpx.post(f"{BASAL_URL}/v1/systemone", json=build_request(task, models), timeout=180)
    response.raise_for_status()
    body = response.json()
    answer = body["answers"]["model"]
    ranking = sorted(answer["probabilities"].items(), key=lambda item: -item[1])
    confidence = answer["confidence"]
    chosen = answer["choice"]
    floor = data.get("confidence_floor", 0.55)
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
        raise RuntimeError(f"nieznany model: {model_key}")
    runner = entry.get("runner", "opencode")
    if runner == "opencode":
        proc = subprocess.run(
            [OPENCODE, "run", "-m", entry["id"], task],
            capture_output=True, text=True, timeout=timeout_s,
        )
        if proc.stdout.strip():
            return proc.stdout[-20000:]
        raise RuntimeError(f"opencode bez wyniku: {proc.stderr[-500:] or '(brak stderr)'}")
    if runner == "direct":
        provider = load_providers().get(entry.get("provider"), {})
        if not provider.get("apiKey"):
            raise RuntimeError(f"brak klucza API dla '{entry.get('provider')}': uzupełnij providers.json")
        response = httpx.post(
            f"{provider['baseURL'].rstrip('/')}/chat/completions",
            headers={"Authorization": f"Bearer {provider['apiKey']}"},
            json={"model": entry.get("model", entry["id"]),
                  "messages": [{"role": "user", "content": task}]},
            timeout=timeout_s,
        )
        response.raise_for_status()
        return response.json()["choices"][0]["message"]["content"]
    raise RuntimeError(f"nieznany runner: {runner}")


def annotate():
    lines = [json.loads(line) for line in LOG_PATH.read_text(encoding="utf-8").splitlines() if line.strip()]
    for entry in lines:
        question = entry["questions"]["model"]
        if question.get("gold"):
            continue
        print("\n---", entry["id"], entry["ts"])
        print(entry["state"][:400])
        print("basal:", entry["chosen"], f"conf={entry['confidence']:.2f}")
        print("opcje:", ", ".join(question["criteria"]))
        answer = input("gold (enter=potwierdź basal, s=pomiń): ").strip()
        if answer.lower() == "s":
            continue
        question["gold"] = answer or entry["chosen"]
    with LOG_PATH.open("w", encoding="utf-8") as handle:
        for entry in lines:
            handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
    print("zapisano", LOG_PATH)


def main():
    command = sys.argv[1] if len(sys.argv) > 1 else "route"
    if command == "route":
        task = " ".join(sys.argv[2:]).strip()
        if not task:
            raise SystemExit('użycie: router.py route "opis zadania"')
        print(json.dumps(route(task), ensure_ascii=False, indent=2))
    elif command == "models":
        for model in load_registry()["models"]:
            print(f"{model['key']:20} {model['id']}")
    elif command == "annotate":
        annotate()
    else:
        raise SystemExit("komendy: route | models | annotate")


if __name__ == "__main__":
    main()
