#!/home/krzysiek/basal-env/bin/python
"""Rebuilds the raw models.dev card variant (models.net.json).

Pulls https://models.dev/api.json (the registry opencode itself uses) and builds cards from real data:
context/output limits, cost, capabilities and the source description. Models without data keep their
current card. The production registry (models.json) is never touched by this script.
"""
import json
import time
from pathlib import Path

import httpx

DIR = Path(__file__).resolve().parent
TARGET = DIR / "models.net.json"
API = "https://models.dev/api.json"
CACHE = Path("/tmp/kilo/modelsdev.json")


def human(n):
    if not n:
        return "?"
    if n >= 1000:
        return f"{n // 1000}k"
    return str(n)


def capabilities_of(model):
    caps = []
    if model.get("reasoning"):
        caps.append("deep reasoning")
    if model.get("tool_call"):
        caps.append("tools (function calling)")
    inputs = (model.get("modalities") or {}).get("input", [])
    for token, label in (("image", "images (vision)"), ("video", "video"), ("pdf", "PDF")):
        if token in inputs:
            caps.append(label)
    if model.get("structured_output"):
        caps.append("structured outputs (JSON)")
    return caps


def shorten(text, limit=160):
    text = " ".join((text or "").split())
    if len(text) <= limit:
        return text
    return text[:limit].rsplit(" ", 1)[0] + "…"


def find_entry(dev, entry_id):
    provider, _, model_id = entry_id.partition("/")
    models = (dev.get(provider) or {}).get("models") or {}
    candidates = [model_id]
    if provider == "nvidia":
        candidates.append(f"nvidia/{model_id}")
    for candidate in candidates:
        if candidate in models:
            return models[candidate]
    return None


def build_card(model):
    limit = model.get("limit") or {}
    cost = model.get("cost") or {}
    parts = [f"Context: {human(limit.get('context'))}, output: {human(limit.get('output'))}."]
    if cost.get("input") == 0 and cost.get("output") == 0:
        parts.append("Free.")
    caps = capabilities_of(model)
    if caps:
        parts.append("Capabilities: " + "; ".join(caps) + ".")
    if model.get("description"):
        parts.append(shorten(model["description"]))
    return " ".join(parts)


def main():
    data = json.loads(TARGET.read_text(encoding="utf-8"))
    if CACHE.exists():
        dev = json.loads(CACHE.read_bytes())
    else:
        dev = httpx.get(API, timeout=180).json()
    updated, missing = [], []
    for entry in data["models"]:
        model = find_entry(dev, entry["id"])
        if model is None:
            missing.append(entry["id"])
            continue
        entry["name"] = model.get("name", entry["name"])
        entry["card"] = build_card(model)
        entry["meta"] = {
            "card_source": "models.dev",
            "fetched": time.strftime("%Y-%m-%d %H:%M"),
            "context": (model.get("limit") or {}).get("context"),
            "output": (model.get("limit") or {}).get("output"),
            "cost_input": (model.get("cost") or {}).get("input"),
            "cost_output": (model.get("cost") or {}).get("output"),
            "capabilities": capabilities_of(model),
            "canonical_model_id": model.get("canonical_model_id"),
            "description_full": model.get("description"),
        }
        updated.append(entry["id"])
    TARGET.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"cards from models.dev: {len(updated)}")
    for model_id in updated:
        print("  +", model_id)
    print(f"no data (card kept): {missing}")


if __name__ == "__main__":
    main()
